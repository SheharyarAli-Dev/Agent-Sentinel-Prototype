"""
app/policy/policy_engine.py
────────────────────────────
Module 1 — AI Policy Engine (Governance Before Execution)

Enforces declarative organizational policies against every proposed action
*before* the ML/heuristic module checks run. Policies are data, not code:
they live in data/policies.json and are loaded at import time, so a governance
team can change the rulebook without touching the engine.

This is the "policy-as-code" governance layer: it answers *"is this action
permitted by organizational rules?"* independently of whether it looks
statistically safe. A BLOCK policy is authoritative — it cannot be softened by
any other module because the rules engine takes the most-severe verdict.

Entry point: evaluate_policies(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.models.decision import DecisionCreate
from app.models.event import EventCreate

_POLICIES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "policies.json"


def _load_policies() -> list[dict[str, Any]]:
    try:
        with open(_POLICIES_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh).get("policies", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


_POLICIES: list[dict[str, Any]] = _load_policies()


# ── Field resolution ───────────────────────────────────────────────────────────

def _resolve_field(event: EventCreate, field: str) -> Any:
    """
    Resolve a dotted field path against the event.

    Special field 'any_step' returns a single concatenated string of all textual
    content in the event (event_type + every payload value + every step field),
    so a single regex condition can scan the whole action.
    """
    payload = event.payload or {}

    if field == "any_step":
        parts: list[str] = [event.event_type, event.original_goal or ""]
        parts.extend(_flatten_strings(payload))
        return " \n ".join(p for p in parts if p)

    if field.startswith("payload."):
        return payload.get(field.split(".", 1)[1])

    if field == "source":
        return event.source
    if field == "event_type":
        return event.event_type
    if field == "original_goal":
        return event.original_goal

    return payload.get(field)


def _flatten_strings(obj: Any) -> list[str]:
    """Recursively collect all string/number values from a nested structure."""
    out: list[str] = []
    if isinstance(obj, dict):
        for v in obj.values():
            out.extend(_flatten_strings(v))
    elif isinstance(obj, list):
        for v in obj:
            out.extend(_flatten_strings(v))
    elif isinstance(obj, (str, int, float)):
        out.append(str(obj))
    return out


# ── Condition evaluation ───────────────────────────────────────────────────────

def _check_condition(event: EventCreate, cond: dict[str, Any]) -> bool:
    actual = _resolve_field(event, cond.get("field", ""))
    op = cond.get("op", "equals")
    expected = cond.get("value")

    if actual is None and op not in ("not_equals", "not_in"):
        return False

    try:
        if op == "equals":
            return actual == expected
        if op == "not_equals":
            return actual != expected
        if op == "contains":
            return str(expected).lower() in str(actual).lower()
        if op == "in":
            return actual in expected
        if op == "not_in":
            return actual not in expected
        if op == "gt":
            return float(actual) > float(expected)
        if op == "gte":
            return float(actual) >= float(expected)
        if op == "lt":
            return float(actual) < float(expected)
        if op == "lte":
            return float(actual) <= float(expected)
        if op == "regex":
            return re.search(str(expected), str(actual), re.IGNORECASE) is not None
    except (ValueError, TypeError):
        return False
    return False


def _policy_matches(event: EventCreate, policy: dict[str, Any]) -> bool:
    applies = policy.get("applies_to", ["all"])
    if "all" not in applies and event.source not in applies:
        return False
    # All conditions must hold (logical AND).
    return all(_check_condition(event, c) for c in policy.get("conditions", []))


# ── Entry point ────────────────────────────────────────────────────────────────

_SEVERITY = {"ALLOW": 0, "WARN": 1, "BLOCK": 2}
_RISK = {"ALLOW": 0.0, "WARN": 0.6, "BLOCK": 0.95}


def evaluate_policies(event: EventCreate) -> DecisionCreate:
    """
    Evaluate all applicable policies. Returns the most-severe matching policy's
    verdict; if nothing matches, returns a clean ALLOW.
    """
    matched = [p for p in _POLICIES if _policy_matches(event, p)]

    if not matched:
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Policy engine: no organizational policy matched this action."],
            suggested_fix="",
            module="policy_engine",
            risk_score=0.0,
        )

    worst = max(matched, key=lambda p: _SEVERITY.get(p.get("action", "WARN"), 1))
    verdict = worst.get("action", "WARN")

    reasons = [
        f"Policy {p['id']} ({p.get('action')}): {p.get('message', p.get('description', ''))}"
        for p in matched
    ]
    # suggested_fix required for WARN/BLOCK — take the worst policy's fix.
    fix = worst.get("suggested_fix", "").strip()
    if verdict in ("WARN", "BLOCK") and not fix:
        fix = "Review this action against the cited organizational policy before proceeding."

    return DecisionCreate(
        verdict=verdict,
        reasons=reasons,
        suggested_fix=fix if verdict != "ALLOW" else "",
        module="policy_engine",
        risk_score=_RISK.get(verdict, 0.0),
    )
