"""
app/api/evaluate.py
────────────────────
POST /evaluate — the primary endpoint of the Risk Gatekeeper.

Flow:
  1. Accept a normalised EventCreate payload.
  2. Get or create operation record with exact-action binding.
  3. Persist the event to SQLite.
  4. Run the event through the policy engine (rules_engine.evaluate_event).
  5. Persist the resulting decision to SQLite.
  6. Update operation record with evaluation result.
  7. Broadcast the event+decision over WebSocket to connected dashboard clients.
  8. Return the combined EvaluateResponse to the caller.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.decision import DecisionORM, DecisionResponse, EvaluateResponse
from app.models.event import EventCreate, EventORM, EventResponse
from app.models.operation import (
    OperationORM,
    get_or_create_operation,
    update_operation_state,
    build_canonical_action,
    compute_fingerprint_from_event,
)
from app.policy.rules_engine import evaluate_event
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["evaluate"])


@router.post(
    "/evaluate",
    response_model=EvaluateResponse,
    summary="Submit an agent action for risk evaluation",
    description=(
        "Accepts a normalised event from any adapter (cursor, n8n, or transaction), "
        "runs it through the policy engine, stores the event and decision, "
        "broadcasts over WebSocket, and returns the decision. "
        "Enforces exact-action binding via operation identity."
    ),
)
async def evaluate(
    event_in: EventCreate,
    db: Session = Depends(get_db),
    # Optional: caller can provide operation_id for idempotent retries
    operation_id: str | None = None,
    # Optional: caller can provide agent identity for fingerprinting
    agent_identity: str | None = None,
) -> EvaluateResponse:
    # ── 1. Persist event ───────────────────────────────────────────────────────
    event_orm = EventORM(
        source=event_in.source,
        event_type=event_in.event_type,
        payload=json.dumps(event_in.payload),
        original_goal=event_in.original_goal,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(event_orm)
    db.commit()
    db.refresh(event_orm)

    logger.info(
        "Event %d received — source=%s type=%s",
        event_orm.id,
        event_in.source,
        event_in.event_type,
    )

    # ── 0. Get or create operation with exact-action binding ─────────────────────
    # This enforces idempotency: same operation_id + same fingerprint = existing
    # same operation_id + different fingerprint = 409 Conflict
    try:
        operation, is_new = get_or_create_operation(
            db=db,
            event=event_in,
            event_id=event_orm.id,
            operation_id=operation_id,
            agent_identity=agent_identity,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Operation creation failed: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Operation creation failed: {exc}",
        ) from exc

    logger.info(
        "Event %d received — source=%s type=%s operation_id=%s fingerprint=%s",
        event_orm.id,
        event_in.source,
        event_in.event_type,
        operation.operation_id,
        operation.action_fingerprint[:16],
    )

    # ── 2. Run policy engine (timed — spec KPI Δt < 40ms) ──────────────────────
    import time as _time
    _t0 = _time.perf_counter()
    try:
        decision_data = evaluate_event(event_in)
    except Exception as exc:
        logger.exception("Policy engine error for event %d: %s", event_orm.id, exc)
        update_operation_state(db, operation, "failed", error_info=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Policy engine error: {exc}",
        ) from exc
    _latency_ms = round((_time.perf_counter() - _t0) * 1000.0, 2)

    # ── 3. Persist decision ────────────────────────────────────────────────────
    review_expires_at = None
    if decision_data.verdict == "WARN":
        review_expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.review_timeout_seconds)

    decision_orm = DecisionORM(
        event_id=event_orm.id,
        verdict=decision_data.verdict,
        reasons=json.dumps(decision_data.reasons),
        suggested_fix=decision_data.suggested_fix,
        module=decision_data.module,
        risk_score=decision_data.risk_score,
        explanation=getattr(decision_data, "explanation", ""),
        latency_ms=_latency_ms,
        timestamp=datetime.now(timezone.utc),
        human_decision=None,
        human_timestamp=None,
        review_expires_at=review_expires_at,
    )
    db.add(decision_orm)
    db.commit()
    db.refresh(decision_orm)

    logger.info(
        "Decision %d for event %d — verdict=%s risk=%.4f",
        decision_orm.id,
        event_orm.id,
        decision_orm.verdict,
        decision_orm.risk_score,
    )

    # ── 4. Update operation with evaluation result ──────────────────────────────
    try:
        # Update operation state based on verdict
        if decision_data.verdict == "WARN":
            update_operation_state(
                db=db,
                operation=operation,
                new_state="evaluated",
                review_expires_at=review_expires_at,
            )
        elif decision_data.verdict == "BLOCK":
            update_operation_state(db, operation, "blocked")
        elif decision_data.verdict == "ALLOW":
            update_operation_state(db, operation, "approved")
        else:
            update_operation_state(db, operation, "evaluated")

        # Update canonical action if it was the first evaluation (version 1)
        if operation.action_version == 1:
            canonical = build_canonical_action(event_in, operation.source)
            operation.canonical_action_json = canonical.to_canonical_json()
            operation.action_fingerprint = compute_fingerprint_from_event(event_in, operation.source)
            operation.action_version = 1
            db.commit()
            db.refresh(operation)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Operation state update failed: %s", exc)
        # Don't fail the request if operation update fails
        logger.warning("Operation state update failed (non-fatal): %s", exc)

    logger.info(
        "Decision %d for event %d — verdict=%s risk=%.4f operation_id=%s",
        decision_orm.id,
        event_orm.id,
        decision_orm.verdict,
        decision_orm.risk_score,
        operation.operation_id,
    )

    # ── 5. Build response objects ──────────────────────────────────────────────
    event_resp = EventResponse.model_validate(event_orm)
    decision_resp = DecisionResponse.model_validate(decision_orm)

    # ── 5. Broadcast over WebSocket ────────────────────────────────────────────
    try:
        await manager.broadcast(
            {
                "type": "new_decision",
                "event": event_resp.model_dump(mode="json"),
                "decision": decision_resp.model_dump(mode="json"),
                "operation": {
                    "operation_id": operation.operation_id,
                    "action_fingerprint": operation.action_fingerprint,
                    "lifecycle_state": operation.lifecycle_state,
                },
            }
        )
    except Exception as exc:
        # WebSocket broadcast failure must never block the API response.
        logger.warning("WebSocket broadcast failed: %s", exc)

    # ── 6. Return combined response ────────────────────────────────────────────
    return EvaluateResponse(event=event_resp, decision=decision_resp)
