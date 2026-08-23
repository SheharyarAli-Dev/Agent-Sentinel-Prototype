"""
app/models/decision.py
───────────────────────
Decision model — both the SQLAlchemy ORM table and the Pydantic schemas.

A Decision is the output of the policy engine for a given Event.  Every
decision produced by any of the three modules (Module 2, 6, or 7) is
normalised to this same shape before being stored and broadcast.

Verdict vocabulary
──────────────────
The three modules use slightly different terms in their specifications, but
they all map to the same three canonical verdict values used here:

  Module 2 (ATTVE)           SAFE  → ALLOW   WARN   BLOCK
  Module 6 (Intent)          aligned → ALLOW  drifted → WARN (or BLOCK)
  Module 7 (Planning)        PASS  → ALLOW   WARN   BLOCK

  The adapters / module entry functions perform this mapping before returning
  a DecisionCreate; the rest of the system only sees ALLOW / WARN / BLOCK.

human_decision column
─────────────────────
When a WARN decision is awaiting human review, human_decision is NULL.
The POST /decide/{event_id} endpoint sets it to "approved" or "rejected".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.event import EventResponse

# ── Canonical verdict values ───────────────────────────────────────────────────
Verdict = Literal["ALLOW", "WARN", "BLOCK", "EXPIRED"]
HumanDecision = Literal["approved", "rejected"]


# ═══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy ORM model
# ═══════════════════════════════════════════════════════════════════════════════
class DecisionORM(Base):
    __tablename__ = "decisions"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("events.id"), nullable=False, index=True
    )
    verdict: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    # reasons is stored as a JSON array of strings.
    reasons: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    # suggested_fix is a single human-readable recommendation; empty string for ALLOW.
    suggested_fix: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Which policy module produced this verdict.
    module: Mapped[str] = mapped_column(String(255), nullable=False)
    # Normalised risk score 0.0 (benign) → 1.0 (critical).
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Module 11 — plain-language explanation of the decision.
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Evaluation latency in milliseconds (spec KPI: Δt < 40ms).
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # NULL until a human acts on a WARN decision.
    human_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    human_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # When a pending WARN review expires (review_timeout_seconds after decision).
    review_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # True when a human operator overrides (unblocks) a BLOCK decision.
    unblocked_by_human: Mapped[bool] = mapped_column(Integer, nullable=False, default=0)
    unblock_timestamp: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # ── helpers ────────────────────────────────────────────────────────────────
    def get_reasons(self) -> list[str]:
        return json.loads(self.reasons)

    def set_reasons(self, data: list[str]) -> None:
        self.reasons = json.dumps(data)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ═══════════════════════════════════════════════════════════════════════════════
class DecisionCreate(BaseModel):
    """
    Internal schema — produced by each policy module and consumed by
    rules_engine.py.  Not exposed directly as an API request body.
    """

    verdict: Verdict
    reasons: list[str] = Field(default_factory=list)
    suggested_fix: str = Field(
        default="",
        description=(
            "Human-readable suggestion for remediation. "
            "MUST be non-empty for every WARN or BLOCK verdict."
        ),
    )
    module: str = Field(
        ..., description="Identifier of the module that produced this decision."
    )
    risk_score: float = Field(
        ..., ge=0.0, le=1.0, description="Normalised risk score 0-1."
    )
    explanation: str = Field(
        default="",
        description="Module 11 — plain-language justification of the decision.",
    )

    @field_validator("suggested_fix")
    @classmethod
    def _fix_required_for_non_allow(cls, v: str, info: Any) -> str:
        verdict = info.data.get("verdict")
        if verdict in ("WARN", "BLOCK") and not v.strip():
            raise ValueError(
                "suggested_fix must be non-empty for WARN and BLOCK verdicts."
            )
        return v


class DecisionResponse(BaseModel):
    """Full decision record returned by the API."""

    id: int
    event_id: int
    verdict: Verdict
    reasons: list[str]
    suggested_fix: str
    module: str
    risk_score: float
    explanation: str = ""
    latency_ms: float = 0.0
    timestamp: datetime
    human_decision: HumanDecision | None = None
    human_timestamp: datetime | None = None
    review_expires_at: datetime | None = None
    unblocked_by_human: bool = False
    unblock_timestamp: datetime | None = None

    model_config = {"from_attributes": True}

    @field_validator("reasons", mode="before")
    @classmethod
    def _parse_reasons(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            return json.loads(v)
        return v


class HumanDecisionRequest(BaseModel):
    """Request body for POST /decide/{event_id}."""

    decision: HumanDecision = Field(
        ..., description="Human's choice: 'approved' or 'rejected'."
    )
    notes: str | None = Field(
        default=None,
        description="Optional free-text notes from the human reviewer.",
    )


class EvaluateResponse(BaseModel):
    """
    Combined response returned by POST /evaluate — includes both the
    stored event and the resulting decision so the caller has everything
    they need in a single round-trip.
    """

    event: EventResponse
    decision: DecisionResponse
