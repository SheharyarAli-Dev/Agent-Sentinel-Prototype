"""
app/api/liveops.py
───────────────────
LiveOps Increment 3 — authorised, exactly-once execution gateway.

This module connects Agent Sentinel policy decisions to the simulated cloud
sandbox. It deliberately does NOT create the agent runner or any frontend
integration (both are later increments).

Endpoints
─────────
  GET  /api/liveops/state               — current simulated cloud state
  POST /api/liveops/reset               — restore canonical seed (dev/demo ONLY)
  POST /api/liveops/execute/{event_id}  — execute an ALLOW / approved-WARN action
  GET  /api/liveops/execution/{event_id}— inspect the execution ledger

Execution contract
──────────────────
Validation order (all must pass) for POST /execute/{event_id}:
  1. Event exists.
  2. Event source is exactly "liveops".
  3. A decision exists for the event.
  4. Event payload is valid JSON.
  5. Tool is in the LiveOps allowlist (and matches event.event_type).
  6. Tool and target match the persisted event payload.
  7. No executed ledger row already exists for this event_id.
  8. Decision authorises execution:
       ALLOW                      -> proceed
       WARN + approved            -> proceed
       WARN + rejected            -> record rejected; do NOT execute
       WARN + no human decision   -> HTTP 409 (review pending)
       BLOCK                      -> HTTP 403 (never overridable in this V1)

Exactly-once
────────────
  - liveops_executions.event_id is UNIQUE (database-level guard).
  - Execution first reserves a "pending" row inside a committed transaction.
    A racing request for the same event hits the unique constraint and receives
    a conflict without touching the simulated cloud.
  - An "executed" row is committed ONLY after the sandbox operation succeeds.
  - If the sandbox operation raises, the row is set to
    "failed" (never "executed") and the endpoint returns HTTP 500.
  - Failed executions are NOT auto-retried by this gateway (a repeated request
    returns HTTP 409). This deliberately prevents accidental double execution;
    a manual reviewer may inspect the ledger before deciding next steps.

No automatic execution inside /evaluate: the future agent runner explicitly
calls /api/liveops/execute/{event_id} after receiving ALLOW or approved WARN.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.liveops_adapter import ALLOWED_TOOLS
from app.database import get_db
from app.models.decision import DecisionORM
from app.models.event import EventCreate, EventORM
from app.models.liveops_execution import (
    LiveOpsExecutionORM,
    LiveOpsExecutionResponse,
)
from app.models.operation import OperationORM, update_operation_state, verify_outcome, OutcomeVerificationResult, build_canonical_action, compute_action_fingerprint
from app.sandbox.simulated_cloud import SimulatedCloud

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/liveops", tags=["liveops"])

# ── Simulated cloud (dedicated runtime state, separate from the seed) ──────────
# A lazy module-level factory: the cloud is only constructed when an endpoint
# without a test override first asks for it, so importing/collecting tests never
# writes a runtime state file into the repository data directory.
_SEED_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "simulated_cloud_seed.json"
)
_STATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "liveops_runtime_state.json"
)

_cloud_instance: SimulatedCloud | None = None


def get_cloud() -> SimulatedCloud:
    """Lazily constructed default simulated cloud shared by the liveops API."""
    global _cloud_instance
    if _cloud_instance is None:
        _cloud_instance = SimulatedCloud(_SEED_PATH, _STATE_PATH)
    return _cloud_instance


# ── State + reset ──────────────────────────────────────────────────────────────

@router.get(
    "/state",
    summary="Get current LiveOps simulated cloud state",
    description="Returns the current runtime state of the simulated cloud. Never modifies the seed file.",
)
async def liveops_state(
    cloud: SimulatedCloud = Depends(get_cloud),
) -> dict[str, Any]:
    return cloud.get_state()


@router.post(
    "/reset",
    summary="Reset LiveOps simulated cloud state (development/demo only)",
    description=(
        "Restores the simulated cloud runtime state from the canonical seed. "
        "Intended for development/demo use ONLY — it does not delete audit or "
        "decision history, and it never modifies the seed file. "
        "No authentication is wired in this increment."
    ),
)
async def liveops_reset(
    cloud: SimulatedCloud = Depends(get_cloud),
) -> dict[str, Any]:
    return cloud.reset()


# ── Execution ledger ───────────────────────────────────────────────────────────

def _conflict_response(row: LiveOpsExecutionORM) -> HTTPException:
    """Map an existing ledger row to the correct conflict HTTP response."""
    if row.status == "executed":
        return HTTPException(
            409,
            detail=(
                f"Event {row.event_id} was already executed exactly-once. "
                "Repeated execution is refused and the simulated cloud is untouched."
            ),
        )
    if row.status == "pending":
        return HTTPException(
            409,
            detail=f"Event {row.event_id} execution is already in progress.",
        )
    if row.status == "failed":
        return HTTPException(
            409,
            detail=(
                f"Event {row.event_id} previously failed; automatic retry is "
                "disabled in this gateway to prevent double execution."
            ),
        )
    if row.status == "rejected":
        return HTTPException(
            409,
            detail=f"Event {row.event_id} was previously rejected by human review.",
        )
    if row.status == "blocked":
        return HTTPException(
            403,
            detail=f"Event {row.event_id} has verdict BLOCK; execution is not permitted.",
        )
    return HTTPException(
        409,
        detail=f"Event {row.event_id} already has ledger status '{row.status}'.",
    )


def _record_terminal(
    db: Session,
    event_id: int,
    tool: str,
    target: str | None,
    status_value: str,
) -> None:
    """Record a terminal (blocked/rejected) ledger row once, exactly-once."""
    row = LiveOpsExecutionORM(
        event_id=event_id,
        tool=tool,
        target=target,
        status=status_value,
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(LiveOpsExecutionORM).where(LiveOpsExecutionORM.event_id == event_id)
        )
        raise _conflict_response(existing) from None


def _summarise(tool: str, target: str | None, state: dict[str, Any]) -> dict[str, Any]:
    """Build a short, path-free summary of the sandbox outcome / resulting state."""
    return {
        "tool": tool,
        "target": target,
        "vms": [
            {"id": vm["id"], "state": vm["state"]}
            for vm in state.get("vms", [])
        ],
        "snapshots": [
            {"id": s["id"], "source_vm": s.get("source_vm")}
            for s in state.get("snapshots", [])
        ],
    }


def _dispatch_tool(
    cloud: SimulatedCloud, tool: str, target: str | None, payload: dict[str, Any]
) -> dict[str, Any]:
    """
    Explicit, allowlist-gated dispatch to SimulatedCloud methods.

    No dynamic getattr is ever performed on caller-controlled input — each tool
    maps to an explicit method call and an explicit argument shape.
    """
    if tool == "list_resources":
        return cloud.list_resources()
    if tool == "start_vm":
        return cloud.start_vm(target)
    if tool == "stop_vm":
        return cloud.stop_vm(target)
    if tool == "create_snapshot":
        snapshot_id = payload.get("resource") or ""
        return cloud.create_snapshot(target, snapshot_id)
    if tool == "delete_snapshot":
        return cloud.delete_snapshot(target)
    raise ValueError(f"No explicit dispatcher registered for tool {tool!r}.")


@router.post(
    "/execute/{event_id}",
    response_model=LiveOpsExecutionResponse,
    summary="Execute an authorise LiveOps action exactly once",
    description=(
        "Executes the simulated-cloud operation for a LiveOps event whose policy "
        "verdict is ALLOW or WARN-with-human-approval. The database-level UNIQUE "
        "constraint on liveops_executions.event_id guarantees exactly-once "
        "execution: a repeated or racing request receives HTTP 409 without "
        "touching the sandbox. BLOCK verdicts are always refused (HTTP 403)."
    ),
)
async def execute_liveops_action(
    event_id: int,
    db: Session = Depends(get_db),
    cloud: SimulatedCloud = Depends(get_cloud),
) -> LiveOpsExecutionResponse:
    # ── 1. Event exists ───────────────────────────────────────────────────────
    event = db.get(EventORM, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found.")

    # ── 2. Event source is exactly "liveops" ──────────────────────────────────
    if event.source != "liveops":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Event {event_id} has source '{event.source}'; "
                "only 'liveops' events can be executed here."
            ),
        )

    # ── 3. A decision exists for the event ────────────────────────────────────
    decision = (
        db.query(DecisionORM)
        .filter(DecisionORM.event_id == event_id)
        .order_by(DecisionORM.id.desc())
        .first()
    )
    if decision is None:
        raise HTTPException(
            status_code=404,
            detail=f"No decision found for event {event_id} — no authorisation to execute.",
        )

    # ── 4. Event payload is valid JSON ────────────────────────────────────────
    try:
        payload = json.loads(event.payload) if isinstance(event.payload, str) else event.payload
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Event {event_id} has a malformed persisted payload: {exc}",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail=f"Event {event_id} persisted payload is not a JSON object.",
        )

    # ── 5. Tool is in the LiveOps allowlist ───────────────────────────────────
    tool = payload.get("tool")
    if tool not in ALLOWED_TOOLS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Tool {tool!r} is not in the LiveOps allowlist. "
                f"Allowed tools: {sorted(ALLOWED_TOOLS)}."
            ),
        )
    if tool != event.event_type:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Persisted payload tool {tool!r} does not match event_type "
                f"{event.event_type!r}; refusing to dispatch."
            ),
        )

    # ── 6. Tool and target match the persisted event payload ─────────────────
    target = payload.get("target") or None
    if tool != "list_resources" and not target:
        raise HTTPException(
            status_code=422,
            detail=f"Tool {tool!r} requires a target in the persisted payload.",
        )
    if tool == "create_snapshot":
        snapshot_id = payload.get("resource") or ""
        if not snapshot_id:
            raise HTTPException(
                status_code=422,
                detail="create_snapshot requires a 'resource' (snapshot id) in the persisted payload.",
            )

    # ── 7. No executed ledger row already exists (exactly-once pre-check) ─────
    existing = db.scalar(
        select(LiveOpsExecutionORM).where(LiveOpsExecutionORM.event_id == event_id)
    )
    if existing is not None and existing.status == "executed":
        raise _conflict_response(existing)

    # ── 8. Decision authorises execution ──────────────────────────────────────
    if decision.verdict == "BLOCK":
        if existing is None:
            _record_terminal(db, event_id, tool, target, "blocked")
        raise _conflict_response(existing) if existing else HTTPException(
            status_code=403,
            detail=(
                f"Event {event_id} has final verdict BLOCK; execution is refused. "
                "BLOCK override is not enabled in this V1 execution gateway."
            ),
        )

    if decision.verdict == "WARN":
        if decision.human_decision is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Event {event_id} is WARN and has no human decision yet. "
                    "Approve via POST /api/decide/{event_id} before execution."
                ),
            )
        if decision.human_decision == "rejected":
            if existing is None:
                _record_terminal(db, event_id, tool, target, "rejected")
            raise HTTPException(
                status_code=409,
                detail=f"Event {event_id} was rejected by human review; not executed.",
            )
        # approved → fall through to execute

    # ── Get or create operation record ────────────────────────────────────────
    from sqlalchemy.exc import IntegrityError
    max_retries = 3
    for attempt in range(max_retries):
        operation = (
            db.query(OperationORM)
            .filter(OperationORM.event_id == event_id)
            .order_by(OperationORM.id.desc())
            .first()
        )

        if operation is None:
            # Operation should always exist if decision exists
            logger.warning("No operation found for event %d, creating minimal record", event_id)
            # Build a minimal canonical action for legacy operations
            from app.models.operation import build_canonical_action
            legacy_event = EventCreate(
                source=event.source,
                event_type=event.event_type,
                payload=json.loads(event.payload) if isinstance(event.payload, str) else event.payload,
                original_goal=event.original_goal,
            )
            canonical = build_canonical_action(legacy_event)
            canonical_json = canonical.to_canonical_json()
            fingerprint = compute_action_fingerprint(canonical)
            
            operation = OperationORM(
                operation_id=f"op-legacy-{event_id}",
                source=event.source,
                event_id=event_id,
                canonical_action_json=canonical_json,
                action_fingerprint=fingerprint,
                lifecycle_state="approved",
            )
            db.add(operation)
            try:
                db.commit()
                db.refresh(operation)
            except IntegrityError:
                db.rollback()
                # Another request created it concurrently, retry
                if attempt < max_retries - 1:
                    continue
                raise
        break
    else:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get or create operation for event {event_id} after {max_retries} attempts",
        )

    # Check operation state allows execution
    if operation.lifecycle_state not in ("approved", "executing", "executed"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Operation {operation.operation_id} is in state '{operation.lifecycle_state}' "
                f"and cannot be executed. Expected 'approved', 'executing', or 'executed'."
            ),
        )

    # If already executed, return conflict
    if operation.lifecycle_state == "executed":
        raise HTTPException(
            status_code=409,
            detail=f"Operation {operation.operation_id} already executed exactly-once.",
        )

    # ── 7. No executed ledger row already exists (exactly-once pre-check) ─────
    existing = db.scalar(
        select(LiveOpsExecutionORM).where(LiveOpsExecutionORM.event_id == event_id)
    )
    if existing is not None and existing.status == "executed":
        raise _conflict_response(existing)

    # ── Exactly-once reservation (authoritative DB guard) ─────────────────────
    if existing is not None:
        # Any non-executed existing row (pending/failed/rejected/blocked) conflicts.
        raise _conflict_response(existing)

    ledger = LiveOpsExecutionORM(
        event_id=event_id,
        tool=tool,
        target=target if tool != "list_resources" else None,
        status="pending",
        created_at=datetime.now(timezone.utc),
    )
    db.add(ledger)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        racing = db.scalar(
            select(LiveOpsExecutionORM).where(LiveOpsExecutionORM.event_id == event_id)
        )
        raise _conflict_response(racing) from None
    db.refresh(ledger)

    # ── Update operation state to mark execution started ──────────────────────
    from app.models.operation import update_operation_state
    update_operation_state(db, operation, "executing")

    # ── Dispatch to the simulated cloud (no dynamic getattr on caller input) ──
    try:
        outcome = _dispatch_tool(cloud, tool, target, payload)
    except Exception as exc:
        logger.exception("LiveOps execution failed for event %d", event_id)
        ledger.status = "failed"
        ledger.result = json.dumps({"error": str(exc)})
        db.commit()
        # Update operation state to failed
        update_operation_state(db, operation, "failed", error_info=str(exc))
        raise HTTPException(
            status_code=500,
            detail=f"Simulated cloud operation failed for event {event_id}: {exc}",
        ) from exc

    # ── Mark executed ONLY after the sandbox operation succeeded ──────────────
    ledger.status = "executed"
    ledger.executed_at = datetime.now(timezone.utc)
    ledger.result = json.dumps(_summarise(tool, target if tool != "list_resources" else None, outcome))
    db.commit()
    
    # Update operation state to executed
    update_operation_state(db, operation, "executed")
    db.refresh(ledger)

    # ── Authorized Outcome Verification ───────────────────────────────────────
    cloud = get_cloud()
    verification = verify_outcome(db, operation, cloud, ledger)

    logger.info("LiveOps event %d executed — tool=%s target=%s operation=%s verification=%s",
                event_id, tool, target, operation.operation_id, verification.status)
    
    return LiveOpsExecutionResponse.model_validate(ledger)


# ── Execution lookup ───────────────────────────────────────────────────────────

@router.get(
    "/execution/{event_id}",
    response_model=LiveOpsExecutionResponse,
    summary="Get the execution-ledger record for a LiveOps event",
    description=(
        "Returns the current execution-ledger status for an event. Avoids "
        "coupling a future agent runner to database internals."
    ),
)
async def get_liveops_execution(
    event_id: int,
    db: Session = Depends(get_db),
) -> LiveOpsExecutionResponse:
    ledger = db.scalar(
        select(LiveOpsExecutionORM).where(LiveOpsExecutionORM.event_id == event_id)
    )
    if ledger is None:
        raise HTTPException(
            status_code=404,
            detail=f"No execution record found for event {event_id}.",
        )
    return LiveOpsExecutionResponse.model_validate(ledger)


# ── Outcome Verification lookup ──────────────────────────────────────────────────

@router.get(
    "/outcome/{event_id}",
    response_model=OutcomeVerificationResult,
    summary="Get the authorized outcome verification result for a LiveOps event",
    description=(
        "Returns the outcome verification result for a LiveOps event, including "
        "the verification status (VERIFIED, PARTIAL, MISMATCH, EXECUTION_FAILED, "
        "OUTCOME_UNKNOWN), expected vs observed state, and any invariant violations."
    ),
)
async def get_outcome_verification(
    event_id: int,
    db: Session = Depends(get_db),
) -> OutcomeVerificationResult:
    # Get the operation record
    operation = (
        db.query(OperationORM)
        .filter(OperationORM.event_id == event_id)
        .order_by(OperationORM.id.desc())
        .first()
    )
    if operation is None:
        raise HTTPException(
            status_code=404,
            detail=f"No operation found for event {event_id}.",
        )

    # Get the execution record
    execution_record = db.scalar(
        select(LiveOpsExecutionORM).where(LiveOpsExecutionORM.event_id == event_id)
    )
    if execution_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No execution record found for event {event_id}.",
        )

    # Perform verification
    cloud = get_cloud()
    verification = verify_outcome(db, operation, cloud, execution_record)
    return verification