"""
app/api/governance.py
───────────────────────
Module 4 — Decision Governance & Incident Response API.

  GET /api/audit            — queryable audit trail (event + decision history)
  GET /api/incident-report  — forensic summary + list of blocked incidents
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.policy.governance import build_incident_report, get_audit_trail

router = APIRouter(prefix="/api", tags=["governance"])


@router.get("/audit", summary="Queryable audit trail of all evaluated actions")
async def audit(
    source: str | None = Query(None, description="Filter by source: cursor|n8n|transaction"),
    verdict: str | None = Query(None, description="Filter by verdict: ALLOW|WARN|BLOCK"),
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict:
    trail = get_audit_trail(db, source=source, verdict=verdict, limit=limit)
    return {"count": len(trail), "records": trail}


@router.get("/incident-report", summary="Forensic incident report across all decisions")
async def incident_report(db: Session = Depends(get_db)) -> dict:
    return build_incident_report(db)
