"""
tests/test_coding_executor.py
─────────────────────────────
Focused regression tests for the contained coding file-write executor (Stage 2).

Covers:
  - Allowed file write succeeds
  - Tracked fixture remains unchanged
  - Runtime copy receives expected content
  - Before/after hash correctness
  - Old-hash mismatch rejection
  - Protected/sensitive target rejection
  - Structural path rejection (Unix, Windows, UNC, traversal, null byte, symlink)
  - Missing target rejection
  - Content size enforcement
  - Atomic replacement (no temp files)
  - Simulated write/after-hash failure restoration
  - Unexpected secondary-file change detection
  - Production constructor cannot accept custom runtime root
  - Seed verification failure rejection
  - Concurrency serialization
  - Independent workspace instances
  - No subprocess, network, or shell execution mechanisms
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from app.models.coding_proposal import CodingProposal, PathSafetyRejection
from app.sandbox.coding_executor import (
    CodingExecutionResult,
    CodingWorkspace,
    coding_executor,
    _FIXTURE_ROOT,
    _SEED_PATH,
    _file_hash,
)
from app.sandbox import coding_executor as executor_module


# ── Paths ──────────────────────────────────────────────────────────────────────

_DEMO_ROOT = Path(__file__).resolve().parent.parent.parent / "coding-demo"
_SEED = Path(__file__).resolve().parent.parent / "data" / "coding_demo_seed.json"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_proposal(**overrides: Any) -> CodingProposal:
    """Build a valid coding proposal targeting src/status.py."""
    content = overrides.pop(
        "new_content", "def get_status():\n    return {'ok': True}\n"
    )
    defaults = {
        "action_type": "file_write",
        "relative_path": "src/status.py",
        "expected_old_hash": _sha256(
            (_DEMO_ROOT / "src" / "status.py").read_text(encoding="utf-8")
        ),
        "new_content": content,
        "expected_new_hash": _sha256(content),
        "test_profile": "unit",
        "protected_invariants": [],
    }
    defaults.update(overrides)
    return CodingProposal(**defaults)


def _make_workspace(tmp_path: Path) -> CodingWorkspace:
    """Create a CodingWorkspace using a test-only fixture root."""
    return CodingWorkspace(
        fixture_root=_DEMO_ROOT,
        seed_path=_SEED,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Allowed file write succeeds
# ═══════════════════════════════════════════════════════════════════════════════

class TestAllowedWrite:
    def test_allowed_file_write_succeeds(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            result = ws.execute_file_write(proposal)
            assert result.status == "executed"
            assert result.error_code == ""
        finally:
            ws.cleanup()

    def test_executed_at_is_populated(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            result = ws.execute_file_write(_make_proposal())
            assert result.executed_at
            assert "T" in result.executed_at
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Tracked fixture remains unchanged
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixtureUnchanged:
    def test_tracked_fixture_remains_unchanged(self):
        seed = _load_seed()
        originals = {
            rel: (_DEMO_ROOT / rel).read_bytes()
            for rel in seed["files"]
        }
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            ws.execute_file_write(_make_proposal())
        finally:
            ws.cleanup()
        for rel, orig in originals.items():
            assert (_DEMO_ROOT / rel).read_bytes() == orig, (
                f"Tracked fixture modified: {rel}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Runtime copy receives expected content
# ═══════════════════════════════════════════════════════════════════════════════

class TestRuntimeCopyContent:
    def test_runtime_copy_receives_expected_content(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            ws.execute_file_write(proposal)
            runtime_status = ws.runtime_root / "coding-demo" / "src" / "status.py"
            assert runtime_status.read_text(encoding="utf-8") == proposal.new_content
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Before hash is correct
# ═══════════════════════════════════════════════════════════════════════════════

class TestBeforeHash:
    def test_before_hash_matches_seed(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            result = ws.execute_file_write(proposal)
            seed = _load_seed()
            assert result.before_hash == seed["files"]["src/status.py"]["hash"]
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 5. After hash equals expected_new_hash
# ═══════════════════════════════════════════════════════════════════════════════

class TestAfterHash:
    def test_after_hash_equals_expected_new_hash(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            result = ws.execute_file_write(proposal)
            assert result.after_hash == proposal.expected_new_hash
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Old-hash mismatch rejects before writing
# ═══════════════════════════════════════════════════════════════════════════════

class TestOldHashMismatch:
    def test_old_hash_mismatch_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(expected_old_hash="a" * 64)
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_OLD_HASH_MISMATCH"
            runtime_status = ws.runtime_root / "coding-demo" / "src" / "status.py"
            seed = _load_seed()
            assert _file_hash(runtime_status) == seed["files"]["src/status.py"]["hash"]
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Protected target rejects at execution time
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectedTarget:
    def test_protected_target_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(
                relative_path="protected/secrets.env",
                new_content="EVIL",
                expected_old_hash=_sha256("EVIL"),
            )
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_PROTECTED"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Sensitive target rejects without trusted review authorization
# ═══════════════════════════════════════════════════════════════════════════════

class TestSensitiveTarget:
    def test_sensitive_target_rejects_without_auth(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(
                relative_path="config/app.json",
                new_content='{"x": 1}',
                expected_old_hash=_sha256('{"x": 1}'),
            )
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_REVIEW_REQUIRED"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Sensitive target accepted with trusted review authorization
# ═══════════════════════════════════════════════════════════════════════════════

class TestSensitiveTargetAuthorized:
    def test_sensitive_target_accepted_with_auth(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            new_content = '{"app_name": "coding-demo", "debug": true}'
            seed = _load_seed()
            proposal = _make_proposal(
                relative_path="config/app.json",
                new_content=new_content,
                expected_old_hash=seed["files"]["config/app.json"]["hash"],
            )
            result = ws.execute_file_write(
                proposal, review_authorized=True
            )
            assert result.status == "executed"
            runtime_config = ws.runtime_root / "coding-demo" / "config" / "app.json"
            assert runtime_config.read_text(encoding="utf-8") == new_content
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Unix absolute path rejects
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnixAbsoluteReject:
    def test_unix_absolute_path_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(relative_path="/etc/passwd")
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_ABSOLUTE_PATH"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Windows drive path rejects
# ═══════════════════════════════════════════════════════════════════════════════

class TestWindowsDriveReject:
    def test_windows_drive_path_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(relative_path="C:\\Windows\\System32\\file")
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_WINDOWS_PATH"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 12. UNC path rejects
# ═══════════════════════════════════════════════════════════════════════════════

class TestUNCReject:
    def test_unc_path_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(relative_path="\\\\server\\share\\file")
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_UNC_PATH"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 13. Traversal rejects
# ═══════════════════════════════════════════════════════════════════════════════

class TestTraversalReject:
    def test_traversal_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(relative_path="../../etc/passwd")
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_TRAVERSAL"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 14. Mixed-separator traversal rejects
# ═══════════════════════════════════════════════════════════════════════════════

class TestMixedTraversalReject:
    def test_mixed_separator_traversal_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(relative_path="src\\..\\..\\etc\\passwd")
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_TRAVERSAL"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 15. Null byte rejects
# ═══════════════════════════════════════════════════════════════════════════════

class TestNullByteReject:
    def test_null_byte_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(relative_path="src\x00status.py")
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_NULL_BYTE"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 16. Symlink target rejects where supported
# ═══════════════════════════════════════════════════════════════════════════════

class TestSymlinkReject:
    @pytest.mark.skipif(
        os.name != "nt" and not hasattr(os, "symlink"),
        reason="Symlink test requires OS support",
    )
    def test_symlink_target_rejects(self, tmp_path):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            workspace = ws.runtime_root / "coding-demo"
            link_path = workspace / "escape_link"
            target = tmp_path / "outside.txt"
            target.write_text("escape", encoding="utf-8")
            try:
                link_path.symlink_to(target)
                proposal = _make_proposal(relative_path="escape_link")
                result = ws.execute_file_write(proposal)
                assert result.status == "rejected"
                assert result.error_code == "REJECTED_SYMLINK"
            except OSError:
                pytest.skip("Symlinks not supported on this filesystem")
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 17. Missing target rejects
# ═══════════════════════════════════════════════════════════════════════════════

class TestMissingTargetReject:
    def test_missing_target_rejects(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal(
                relative_path="src/nonexistent.py",
                new_content="x = 1",
                expected_old_hash="a" * 64,
            )
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_FILE_NOT_FOUND"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 18. Size limit is enforced again
# ═══════════════════════════════════════════════════════════════════════════════

class TestSizeLimit:
    def test_size_limit_enforced(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            large_content = "x" * 50_001
            proposal = _make_proposal(new_content=large_content)
            result = ws.execute_file_write(proposal)
            assert result.status == "rejected"
            assert result.error_code == "REJECTED_CONTENT_SIZE"
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 19. Atomic replacement leaves no temporary file
# ═══════════════════════════════════════════════════════════════════════════════

class TestAtomicReplacement:
    def test_no_temp_files_after_write(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            ws.execute_file_write(_make_proposal())
            src_dir = ws.runtime_root / "coding-demo" / "src"
            temp_files = [
                f for f in src_dir.iterdir()
                if f.suffix == ".tmp"
            ]
            assert temp_files == []
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 20. Simulated write failure preserves original content
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteFailureRestores:
    def test_simulated_write_failure_preserves_content(self, monkeypatch):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            seed = _load_seed()
            original_hash = seed["files"]["src/status.py"]["hash"]

            def _fail_write(target: Path, content: str) -> None:
                raise OSError("Simulated disk failure")

            monkeypatch.setattr(executor_module, "_atomic_write", _fail_write)
            result = ws.execute_file_write(proposal)
            assert result.status == "failed"
            assert result.error_code == "FAILED_WRITE"
            assert result.restoration_attempted
            runtime_status = ws.runtime_root / "coding-demo" / "src" / "status.py"
            assert _file_hash(runtime_status) == original_hash
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 21. Simulated after-hash failure restores original content
# ═══════════════════════════════════════════════════════════════════════════════

class TestAfterHashFailureRestores:
    def test_after_hash_failure_restores_content(self, monkeypatch):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            seed = _load_seed()
            original_hash = seed["files"]["src/status.py"]["hash"]
            call_count = 0
            original_atomic = executor_module._atomic_write

            def _tamper_write(target: Path, content: str) -> None:
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    original_atomic(target, "TAMPERED")
                else:
                    original_atomic(target, content)

            monkeypatch.setattr(executor_module, "_atomic_write", _tamper_write)
            result = ws.execute_file_write(proposal)
            assert result.status == "failed"
            assert result.restoration_attempted
            runtime_status = ws.runtime_root / "coding-demo" / "src" / "status.py"
            assert _file_hash(runtime_status) == original_hash
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 22. Unexpected secondary-file change is detected
# ═══════════════════════════════════════════════════════════════════════════════

class TestUnexpectedChanges:
    def test_unexpected_secondary_change_detected(self, monkeypatch):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            original_atomic = executor_module._atomic_write
            workspace = ws.runtime_root / "coding-demo"

            def _tamper_secondary(target: Path, content: str) -> None:
                original_atomic(target, content)
                tests_file = workspace / "tests" / "test_status.py"
                tests_file.write_text("TAMPERED", encoding="utf-8")

            monkeypatch.setattr(
                executor_module, "_atomic_write", _tamper_secondary
            )
            result = ws.execute_file_write(proposal)
            assert result.status == "failed"
            assert result.error_code == "FAILED_UNEXPECTED_CHANGES"
            assert "tests/test_status.py" in result.unexpected_changes
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 23. Tracked fixture remains unchanged after every failure
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixtureUnchangedAfterFailure:
    def test_fixture_unchanged_after_write_failure(self, monkeypatch):
        seed = _load_seed()
        originals = {
            rel: (_DEMO_ROOT / rel).read_bytes()
            for rel in seed["files"]
        }
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()

            def _fail(target: Path, content: str) -> None:
                raise OSError("fail")

            monkeypatch.setattr(executor_module, "_atomic_write", _fail)
            ws.execute_file_write(proposal)
        finally:
            ws.cleanup()
        for rel, orig in originals.items():
            assert (_DEMO_ROOT / rel).read_bytes() == orig

    def test_fixture_unchanged_after_after_hash_failure(self, monkeypatch):
        seed = _load_seed()
        originals = {
            rel: (_DEMO_ROOT / rel).read_bytes()
            for rel in seed["files"]
        }
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            original_atomic = executor_module._atomic_write

            def _tamper(target: Path, content: str) -> None:
                original_atomic(target, "WRONG")

            monkeypatch.setattr(executor_module, "_atomic_write", _tamper)
            ws.execute_file_write(proposal)
        finally:
            ws.cleanup()
        for rel, orig in originals.items():
            assert (_DEMO_ROOT / rel).read_bytes() == orig

    def test_fixture_unchanged_after_unexpected_change(self, monkeypatch):
        seed = _load_seed()
        originals = {
            rel: (_DEMO_ROOT / rel).read_bytes()
            for rel in seed["files"]
        }
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal = _make_proposal()
            original_atomic = executor_module._atomic_write
            workspace = ws.runtime_root / "coding-demo"

            def _tamper(target: Path, content: str) -> None:
                original_atomic(target, content)
                (workspace / "tests" / "test_status.py").write_text(
                    "X", encoding="utf-8"
                )

            monkeypatch.setattr(executor_module, "_atomic_write", _tamper)
            ws.execute_file_write(proposal)
        finally:
            ws.cleanup()
        for rel, orig in originals.items():
            assert (_DEMO_ROOT / rel).read_bytes() == orig


# ═══════════════════════════════════════════════════════════════════════════════
# 24. Production constructor cannot accept custom runtime root
# ═══════════════════════════════════════════════════════════════════════════════

class TestProductionConstructor:
    def test_production_constructor_no_custom_runtime_root(self):
        ws = CodingWorkspace()
        assert ws._fixture_root == _FIXTURE_ROOT
        assert ws._seed_path == _SEED


# ═══════════════════════════════════════════════════════════════════════════════
# 25. Copied fixture failing seed verification is rejected
# ═══════════════════════════════════════════════════════════════════════════════

class TestSeedVerification:
    def test_copied_fixture_failing_seed_verification_rejected(self, tmp_path):
        bad_fixture = tmp_path / "bad-demo"
        bad_fixture.mkdir()
        (bad_fixture / "src").mkdir()
        (bad_fixture / "src" / "status.py").write_text(
            "BAD CONTENT", encoding="utf-8"
        )
        ws = CodingWorkspace(
            fixture_root=bad_fixture,
            seed_path=_SEED,
        )
        with pytest.raises(RuntimeError, match="Seed verification failed"):
            ws.copy_demo()


# ═══════════════════════════════════════════════════════════════════════════════
# 26. Two simultaneous writes to the same target are serialized
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrency:
    def test_simultaneous_writes_serialized(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            proposal_a = _make_proposal(
                new_content="def get_status():\n    return {'v': 'a'}\n"
            )
            proposal_b = _make_proposal(
                new_content="def get_status():\n    return {'v': 'b'}\n"
            )
            barrier = threading.Barrier(2)
            results: list[CodingExecutionResult] = []

            def _write(proposal: CodingProposal) -> None:
                barrier.wait(timeout=5)
                results.append(ws.execute_file_write(proposal))

            t1 = threading.Thread(target=_write, args=(proposal_a,))
            t2 = threading.Thread(target=_write, args=(proposal_b,))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)
            assert len(results) == 2
            statuses = sorted(r.status for r in results)
            assert statuses.count("executed") == 1
            assert statuses.count("rejected") == 1
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 27. Only one same-old-hash concurrent write may succeed
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrentSameOldHash:
    def test_only_one_concurrent_write_succeeds(self):
        ws = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws.copy_demo()
        try:
            seed = _load_seed()
            current_hash = seed["files"]["src/status.py"]["hash"]
            proposal_a = _make_proposal(
                new_content="def get_status():\n    return {'v': 'a'}\n"
            )
            proposal_b = _make_proposal(
                new_content="def get_status():\n    return {'v': 'b'}\n"
            )
            barrier = threading.Barrier(2)
            results: list[CodingExecutionResult] = []

            def _write(proposal: CodingProposal) -> None:
                barrier.wait(timeout=5)
                results.append(ws.execute_file_write(proposal))

            t1 = threading.Thread(target=_write, args=(proposal_a,))
            t2 = threading.Thread(target=_write, args=(proposal_b,))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)
            executed = [r for r in results if r.status == "executed"]
            assert len(executed) == 1
            assert executed[0].before_hash == current_hash
        finally:
            ws.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 28. Different workspace instances remain independent
# ═══════════════════════════════════════════════════════════════════════════════

class TestIndependentWorkspaces:
    def test_different_workspaces_independent(self):
        ws1 = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws2 = CodingWorkspace(fixture_root=_DEMO_ROOT, seed_path=_SEED)
        ws1.copy_demo()
        ws2.copy_demo()
        try:
            proposal_a = _make_proposal(
                new_content="def get_status():\n    return {'v': 'a'}\n"
            )
            proposal_b = _make_proposal(
                new_content="def get_status():\n    return {'v': 'b'}\n"
            )
            result1 = ws1.execute_file_write(proposal_a)
            assert result1.status == "executed"
            assert ws1.runtime_root != ws2.runtime_root
            result2 = ws2.execute_file_write(proposal_b)
            assert result2.status == "executed"
            assert result1.after_hash != result2.after_hash
        finally:
            ws1.cleanup()
            ws2.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# 29. No subprocess, network, command, or package-execution mechanism exists
# ═══════════════════════════════════════════════════════════════════════════════

class TestNoExecutionMechanisms:
    def test_no_subprocess_or_network_mechanisms(self):
        import ast
        source = executor_module.__file__
        tree = ast.parse(Path(source).read_text(encoding="utf-8"))
        forbidden = {"subprocess", "os.system", "os.popen", "shlex", "socket",
                      "urllib", "requests", "httpx", "asyncio.create_subprocess"}
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module.split(".")[0])
        violations = imports & forbidden
        assert not violations, (
            f"Forbidden imports found in coding_executor.py: {violations}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _load_seed() -> dict[str, Any]:
    return json.loads(_SEED.read_text(encoding="utf-8"))
