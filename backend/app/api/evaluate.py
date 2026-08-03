"""
app/api/evaluate.py
────────────────────
POST /evaluate — the primary endpoint of the Risk Gatekeeper.

Flow:
  1. Accept a normalised EventCreate payload.
  2. Persist the event to SQLite.
  3. Run the event through the policy engine (rules_engine.evaluate_event).
  4. Persist the resulting decision to SQLite.
  5. Broadcast the event+decision over WebSocket to connected dashboard clients.
  6. Return the combined EvaluateResponse to the caller.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.decision import DecisionORM, DecisionResponse, EvaluateResponse
from app.models.event import EventCreate, EventORM, EventResponse
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
        "broadcasts over WebSocket, and returns the decision."
    ),
)
async def evaluate(event_in: EventCreate, db: Session = Depends(get_db)) -> EvaluateResponse:
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

    # ── 2. Run policy engine ───────────────────────────────────────────────────
    try:
        decision_data = evaluate_event(event_in)
    except Exception as exc:
        logger.exception("Policy engine error for event %d: %s", event_orm.id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Policy engine error: {exc}",
        ) from exc

    # ── 3. Persist decision ────────────────────────────────────────────────────
    decision_orm = DecisionORM(
        event_id=event_orm.id,
        verdict=decision_data.verdict,
        reasons=json.dumps(decision_data.reasons),
        suggested_fix=decision_data.suggested_fix,
        module=decision_data.module,
        risk_score=decision_data.risk_score,
        explanation=getattr(decision_data, "explanation", ""),
        timestamp=datetime.now(timezone.utc),
        human_decision=None,
        human_timestamp=None,
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

    # ── 4. Build response objects ──────────────────────────────────────────────
    event_resp = EventResponse.model_validate(event_orm)
    decision_resp = DecisionResponse.model_validate(decision_orm)

    # ── 5. Broadcast over WebSocket ────────────────────────────────────────────
    try:
        await manager.broadcast(
            {
                "type": "new_decision",
                "event": event_resp.model_dump(mode="json"),
                "decision": decision_resp.model_dump(mode="json"),
            }
        )
    except Exception as exc:
        # WebSocket broadcast failure must never block the API response.
        logger.warning("WebSocket broadcast failed: %s", exc)

    # ── 6. Return combined response ────────────────────────────────────────────
    return EvaluateResponse(event=event_resp, decision=decision_resp)
