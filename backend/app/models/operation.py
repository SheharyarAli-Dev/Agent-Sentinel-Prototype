"""
app/models/operation.py
───────────────────────
Operation Identity Model — Persistent operation identity and exact-action binding.

This module defines the canonical operation model that provides persistent
duplicate-side-effect protection within the supported ASENT execution model.

An Operation represents a unique agent action proposal with its canonical
representation and SHA-256 fingerprint. The operation record enables:
  - Exact-action deduplication (same operation_id + same fingerprint = existing)
  - Conflict detection (same operation_id + different fingerprint = 409)
  - Approval bound to exact fingerprint
  - Material change detection requiring new evaluation
  - Lifecycle state tracking with expiry support
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.event import EventCreate

# ── Canonical lifecycle states ───────────────────────────────────────────────────
OperationLifecycle = Literal[
    "pending",      # Created, awaiting evaluation
    "evaluated",    # Policy evaluation complete, awaiting human review (if WARN)
    "approved",     # Human approved (WARN -> human_decision=approved)
    "rejected",     # Human rejected (WARN -> human_decision=rejected)
    "expired",      # Review timeout exceeded
    "blocked",      # BLOCK verdict (never executable)
    "executed",     # Executed successfully (LiveOps)
    "failed",       # Execution failed
]

# ── Canonical action schema for fingerprinting ───────────────────────────────────
# The canonical representation includes only the semantically relevant fields
# that define the action's identity. Volatile fields (timestamps, request IDs,
# session IDs, auto-generated IDs) are EXCLUDED from the fingerprint.

class CanonicalAction(BaseModel):
    """
    Canonical representation of an agent action for fingerprinting.

    This is the single source of truth for action identity. Only fields that
    define the semantic intent of the action are included. Volatile/ephemeral
    fields are deliberately excluded.
    """
    source: str
    agent_identity: Optional[str] = None  # e.g., "cursor-agent-1", "n8n-workflow-42"
    action_type: str                      # e.g., "purchase", "stop_vm", "plan_execution"
    target: Optional[str] = None          # Target resource (file path, VM id, merchant_id)
    normalized_parameters: dict[str, Any] = Field(default_factory=dict)
    original_goal: Optional[str] = None
    expected_effect: Optional[str] = None  # Human-readable expected outcome

    def to_canonical_json(self) -> str:
        """Serialize to canonical JSON for fingerprinting."""
        # Sort keys for deterministic serialization
        return json.dumps(
            self.model_dump(exclude_none=True, mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )


def build_canonical_action(event: EventCreate, agent_identity: Optional[str] = None) -> CanonicalAction:
    """
    Build a CanonicalAction from an EventCreate.

    Extracts and normalizes the semantically relevant fields from the event.
    """
    payload = event.payload or {}

    # Extract target from various possible payload fields
    target = None
    for key in ("target", "merchant_id", "file_path", "vm_id", "snapshot_id", "resource"):
        if payload.get(key):
            target = str(payload[key])
            break

    # Build normalized parameters (exclude volatile fields)
    volatile_keys = {
        "session_id", "request_id", "transaction_id", "timestamp",
        "nonce", "correlation_id", "trace_id", "span_id"
    }
    normalized_params = {
        k: v for k, v in payload.items()
        if k not in volatile_keys and v is not None
    }

    # Determine agent identity from source and payload
    agent_id = agent_identity
    if not agent_id:
        if payload.get("agent_id"):
            agent_id = str(payload["agent_id"])
        elif payload.get("agent"):
            agent_id = str(payload["agent"])
        else:
            agent_id = f"{event.source}-default"

    # Expected effect from description or command
    expected_effect = None
    for key in ("description", "command", "expected_effect", "intent"):
        if payload.get(key):
            expected_effect = str(payload[key])
            break

    return CanonicalAction(
        source=event.source,
        agent_identity=agent_id,
        action_type=event.event_type,
        target=target,
        normalized_parameters=normalized_params,
        original_goal=event.original_goal,
        expected_effect=expected_effect,
    )


def compute_action_fingerprint(canonical_action: CanonicalAction) -> str:
    """
    Compute SHA-256 fingerprint from canonical action.

    Returns hex-encoded SHA-256 hash (64 characters).
    """
    canonical_json = canonical_action.to_canonical_json()
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def compute_fingerprint_from_event(event: EventCreate, agent_identity: Optional[str] = None) -> str:
    """
    Convenience function to compute fingerprint directly from EventCreate.
    """
    canonical = build_canonical_action(event, agent_identity)
    return compute_action_fingerprint(canonical)


# ── Pydantic schemas for API ─────────────────────────────────────────────────────

class OperationCreate(BaseModel):
    """
    Internal schema for creating an operation record.
    """
    operation_id: str
    source: str
    event_id: int
    canonical_action_json: str
    action_fingerprint: str
    action_version: int = 1
    lifecycle_state: str = "pending"
    review_expires_at: Optional[datetime] = None


class OperationResponse(BaseModel):
    """Full operation record returned by the API."""
    id: int
    operation_id: str
    source: str
    event_id: int
    canonical_action_json: str
    action_fingerprint: str
    action_version: int
    lifecycle_state: str
    created_at: datetime
    updated_at: datetime
    review_expires_at: Optional[datetime] = None
    execution_started_at: Optional[datetime] = None
    execution_completed_at: Optional[datetime] = None
    error_info: Optional[str] = None

    model_config = {"from_attributes": True}


class OperationStatusResponse(BaseModel):
    """Lightweight operation status for quick lookups."""
    operation_id: str
    action_fingerprint: str
    lifecycle_state: str
    event_id: int
    review_expires_at: Optional[datetime] = None
    execution_started_at: Optional[datetime] = None
    execution_completed_at: Optional[datetime] = None
    error_info: Optional[str] = None


# ── SQLAlchemy ORM model ─────────────────────────────────────────────────────────

class OperationORM(Base):
    """
    Persistent operation identity record.

    Provides durable exactly-once semantics within the ASENT execution model.
    The unique constraint on (operation_id, action_fingerprint) enforces
    exact-action binding at the database level.
    """
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("operation_id", "action_fingerprint", name="uq_operation_id_fingerprint"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("events.id"), nullable=True, index=True
    )
    # Canonical JSON representation of the action (for audit/replay)
    canonical_action_json: Mapped[str] = mapped_column(Text, nullable=False)
    # SHA-256 fingerprint of the canonical action (64 hex chars)
    action_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Version for optimistic locking / change tracking
    action_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Lifecycle state
    lifecycle_state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    # When a pending WARN review expires (review_timeout_seconds after evaluation)
    review_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Execution timestamps
    execution_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Error/uncertainty information
    error_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )

    def get_canonical_action(self) -> CanonicalAction:
        """Deserialise the stored canonical action JSON."""
        return CanonicalAction.model_validate_json(self.canonical_action_json)


# ── Helper functions ─────────────────────────────────────────────────────────────

def get_or_create_operation(
    db: Session,
    event: EventCreate,
    event_id: int,
    operation_id: Optional[str] = None,
    agent_identity: Optional[str] = None,
) -> tuple[OperationORM, bool]:
    """
    Get existing operation or create new one with exact-action binding.

    Returns (operation, is_new). Raises HTTPException on fingerprint conflict.

    Logic:
    - If operation_id provided and exists:
      - Same fingerprint -> return existing (idempotent)
      - Different fingerprint -> HTTP 409 Conflict
    - If operation_id not provided or not found:
      - Check if any operation with same fingerprint exists for this event_id
      - If exists, return it (idempotent by fingerprint)
      - Create new with generated or provided operation_id
    """
    import uuid
    from fastapi import HTTPException, status

    # Generate operation_id if not provided
    if not operation_id:
        operation_id = f"op-{uuid.uuid4().hex[:16]}"

    # Compute fingerprint
    fingerprint = compute_fingerprint_from_event(event, agent_identity)

    # Check if operation_id already exists
    existing_by_id = (
        db.query(OperationORM)
        .filter(OperationORM.operation_id == operation_id)
        .first()
    )

    if existing_by_id:
        # Same operation_id exists - enforce fingerprint match
        if existing_by_id.action_fingerprint != fingerprint:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Operation ID '{operation_id}' already exists with a different "
                    f"action fingerprint. Existing: {existing_by_id.action_fingerprint[:16]}..., "
                    f"New: {fingerprint[:16]}... "
                    f"Cannot reuse operation_id for a different action."
                ),
            )
        # Same fingerprint - idempotent return
        return existing_by_id, False

    # Check for existing operation with same fingerprint (idempotent by fingerprint)
    existing_by_fingerprint = (
        db.query(OperationORM)
        .filter(
            OperationORM.action_fingerprint == fingerprint,
            OperationORM.event_id == event_id,
        )
        .first()
    )

    if existing_by_fingerprint:
        return existing_by_fingerprint, False

    # Create new operation
    canonical = build_canonical_action(event, agent_identity)
    canonical_json = canonical.to_canonical_json()

    # Determine review_expires_at based on settings (for WARN decisions later)
    from app.config import settings
    review_expires_at = None
    # Note: review_expires_at is set after evaluation when verdict is known

    operation = OperationORM(
        operation_id=operation_id,
        source=event.source,
        event_id=event_id,
        canonical_action_json=canonical_json,
        action_fingerprint=fingerprint,
        action_version=1,
        lifecycle_state="pending",
        review_expires_at=review_expires_at,
    )

    db.add(operation)
    db.commit()
    db.refresh(operation)

    return operation, True


def update_operation_state(
    db: Session,
    operation: OperationORM,
    new_state: str,
    review_expires_at: Optional[datetime] = None,
    error_info: Optional[str] = None,
) -> OperationORM:
    """
    Update operation lifecycle state with validation.

    Valid transitions:
    - pending -> evaluated (after policy evaluation)
    - evaluated -> approved/rejected/expired/blocked (human review or auto)
    - approved -> executed/failed (LiveOps execution)
    - Any -> failed (on error)

    Invalid transitions raise HTTPException.
    """
    valid_transitions = {
        "pending": ["evaluated", "failed", "approved", "blocked"],
        "evaluated": ["approved", "rejected", "expired", "blocked"],
        "approved": ["executing", "rejected", "expired", "blocked"],
        "executing": ["executed", "failed"],
        "rejected": [],  # Terminal
        "expired": [],   # Terminal
        "blocked": [],   # Terminal
        "executed": [],  # Terminal
        "failed": [],    # Terminal
    }

    if new_state not in valid_transitions.get(operation.lifecycle_state, []):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Invalid lifecycle transition: {operation.lifecycle_state} -> {new_state}. "
                f"Valid transitions: {valid_transitions.get(operation.lifecycle_state, [])}"
            ),
        )

    operation.lifecycle_state = new_state
    operation.updated_at = datetime.now(timezone.utc)

    if review_expires_at:
        operation.review_expires_at = review_expires_at

    if error_info:
        operation.error_info = error_info

    if new_state == "executed":
        operation.execution_completed_at = datetime.now(timezone.utc)
    elif new_state == "failed":
        operation.execution_completed_at = datetime.now(timezone.utc)
    elif new_state == "approved" and not operation.execution_started_at:
        operation.execution_started_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(operation)
    return operation


# Import at bottom to avoid circular imports
from sqlalchemy.orm import Session
from fastapi import HTTPException, status