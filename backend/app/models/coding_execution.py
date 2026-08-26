"""
app/models/coding_execution.py
──────────────────────────────
Coding execution ledger — governance-gated exactly-once execution tracking.

One row per event_id. The UNIQUE constraint on event_id is the authoritative
database-level exactly-once guard.

Status lifecycle:
  pending        → reserved (a request owns the event; executor not yet run)
  executing      → executor invoked (filesystem mutation in progress)
  executed       → executor succeeded (terminal)
  failed         → executor raised or post-execution check failed (terminal)
  outcome_unknown→ reservation exists but completion status is uncertain
                   (stale reservation or process interruption)

BLOCK / REJECTED / EXPIRED conditions prevent reservation entirely and are
never stored as execution statuses.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# ── Canonical execution statuses ───────────────────────────────────────────────
CodingExecutionStatus = Literal[
    "pending", "executing", "executed", "failed", "outcome_unknown"
]


# ═══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy ORM model
# ═══════════════════════════════════════════════════════════════════════════════
class CodingExecutionORM(Base):
    __tablename__ = "coding_executions"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("events.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_old_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_new_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    bytes_written: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changed_files_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    unexpected_changes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    restoration_attempted: Mapped[bool] = mapped_column(Boolean, default=False)
    restoration_succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def get_changed_files(self) -> list[str]:
        if not self.changed_files_json:
            return []
        try:
            result = json.loads(self.changed_files_json)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            return []

    def get_unexpected_changes(self) -> list[str]:
        if not self.unexpected_changes_json:
            return []
        try:
            result = json.loads(self.unexpected_changes_json)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            return []


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ═══════════════════════════════════════════════════════════════════════════════
class CodingExecutionResponse(BaseModel):
    """Execution-ledger record returned by the coding execution API."""

    id: int
    event_id: int
    operation_id: str
    action_fingerprint: str
    relative_path: str
    status: CodingExecutionStatus
    before_hash: str = ""
    after_hash: str = ""
    expected_old_hash: str = ""
    expected_new_hash: str = ""
    bytes_written: int = 0
    changed_files: list[str] = Field(default_factory=list)
    unexpected_changes: list[str] = Field(default_factory=list)
    error_code: str = ""
    error_message: str = ""
    restoration_attempted: bool = False
    restoration_succeeded: bool | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    replayed: bool = False

    model_config = {"from_attributes": True}

    @field_validator("changed_files", mode="before")
    @classmethod
    def _parse_changed_files(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return v if isinstance(v, list) else []

    @field_validator("unexpected_changes", mode="before")
    @classmethod
    def _parse_unexpected_changes(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return v if isinstance(v, list) else []
