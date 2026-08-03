"""
app/policy/sequential_behaviour.py
───────────────────────────────────
Module — Sequential Behaviour Analysis Engine (Temporal / Trajectory Monitoring)

Single-action checks miss attacks that only emerge across a SEQUENCE of steps.
Individually-innocuous actions — read a sensitive file, change a permission,
open an outbound connection, send data externally — form a *kill chain* when
they occur together in one session. This module models the trajectory of a
session and flags dangerous patterns and risk accumulation that per-action
modules cannot see.

State: a lightweight in-memory per-session tracker (resets on restart — fine for
a prototype). Each incoming action is categorised, appended to its session's
history, and the accumulated trajectory is evaluated.

Categories detected from tool/target/command/description text:
  READ_SENSITIVE · PERMISSION_CHANGE · NETWORK_EGRESS · EXFIL · DELETE ·
  CREDENTIAL_ACCESS · WRITE · OTHER

Dangerous trajectory patterns:
  - READ_SENSITIVE  →  later EXFIL/NETWORK_EGRESS      = data-exfiltration chain (BLOCK)
  - PERMISSION_CHANGE → later NETWORK_EGRESS           = tunnel / backdoor setup (WARN→BLOCK)
  - CREDENTIAL_ACCESS → later NETWORK_EGRESS/EXFIL     = credential theft chain (BLOCK)
  - many risky steps accumulating in one session        = escalation (WARN)
  - burst of actions in a short window                  = velocity anomaly (WARN)
  - repeated risky/blocked-style steps                  = probing (WARN)

Entry point: evaluate_sequence(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from typing import Any

from app.models.decision import DecisionCreate
from app.models.event import EventCreate

# ── In-memory session state ─────────────────────────────────────────────────────
# session_id -> list of {cat, text, ts}
_SESSIONS: dict[str, list[dict[str, Any]]] = defaultdict(list)

_MAX_HISTORY = 50            # cap per session
_BURST_WINDOW_S = 10.0       # seconds
_BURST_COUNT = 5            # >5 actions within window = burst
_ESCALATION_RISKY = 3       # >=3 risky-category steps in a session = escalation

# ── Categorisation ──────────────────────────────────────────────────────────────
_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("CREDENTIAL_ACCESS", re.compile(r"\b(secret|credential|password|api[\s_-]?key|token|\.env|id_rsa|shadow)\b", re.I)),
    ("READ_SENSITIVE", re.compile(r"\b(read|open|cat|load|fetch|select)\b.*\b(customer|user|prod(uction)?|private|confidential|salary|payroll|/etc|database|db)\b", re.I)),
    ("EXFIL", re.compile(r"\b(send|email|upload|post|transmit|forward)\b.*\b(external|client|customer|@|http)", re.I)),
    ("NETWORK_EGRESS", re.compile(r"\b(curl|wget|http[s]?://|socket|tunnel|nc\s|netcat|outbound|connect)\b", re.I)),
    ("PERMISSION_CHANGE", re.compile(r"\b(chmod|chown|setfacl|permission|access[\s_-]?control|grant|sudo)\b", re.I)),
    ("DELETE", re.compile(r"\b(rm\s|delete|drop\s+table|truncate|unlink|destroy)\b", re.I)),
    ("WRITE", re.compile(r"\b(write|update|insert|modify|save|commit)\b", re.I)),
]

_RISKY_CATS = {"CREDENTIAL_ACCESS", "READ_SENSITIVE", "EXFIL", "NETWORK_EGRESS", "PERMISSION_CHANGE", "DELETE"}


def _action_text(event: EventCreate) -> str:
    payload = event.payload or {}
    parts: list[str] = [event.event_type]
    for k in ("command", "target", "description", "item", "merchant_name"):
        if payload.get(k):
            parts.append(str(payload[k]))
    if isinstance(payload.get("steps"), list):
        for s in payload["steps"]:
            if isinstance(s, dict):
                for k in ("command", "target", "description", "code"):
                    if s.get(k):
                        parts.append(str(s[k]))
    return " ".join(parts)


def _categorise(text: str) -> str:
    for cat, pat in _CATEGORY_PATTERNS:
        if pat.search(text):
            return cat
    return "OTHER"


def _session_id(event: EventCreate) -> str:
    payload = event.payload or {}
    return str(payload.get("session_id") or f"{event.source}:default")


def reset_sessions() -> None:
    """Testing helper — clear all session state."""
    _SESSIONS.clear()


def evaluate_sequence(event: EventCreate) -> DecisionCreate:
    sid = _session_id(event)
    text = _action_text(event)
    cat = _categorise(text)
    now = time.time()

    history = _SESSIONS[sid]
    prior_cats = [h["cat"] for h in history]

    # Append current action to the trajectory.
    history.append({"cat": cat, "text": text[:160], "ts": now})
    if len(history) > _MAX_HISTORY:
        del history[0: len(history) - _MAX_HISTORY]

    reasons: list[str] = []
    verdict = "ALLOW"
    risk = 0.0

    def _escalate(v: str, r: float, msg: str) -> None:
        nonlocal verdict, risk
        order = {"ALLOW": 0, "WARN": 1, "BLOCK": 2}
        if order[v] > order[verdict]:
            verdict = v
        risk = max(risk, r)
        reasons.append(msg)

    # ── Pattern 1: exfiltration chain ──────────────────────────────────────────
    if cat in ("EXFIL", "NETWORK_EGRESS"):
        if "READ_SENSITIVE" in prior_cats or "CREDENTIAL_ACCESS" in prior_cats:
            src = "sensitive-data read" if "READ_SENSITIVE" in prior_cats else "credential access"
            _escalate(
                "BLOCK", 0.9,
                f"Data-exfiltration chain detected in session '{sid}': earlier {src} "
                f"followed now by outbound/external transfer. Individually benign steps form "
                f"an exfiltration kill-chain.",
            )

    # ── Pattern 2: backdoor/tunnel setup ───────────────────────────────────────
    if cat == "NETWORK_EGRESS" and "PERMISSION_CHANGE" in prior_cats:
        _escalate(
            "WARN", 0.65,
            f"Possible backdoor setup in session '{sid}': permission change earlier, "
            f"outbound connection now.",
        )

    # ── Pattern 3: risk escalation across the session ──────────────────────────
    risky_count = sum(1 for c in prior_cats + [cat] if c in _RISKY_CATS)
    if risky_count >= _ESCALATION_RISKY and verdict == "ALLOW":
        _escalate(
            "WARN", 0.55,
            f"Risk escalation in session '{sid}': {risky_count} sensitive/risky actions "
            f"accumulated in this trajectory.",
        )

    # ── Pattern 4: velocity / burst anomaly ────────────────────────────────────
    recent = [h for h in history if now - h["ts"] <= _BURST_WINDOW_S]
    if len(recent) > _BURST_COUNT and verdict == "ALLOW":
        _escalate(
            "WARN", 0.5,
            f"Velocity anomaly in session '{sid}': {len(recent)} actions within "
            f"{int(_BURST_WINDOW_S)}s (possible automated abuse).",
        )

    if verdict == "ALLOW":
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[
                f"Sequential behaviour normal (session '{sid}', step {len(history)}, "
                f"category {cat})."
            ],
            suggested_fix="",
            module="sequential_behaviour",
            risk_score=0.0,
        )

    fix = (
        "Review the full session trajectory, not just this action. Suspend the session and "
        "confirm the sequence of steps is intended before allowing the current action; a "
        "multi-step attack pattern was detected across the agent's recent history."
    )
    return DecisionCreate(
        verdict=verdict,
        reasons=reasons,
        suggested_fix=fix,
        module="sequential_behaviour",
        risk_score=round(risk, 4),
    )
