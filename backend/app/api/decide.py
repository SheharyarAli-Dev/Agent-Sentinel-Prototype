"""
app/api/decide.py
──────────────────
POST /decide/{event_id} — human approve/reject endpoint.

Allows a human reviewer to act on a WARN-status decision that is pending
approval.  Validates that:
  - The event exists.
  - The event has exactly one associated decision.
  - The decision's current verdict is WARN (only WARN events are actionable;
    ALLOW decisions need no human input, BLOCK decisions are refused outright).

Updates the decision's human_decision and human_timestamp columns, then
broadcasts the updated decision over WebSocket so the dashboard reflects
the human's choice immediately.

GET /decide/{event_id} — returns the current decision for an event (useful
for polling or inspecting a specific event's status from the frontend).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.decision import DecisionResponse, HumanDecisionRequest, DecisionORM
from app.models.event import EventORM
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["decide"])


@router.get(
    "/decide/{event_id}",
    response_model=DecisionResponse,
    summary="Get the current decision for an event",
)
async def get_decision(event_id: int, db: Session = Depends(get_db)) -> DecisionResponse:
    decision = _get_decision_or_404(event_id, db)
    return DecisionResponse.model_validate(decision)


@router.post(
    "/decide/{event_id}",
    response_model=DecisionResponse,
    summary="Submit a human approve/reject decision for a WARN event",
    description=(
        "Only WARN-status events are actionable. "
        "ALLOW events don't need review; BLOCK events cannot be overridden here."
    ),
)
async def submit_decision(
    event_id: int,
    body: HumanDecisionRequest,
    db: Session = Depends(get_db),
) -> DecisionResponse:
    # ── Validate event exists ──────────────────────────────────────────────────
    event = db.query(EventORM).filter(EventORM.id == event_id).first()
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )

    # ── Validate decision exists and is WARN ───────────────────────────────────
    decision = _get_decision_or_404(event_id, db)

    if decision.verdict != "WARN":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Only WARN decisions can receive a human review. "
                f"Event {event_id} has verdict='{decision.verdict}'."
            ),
        )

    if decision.human_decision is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Event {event_id} has already been reviewed "
                f"(human_decision='{decision.human_decision}')."
            ),
        )

    # ── Record human decision ──────────────────────────────────────────────────
    decision.human_decision = body.decision
    decision.human_timestamp = datetime.now(timezone.utc)
    db.commit()
    db.refresh(decision)

    # ── Module 10 — feed the human decision into continual learning ────────────
    try:
        from app.models.event import EventCreate
        from app.policy.feedback_learning import signature_for, record_feedback
        ev = EventCreate(
            source=event.source,
            event_type=event.event_type,
            payload=json.loads(event.payload) if isinstance(event.payload, str) else (event.payload or {}),
            original_goal=event.original_goal,
        )
        record_feedback(signature_for(ev), body.decision)
    except Exception as exc:  # never let learning break the endpoint
        logger.warning("Feedback learning record failed: %s", exc)

    logger.info(
        "Human decision for event %d: %s", event_id, body.decision
    )

    # ── Broadcast updated decision ─────────────────────────────────────────────
    decision_resp = DecisionResponse.model_validate(decision)
    try:
        await manager.broadcast(
            {
                "type": "human_decision",
                "event_id": event_id,
                "decision": decision_resp.model_dump(mode="json"),
            }
        )
    except Exception as exc:
        logger.warning("WebSocket broadcast failed after human decision: %s", exc)

    return decision_resp


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_decision_or_404(event_id: int, db: Session) -> DecisionORM:
    decision = (
        db.query(DecisionORM)
        .filter(DecisionORM.event_id == event_id)
        .order_by(DecisionORM.id.desc())
        .first()
    )
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No decision found for event {event_id}.",
        )
    return decision
