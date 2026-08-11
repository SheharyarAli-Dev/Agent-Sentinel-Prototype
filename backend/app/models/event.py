"""
app/models/event.py
────────────────────
Event model — both the SQLAlchemy ORM table and the Pydantic request/response
schemas live here so the two representations stay in sync.

An "event" is a normalised representation of any action proposed by an agent,
regardless of whether it originated from Cursor, an n8n workflow, or the
transaction simulator.  Every adapter is responsible for mapping its raw
payload into this common schema before handing it to the policy engine.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

# ── Source literals ────────────────────────────────────────────────────────────
EventSource = Literal["cursor", "n8n", "transaction", "liveops"]


# ═══════════════════════════════════════════════════════════════════════════════
# SQLAlchemy ORM model
# ═══════════════════════════════════════════════════════════════════════════════
class EventORM(Base):
    __tablename__ = "events"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # payload is stored as a JSON string; helpers below handle (de)serialisation.
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    # original_goal is the user's stated objective at session start (plain text).
    original_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    # ── helpers ────────────────────────────────────────────────────────────────
    def get_payload(self) -> dict[str, Any]:
        """Deserialise the stored JSON payload string to a Python dict."""
        return json.loads(self.payload)

    def set_payload(self, data: dict[str, Any]) -> None:
        """Serialise a dict and store it in the payload column."""
        self.payload = json.dumps(data)


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic schemas
# ═══════════════════════════════════════════════════════════════════════════════
class EventCreate(BaseModel):
    """
    Schema for the POST /evaluate request body.

    payload  — arbitrary, adapter-specific data describing the proposed action.
    original_goal — optional; the user's stated goal for this session/task,
                    used by Module 6 (Intent Verification) to detect drift.
    """

    source: EventSource = Field(
        ...,
        description="Which adapter is submitting this event.",
        examples=["cursor", "n8n", "transaction"],
    )
    event_type: str = Field(
        ...,
        description="A short label for the type of action being proposed.",
        examples=["shell_command", "webhook_action", "purchase"],
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-specific data describing the proposed action.",
    )
    original_goal: str | None = Field(
        default=None,
        description=(
            "The user's stated objective for this session. "
            "Required for Module 6 intent-drift detection."
        ),
    )

    @field_validator("payload", mode="before")
    @classmethod
    def _ensure_dict(cls, v: Any) -> dict[str, Any]:
        """Accept a JSON string or a dict interchangeably."""
        if isinstance(v, str):
            return json.loads(v)
        return v


class EventResponse(BaseModel):
    """Schema returned by /evaluate and stored in the audit log."""

    id: int
    source: EventSource
    event_type: str
    payload: dict[str, Any]
    original_goal: str | None
    timestamp: datetime

    model_config = {"from_attributes": True}

    @field_validator("payload", mode="before")
    @classmethod
    def _parse_payload(cls, v: Any) -> dict[str, Any]:
        """ORM stores payload as a JSON string — parse it back on read."""
        if isinstance(v, str):
            return json.loads(v)
        return v
