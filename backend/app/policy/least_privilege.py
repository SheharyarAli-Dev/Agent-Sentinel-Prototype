"""
app/policy/least_privilege.py
──────────────────────────────
Module — Least-Privilege / Least-Agency Enforcement

The single highest-impact control in the 2026 literature: even a low-permission
agent can do real harm if it is allowed to act without checks. This module
enforces two boundaries per agent, from data/agent_capabilities.json:

  1. Least PRIVILEGE — the action's capability (tool/event_type) must be on the
     agent's granted allowlist. An ungranted capability is a privilege
     violation → BLOCK. This is the "confused deputy" / over-privilege defense:
     an agent that cannot call a capability cannot be tricked into misusing it.

  2. Least AGENCY — even a *granted* capability may not exceed the agent's
     authorised impact tier (reversibility 0–3) without human review. A
     high-impact/irreversible action beyond the cap → WARN (escalate to human).

Entry point: evaluate_least_privilege(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.decision import DecisionCreate
from app.models.event import EventCreate

_CAPS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "agent_capabilities.json"


def _load() -> dict[str, Any]:
    try:
        with open(_CAPS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh).get("agents", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_AGENTS = _load()

# Reversibility tiers for common capabilities (0=read → 3=irreversible).
_IMPACT_TIER = {
    "read_file": 0, "list_directory": 0, "search_web": 0, "rag_retrieval": 0,
    "query_balance": 0, "read_record": 0, "run_tests": 0, "query_database": 0,
    "write_file": 1, "write_record": 1, "transform_data": 1, "webhook_action": 1,
    "http_request": 1, "send_notification": 1, "plan_execution": 1,
    "update_record": 2, "set_permissions": 2, "purchase": 2, "move_file": 2,
    "delete_file": 3, "drop_table": 3, "send_email": 3, "refund": 3,
    "api_post_external": 3,
}


def _declared_capability(event: EventCreate) -> str | None:
    """
    Only enforce least-privilege when a capability is EXPLICITLY declared, so
    freeform event_types are never wrongly blocked. Recognised declarations:
    payload['capability'], payload['tool_name'], or a step's 'type'/'tool'.
    """
    payload = event.payload or {}
    cap = payload.get("capability") or payload.get("tool_name")
    if not cap and isinstance(payload.get("steps"), list) and payload["steps"]:
        first = payload["steps"][0]
        if isinstance(first, dict):
            cap = first.get("capability") or first.get("tool")
    return str(cap) if cap else None


def evaluate_least_privilege(event: EventCreate) -> DecisionCreate:
    grant = _AGENTS.get(event.source)
    cap = _declared_capability(event)

    if not grant or cap is None:
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Least-privilege: no explicit capability declared to check."],
            suggested_fix="",
            module="least_privilege",
            risk_score=0.0,
        )

    allowed = grant.get("allowed_capabilities", [])
    max_tier = grant.get("max_impact_tier", 3)
    tier = _IMPACT_TIER.get(cap, 1)

    # 1. Least privilege — capability must be granted.
    if cap not in allowed:
        return DecisionCreate(
            verdict="BLOCK",
            reasons=[
                f"Privilege violation: agent '{event.source}' is not granted the capability "
                f"'{cap}'. Least-privilege denies capabilities outside the agent's role."
            ],
            suggested_fix=(
                f"If '{cap}' is legitimately required, add it to the agent's grant in "
                f"agent_capabilities.json; otherwise this is an over-privilege / confused-deputy attempt."
            ),
            module="least_privilege",
            risk_score=0.9,
        )

    # 2. Least agency — granted, but impact exceeds the cap.
    if tier > max_tier:
        return DecisionCreate(
            verdict="WARN",
            reasons=[
                f"Least-agency limit: capability '{cap}' is granted but its impact tier ({tier}) "
                f"exceeds the agent's authorised ceiling ({max_tier}); requires human review."
            ],
            suggested_fix=(
                "This is an irreversible/high-impact action beyond the agent's autonomous authority. "
                "Require explicit human approval before it executes."
            ),
            module="least_privilege",
            risk_score=0.6,
        )

    return DecisionCreate(
        verdict="ALLOW",
        reasons=[f"Least-privilege OK: '{cap}' is granted to '{event.source}' (impact tier {tier} ≤ {max_tier})."],
        suggested_fix="",
        module="least_privilege",
        risk_score=0.0,
    )
