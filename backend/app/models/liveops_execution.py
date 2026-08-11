"""
app/models/liveops_execution.py
────────────────────────────────
LiveOps execution ledger — Oracle / exactly-once execution tracking.

Together with a dedicated table ("liveops_executions"), one row per event_id.
The UNIQUE constraint on event_id is the authoritative database-level exactly-once
guard. A repeated execute request for an already-executed event hits the constraint
(or the pre-check) and returns HTTP 409 without touching the simulated cloud.

Status lifecycle:
  pending   → reserved (a request owns the event; sandbox op not yet run)
  executed  → sandbox operation succeeded (executed_at + result set)
  rejected  → WARN + human_decision == "rejected" (never executed)
  blocked   → BLOCK verdict (never executed in this V1 gateway)
  failed    → sandbox operation raised (never marked executed)

This model deliberately lives OUTSIDE decisions/events: the execution audit trail
is separate from evaluation results, and neither the DecisionORM nor the EventORM
schemas are touched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# ── Canonical execution statuses ───────────────────────────────────────────────
LiveOpsExecutionStatus = Literal["pending", "executed", "rejected", "blocked", "failed"]


# ═══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy ORM model
# ═══════════════════════════════════════════════════════════════════════════════
class LiveOpsExecutionORM(Base):
    __tablename__ = "liveops_executions"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # One row per event — UNIQUE is the authoritative exactly-once guard.
    event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("events.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    # None for tool == "list_resources" (no target required).
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # JSON-serialised result summary (sandbox outcome / state summary / error).
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def get_result(self) -> Any:
        """Deserialise the stored JSON result (None when nothing stored)."""
        if not self.result:
            return None
        try:
            return json.loads(self.result)
        except json.JSONDecodeError:
            return self.result


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ═══════════════════════════════════════════════════════════════════════════════
class LiveOpsExecutionResponse(BaseModel):
    """
    Execution-ledger record returned by POST /api/liveops/execute/{event_id}
    and GET /api/liveops/execution/{event_id}.

    result is exposed as a parsed dict (or null); no filesystem paths are ever
    included in the stored result payload.
    """

    id: int
    event_id: int
    tool: str
    target: str | None = None
    status: LiveOpsExecutionStatus = Field(
        ..., description="pending | executed | rejected | blocked | failed"
    )
    result: dict[str, Any] | None = Field(
        default=None,
        description="Parsed sandbox outcome / state summary, or error detail.",
    )
    executed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("result", mode="before")
    @classmethod
    def _parse_result(cls, v: Any) -> dict[str, Any] | None:
        """ORM stores result as a JSON string — parse it back on read."""
        if v is None:
            return None
        if isinstance(v, str):
            if not v.strip():
                return None
            parsed = json.loads(v)
            return parsed if isinstance(parsed, dict) else (parsed or None)
        return v