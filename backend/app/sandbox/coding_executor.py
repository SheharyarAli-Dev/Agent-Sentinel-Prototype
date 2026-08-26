"""
app/sandbox/coding_executor.py
──────────────────────────────
Stage 2 — contained coding file-write executor.

Provides an ASENT-controlled temporary runtime copy of the coding-demo
fixture and a single bounded file_write action.  The executor never writes
into the tracked fixture, the ASENT source tree, or a caller-provided root.

Design notes
────────────
  * Production fixture root and seed path are fixed internally.
  * Each execution creates a fresh temporary workspace via shutil.copytree.
  * A workspace-level reentrant lock serializes full evidence transactions.
  * Different workspace instances remain independent (no global lock sharing).
  * The tracked coding-demo fixture is verified unchanged after every operation.
  * All evidence hashes are bytes-based (read_bytes, not text-then-encode).
  * Workspace snapshot covers every regular file under the runtime root,
    not just seed-listed files.  Executor-owned temp files (*.tmp matching
    the controlled naming convention) are excluded while an atomic write
    is active.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.models.coding_proposal import (
    CodingProposal,
    PathSafetyRejection,
    classify_path,
    validate_coding_path,
    _load_path_rules,
)


# ── Fixed paths ───────────────────────────────────────────────────────────────

_FIXTURE_ROOT = (
    Path(__file__).resolve().parent.parent.parent.parent / "coding-demo"
)
_SEED_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "coding_demo_seed.json"
)


# ── Error codes ───────────────────────────────────────────────────────────────

REJECTED_ABSOLUTE_PATH = "REJECTED_ABSOLUTE_PATH"
REJECTED_WINDOWS_PATH = "REJECTED_WINDOWS_PATH"
REJECTED_UNC_PATH = "REJECTED_UNC_PATH"
REJECTED_TRAVERSAL = "REJECTED_TRAVERSAL"
REJECTED_NULL_BYTE = "REJECTED_NULL_BYTE"
REJECTED_SYMLINK = "REJECTED_SYMLINK"
REJECTED_OUTSIDE_ROOT = "REJECTED_OUTSIDE_ROOT"
REJECTED_PROTECTED = "REJECTED_PROTECTED"
REJECTED_REVIEW_REQUIRED = "REJECTED_REVIEW_REQUIRED"
REJECTED_FILE_NOT_FOUND = "REJECTED_FILE_NOT_FOUND"
REJECTED_OLD_HASH_MISMATCH = "REJECTED_OLD_HASH_MISMATCH"
REJECTED_CONTENT_SIZE = "REJECTED_CONTENT_SIZE"
FAILED_SEED_VERIFICATION = "FAILED_SEED_VERIFICATION"
FAILED_WRITE = "FAILED_WRITE"
FAILED_HASH_VERIFICATION = "FAILED_HASH_VERIFICATION"
FAILED_UNEXPECTED_CHANGES = "FAILED_UNEXPECTED_CHANGES"
FAILED_RESTORATION = "FAILED_RESTORATION"
FAILED_EVIDENCE_COLLECTION = "FAILED_EVIDENCE_COLLECTION"


# ── Result model ──────────────────────────────────────────────────────────────

class CodingExecutionResult(BaseModel):
    """Structured evidence returned by the coding executor."""

    status: Literal["executed", "rejected", "failed"]
    relative_path: str
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
    executed_at: str = ""
    old_content: bytes = b""
    new_content: bytes = b""

    @field_validator(
        "before_hash", "after_hash", "expected_old_hash", "expected_new_hash"
    )
    @classmethod
    def _validate_hash_field(cls, v: str) -> str:
        if v and len(v) != 64:
            raise ValueError(f"Hash must be 64 hex characters, got {len(v)}")
        return v


# ── CodingWorkspace ───────────────────────────────────────────────────────────

class CodingWorkspace:
    """Manages a temporary runtime copy of the coding-demo fixture.

    The workspace is created by copying the tracked fixture into a fresh
    temporary directory.  The tracked fixture is never modified.

    A workspace-level reentrant lock serializes full evidence transactions.
    Different workspace instances remain independent.
    """

    def __init__(
        self,
        *,
        fixture_root: str | Path | None = None,
        seed_path: str | Path | None = None,
    ) -> None:
        self._fixture_root = Path(fixture_root) if fixture_root else _FIXTURE_ROOT
        self._seed_path = Path(seed_path) if seed_path else _SEED_PATH
        self._runtime_root: Path | None = None
        # Workspace-level lock: serializes the full evidence transaction.
        self._execution_lock = threading.RLock()

    @property
    def runtime_root(self) -> Path:
        if self._runtime_root is None:
            raise RuntimeError("Workspace not created. Call copy_demo() first.")
        return self._runtime_root

    def copy_demo(self) -> Path:
        """Copy the tracked fixture into a fresh temporary directory.

        Validates the copy against the seed manifest before returning.
        Cleanup success is not evidence of execution success.
        """
        if not self._fixture_root.is_dir():
            raise FileNotFoundError(
                f"Fixture directory does not exist: {self._fixture_root}"
            )
        self._runtime_root = Path(
            tempfile.mkdtemp(prefix="coding_workspace_")
        )
        dest = self._runtime_root / "coding-demo"
        shutil.copytree(str(self._fixture_root), str(dest))
        try:
            self._verify_seed(dest)
        except Exception:
            shutil.rmtree(str(self._runtime_root), ignore_errors=True)
            self._runtime_root = None
            raise
        return dest

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_seed(self) -> dict[str, Any]:
        with open(self._seed_path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _snapshot_workspace(self, workspace: Path) -> dict[str, str]:
        """Recursively compute bytes-based SHA-256 for every regular file.

        Excludes executor-owned temporary files matching the controlled
        atomic-write naming convention (name.tmp suffix).
        Does not follow symlinks.
        """
        hashes: dict[str, str] = {}
        for fp in sorted(workspace.rglob("*")):
            if not fp.is_file():
                continue
            if fp.is_symlink():
                continue
            if fp.suffix == ".tmp":
                continue
            rel = str(fp.relative_to(workspace)).replace("\\", "/")
            hashes[rel] = _file_hash(fp)
        return hashes

    def _verify_seed(self, workspace: Path) -> None:
        """Raise if any copied file hash mismatches the seed manifest."""
        seed = self._load_seed()
        for rel_path, info in seed.get("files", {}).items():
            fp = workspace / rel_path
            if not fp.is_file():
                raise RuntimeError(
                    f"Copied fixture missing file: {rel_path}"
                )
            actual_hash = _file_hash(fp)
            if actual_hash != info["hash"]:
                raise RuntimeError(
                    f"Seed verification failed for {rel_path}: "
                    f"expected {info['hash'][:16]}..., "
                    f"got {actual_hash[:16]}..."
                )

    # ── File write execution ──────────────────────────────────────────────────

    def execute_file_write(
        self,
        proposal: CodingProposal,
        *,
        review_authorized: bool = False,
    ) -> CodingExecutionResult:
        """Execute a bounded file_write action inside the runtime workspace.

        The full evidence transaction is serialized by the workspace-level
        execution lock.  Different workspace instances remain independent.
        """
        workspace = self.runtime_root / "coding-demo"
        raw_path = proposal.relative_path
        rel = raw_path.replace("\\", "/")
        rules = _load_path_rules()
        max_size = rules.get("max_content_size_chars", 50_000)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # ── 1. Action type ────────────────────────────────────────────────────
        if proposal.action_type != "file_write":
            return CodingExecutionResult(
                status="rejected",
                relative_path=rel,
                expected_old_hash=proposal.expected_old_hash,
                expected_new_hash=proposal.expected_new_hash,
                error_code="REJECTED_ACTION_TYPE",
                error_message=f"Unsupported action_type: {proposal.action_type}",
                executed_at=now,
            )

        # ── 2. Structural path validation (uses raw path for UNC detection) ───
        try:
            tier = validate_coding_path(raw_path, workspace)
        except PathSafetyRejection as exc:
            code = _rejection_code(exc.rule)
            return CodingExecutionResult(
                status="rejected",
                relative_path=rel,
                expected_old_hash=proposal.expected_old_hash,
                expected_new_hash=proposal.expected_new_hash,
                error_code=code,
                error_message=str(exc),
                executed_at=now,
            )

        # ── 3. Protected path → reject at execution time ──────────────────────
        if tier == "protected":
            return CodingExecutionResult(
                status="rejected",
                relative_path=rel,
                expected_old_hash=proposal.expected_old_hash,
                expected_new_hash=proposal.expected_new_hash,
                error_code=REJECTED_PROTECTED,
                error_message=(
                    f"Protected target rejected at execution time: {rel}"
                ),
                executed_at=now,
            )

        # ── 4. Sensitive path → require trusted review authorization ───────────
        if tier == "sensitive" and not review_authorized:
            return CodingExecutionResult(
                status="rejected",
                relative_path=rel,
                expected_old_hash=proposal.expected_old_hash,
                expected_new_hash=proposal.expected_new_hash,
                error_code=REJECTED_REVIEW_REQUIRED,
                error_message=(
                    f"Sensitive target requires trusted review "
                    f"authorization: {rel}"
                ),
                executed_at=now,
            )

        # ── Acquire workspace-level execution lock ────────────────────────────
        with self._execution_lock:
            # ── 5. File existence ─────────────────────────────────────────────
            target = workspace / rel
            if not target.is_file():
                return CodingExecutionResult(
                    status="rejected",
                    relative_path=rel,
                    expected_old_hash=proposal.expected_old_hash,
                    expected_new_hash=proposal.expected_new_hash,
                    error_code=REJECTED_FILE_NOT_FOUND,
                    error_message=f"File not found: {rel}",
                    executed_at=now,
                )

            # ── 6. Content size ───────────────────────────────────────────────
            if len(proposal.new_content) > max_size:
                return CodingExecutionResult(
                    status="rejected",
                    relative_path=rel,
                    expected_old_hash=proposal.expected_old_hash,
                    expected_new_hash=proposal.expected_new_hash,
                    error_code=REJECTED_CONTENT_SIZE,
                    error_message=(
                        f"Content size {len(proposal.new_content)} exceeds "
                        f"limit of {max_size}"
                    ),
                    executed_at=now,
                )

            # ── 7. Current hash ───────────────────────────────────────────────
            current_bytes = target.read_bytes()
            before_hash = hashlib.sha256(current_bytes).hexdigest()

            # ── 8. Old-hash check ─────────────────────────────────────────────
            if before_hash != proposal.expected_old_hash:
                return CodingExecutionResult(
                    status="rejected",
                    relative_path=rel,
                    before_hash=before_hash,
                    expected_old_hash=proposal.expected_old_hash,
                    expected_new_hash=proposal.expected_new_hash,
                    error_code=REJECTED_OLD_HASH_MISMATCH,
                    error_message=(
                        f"Old hash mismatch for {rel}: "
                        f"current={before_hash[:16]}..., "
                        f"expected={proposal.expected_old_hash[:16]}..."
                    ),
                    executed_at=now,
                )

            # ── 9. New-content hash ───────────────────────────────────────────
            new_hash = hashlib.sha256(
                proposal.new_content.encode("utf-8")
            ).hexdigest()
            if new_hash != proposal.expected_new_hash:
                return CodingExecutionResult(
                    status="rejected",
                    relative_path=rel,
                    before_hash=before_hash,
                    expected_old_hash=proposal.expected_old_hash,
                    expected_new_hash=proposal.expected_new_hash,
                    error_code=FAILED_HASH_VERIFICATION,
                    error_message="New content hash mismatch",
                    executed_at=now,
                )

            # ── 10. Pre-write workspace snapshot ──────────────────────────────
            pre_write_hashes = self._snapshot_workspace(workspace)

            # ── 11. Atomic write ──────────────────────────────────────────────
            original_bytes: bytes | None = None
            write_error: str | None = None
            try:
                original_bytes = target.read_bytes()
                _atomic_write(target, proposal.new_content)
            except Exception as exc:
                write_error = str(exc)

            # ── Write failure → restore ───────────────────────────────────────
            if write_error is not None:
                restoration_attempted = True
                restoration_succeeded = False
                if original_bytes is not None:
                    try:
                        _atomic_write_bytes(target, original_bytes)
                        restoration_succeeded = True
                    except Exception:
                        pass
                final_hash = _file_hash(target)
                return CodingExecutionResult(
                    status="failed",
                    relative_path=rel,
                    before_hash=before_hash,
                    after_hash=final_hash,
                    expected_old_hash=proposal.expected_old_hash,
                    expected_new_hash=proposal.expected_new_hash,
                    error_code=FAILED_WRITE,
                    error_message=f"Write failed: {write_error}",
                    restoration_attempted=restoration_attempted,
                    restoration_succeeded=restoration_succeeded,
                    executed_at=now,
                )

            # ── 12. After-hash check ──────────────────────────────────────────
            after_hash = _file_hash(target)
            if after_hash != proposal.expected_new_hash:
                restoration_attempted = True
                restoration_succeeded = False
                try:
                    _atomic_write_bytes(target, original_bytes)
                    restoration_succeeded = True
                except Exception:
                    pass
                final_hash = _file_hash(target)
                return CodingExecutionResult(
                    status="failed",
                    relative_path=rel,
                    before_hash=before_hash,
                    after_hash=final_hash,
                    expected_old_hash=proposal.expected_old_hash,
                    expected_new_hash=proposal.expected_new_hash,
                    error_code=FAILED_HASH_VERIFICATION,
                    error_message=(
                        f"After-hash mismatch for {rel}: "
                        f"got {after_hash[:16]}..., "
                        f"expected {proposal.expected_new_hash[:16]}..."
                    ),
                    restoration_attempted=restoration_attempted,
                    restoration_succeeded=restoration_succeeded,
                    executed_at=now,
                )

            # ── 13. Post-write evidence collection (guarded) ──────────────────
            try:
                post_write_hashes = self._snapshot_workspace(workspace)
                unexpected = sorted(
                    fp
                    for fp in set(post_write_hashes) | set(pre_write_hashes)
                    if fp != rel
                    and post_write_hashes.get(fp) != pre_write_hashes.get(fp)
                )
                changed = sorted(
                    fp
                    for fp in set(post_write_hashes) | set(pre_write_hashes)
                    if post_write_hashes.get(fp) != pre_write_hashes.get(fp)
                )
            except Exception as exc:
                restoration_attempted = True
                restoration_succeeded = False
                try:
                    _atomic_write_bytes(target, original_bytes)
                    restoration_succeeded = True
                except Exception:
                    pass
                final_hash = _file_hash(target)
                return CodingExecutionResult(
                    status="failed",
                    relative_path=rel,
                    before_hash=before_hash,
                    after_hash=final_hash,
                    expected_old_hash=proposal.expected_old_hash,
                    expected_new_hash=proposal.expected_new_hash,
                    error_code=FAILED_EVIDENCE_COLLECTION,
                    error_message=f"Evidence collection failed: {exc}",
                    restoration_attempted=restoration_attempted,
                    restoration_succeeded=restoration_succeeded,
                    executed_at=now,
                )

            # ── 14. Unexpected changes → restore ──────────────────────────────
            if unexpected:
                restoration_attempted = True
                restoration_succeeded = False
                try:
                    _atomic_write_bytes(target, original_bytes)
                    restoration_succeeded = True
                except Exception:
                    pass
                final_hash = _file_hash(target)
                return CodingExecutionResult(
                    status="failed",
                    relative_path=rel,
                    before_hash=before_hash,
                    after_hash=final_hash,
                    expected_old_hash=proposal.expected_old_hash,
                    expected_new_hash=proposal.expected_new_hash,
                    bytes_written=len(
                        proposal.new_content.encode("utf-8")
                    ),
                    changed_files=changed,
                    unexpected_changes=unexpected,
                    error_code=FAILED_UNEXPECTED_CHANGES,
                    error_message=(
                        f"Unexpected changes to: {', '.join(unexpected)}"
                    ),
                    restoration_attempted=restoration_attempted,
                    restoration_succeeded=restoration_succeeded,
                    executed_at=now,
                )

            # ── 15. Success ───────────────────────────────────────────────────
            observed_new_bytes = target.read_bytes()
            return CodingExecutionResult(
                status="executed",
                relative_path=rel,
                before_hash=before_hash,
                after_hash=after_hash,
                expected_old_hash=proposal.expected_old_hash,
                expected_new_hash=proposal.expected_new_hash,
                bytes_written=len(proposal.new_content.encode("utf-8")),
                changed_files=changed,
                executed_at=now,
                old_content=current_bytes,
                new_content=observed_new_bytes,
            )

    def cleanup(self) -> None:
        """Remove the temporary runtime workspace.

        Cleanup uses ignore_errors=True.  Cleanup success is not evidence
        of execution success.
        """
        if self._runtime_root is not None and self._runtime_root.exists():
            shutil.rmtree(str(self._runtime_root), ignore_errors=True)
            self._runtime_root = None

    def get_protected_invariant_hashes(self) -> dict[str, str]:
        """Compute byte-hash of every protected fixture file in the workspace.

        Returns a mapping of relative path → SHA-256 hex digest for all
        concretely classified protected fixture files.  Returns empty dict
        if workspace is not active.
        """
        if self._runtime_root is None:
            return {}
        workspace = self._runtime_root / "coding-demo"
        if not workspace.is_dir():
            return {}
        seed = self._load_seed()
        rules = _load_path_rules()
        protected_prefixes = rules.get("tiers", {}).get("protected", {}).get("paths", [])
        result: dict[str, str] = {}
        for rel_path in seed.get("files", {}):
            if any(rel_path.startswith(p) or rel_path == p for p in protected_prefixes):
                fp = workspace / rel_path
                if fp.is_file():
                    result[rel_path] = _file_hash(fp)
        return result

    def __enter__(self) -> "CodingWorkspace":
        self.copy_demo()
        return self

    def __exit__(self, *args: Any) -> None:
        self.cleanup()


# ── Internal helpers ──────────────────────────────────────────────────────────

def _rejection_code(rule: str) -> str:
    """Map a PathSafetyRejection rule to a stable error code."""
    mapping = {
        "absolute_path": REJECTED_ABSOLUTE_PATH,
        "windows_drive_path": REJECTED_WINDOWS_PATH,
        "unc_path": REJECTED_UNC_PATH,
        "dotdot_traversal": REJECTED_TRAVERSAL,
        "null_byte": REJECTED_NULL_BYTE,
        "symlink": REJECTED_SYMLINK,
        "escape": REJECTED_OUTSIDE_ROOT,
    }
    return mapping.get(rule, REJECTED_OUTSIDE_ROOT)


def _atomic_write(target: Path, content: str) -> None:
    """Atomically write UTF-8 content to the target file.

    Uses tempfile.mkstemp in the same directory, flush, fsync, os.replace.
    """
    directory = target.parent
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(directory),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """Atomically write raw bytes to the target file."""
    directory = target.parent
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(directory),
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, str(target))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _file_hash(path: Path) -> str:
    """Compute SHA-256 hex digest of a file's raw bytes."""
    content = path.read_bytes()
    return hashlib.sha256(content).hexdigest()


# ── Public API ────────────────────────────────────────────────────────────────

def coding_executor(
    proposal: CodingProposal,
    *,
    review_authorized: bool = False,
) -> CodingExecutionResult:
    """Execute a bounded coding file-write proposal.

    Creates a temporary runtime workspace, executes the write, and cleans up.
    The production fixture root and seed path are fixed internally.
    """
    workspace = CodingWorkspace()
    try:
        workspace.copy_demo()
        return workspace.execute_file_write(
            proposal,
            review_authorized=review_authorized,
        )
    finally:
        workspace.cleanup()


def reset_lock_registry() -> None:
    """Clear global state. Primarily for testing."""
    pass
