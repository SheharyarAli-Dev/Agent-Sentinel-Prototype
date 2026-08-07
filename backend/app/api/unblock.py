"""
app/api/unblock.py
──────────────────
POST /api/unblock/{event_id} — human operator override for a BLOCK decision.

Allows an authorised reviewer to unblock a BLOCK verdict that was issued by
the policy engine, recording the override decision and broadcasting it to the
live dashboard via WebSocket.

This is intentionally separate from the /decide endpoint (which handles WARN
approvals/rejections) so that the two flows cannot accidentally interfere with
each other.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.decision import DecisionORM, DecisionResponse
from app.models.event import EventORM
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["unblock"])


@router.post(
    "/unblock/{event_id}",
    response_model=DecisionResponse,
    summary="Unblock a BLOCK decision (human operator override)",
    description=(
        "Allows a human operator to override a BLOCK verdict. "
        "Records unblocked_by_human=True and broadcasts the update to the dashboard. "
        "Only applicable to BLOCK decisions that have not already been unblocked."
    ),
)
async def unblock_action(
    event_id: int,
    db: Session = Depends(get_db),
) -> DecisionResponse:
    # ── Validate event exists ──────────────────────────────────────────────────
    event = db.query(EventORM).filter(EventORM.id == event_id).first()
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id} not found.",
        )

    # ── Validate decision exists ───────────────────────────────────────────────
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

    # ── Only BLOCK decisions can be unblocked ──────────────────────────────────
    if decision.verdict != "BLOCK":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Only BLOCK decisions can be unblocked. "
                f"Event {event_id} has verdict='{decision.verdict}'."
            ),
        )

    # ── Already unblocked? ─────────────────────────────────────────────────────
    if decision.unblocked_by_human:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Event {event_id} has already been unblocked.",
        )

    # ── Record the unblock ─────────────────────────────────────────────────────
    decision.unblocked_by_human = True
    decision.unblock_timestamp = datetime.now(timezone.utc)
    db.commit()
    db.refresh(decision)

    logger.info("Human unblock for event %d — BLOCK overridden.", event_id)

    # ── Broadcast to live dashboard ────────────────────────────────────────────
    decision_resp = DecisionResponse.model_validate(decision)
    try:
        await manager.broadcast(
            {
                "type": "human_unblock",
                "event_id": event_id,
                "decision": decision_resp.model_dump(mode="json"),
            }
        )
    except Exception as exc:
        logger.warning("WebSocket broadcast failed after unblock: %s", exc)

    return decision_resp
