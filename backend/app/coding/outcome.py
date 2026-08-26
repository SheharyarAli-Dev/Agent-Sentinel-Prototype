"""
app/coding/outcome.py
─────────────────────
Stage 4 — coding outcome verification and bounded diff evidence.

Verifies that a governed file-write produced the authorized result by
comparing observed workspace evidence against persisted trusted records.
Generates a bounded unified diff from actual original and observed content.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.coding_execution import CodingExecutionORM
from app.models.coding_outcome import CodingOutcomeORM
from app.models.decision import DecisionORM
from app.models.event import EventORM
from app.models.operation import OperationORM
from app.sandbox.coding_executor import (
    _SEED_PATH,
    _file_hash,
)
from app.models.coding_proposal import _load_path_rules

logger = logging.getLogger(__name__)

_DIFF_MAX_LINES = 500
_DIFF_MAX_CHARS = 100_000


# ── Helpers ────────────────────────────────────────────────────────────────────


def _load_seed() -> dict[str, Any]:
    with open(_SEED_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _generate_diff(
    old_content: bytes,
    new_content: bytes,
    relative_path: str,
    max_lines: int = _DIFF_MAX_LINES,
    max_chars: int = _DIFF_MAX_CHARS,
) -> tuple[str, bool]:
    """Generate a bounded unified diff from actual original and observed content.

    Returns (diff_text, truncated).
    """
    try:
        old_text = old_content.decode("utf-8")
        new_text = new_content.decode("utf-8")
    except UnicodeDecodeError:
        return "", False

    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{relative_path}",
            tofile=f"b/{relative_path}",
            lineterm="",
        )
    )
    truncated = False
    if len(diff_lines) > max_lines:
        diff_lines = diff_lines[:max_lines]
        truncated = True
    diff_text = "\n".join(diff_lines)
    if len(diff_text) > max_chars:
        diff_text = diff_text[:max_chars]
        truncated = True
    return diff_text, truncated


def _classify_changed_files(
    changed_files: list[str],
    unexpected_changes: list[str],
    authorized_path: str,
) -> tuple[list[str], list[str], list[str]]:
    """Classify changed files into created, deleted, modified.

    For the single-file-write model:
    - modified = changed_files that are not the authorized path (unexpected modifications)
    - created = files in changed_files but not in seed (new files)
    - deleted = files in seed but not in changed_files (would require pre snapshot)

    Since we only have changed_files (files whose hash changed), we approximate:
    - unexpected_modified = unexpected_changes that exist in seed
    - unexpected_created = unexpected_changes that don't exist in seed
    """
    seed = _load_seed()
    seed_files = set(seed.get("files", {}).keys())

    unexpected_modified = sorted(
        f for f in unexpected_changes if f in seed_files
    )
    unexpected_created = sorted(
        f for f in unexpected_changes if f not in seed_files
    )
    unexpected_deleted: list[str] = []

    return unexpected_created, unexpected_deleted, unexpected_modified


# ── Main verification function ────────────────────────────────────────────────


def verify_coding_outcome(
    db: Session,
    execution: CodingExecutionORM,
    event: EventORM,
    workspace_path: Path | None = None,
    old_content: bytes = b"",
    new_content: bytes = b"",
    protected_before: dict[str, str] | None = None,
    protected_after: dict[str, str] | None = None,
) -> CodingOutcomeORM:
    """Verify a coding execution against expected authorization.

    Captures bounded evidence from the runtime workspace before cleanup.
    Persists a separate CodingOutcomeORM row.
    Verification failure must NOT rewrite the execution record.
    """
    now = datetime.now(timezone.utc)

    # ── Load context ────────────────────────────────────────────────────────
    decision = (
        db.query(DecisionORM)
        .filter(DecisionORM.event_id == execution.event_id)
        .order_by(DecisionORM.id.desc())
        .first()
    )
    operation = (
        db.query(OperationORM)
        .filter(OperationORM.event_id == execution.event_id)
        .order_by(OperationORM.id.desc())
        .first()
    )

    proposal = None
    try:
        payload = json.loads(event.payload) if isinstance(event.payload, str) else event.payload
        if isinstance(payload, dict):
            from app.models.coding_proposal import CodingProposal
            proposal = CodingProposal(**payload)
    except Exception:
        pass

    seed = _load_seed()
    seed_files = set(seed.get("files", {}).keys())

    # ── Short-circuit: execution failed ─────────────────────────────────────
    if execution.status == "failed":
        return _persist_outcome(
            db, execution, operation, proposal, seed_files, now,
            verification_status="EXECUTION_FAILED",
            verification_error_code=execution.error_code or "EXECUTION_FAILED",
            verification_error_message=execution.error_message or "",
            old_content=old_content, new_content=new_content,
        )

    # ── Short-circuit: not executed ─────────────────────────────────────────
    if execution.status != "executed":
        return _persist_outcome(
            db, execution, operation, proposal, seed_files, now,
            verification_status="OUTCOME_UNKNOWN",
            verification_error_code="NOT_EXECUTED",
            verification_error_message=f"Execution status is '{execution.status}'",
            old_content=old_content, new_content=new_content,
        )

    # ── Reconstruct expected outcome ────────────────────────────────────────
    if proposal is None:
        return _persist_outcome(
            db, execution, operation, proposal, seed_files, now,
            verification_status="OUTCOME_UNKNOWN",
            verification_error_code="MISSING_PROPOSAL",
            verification_error_message="Could not reconstruct proposal from event payload",
            old_content=old_content, new_content=new_content,
        )

    expected_path = proposal.relative_path
    expected_old_hash = proposal.expected_old_hash
    expected_new_hash = proposal.expected_new_hash

    # ── Reconstruct observed outcome from execution record ──────────────────
    observed_path = execution.relative_path
    observed_old_hash = execution.before_hash or ""
    observed_final_hash = execution.after_hash or ""
    observed_changed_files = execution.get_changed_files()
    unexpected_changes = execution.get_unexpected_changes()

    # ── Classify changes ────────────────────────────────────────────────────
    unexpected_created, unexpected_deleted, unexpected_modified = _classify_changed_files(
        observed_changed_files, unexpected_changes, expected_path
    )

    # ── Check protected invariants ──────────────────────────────────────────
    invariant_violations: list[str] = []
    if protected_before and protected_after:
        for path, before_hash in sorted(protected_before.items()):
            after_hash = protected_after.get(path, "")
            if after_hash != before_hash:
                invariant_violations.append(
                    f"Protected invariant changed: {path}"
                )
    elif protected_before and not protected_after:
        for path in sorted(protected_before.keys()):
            invariant_violations.append(
                f"Protected invariant hash unavailable after execution: {path}"
            )

    # ── Determine verification status ───────────────────────────────────────
    verification_status = "OUTCOME_UNKNOWN"
    verification_error_code = ""
    verification_error_message = ""

    if invariant_violations:
        verification_status = "MISMATCH"
        verification_error_message = "; ".join(invariant_violations)
    elif observed_final_hash and observed_final_hash != expected_new_hash:
        verification_status = "MISMATCH"
        verification_error_code = "FINAL_HASH_MISMATCH"
        verification_error_message = (
            f"Final hash {observed_final_hash[:16]}... "
            f"differs from expected {expected_new_hash[:16]}..."
        )
    elif unexpected_changes:
        verification_status = "MISMATCH"
        verification_error_code = "UNAUTHORIZED_CHANGES"
        verification_error_message = f"Unexpected changes: {', '.join(unexpected_changes)}"
    elif observed_old_hash and observed_old_hash != expected_old_hash:
        verification_status = "MISMATCH"
        verification_error_code = "OLD_HASH_MISMATCH"
        verification_error_message = (
            f"Observed old hash {observed_old_hash[:16]}... "
            f"differs from expected {expected_old_hash[:16]}..."
        )
    elif observed_path != expected_path:
        verification_status = "MISMATCH"
        verification_error_code = "PATH_MISMATCH"
        verification_error_message = (
            f"Observed path '{observed_path}' differs from expected '{expected_path}'"
        )
    elif execution.status == "executed":
        verification_status = "VERIFIED"

    # ── Generate diff ───────────────────────────────────────────────────────
    diff_text: str | None = None
    diff_truncated = False
    diff_omitted_reason = ""

    if verification_status in ("VERIFIED", "MISMATCH") and old_content and new_content:
        tier = "unknown"
        try:
            from app.models.coding_proposal import classify_path
            tier = classify_path(expected_path)
        except Exception:
            pass
        if tier == "protected":
            diff_omitted_reason = "PROTECTED_PATH"
        elif tier == "sensitive":
            diff_omitted_reason = "SENSITIVE_PATH"
        else:
            diff_text, diff_truncated = _generate_diff(
                old_content, new_content, expected_path
            )

    # ── Persist outcome ─────────────────────────────────────────────────────
    return _persist_outcome(
        db, execution, operation, proposal, seed_files, now,
        verification_status=verification_status,
        verification_error_code=verification_error_code,
        verification_error_message=verification_error_message,
        old_content=old_content, new_content=new_content,
        expected_path=expected_path,
        expected_old_hash=expected_old_hash,
        expected_new_hash=expected_new_hash,
        observed_path=observed_path,
        observed_old_hash=observed_old_hash,
        observed_final_hash=observed_final_hash,
        unexpected_created=unexpected_created,
        unexpected_deleted=unexpected_deleted,
        unexpected_modified=unexpected_modified,
        invariant_violations=invariant_violations,
        protected_before=protected_before,
        protected_after=protected_after,
        diff_text=diff_text,
        diff_truncated=diff_truncated,
        diff_omitted_reason=diff_omitted_reason,
    )


def _persist_outcome(
    db: Session,
    execution: CodingExecutionORM,
    operation: OperationORM | None,
    proposal: Any,
    seed_files: set[str],
    now: datetime,
    *,
    verification_status: str,
    verification_error_code: str = "",
    verification_error_message: str = "",
    old_content: bytes = b"",
    new_content: bytes = b"",
    expected_path: str = "",
    expected_old_hash: str = "",
    expected_new_hash: str = "",
    observed_path: str = "",
    observed_old_hash: str = "",
    observed_final_hash: str = "",
    unexpected_created: list[str] | None = None,
    unexpected_deleted: list[str] | None = None,
    unexpected_modified: list[str] | None = None,
    invariant_violations: list[str] | None = None,
    protected_before: dict[str, str] | None = None,
    protected_after: dict[str, str] | None = None,
    diff_text: str | None = None,
    diff_truncated: bool = False,
    diff_omitted_reason: str = "",
) -> CodingOutcomeORM:
    """Build and persist a CodingOutcomeORM row."""
    if proposal is not None:
        expected_path = expected_path or proposal.relative_path
        expected_old_hash = expected_old_hash or proposal.expected_old_hash
        expected_new_hash = expected_new_hash or proposal.expected_new_hash

    observed_path = observed_path or execution.relative_path
    observed_old_hash = observed_old_hash or execution.before_hash or ""
    observed_final_hash = observed_final_hash or execution.after_hash or ""

    outcome = CodingOutcomeORM(
        event_id=execution.event_id,
        execution_id=execution.id,
        operation_id=execution.operation_id,
        action_fingerprint=execution.action_fingerprint,
        verification_status=verification_status,
        expected_path=expected_path,
        observed_path=observed_path,
        expected_old_hash=expected_old_hash,
        observed_old_hash=observed_old_hash,
        expected_new_hash=expected_new_hash,
        observed_final_hash=observed_final_hash,
        expected_changed_files_json=json.dumps([expected_path] if expected_path else []),
        observed_modified_json=json.dumps(execution.get_changed_files()),
        unexpected_created_json=json.dumps(unexpected_created or []),
        unexpected_deleted_json=json.dumps(unexpected_deleted or []),
        unexpected_modified_json=json.dumps(unexpected_modified or []),
        protected_invariants_before_json=json.dumps(protected_before or {}),
        protected_invariants_after_json=json.dumps(protected_after or {}),
        invariant_violations_json=json.dumps(invariant_violations or []),
        diff_text=diff_text,
        diff_truncated=diff_truncated,
        diff_omitted_reason=diff_omitted_reason,
        verification_error_code=verification_error_code,
        verification_error_message=verification_error_message[:1024] if verification_error_message else "",
        verified_at=now,
        created_at=now,
    )
    db.add(outcome)
    try:
        db.commit()
        db.refresh(outcome)
    except Exception as exc:
        db.rollback()
        logger.warning("Outcome persistence failed for event %d: %s", execution.event_id, exc)
        # Return a transient outcome that is not committed
        outcome.id = 0
        outcome.verification_status = "OUTCOME_UNKNOWN"
        outcome.verification_error_code = "OUTCOME_PERSISTENCE_FAILED"
        outcome.verification_error_message = str(exc)[:1024]
    return outcome
