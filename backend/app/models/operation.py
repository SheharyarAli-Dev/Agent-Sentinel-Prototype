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
from enum import Enum
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

class ExpectedOutcome(BaseModel):
    """
    Expected outcome for authorized outcome verification.

    This defines what the system expects to observe after a successful execution
    within the bounded simulated LiveOps model. This is NOT a generic cloud
    reconciliation — it is bounded to the supported simulated LiveOps model.
    """
    # Target resource that should be affected
    target_resource: str

    # Expected state transition (e.g., "running" -> "stopped", "present" -> "absent")
    allowed_state_transition: Optional[str] = None

    # Permitted mutations on the target resource (e.g., ["state", "metadata"])
    permitted_mutations: list[str] = Field(default_factory=list)

    # Protected-resource invariants that must remain unchanged
    protected_invariants: list[str] = Field(default_factory=list)

    # Expected final state of the target resource (for exact matching)
    expected_final_state: Optional[dict[str, Any]] = None

    def to_canonical_json(self) -> str:
        """Serialize to canonical JSON for fingerprinting."""
        # Sort keys for deterministic serialization
        return json.dumps(
            self.model_dump(exclude_none=True, mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )


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

    # Expected outcome verification fields (bounded to simulated LiveOps model)
    expected_outcome: Optional["ExpectedOutcome"] = None

    def to_canonical_json(self) -> str:
        """Serialize to canonical JSON for fingerprinting."""
        # Sort keys for deterministic serialization
        return json.dumps(
            self.model_dump(exclude_none=True, mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )


# ── Outcome Verification Models ───────────────────────────────────────────────────

class VerificationStatus(str, Enum):
    """Outcome verification status (bounded to simulated LiveOps model)."""
    VERIFIED = "VERIFIED"           # Observed matches expected exactly
    PARTIAL = "PARTIAL"             # Partial match (some invariants hold, some mutations permitted)
    MISMATCH = "MISMATCH"           # Observed does not match expected
    EXECUTION_FAILED = "EXECUTION_FAILED"  # Sandbox operation raised an exception
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"    # Could not determine outcome


class OutcomeVerificationResult(BaseModel):
    """
    Result of authorized outcome verification.

    Bounded to the supported simulated LiveOps model - not a generic cloud
    reconciliation.
    """
    status: VerificationStatus
    operation_id: str
    action_fingerprint: str
    event_id: int
    expected_outcome: Optional[ExpectedOutcome] = None
    observed_state: Optional[dict[str, Any]] = None
    invariant_violations: list[str] = Field(default_factory=list)
    permitted_mutations_observed: list[str] = Field(default_factory=list)
    unexpected_mutations: list[str] = Field(default_factory=list)
    verified_at: datetime
    execution_record_id: Optional[int] = None
    human_review_id: Optional[int] = None  # Decision ID if human review was involved


def build_canonical_action(event: EventCreate, agent_identity: Optional[str] = None) -> CanonicalAction:
    """
    Build a CanonicalAction from an EventCreate.

    Extracts and normalizes the semantically relevant fields from the event.
    For coding_proposal events, includes coding-specific fields in the
    canonical representation to bind the exact-action contract.
    """
    payload = event.payload or {}

    # Determine agent identity from source and payload
    agent_id = agent_identity
    if not agent_id:
        if payload.get("agent_id"):
            agent_id = str(payload["agent_id"])
        elif payload.get("agent"):
            agent_id = str(payload["agent"])
        else:
            agent_id = f"{event.source}-default"

    # Coding proposal: build canonical with coding-specific fields
    if event.event_type == "coding_proposal" and payload.get("relative_path"):
        target = str(payload.get("relative_path", ""))
        expected_effect = f"file_write: {target}"

        # Normalized parameters for coding proposal (exclude volatile fields)
        volatile_keys = {
            "session_id", "request_id", "transaction_id", "timestamp",
            "nonce", "correlation_id", "trace_id", "span_id"
        }
        coding_keys = {
            "action_type", "relative_path", "expected_old_hash",
            "new_content", "expected_new_hash", "test_profile",
            "protected_invariants",
        }
        normalized_params = {
            k: v for k, v in payload.items()
            if k in coding_keys and k not in volatile_keys and v is not None
        }

        return CanonicalAction(
            source=event.source,
            agent_identity=agent_id,
            action_type=event.event_type,
            target=target,
            normalized_parameters=normalized_params,
            original_goal=event.original_goal,
            expected_effect=expected_effect,
        )

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

    # Expected effect from description or command
    expected_effect = None
    for key in ("description", "command", "expected_effect", "intent"):
        if payload.get(key):
            expected_effect = str(payload[key])
            break

    # Expected outcome verification (only for LiveOps events with expected outcome data)
    expected_outcome = None
    if event.source == "liveops" and payload.get("expected_outcome"):
        expected_outcome = ExpectedOutcome.model_validate(payload["expected_outcome"])

    return CanonicalAction(
        source=event.source,
        agent_identity=agent_id,
        action_type=event.event_type,
        target=target,
        normalized_parameters=normalized_params,
        original_goal=event.original_goal,
        expected_effect=expected_effect,
        expected_outcome=expected_outcome,
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
    - approved -> executing/rejected/expired/blocked
    - executing -> executed/failed
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
    elif new_state == "executing":
        operation.execution_started_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(operation)
    return operation


# ── Outcome Verification Logic ────────────────────────────────────────────────────

def verify_outcome(
    db: Session,
    operation: OperationORM,
    cloud: "SimulatedCloud",
    execution_record: "LiveOpsExecutionORM",
) -> "OutcomeVerificationResult":
    """
    Verify the outcome of a LiveOps execution against the expected outcome.

    This performs bounded outcome verification within the supported simulated
    LiveOps model. It does NOT perform generic cloud reconciliation.

    Returns an OutcomeVerificationResult with status:
    - VERIFIED: Observed matches expected exactly
    - PARTIAL: Partial match (some invariants hold, some mutations permitted)
    - MISMATCH: Observed does not match expected
    - EXECUTION_FAILED: Sandbox operation raised an exception
    - OUTCOME_UNKNOWN: Could not determine outcome
    """

    verified_at = datetime.now(timezone.utc)

    # If execution failed, return EXECUTION_FAILED
    if execution_record.status == "failed":
        return OutcomeVerificationResult(
            status=VerificationStatus.EXECUTION_FAILED,
            operation_id=operation.operation_id,
            action_fingerprint=operation.action_fingerprint,
            event_id=operation.event_id or 0,
            expected_outcome=operation.get_canonical_action().expected_outcome,
            observed_state=None,
            invariant_violations=["Execution failed: " + (execution_record.result or {}).get("error", "Unknown error")],
            verified_at=datetime.now(timezone.utc),
            execution_record_id=execution_record.id,
        )

    # Get the canonical action to access expected outcome
    canonical = operation.get_canonical_action()
    expected = canonical.expected_outcome

    if not expected:
        # No expected outcome defined - cannot verify
        return OutcomeVerificationResult(
            status=VerificationStatus.OUTCOME_UNKNOWN,
            operation_id=operation.operation_id,
            action_fingerprint=operation.action_fingerprint,
            event_id=operation.event_id or 0,
            expected_outcome=None,
            observed_state=None,
            invariant_violations=["No expected outcome defined for verification"],
            verified_at=datetime.now(timezone.utc),
            execution_record_id=execution_record.id,
        )

    # Get the observed state from the simulated cloud
    observed_state = cloud.get_state()
    target_resource = expected.target_resource

    # Extract observed state for the target resource
    observed_resource_state = None
    for vm in observed_state.get("vms", []):
        if vm["id"] == target_resource:
            observed_resource_state = vm
            break

    for snap in observed_state.get("snapshots", []):
        if snap["id"] == target_resource:
            observed_resource_state = snap
            break

    if not observed_resource_state:
        return OutcomeVerificationResult(
            status=VerificationStatus.MISMATCH,
            operation_id=operation.operation_id,
            action_fingerprint=operation.action_fingerprint,
            event_id=operation.event_id or 0,
            expected_outcome=expected,
            observed_state=None,
            invariant_violations=[f"Target resource '{target_resource}' not found in observed state"],
            verified_at=datetime.now(timezone.utc),
            execution_record_id=execution_record.id,
        )

    # Verify protected invariants
    invariant_violations = []
    for invariant in expected.protected_invariants:
        # Check if invariant is violated in observed state
        if invariant == "protected" and observed_resource_state.get("protected") is not True:
            invariant_violations.append(f"Protected invariant violated: resource should be protected")
        elif invariant == "environment" and observed_resource_state.get("environment") != expected.expected_final_state.get("environment"):
            invariant_violations.append(f"Environment invariant violated")

    # Check permitted mutations
    permitted_mutations_observed = []
    unexpected_mutations = []

    if expected.expected_final_state:
        for key, expected_value in expected.expected_final_state.items():
            observed_value = observed_resource_state.get(key)
            if observed_value != expected_value:
                if key in expected.permitted_mutations:
                    permitted_mutations_observed.append(key)
                else:
                    unexpected_mutations.append(f"Unexpected mutation: {key} = {observed_value} (expected {expected_value})")

    # Check allowed state transition
    if expected.allowed_state_transition:
        expected_from, expected_to = expected.allowed_state_transition.split("->")
        expected_from = expected_from.strip()
        expected_to = expected_to.strip()
        current_state = observed_resource_state.get("state", "")
        if current_state != expected_to:
            invariant_violations.append(f"State transition mismatch: expected {expected_from} -> {expected_to}, got {current_state}")

    # Determine overall status
    if invariant_violations:
        status_result = VerificationStatus.MISMATCH
    elif unexpected_mutations:
        status_result = VerificationStatus.PARTIAL
    else:
        status_result = VerificationStatus.VERIFIED

    # Build observed state summary
    target = expected.target_resource
    observed_summary = {
        "target": target,
        "state": observed_resource_state.get("state"),
        "protected": observed_resource_state.get("protected"),
        "environment": observed_resource_state.get("environment"),
    }

    return OutcomeVerificationResult(
        status=status_result,
        operation_id=operation.operation_id,
        action_fingerprint=operation.action_fingerprint,
        event_id=operation.event_id or 0,
        expected_outcome=expected,
        observed_state=observed_summary,
        invariant_violations=invariant_violations,
        permitted_mutations_observed=permitted_mutations_observed,
        unexpected_mutations=unexpected_mutations,
        verified_at=datetime.now(timezone.utc),
        execution_record_id=execution_record.id,
    )


# Import at bottom to avoid circular imports
from sqlalchemy.orm import Session
from fastapi import HTTPException, status