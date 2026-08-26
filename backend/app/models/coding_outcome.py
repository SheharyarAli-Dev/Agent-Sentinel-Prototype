"""
app/models/coding_outcome.py
────────────────────────────
Stage 4 — coding outcome verification ledger.

Persists the verification result for a governed coding file-write.
One row per event_id. The UNIQUE constraint on event_id is the
authoritative guard against duplicate outcome records.

Status values:
  VERIFIED         — authorized target changed as expected
  MISMATCH         — unauthorized change or hash mismatch
  EXECUTION_FAILED — contained executor returned definite failure
  OUTCOME_UNKNOWN  — required evidence unavailable
  PARTIAL          — reserved for future use; not emitted currently
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# ── Canonical verification statuses ────────────────────────────────────────────
CodingVerificationStatus = Literal[
    "VERIFIED", "PARTIAL", "MISMATCH", "EXECUTION_FAILED", "OUTCOME_UNKNOWN"
]


# ═══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy ORM model
# ═══════════════════════════════════════════════════════════════════════════════
class CodingOutcomeORM(Base):
    __tablename__ = "coding_outcomes"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("events.id"),
        nullable=False,
        unique=True,
        index=True,
    )
    execution_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("coding_executions.id"),
        nullable=False,
        index=True,
    )
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    expected_path: Mapped[str] = mapped_column(String(512), nullable=False)
    observed_path: Mapped[str] = mapped_column(String(512), nullable=False)
    expected_old_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_old_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_new_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_final_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_changed_files_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    observed_modified_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    unexpected_created_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    unexpected_deleted_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    unexpected_modified_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    protected_invariants_before_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    protected_invariants_after_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    invariant_violations_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    diff_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    diff_omitted_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    verification_error_code: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    verification_error_message: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def _json_list(self, raw: str) -> list[str]:
        try:
            result = json.loads(raw)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def _json_dict(self, raw: str) -> dict[str, str]:
        try:
            result = json.loads(raw)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    def get_expected_changed_files(self) -> list[str]:
        return self._json_list(self.expected_changed_files_json)

    def get_observed_modified(self) -> list[str]:
        return self._json_list(self.observed_modified_json)

    def get_unexpected_created(self) -> list[str]:
        return self._json_list(self.unexpected_created_json)

    def get_unexpected_deleted(self) -> list[str]:
        return self._json_list(self.unexpected_deleted_json)

    def get_unexpected_modified(self) -> list[str]:
        return self._json_list(self.unexpected_modified_json)

    def get_protected_invariants_before(self) -> dict[str, str]:
        return self._json_dict(self.protected_invariants_before_json)

    def get_protected_invariants_after(self) -> dict[str, str]:
        return self._json_dict(self.protected_invariants_after_json)

    def get_invariant_violations(self) -> list[str]:
        return self._json_list(self.invariant_violations_json)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ═══════════════════════════════════════════════════════════════════════════════
class CodingOutcomeResponse(BaseModel):
    """Outcome verification record returned by the coding outcome API."""

    id: int
    event_id: int
    execution_id: int
    operation_id: str
    action_fingerprint: str
    verification_status: CodingVerificationStatus
    expected_path: str
    observed_path: str
    expected_old_hash: str
    observed_old_hash: str
    expected_new_hash: str
    observed_final_hash: str
    expected_changed_files: list[str] = Field(default_factory=list)
    observed_modified: list[str] = Field(default_factory=list)
    unexpected_created: list[str] = Field(default_factory=list)
    unexpected_deleted: list[str] = Field(default_factory=list)
    unexpected_modified: list[str] = Field(default_factory=list)
    invariant_violations: list[str] = Field(default_factory=list)
    diff_text: str | None = None
    diff_truncated: bool = False
    diff_omitted_reason: str = ""
    verification_error_code: str = ""
    verification_error_message: str = ""
    verified_at: datetime
    created_at: datetime
    replayed: bool = False

    model_config = {"from_attributes": True}

    @field_validator(
        "expected_changed_files", "observed_modified",
        "unexpected_created", "unexpected_deleted", "unexpected_modified",
        "invariant_violations",
        mode="before",
    )
    @classmethod
    def _parse_json_list(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return v if isinstance(v, list) else []
