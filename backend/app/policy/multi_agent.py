"""
app/policy/multi_agent.py
──────────────────────────
Module — Multi-Agent Safety Reasoning

When agents collaborate and delegate to one another, new risks appear that
single-agent checks cannot see. This module fires when an event describes a
multi-agent interaction (payload 'delegation' or 'from_agent'/'to_agent').

It checks:
  1. Cross-agent privilege escalation — agent A delegates a capability to agent B
     that A itself is not granted (using B as a confused deputy) → BLOCK.
  2. Unsafe delegated task — the delegated task contains a destructive/high-risk
     instruction → BLOCK.
  3. Goal conflict — the delegated task contradicts the stated shared objective → WARN.
  4. Shared-memory poisoning — the interaction writes attacker-style content into
     shared context passed between agents → BLOCK.

Uses the same per-agent capability grants as least-privilege
(data/agent_capabilities.json).

Entry point: evaluate_multi_agent(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import json
import re
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

_UNSAFE = re.compile(
    r"\b(rm\s+-rf|drop\s+table|delete\s+all|wipe|exfiltrat|send\s+.{0,30}(secret|credential|customer).{0,30}(http|@)|disable\s+(safety|guardrail|logging))\b",
    re.I,
)
_POISON = re.compile(
    r"(ignore\s+(previous|system)\s+instructions|you\s+are\s+now|override\s+(safety|policy))",
    re.I,
)


def _is_multi_agent(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("delegation")
        or payload.get("from_agent")
        or payload.get("to_agent")
        or payload.get("agents")
    )


def evaluate_multi_agent(event: EventCreate) -> DecisionCreate:
    payload = event.payload or {}
    if not _is_multi_agent(payload):
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Multi-agent: not a multi-agent interaction."],
            suggested_fix="",
            module="multi_agent",
            risk_score=0.0,
        )

    deleg = payload.get("delegation") or {}
    from_agent = str(payload.get("from_agent") or deleg.get("from_agent") or "")
    to_agent = str(payload.get("to_agent") or deleg.get("to_agent") or "")
    task = str(payload.get("task") or deleg.get("task") or "")
    shared_context = str(payload.get("shared_context") or deleg.get("shared_context") or "")
    capability = str(payload.get("capability") or deleg.get("capability") or "")

    reasons: list[str] = []
    verdict = "ALLOW"
    risk = 0.0
    order = {"ALLOW": 0, "WARN": 1, "BLOCK": 2}

    def _esc(v: str, r: float, msg: str) -> None:
        nonlocal verdict, risk
        if order[v] > order[verdict]:
            verdict = v
        risk = max(risk, r)
        reasons.append(msg)

    # 1. Cross-agent privilege escalation
    if capability and from_agent:
        granter = _AGENTS.get(from_agent, {})
        if granter and capability not in granter.get("allowed_capabilities", []):
            _esc("BLOCK", 0.9,
                 f"Cross-agent privilege escalation: '{from_agent}' delegates capability "
                 f"'{capability}' to '{to_agent}' but is not itself granted it (confused-deputy).")

    # 2. Unsafe delegated task
    if _UNSAFE.search(task):
        _esc("BLOCK", 0.9,
             f"Unsafe delegated task from '{from_agent}' to '{to_agent}': contains a "
             f"destructive/high-risk instruction.")

    # 3. Shared-memory poisoning
    if _POISON.search(shared_context) or _POISON.search(task):
        _esc("BLOCK", 0.9,
             "Shared-context poisoning: the inter-agent message contains instruction-override "
             "content that would compromise the receiving agent.")

    # 4. Goal conflict
    goal = (event.original_goal or "").lower()
    if goal and task and verdict == "ALLOW":
        # crude contradiction check: task negates or diverges strongly from goal
        if re.search(r"\b(instead|regardless|ignore the goal|contrary)\b", task, re.I):
            _esc("WARN", 0.55,
                 "Possible goal conflict: delegated task appears to diverge from the shared objective.")

    if verdict == "ALLOW":
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[f"Multi-agent interaction verified ('{from_agent}' → '{to_agent}')."],
            suggested_fix="",
            module="multi_agent",
            risk_score=0.0,
        )

    fix = (
        "Suspend the collaboration. A delegated agent must never receive capabilities the "
        "delegator lacks, unsafe tasks, or poisoned shared context. Re-scope the delegation to "
        "least privilege and validate shared context before the receiving agent acts."
    )
    return DecisionCreate(
        verdict=verdict, reasons=reasons, suggested_fix=fix,
        module="multi_agent", risk_score=round(risk, 4),
    )
