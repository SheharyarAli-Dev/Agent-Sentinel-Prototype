"""
app/api/n8n_webhook.py
───────────────────────
Dedicated n8n webhook endpoint.

n8n workflows call POST /api/n8n/evaluate directly before executing a risky
action node.  This endpoint:
  1. Accepts n8n-style raw payload.
  2. Normalises it via n8n_adapter.normalise_n8n_event().
  3. Routes through the shared /evaluate pipeline.
  4. Returns a simplified verdict response n8n can act on directly.

A BLOCK verdict means n8n should NOT proceed (the node will throw an error).
A WARN verdict means the action needs human review (the dashboard modal fires).
An ALLOW verdict means proceed normally.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.decision import DecisionORM, DecisionResponse, EvaluateResponse
from app.models.event import EventCreate, EventORM, EventResponse
from app.adapters.n8n_adapter import normalise_n8n_event
from app.policy.rules_engine import evaluate_event
from app.websocket.manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/n8n", tags=["n8n"])


@router.post(
    "/evaluate",
    response_model=EvaluateResponse,
    summary="n8n Webhook — Evaluate workflow action before execution",
    description=(
        "Called by n8n custom node (RiskGatekeeper.node.ts) before a risky action executes. "
        "Normalises the payload, runs policy checks, stores result, broadcasts to dashboard."
    ),
)
async def n8n_evaluate(
    request: Request,
    db: Session = Depends(get_db),
) -> EvaluateResponse:
    """Accept raw n8n payloads and route through the shared evaluation pipeline."""
    body = await request.json()

    # Normalise n8n payload to canonical EventCreate
    event_in: EventCreate = normalise_n8n_event(body)

    # Persist event
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

    # Run policy engine
    decision_data = evaluate_event(event_in)

    # Persist decision
    decision_orm = DecisionORM(
        event_id=event_orm.id,
        verdict=decision_data.verdict,
        reasons=json.dumps(decision_data.reasons),
        suggested_fix=decision_data.suggested_fix,
        module=decision_data.module,
        risk_score=decision_data.risk_score,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(decision_orm)
    db.commit()
    db.refresh(decision_orm)

    event_resp = EventResponse.model_validate(event_orm)
    decision_resp = DecisionResponse.model_validate(decision_orm)

    # Broadcast to live dashboard
    try:
        await manager.broadcast({
            "type": "new_decision",
            "event": event_resp.model_dump(mode="json"),
            "decision": decision_resp.model_dump(mode="json"),
        })
    except Exception as exc:
        logger.warning("n8n WebSocket broadcast failed: %s", exc)

    return EvaluateResponse(event=event_resp, decision=decision_resp)
