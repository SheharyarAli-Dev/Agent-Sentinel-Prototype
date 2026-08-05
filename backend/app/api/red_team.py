"""
app/api/red_team.py
─────────────────────
GET /api/red-team — run the automated adversarial test suite against the live
pipeline and return a coverage report (Module — AI Red Team Simulator).
"""
from __future__ import annotations

from fastapi import APIRouter

from app.policy.red_team import run_red_team

router = APIRouter(prefix="/api", tags=["red-team"])


@router.get("/red-team", summary="Run automated adversarial tests and report coverage")
async def red_team() -> dict:
    return run_red_team()
