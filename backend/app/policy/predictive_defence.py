"""
app/policy/predictive_defence.py
─────────────────────────────────
Module 3 — Predictive Defence Engine (Sequence Prediction & Early Attack Forecasting)

Where Sequential Behaviour Analysis BLOCKS a kill-chain once it COMPLETES, this
module FORECASTS the chain while it is still developing and raises an early
warning — before the final damaging step is attempted. It matches the current
session's action trajectory against the *prefixes* of known attack templates and,
when a partial match is found, predicts the likely next step and its risk.

Example: after a single "read sensitive data" step, no rule has been broken yet,
but the Predictive Defence Engine forecasts that the trajectory is on the leading
edge of an exfiltration chain and warns the operator pre-emptively.

Keeps its own lightweight in-memory per-session category history.

Entry point: evaluate_predictive_defence(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from app.models.decision import DecisionCreate
from app.models.event import EventCreate

# session_id -> list[category]
_HISTORY: dict[str, list[str]] = defaultdict(list)
_MAX = 30

# Known multi-step attack templates (ordered category chains) + the human name
# of the forecasted end-state.
_TEMPLATES: list[tuple[list[str], str]] = [
    (["READ_SENSITIVE", "NETWORK_EGRESS"], "data exfiltration"),
    (["READ_SENSITIVE", "EXFIL"], "data exfiltration"),
    (["CREDENTIAL_ACCESS", "NETWORK_EGRESS"], "credential theft"),
    (["PERMISSION_CHANGE", "NETWORK_EGRESS"], "backdoor / tunnel establishment"),
    (["PERMISSION_CHANGE", "CREDENTIAL_ACCESS", "NETWORK_EGRESS"], "privilege-escalation + exfiltration"),
    (["WRITE", "PERMISSION_CHANGE", "DELETE"], "tamper-and-destroy"),
]

_CATEGORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("CREDENTIAL_ACCESS", re.compile(r"\b(secret|credential|password|api[\s_-]?key|token|\.env|id_rsa|shadow)\b", re.I)),
    ("READ_SENSITIVE", re.compile(r"\b(read|open|cat|load|fetch|select|dump)\b.*\b(customer|user|prod|private|confidential|salary|payroll|/etc|database|db|records?)\b", re.I)),
    ("EXFIL", re.compile(r"\b(send|email|upload|post|transmit|forward)\b.*\b(external|client|customer|@|http)", re.I)),
    ("NETWORK_EGRESS", re.compile(r"\b(curl|wget|http[s]?://|socket|tunnel|nc\s|netcat|outbound|connect|upload)\b", re.I)),
    ("PERMISSION_CHANGE", re.compile(r"\b(chmod|chown|setfacl|permission|grant|sudo|acl)\b", re.I)),
    ("DELETE", re.compile(r"\b(rm\s|delete|drop\s+table|truncate|unlink|destroy)\b", re.I)),
    ("WRITE", re.compile(r"\b(write|update|insert|modify|save|commit|create)\b", re.I)),
]


def _text(event: EventCreate) -> str:
    p = event.payload or {}
    parts = [event.event_type]
    for k in ("command", "target", "description", "item"):
        if p.get(k):
            parts.append(str(p[k]))
    if isinstance(p.get("steps"), list):
        for s in p["steps"]:
            if isinstance(s, dict):
                parts += [str(s.get(k, "")) for k in ("command", "target", "description")]
    return " ".join(parts)


def _categorise(text: str) -> str:
    for cat, pat in _CATEGORY_PATTERNS:
        if pat.search(text):
            return cat
    return "OTHER"


def _session(event: EventCreate) -> str:
    return str((event.payload or {}).get("session_id") or f"{event.source}:default")


def reset() -> None:
    _HISTORY.clear()


def evaluate_predictive_defence(event: EventCreate) -> DecisionCreate:
    sid = _session(event)
    cat = _categorise(_text(event))
    hist = _HISTORY[sid]
    hist.append(cat)
    if len(hist) > _MAX:
        del hist[0:len(hist) - _MAX]

    # Only meaningful categories participate in prediction.
    trajectory = [c for c in hist if c != "OTHER"]

    best_forecast: str | None = None
    best_progress = 0.0

    for template, end_state in _TEMPLATES:
        # How much of this template does the tail of the trajectory match, in order?
        # Compute the longest ordered prefix of `template` that is a subsequence
        # of the trajectory ending at the current step.
        matched = 0
        ti = 0
        for c in trajectory:
            if ti < len(template) and c == template[ti]:
                ti += 1
                matched = ti
        # partial (not complete) match on a template of length >= 2
        if 0 < matched < len(template):
            progress = matched / len(template)
            if progress > best_progress:
                best_progress = progress
                nxt = template[matched]
                best_forecast = f"{end_state} (next predicted step: {nxt})"

    if best_forecast and best_progress >= 0.5:
        return DecisionCreate(
            verdict="WARN",
            reasons=[
                f"Predictive defence: the session trajectory matches {int(best_progress*100)}% of a "
                f"known attack pattern — forecasted end-state: {best_forecast}. Early warning raised "
                f"before the chain completes."
            ],
            suggested_fix=(
                "Investigate the session now, before the predicted next step executes. Consider "
                "suspending the agent or requiring approval for the forecasted action."
            ),
            module="predictive_defence",
            risk_score=round(0.4 + 0.3 * best_progress, 4),
        )

    return DecisionCreate(
        verdict="ALLOW",
        reasons=["Predictive defence: trajectory does not match a developing attack pattern."],
        suggested_fix="",
        module="predictive_defence",
        risk_score=0.0,
    )
