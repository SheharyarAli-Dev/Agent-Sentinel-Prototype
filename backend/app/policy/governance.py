"""
app/policy/governance.py
─────────────────────────
Module 4 — AI Decision Governance & Incident Response

Every decision is already persisted to SQLite; this module turns that stored
history into (a) a queryable audit trail and (b) a forensic incident report.
This provides accountability and post-incident investigation — answering
*"what did the agents try, what did we block, and why?"* after the fact.

Pure read-side logic; the API layer (app/api/governance.py) exposes it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.decision import DecisionORM
from app.models.event import EventORM


def get_audit_trail(
    db: Session,
    source: str | None = None,
    verdict: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return joined event+decision records, newest first, with optional filters."""
    q = (
        db.query(EventORM, DecisionORM)
        .join(DecisionORM, DecisionORM.event_id == EventORM.id)
        .order_by(DecisionORM.id.desc())
    )
    if source:
        q = q.filter(EventORM.source == source)
    if verdict:
        q = q.filter(DecisionORM.verdict == verdict)

    rows = q.limit(limit).all()
    trail: list[dict[str, Any]] = []
    for event, decision in rows:
        trail.append(
            {
                "event_id": event.id,
                "source": event.source,
                "event_type": event.event_type,
                "original_goal": event.original_goal,
                "verdict": decision.verdict,
                "risk_score": decision.risk_score,
                "module": decision.module,
                "reasons": json.loads(decision.reasons),
                "explanation": decision.explanation,
                "human_decision": decision.human_decision,
                "timestamp": decision.timestamp.isoformat(),
            }
        )
    return trail


def build_incident_report(db: Session) -> dict[str, Any]:
    """
    Produce a forensic summary across all recorded decisions: verdict/source
    breakdowns, the modules most responsible for blocks/warnings, and the full
    list of blocked actions (the "incidents").
    """
    pairs = (
        db.query(EventORM, DecisionORM)
        .join(DecisionORM, DecisionORM.event_id == EventORM.id)
        .order_by(DecisionORM.id.desc())
        .all()
    )

    total = len(pairs)
    by_verdict: dict[str, int] = {"ALLOW": 0, "WARN": 0, "BLOCK": 0}
    by_source: dict[str, int] = {}
    module_hits: dict[str, int] = {}
    incidents: list[dict[str, Any]] = []
    pending_reviews = 0

    for event, decision in pairs:
        by_verdict[decision.verdict] = by_verdict.get(decision.verdict, 0) + 1
        by_source[event.source] = by_source.get(event.source, 0) + 1

        if decision.verdict in ("WARN", "BLOCK"):
            for m in (decision.module or "").split(","):
                m = m.strip()
                if m and m != "policy_engine":
                    module_hits[m] = module_hits.get(m, 0) + 1

        if decision.verdict == "WARN" and decision.human_decision is None:
            pending_reviews += 1

        if decision.verdict == "BLOCK":
            incidents.append(
                {
                    "event_id": event.id,
                    "source": event.source,
                    "event_type": event.event_type,
                    "risk_score": decision.risk_score,
                    "reasons": json.loads(decision.reasons),
                    "timestamp": decision.timestamp.isoformat(),
                }
            )

    top_modules = sorted(module_hits.items(), key=lambda kv: kv[1], reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_decisions": total,
        "verdict_breakdown": by_verdict,
        "source_breakdown": by_source,
        "pending_human_reviews": pending_reviews,
        "top_triggering_modules": [
            {"module": m, "count": c} for m, c in top_modules
        ],
        "blocked_incident_count": len(incidents),
        "blocked_incidents": incidents,
    }
