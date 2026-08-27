"""
app/api/coding_execution.py
────────────────────────────
Governance-gated coding execution gateway — Stage 3.

Connects ASENT policy decisions to the contained Stage 2 coding executor.
One execution per event_id. The UNIQUE constraint on coding_executions.event_id
is the authoritative database-level exactly-once guard.

Endpoints:
  POST /api/coding/execute/{event_id}  — execute an ALLOW / approved-WARN coding action
  GET  /api/coding/execution/{event_id} — inspect the execution ledger

Execution contract:
  1. Event exists.
  2. Event source is "cursor" and event_type is "coding_proposal".
  3. A decision exists for the event.
  4. An operation exists with a valid fingerprint.
  5. Authoritative fingerprint matches on recompute.
  6. CodingProposal is reconstructed from stored event payload.
  7. Lifecycle state permits execution (ALLOW: evaluated/approved, WARN: approved).
  8. Decision authorises execution:
       ALLOW                      → proceed
       WARN + approved            → proceed
       WARN + rejected/expired    → reject
       WARN + no human decision   → reject
       BLOCK                      → HTTP 403
       REJECTED / EXPIRED         → reject
  9. No execution row already exists (exactly-once pre-check).
  10. Reserve pending row (UNIQUE guard).
  11. Execute through contained Stage 2 executor.
  12. Persist terminal result.
  13. Update operation lifecycle.
  14. Broadcast bounded WebSocket evidence.
  15. Return API response.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.coding_execution import (
    CodingExecutionORM,
    CodingExecutionResponse,
)
from app.models.decision import DecisionORM
from app.models.event import EventCreate, EventORM
from app.models.operation import (
    OperationORM,
    build_canonical_action,
    compute_action_fingerprint,
    update_operation_state,
)
from app.sandbox.coding_executor import CodingWorkspace
from app.models.coding_outcome import CodingOutcomeORM, CodingOutcomeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/coding", tags=["coding"])


# ── Helpers ────────────────────────────────────────────────────────────────────


def _conflict_response(row: CodingExecutionORM) -> HTTPException:
    """Map an existing ledger row to the correct conflict HTTP response."""
    if row.status == "executed":
        return HTTPException(
            409,
            detail={
                "status": "executed",
                "execution_id": row.id,
                "message": f"Event {row.event_id} already executed exactly-once.",
            },
        )
    if row.status == "pending":
        return HTTPException(
            409,
            detail={
                "status": "pending",
                "execution_id": row.id,
                "message": f"Event {row.event_id} execution is already in progress.",
            },
        )
    if row.status == "executing":
        return HTTPException(
            409,
            detail={
                "status": "executing",
                "execution_id": row.id,
                "message": f"Event {row.event_id} execution is in progress.",
            },
        )
    if row.status == "failed":
        return HTTPException(
            409,
            detail={
                "status": "failed",
                "execution_id": row.id,
                "message": (
                    f"Event {row.event_id} previously failed; automatic retry "
                    "is disabled to prevent double execution."
                ),
            },
        )
    if row.status == "outcome_unknown":
        return HTTPException(
            409,
            detail={
                "status": "outcome_unknown",
                "execution_id": row.id,
                "message": (
                    f"Event {row.event_id} has an uncertain outcome from a "
                    "prior execution attempt."
                ),
            },
        )
    return HTTPException(
        409,
        detail={
            "status": row.status,
            "execution_id": row.id,
            "message": f"Event {row.event_id} already has ledger status '{row.status}'.",
        },
    )


def _build_response(
    row: CodingExecutionORM, replayed: bool = False
) -> CodingExecutionResponse:
    """Build a CodingExecutionResponse from an ORM row."""
    return CodingExecutionResponse(
        id=row.id,
        event_id=row.event_id,
        operation_id=row.operation_id,
        action_fingerprint=row.action_fingerprint,
        relative_path=row.relative_path,
        status=row.status,
        before_hash=row.before_hash or "",
        after_hash=row.after_hash or "",
        expected_old_hash=row.expected_old_hash,
        expected_new_hash=row.expected_new_hash,
        bytes_written=row.bytes_written or 0,
        changed_files=row.get_changed_files(),
        unexpected_changes=row.get_unexpected_changes(),
        error_code=row.error_code or "",
        error_message=row.error_message or "",
        restoration_attempted=row.restoration_attempted,
        restoration_succeeded=row.restoration_succeeded,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        replayed=replayed,
    )


# ── POST /api/coding/execute/{event_id} ───────────────────────────────────────


@router.post(
    "/execute/{event_id}",
    response_model=CodingExecutionResponse,
    summary="Execute a governance-gated coding file-write exactly once",
    description=(
        "Executes the contained Stage 2 coding executor for a coding_proposal "
        "event whose policy verdict is ALLOW or WARN-with-human-approval. "
        "The database-level UNIQUE constraint on coding_executions.event_id "
        "guarantees exactly-once execution: a repeated or racing request "
        "receives HTTP 409 without touching the executor."
    ),
)
async def execute_coding_action(
    event_id: int,
    db: Session = Depends(get_db),
) -> CodingExecutionResponse:
    # ── 1. Event exists ───────────────────────────────────────────────────────
    event = db.get(EventORM, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found.")

    # ── 2. Event source is "cursor" and event_type is "coding_proposal" ───────
    if event.source != "cursor":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Event {event_id} has source '{event.source}'; "
                "only 'cursor' source events can be executed here."
            ),
        )
    if event.event_type != "coding_proposal":
        raise HTTPException(
            status_code=422,
            detail=(
                f"Event {event_id} has event_type '{event.event_type}'; "
                "only 'coding_proposal' events can be executed here."
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

    # ── 4. An operation exists with a valid fingerprint ────────────────────────
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

    # ── 5. Authoritative fingerprint matches on recompute ─────────────────────
    try:
        payload = (
            json.loads(event.payload)
            if isinstance(event.payload, str)
            else event.payload
        )
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

    agent_identity = payload.get("agent_id") or payload.get("agent")
    agent_id_str = str(agent_identity) if agent_identity else f"{event.source}-default"

    event_create = EventCreate(
        source=event.source,
        event_type=event.event_type,
        payload=payload,
        original_goal=event.original_goal,
    )
    recomputed_canonical = build_canonical_action(event_create, agent_id_str)
    recomputed_fingerprint = compute_action_fingerprint(recomputed_canonical)

    if recomputed_fingerprint != operation.action_fingerprint:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Action fingerprint mismatch for event {event_id}. "
                "The stored canonical action does not match the current event payload. "
                "This may indicate tampering."
            ),
        )

    # ── 6. Reconstruct CodingProposal from stored event payload ───────────────
    from app.models.coding_proposal import CodingProposal

    try:
        proposal = CodingProposal(**payload)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Event {event_id} payload does not form a valid CodingProposal: {exc}",
        ) from exc

    # ── 7. Exactly-once pre-check ─────────────────────────────────────────────
    # Check for existing execution row BEFORE lifecycle validation so that
    # retries against terminal operations (executed/failed) receive a 409
    # conflict rather than a 422 lifecycle error.
    existing = db.scalar(
        select(CodingExecutionORM).where(CodingExecutionORM.event_id == event_id)
    )
    if existing is not None:
        raise _conflict_response(existing)

    # ── 8. Lifecycle state permits execution ───────────────────────────────────
    if decision.verdict == "BLOCK":
        raise HTTPException(
            status_code=403,
            detail=(
                f"Event {event_id} has final verdict BLOCK; execution is refused. "
                "BLOCK override is not enabled in this execution gateway."
            ),
        )

    if decision.verdict == "EXPIRED":
        raise HTTPException(
            status_code=410,
            detail=f"Event {event_id} has verdict EXPIRED; execution is not permitted.",
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
            raise HTTPException(
                status_code=409,
                detail=f"Event {event_id} was rejected by human review; not executed.",
            )
        if decision.review_expires_at:
            expires_at = decision.review_expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                raise HTTPException(
                    status_code=410,
                    detail=f"Review period for event {event_id} has expired.",
                )
        # approved → fall through to execute
        if operation.lifecycle_state not in ("evaluated", "approved"):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Operation {operation.operation_id} is in state "
                    f"'{operation.lifecycle_state}' and cannot be executed."
                ),
            )
    elif decision.verdict == "ALLOW":
        # ALLOW: may proceed without human approval
        if operation.lifecycle_state not in ("evaluated", "approved"):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Operation {operation.operation_id} is in state "
                    f"'{operation.lifecycle_state}' and cannot be executed."
                ),
            )
    else:
        raise HTTPException(
            status_code=422,
            detail=f"Event {event_id} has unexpected verdict '{decision.verdict}'.",
        )

    # ── 9. Exactly-once reservation (authoritative DB guard) ──────────────────
    now = datetime.now(timezone.utc)
    ledger = CodingExecutionORM(
        event_id=event_id,
        operation_id=operation.operation_id,
        action_fingerprint=operation.action_fingerprint,
        relative_path=proposal.relative_path,
        status="pending",
        expected_old_hash=proposal.expected_old_hash,
        expected_new_hash=proposal.expected_new_hash,
        created_at=now,
    )
    db.add(ledger)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        racing = db.scalar(
            select(CodingExecutionORM).where(CodingExecutionORM.event_id == event_id)
        )
        raise _conflict_response(racing) from None
    db.refresh(ledger)

    # ── 10. Update operation state to executing ───────────────────────────────
    # For ALLOW verdicts, transition evaluated → approved first (ALLOW = human approval)
    if operation.lifecycle_state == "evaluated" and decision.verdict == "ALLOW":
        try:
            update_operation_state(db, operation, "approved")
        except Exception as exc:
            logger.warning("Operation approval transition failed for event %d: %s", event_id, exc)
    try:
        update_operation_state(db, operation, "executing")
    except Exception as exc:
        logger.warning("Operation state update failed for event %d: %s", event_id, exc)

    # ── 11. Determine trusted review authorization ────────────────────────────
    review_authorized = False
    if decision.verdict == "ALLOW":
        review_authorized = True
    elif decision.verdict == "WARN" and decision.human_decision == "approved":
        review_authorized = True

    # ── 12. Execute through the contained Stage 2 executor ────────────────────
    ledger.started_at = datetime.now(timezone.utc)
    ledger.status = "executing"
    db.commit()

    executor_result = None
    outcome = None
    try:
        workspace = CodingWorkspace()
        try:
            workspace.copy_demo()
            # ── 12a. Capture protected-invariant hashes BEFORE execution ───
            protected_before = workspace.get_protected_invariant_hashes() if workspace._runtime_root else {}
            executor_result = workspace.execute_file_write(
                proposal, review_authorized=review_authorized
            )
        finally:
            # Capture protected-invariant hashes AFTER execution, before cleanup
            protected_after = {}
            if workspace._runtime_root:
                try:
                    protected_after = workspace.get_protected_invariant_hashes()
                except Exception:
                    pass
            old_content = executor_result.old_content if executor_result else b""
            new_content = executor_result.new_content if executor_result else b""
            workspace.cleanup()
    except Exception as exc:
        logger.exception("Coding workspace setup failed for event %d", event_id)
        executor_result = None
        ledger.status = "failed"
        ledger.error_code = "FAILED_WORKSPACE"
        ledger.error_message = str(exc)[:1024]
        ledger.completed_at = datetime.now(timezone.utc)
        db.commit()
        try:
            update_operation_state(db, operation, "failed", error_info=str(exc)[:1024])
        except Exception:
            pass
        return _build_response(ledger)

    # ── 13. Persist the structured result ─────────────────────────────────────
    if executor_result.status == "executed":
        ledger.status = "executed"
    elif executor_result.status == "rejected":
        ledger.status = "failed"
    elif executor_result.status == "failed":
        ledger.status = "failed"
    else:
        ledger.status = "failed"

    ledger.before_hash = executor_result.before_hash
    ledger.after_hash = executor_result.after_hash
    ledger.bytes_written = executor_result.bytes_written
    ledger.changed_files_json = json.dumps(executor_result.changed_files)
    ledger.unexpected_changes_json = json.dumps(executor_result.unexpected_changes)
    ledger.error_code = executor_result.error_code or None
    ledger.error_message = executor_result.error_message or None
    ledger.restoration_attempted = executor_result.restoration_attempted
    ledger.restoration_succeeded = executor_result.restoration_succeeded
    ledger.completed_at = datetime.now(timezone.utc)
    db.commit()

    # ── 14. Update operation lifecycle ────────────────────────────────────────
    try:
        if ledger.status == "executed":
            update_operation_state(db, operation, "executed")
        else:
            update_operation_state(
                db, operation, "failed",
                error_info=executor_result.error_code or "EXECUTION_FAILED",
            )
    except Exception as exc:
        logger.warning(
            "Operation lifecycle update failed for event %d: %s", event_id, exc
        )

    # ── 14a. Outcome verification (Stage 4) ─────────────────────────────────
    try:
        from app.coding.outcome import verify_coding_outcome
        outcome = verify_coding_outcome(
            db, ledger, event,
            old_content=old_content,
            new_content=new_content,
            protected_before=protected_before,
            protected_after=protected_after,
        )
    except Exception as exc:
        logger.warning("Outcome verification failed for event %d: %s", event_id, exc)
        outcome = None

    # ── 15. Broadcast bounded WebSocket evidence ──────────────────────────────
    try:
        from app.websocket.manager import manager

        ws_payload: dict[str, Any] = {
            "type": "coding_execution_complete",
            "event_id": event_id,
            "execution_id": ledger.id,
            "operation_id": operation.operation_id,
            "status": ledger.status,
            "relative_path": ledger.relative_path,
            "error_code": ledger.error_code or "",
            "replayed": False,
        }
        if outcome is not None and outcome.id:
            ws_payload["verification_status"] = outcome.verification_status
            ws_payload["invariant_violation_count"] = len(outcome.get_invariant_violations())
            ws_payload["diff_truncated"] = outcome.diff_truncated
            ws_payload["diff_omitted_reason"] = outcome.diff_omitted_reason
        await manager.broadcast(ws_payload)
    except Exception as exc:
        logger.warning("WebSocket broadcast failed for event %d: %s", event_id, exc)

    logger.info(
        "Coding event %d executed — path=%s operation=%s status=%s",
        event_id,
        ledger.relative_path,
        operation.operation_id,
        ledger.status,
    )

    db.refresh(ledger)
    return _build_response(ledger)


# ── GET /api/coding/execution/{event_id} ──────────────────────────────────────


@router.get(
    "/execution/{event_id}",
    response_model=CodingExecutionResponse,
    summary="Get the execution-ledger record for a coding event",
    description=(
        "Returns the current execution-ledger status for a coding_proposal event. "
        "Avoids coupling a future agent runner to database internals."
    ),
)
async def get_coding_execution(
    event_id: int,
    db: Session = Depends(get_db),
) -> CodingExecutionResponse:
    ledger = db.scalar(
        select(CodingExecutionORM).where(CodingExecutionORM.event_id == event_id)
    )
    if ledger is None:
        raise HTTPException(
            status_code=404,
            detail=f"No execution record found for event {event_id}.",
        )
    return _build_response(ledger, replayed=True)


# ── GET /api/coding/outcome/{event_id} ──────────────────────────────────────


@router.get(
    "/outcome/{event_id}",
    response_model=CodingOutcomeResponse,
    summary="Get the outcome verification record for a coding event",
    description=(
        "Returns the persisted outcome verification result for a coding_proposal event. "
        "If an execution exists but no outcome has been persisted, returns 404. "
        "Does not reobserve a temporary workspace that has already been cleaned."
    ),
)
async def get_coding_outcome(
    event_id: int,
    db: Session = Depends(get_db),
) -> CodingOutcomeResponse:
    outcome = db.scalar(
        select(CodingOutcomeORM).where(CodingOutcomeORM.event_id == event_id)
    )
    if outcome is None:
        raise HTTPException(
            status_code=404,
            detail=f"No outcome record found for event {event_id}.",
        )
    return CodingOutcomeResponse(
        id=outcome.id,
        event_id=outcome.event_id,
        execution_id=outcome.execution_id,
        operation_id=outcome.operation_id,
        action_fingerprint=outcome.action_fingerprint,
        verification_status=outcome.verification_status,
        expected_path=outcome.expected_path,
        observed_path=outcome.observed_path,
        expected_old_hash=outcome.expected_old_hash,
        observed_old_hash=outcome.observed_old_hash,
        expected_new_hash=outcome.expected_new_hash,
        observed_final_hash=outcome.observed_final_hash,
        expected_changed_files=outcome.get_expected_changed_files(),
        observed_modified=outcome.get_observed_modified(),
        unexpected_created=outcome.get_unexpected_created(),
        unexpected_deleted=outcome.get_unexpected_deleted(),
        unexpected_modified=outcome.get_unexpected_modified(),
        invariant_violations=outcome.get_invariant_violations(),
        diff_text=outcome.diff_text,
        diff_truncated=outcome.diff_truncated,
        diff_omitted_reason=outcome.diff_omitted_reason,
        verification_error_code=outcome.verification_error_code,
        verification_error_message=outcome.verification_error_message,
        verified_at=outcome.verified_at,
        created_at=outcome.created_at,
        replayed=True,
    )
