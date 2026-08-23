"""
app/policy/feedback_learning.py
────────────────────────────────
Module 10 — Human Feedback Safety Learning Engine (Continual HITL Learning)

Turns human approve/reject decisions into durable learning. Each reviewed action
is reduced to a stable *signature* (source + capability/event-type + key payload
shape); the running tally of human approvals/rejections for that signature is
remembered. On future evaluations the engine adapts:

  - Signature repeatedly APPROVED by humans (>= LEARN_THRESHOLD, no recent
    rejections) → a fresh WARN on the same signature is auto-downgraded to ALLOW,
    reducing reviewer fatigue. (Spec KPI: adapts within <= 3 occurrences.)
  - Signature repeatedly REJECTED → a fresh ALLOW is upgraded to WARN/BLOCK.

State is an in-memory store (resets on restart — fine for a prototype).

Public API:
    signature_for(event) -> str
    record_feedback(signature, human_decision)   # 'approved' | 'rejected'
    apply_learning(event, final_verdict) -> (verdict, note|None)
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from app.models.event import EventCreate

LEARN_THRESHOLD = 3  # human confirmations before auto-adaptation (spec: <= 3)

# signature -> {"approved": int, "rejected": int}
_STORE: dict[str, dict[str, int]] = defaultdict(lambda: {"approved": 0, "rejected": 0})


def signature_for(event: EventCreate) -> str:
    """Stable signature capturing the 'kind' of action, ignoring volatile ids."""
    p = event.payload or {}
    cap = p.get("capability") or p.get("tool_name") or event.event_type
    salient = []
    for k in ("command", "target", "merchant_id", "item"):
        if p.get(k):
            salient.append(f"{k}={p[k]}")
    if isinstance(p.get("steps"), list):
        for s in p["steps"]:
            if isinstance(s, dict):
                salient.append(str(s.get("command") or s.get("target") or s.get("type") or ""))
    raw = f"{event.source}|{cap}|{'|'.join(salient)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def record_feedback(signature: str, human_decision: str) -> None:
    if human_decision in ("approved", "rejected"):
        _STORE[signature][human_decision] += 1


def stats(signature: str) -> dict[str, int]:
    return dict(_STORE.get(signature, {"approved": 0, "rejected": 0}))


def apply_learning(event: EventCreate, final_verdict: str) -> tuple[str, str | None]:
    """Adapt the verdict based on accumulated human feedback for this signature."""
    sig = signature_for(event)
    s = _STORE.get(sig)
    if not s:
        return final_verdict, None

    approved, rejected = s["approved"], s["rejected"]

    # Learned-safe: humans keep approving this flagged action -> stop nagging.
    if final_verdict == "WARN" and approved >= LEARN_THRESHOLD and rejected == 0:
        return "ALLOW", (
            f"Human-feedback learning: this action has been approved by a human "
            f"{approved}× with no rejections; auto-cleared to reduce reviewer fatigue."
        )

    # Learned-unsafe: humans keep rejecting this -> tighten.
    if final_verdict == "ALLOW" and rejected >= LEARN_THRESHOLD and approved == 0:
        return "WARN", (
            f"Human-feedback learning: this action has been rejected by a human "
            f"{rejected}× before; escalated ALLOW -> WARN for review."
        )

    return final_verdict, None


def reset_feedback_store() -> None:
    """Clear the in-memory feedback store. Primarily for testing."""
    _STORE.clear()