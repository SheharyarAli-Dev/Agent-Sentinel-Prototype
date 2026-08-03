"""
app/adapters/n8n_adapter.py
─────────────────────────────
n8n adapter — normalises n8n workflow action events into EventCreate and
exposes a webhook endpoint n8n workflows call before executing an action.

This adapter is imported by main.py in Phase 5, which adds:
  POST /api/n8n/evaluate  — webhook endpoint for n8n to call directly.

Module routing for n8n events:
  - Module 7 (planning_verification): whole-plan safety checks only
    (code-quality pattern checks are skipped for n8n; handled in rules_engine.py
     by passing event.source = "n8n" to evaluate_plan)
  - Module 6 (intent_verification): drift check if original_goal provided
"""
from __future__ import annotations

from typing import Any
from app.models.event import EventCreate


def normalise_n8n_event(raw_payload: dict[str, Any]) -> EventCreate:
    """
    Convert a raw n8n node payload into a normalised EventCreate.

    Expected raw_payload fields:
      workflow_id   — str
      node_type     — str, e.g. "EmailSend", "HttpRequest"
      node_name     — str
      parameters    — dict, node configuration
      goal          — str (optional), user's stated workflow goal
      steps         — list[dict] (optional), full workflow step list

    Returns:
        Normalised EventCreate.
    """
    node_type = raw_payload.get("node_type") or raw_payload.get("event_type") or "workflow_action"
    goal = raw_payload.get("goal") or raw_payload.get("original_goal")

    # Build steps list from n8n node parameters
    steps = raw_payload.get("steps")
    if not steps:
        node_name = raw_payload.get("node_name") or node_type
        params = raw_payload.get("parameters") or raw_payload.get("params") or {}
        desc = f"Execute n8n node: {node_name}"

        # Extract target URL / recipient / table for safety checking
        target = (
            params.get("url")
            or params.get("to")
            or params.get("table")
            or params.get("destination")
            or params.get("command")
            or ""
        )

        steps = [{
            "type": node_type,
            "target": str(target),
            "description": desc,
            "params": params,
        }]

    payload = {
        **raw_payload,
        "steps": steps,
    }

    return EventCreate(
        source="n8n",
        event_type=node_type,
        payload=payload,
        original_goal=goal,
    )
