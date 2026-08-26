"""
tests/test_coding_proposal.py
─────────────────────────────
Focused regression tests for the coding proposal contract (Stage 1).

Covers:
  - CodingProposal model validation (hash verification, field constraints)
  - Path safety rules (structural rejection, classification precedence)
  - Canonical fingerprint computation (determinism, field binding)
  - Evaluation pipeline (ALLOW/WARN/BLOCK verdicts)
  - Fixture integrity (seed hashes match tracked files)
  - Integration with existing OperationORM and rules_engine
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.models.coding_proposal import (
    CodingProposal,
    PathSafetyRejection,
    ProposalValidationError,
    build_coding_canonical_json,
    classify_path,
    compute_proposal_fingerprint,
    validate_coding_path,
    _load_path_rules,
)
from app.models.event import EventCreate
from app.policy.coding_proposal_engine import evaluate_coding_proposal
from app.policy.rules_engine import evaluate_event
from app.models.operation import (
    build_canonical_action,
    compute_fingerprint_from_event,
)

# ── Paths ──────────────────────────────────────────────────────────────────────

_DEMO_ROOT = Path(__file__).resolve().parent.parent.parent / "coding-demo"
_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "coding_demo_seed.json"
_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "coding_path_rules.json"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _make_proposal(**overrides: Any) -> CodingProposal:
    """Build a valid coding proposal with sensible defaults."""
    content = overrides.pop("new_content", "def get_status():\n    return {'ok': True}\n")
    defaults = {
        "action_type": "file_write",
        "relative_path": "src/status.py",
        "expected_old_hash": "a" * 64,
        "new_content": content,
        "expected_new_hash": _sha256(content),
        "test_profile": "unit",
        "protected_invariants": [],
    }
    defaults.update(overrides)
    return CodingProposal(**defaults)


def _make_event(**overrides: Any) -> EventCreate:
    """Build a cursor coding_proposal event."""
    proposal = overrides.pop("proposal", None)
    if proposal is None:
        proposal = _make_proposal()
    defaults = {
        "source": "cursor",
        "event_type": "coding_proposal",
        "payload": proposal.model_dump(),
        "original_goal": "Test coding proposal",
    }
    defaults.update(overrides)
    return EventCreate(**defaults)


def _copy_demo_to_tmp(tmp_path: Path) -> Path:
    """Copy the coding-demo fixture to a temporary directory for testing."""
    dest = tmp_path / "coding-demo"
    shutil.copytree(str(_DEMO_ROOT), str(dest))
    return dest


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestContractValidation:
    """CodingProposal model validation tests."""

    def test_valid_proposal_accepted(self):
        proposal = _make_proposal()
        assert proposal.action_type == "file_write"
        assert proposal.verify_new_hash()

    def test_expected_new_hash_mismatch_rejected(self):
        content = "hello world"
        with pytest.raises(ValidationError, match="expected_new_hash"):
            _make_proposal(
                new_content=content,
                expected_new_hash="b" * 64,  # wrong hash
            )

    def test_malformed_old_hash_rejected(self):
        with pytest.raises(ValidationError, match="expected_old_hash"):
            _make_proposal(expected_old_hash="not-a-hash")

    def test_malformed_new_hash_rejected(self):
        content = "test"
        with pytest.raises(ValidationError, match="expected_new_hash"):
            _make_proposal(
                new_content=content,
                expected_new_hash="short",
            )

    def test_uppercase_new_hash_rejected(self):
        content = "test"
        with pytest.raises(ValidationError, match="expected_new_hash"):
            _make_proposal(
                new_content=content,
                expected_new_hash=_sha256(content).upper(),
            )

    def test_protected_invariants_affect_fingerprint(self):
        p1 = _make_proposal(protected_invariants=[])
        p2 = _make_proposal(protected_invariants=["no_secrets"])
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "agent-1", p1)
        fp2 = compute_proposal_fingerprint("cursor", "coding_proposal", "agent-1", p2)
        assert fp1 != fp2

    def test_new_content_changes_fingerprint_through_hash(self):
        p1 = _make_proposal(new_content="content_a")
        p2 = _make_proposal(new_content="content_b")
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "agent-1", p1)
        fp2 = compute_proposal_fingerprint("cursor", "coding_proposal", "agent-1", p2)
        assert fp1 != fp2

    def test_deterministic_equivalent_input_same_fingerprint(self):
        p1 = _make_proposal()
        p2 = _make_proposal()
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "agent-1", p1)
        fp2 = compute_proposal_fingerprint("cursor", "coding_proposal", "agent-1", p2)
        assert fp1 == fp2

    def test_null_byte_in_content_rejected(self):
        with pytest.raises(ValidationError, match="null bytes"):
            _make_proposal(new_content="hello\x00world")

    def test_empty_relative_path_rejected(self):
        with pytest.raises(ValidationError, match="relative_path"):
            _make_proposal(relative_path="")

    def test_content_size_limit_enforced(self):
        """Content size limit is enforced by the evaluation engine, not the model."""
        rules = _load_path_rules()
        max_size = rules.get("max_content_size_chars", 50_000)
        # Verify the rule exists
        assert max_size > 0
        # Verify the engine catches oversized content
        large_content = "x" * (max_size + 1)
        proposal = _make_proposal(new_content=large_content)
        event = _make_event(proposal=proposal)
        decision = evaluate_coding_proposal(event, proposal)
        assert decision.verdict == "WARN"
        assert any("content size" in r.lower() for r in decision.reasons)


# ═══════════════════════════════════════════════════════════════════════════════
# PATH SAFETY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestPathSafety:
    """Path validation and classification tests."""

    def test_unix_absolute_path_rejected(self, tmp_path):
        with pytest.raises(PathSafetyRejection, match="Absolute Unix path"):
            validate_coding_path("/etc/passwd", tmp_path)

    def test_windows_drive_path_rejected(self, tmp_path):
        with pytest.raises(PathSafetyRejection, match="Windows drive path"):
            validate_coding_path("C:\\Windows\\System32", tmp_path)

    def test_unc_path_rejected(self, tmp_path):
        with pytest.raises(PathSafetyRejection, match="UNC path"):
            validate_coding_path("\\\\server\\share\\file", tmp_path)

    def test_dotdot_traversal_rejected(self, tmp_path):
        with pytest.raises(PathSafetyRejection, match="Dot-dot traversal"):
            validate_coding_path("../../etc/passwd", tmp_path)

    def test_mixed_separator_traversal_rejected(self, tmp_path):
        with pytest.raises(PathSafetyRejection, match="Dot-dot traversal"):
            validate_coding_path("src\\..\\..\\etc\\passwd", tmp_path)

    def test_empty_path_rejected(self, tmp_path):
        with pytest.raises(PathSafetyRejection, match="empty"):
            validate_coding_path("", tmp_path)

    def test_null_byte_rejected(self, tmp_path):
        with pytest.raises(PathSafetyRejection, match="null bytes"):
            validate_coding_path("src\x00status.py", tmp_path)

    @pytest.mark.skipif(
        os.name != "nt" and not hasattr(os, "symlink"),
        reason="Symlink test requires OS support",
    )
    def test_symlink_escape_rejected(self, tmp_path):
        demo = _copy_demo_to_tmp(tmp_path)
        # Create a symlink pointing outside the repo
        link_path = demo / "escape_link"
        target = tmp_path / "outside.txt"
        target.write_text("escape")
        try:
            link_path.symlink_to(target)
            with pytest.raises(PathSafetyRejection, match="escape"):
                validate_coding_path("escape_link", demo)
        except OSError:
            pytest.skip("Symlinks not supported on this filesystem")

    def test_src_status_py_classified_allow(self, tmp_path):
        demo = _copy_demo_to_tmp(tmp_path)
        tier = validate_coding_path("src/status.py", demo)
        assert tier == "allowed"

    def test_config_app_json_classified_warn(self, tmp_path):
        demo = _copy_demo_to_tmp(tmp_path)
        tier = validate_coding_path("config/app.json", demo)
        assert tier == "sensitive"

    def test_tests_test_status_classified_warn(self, tmp_path):
        demo = _copy_demo_to_tmp(tmp_path)
        tier = validate_coding_path("tests/test_status.py", demo)
        assert tier == "sensitive"

    def test_protected_secrets_env_classified_block(self, tmp_path):
        demo = _copy_demo_to_tmp(tmp_path)
        tier = validate_coding_path("protected/secrets.env", demo)
        assert tier == "protected"

    def test_unmatched_path_defaults_to_warn(self, tmp_path):
        demo = _copy_demo_to_tmp(tmp_path)
        # random_file.txt doesn't match any allowed pattern
        tier = validate_coding_path("random_file.txt", demo)
        assert tier == "sensitive"

    def test_protected_precedence_wins_over_extension(self):
        """A .py file inside protected/ should be classified as protected, not allowed."""
        rules = _load_path_rules()
        tier = classify_path("protected/my_script.py", rules)
        assert tier == "protected"

    def test_env_file_in_src_protected(self):
        """A .env file even inside src/ should be classified as protected."""
        rules = _load_path_rules()
        tier = classify_path("src/.env", rules)
        assert tier == "protected"

    def test_secret_pattern_in_any_directory_protected(self):
        """A file matching *secret* in any directory should be classified as protected."""
        rules = _load_path_rules()
        tier = classify_path("src/my_secret_config.py", rules)
        assert tier == "protected"


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURE INTEGRITY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestFixtureIntegrity:
    """Verify seed manifest matches tracked fixture files."""

    def test_seed_hashes_match_actual_fixture_content(self):
        seed = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        for rel_path, info in seed["files"].items():
            actual_file = _DEMO_ROOT / rel_path
            assert actual_file.exists(), f"Fixture file missing: {rel_path}"
            actual_content = actual_file.read_text(encoding="utf-8")
            actual_hash = hashlib.sha256(actual_content.encode("utf-8")).hexdigest()
            assert actual_hash == info["hash"], (
                f"Hash mismatch for {rel_path}: "
                f"seed={info['hash'][:16]}... actual={actual_hash[:16]}..."
            )

    def test_seed_content_matches_actual_fixture(self):
        seed = json.loads(_SEED_PATH.read_text(encoding="utf-8"))
        for rel_path, info in seed["files"].items():
            actual_file = _DEMO_ROOT / rel_path
            actual_content = actual_file.read_text(encoding="utf-8")
            assert actual_content == info["content"], (
                f"Content mismatch for {rel_path}"
            )

    def test_demo_fixture_files_exist(self):
        expected_files = [
            "src/status.py",
            "tests/test_status.py",
            "config/app.json",
            "protected/secrets.env",
            "README.md",
        ]
        for rel_path in expected_files:
            assert (_DEMO_ROOT / rel_path).exists(), f"Missing: {rel_path}"

    def test_tests_use_temporary_copies(self, tmp_path):
        """Verify that test operations work on copies, not the tracked fixture."""
        demo_copy = _copy_demo_to_tmp(tmp_path)
        # Modify the copy
        status_file = demo_copy / "src" / "status.py"
        status_file.write_text("# modified\n", encoding="utf-8")
        # Original must be unchanged
        original = _DEMO_ROOT / "src" / "status.py"
        original_content = original.read_text(encoding="utf-8")
        assert "modified" not in original_content


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL FINGERPRINT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCanonicalFingerprint:
    """Fingerprint computation and binding tests."""

    def test_fingerprint_changes_with_path(self):
        p1 = _make_proposal(relative_path="src/a.py")
        p2 = _make_proposal(relative_path="src/b.py")
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p1)
        fp2 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p2)
        assert fp1 != fp2

    def test_fingerprint_changes_with_content_hash(self):
        p1 = _make_proposal(new_content="aaa")
        p2 = _make_proposal(new_content="bbb")
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p1)
        fp2 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p2)
        assert fp1 != fp2

    def test_fingerprint_changes_with_test_profile(self):
        p1 = _make_proposal(test_profile="unit")
        p2 = _make_proposal(test_profile="none")
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p1)
        fp2 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p2)
        assert fp1 != fp2

    def test_fingerprint_changes_with_invariants(self):
        p1 = _make_proposal(protected_invariants=["a"])
        p2 = _make_proposal(protected_invariants=["b"])
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p1)
        fp2 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p2)
        assert fp1 != fp2

    def test_fingerprint_changes_with_source(self):
        p = _make_proposal()
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "a", p)
        fp2 = compute_proposal_fingerprint("n8n", "coding_proposal", "a", p)
        assert fp1 != fp2

    def test_fingerprint_changes_with_agent_identity(self):
        p = _make_proposal()
        fp1 = compute_proposal_fingerprint("cursor", "coding_proposal", "agent-1", p)
        fp2 = compute_proposal_fingerprint("cursor", "coding_proposal", "agent-2", p)
        assert fp1 != fp2

    def test_canonical_json_deterministic(self):
        p = _make_proposal()
        j1 = build_coding_canonical_json("cursor", "coding_proposal", "a", p)
        j2 = build_coding_canonical_json("cursor", "coding_proposal", "a", p)
        assert j1 == j2


# ═══════════════════════════════════════════════════════════════════════════════
# EVALUATION PIPELINE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvaluationPipeline:
    """Coding proposal evaluation through the policy engine."""

    def test_safe_proposal_produces_allow(self):
        proposal = _make_proposal(relative_path="src/status.py")
        event = _make_event(proposal=proposal)
        # Test the coding proposal engine directly (full pipeline may have
        # other modules producing WARN for unrelated reasons)
        decision = evaluate_coding_proposal(event, proposal)
        assert decision.verdict == "ALLOW"

    def test_sensitive_proposal_produces_warn(self):
        proposal = _make_proposal(relative_path="config/app.json")
        event = _make_event(proposal=proposal)
        decision = evaluate_coding_proposal(event, proposal)
        assert decision.verdict == "WARN"

    def test_protected_proposal_produces_block(self):
        proposal = _make_proposal(relative_path="protected/secrets.env")
        event = _make_event(proposal=proposal)
        decision = evaluate_coding_proposal(event, proposal)
        assert decision.verdict == "BLOCK"

    def test_hash_mismatch_produces_block(self):
        proposal = _make_proposal(relative_path="src/status.py")
        # Tamper the hash after construction
        proposal_copy = proposal.model_copy()
        object.__setattr__(proposal_copy, "expected_new_hash", "b" * 64)
        event = _make_event(proposal=proposal_copy)
        decision = evaluate_coding_proposal(event, proposal_copy)
        assert decision.verdict == "BLOCK"
        assert "expected_new_hash" in decision.reasons[0].lower()

    def test_invalid_proposal_fails_safely_in_full_pipeline(self):
        event = EventCreate(
            source="cursor",
            event_type="coding_proposal",
            payload={"invalid": "data"},
            original_goal="Test",
        )
        decision = evaluate_event(event)
        # Should produce BLOCK due to invalid proposal
        assert decision.verdict == "BLOCK"

    def test_full_pipeline_includes_coding_proposal_module(self):
        """Verify coding_proposal_engine contributes to the full pipeline verdict."""
        proposal = _make_proposal(relative_path="protected/secrets.env")
        event = _make_event(proposal=proposal)
        decision = evaluate_event(event)
        assert decision.verdict == "BLOCK"
        assert "coding_proposal_engine" in decision.module

    def test_full_pipeline_coding_proposal_allow_includes_module(self):
        """A safe coding proposal through the full pipeline."""
        proposal = _make_proposal(relative_path="src/status.py")
        event = _make_event(proposal=proposal)
        decision = evaluate_event(event)
        # The coding_proposal_engine should contribute ALLOW
        # Other modules may adjust the verdict, but the module should be present
        assert "coding_proposal_engine" in decision.module

    def test_non_cursor_sources_unaffected(self):
        event = EventCreate(
            source="transaction",
            event_type="purchase",
            payload={"merchant_id": "coffee-shop-1", "amount": 5.0},
            original_goal="Buy coffee",
        )
        decision = evaluate_event(event)
        # Non-cursor sources should not trigger coding_proposal_engine
        assert "coding_proposal_engine" not in decision.module

    def test_operation_record_created_through_operation_orm(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from app.database import Base
        from app.models.event import EventORM
        from app.models.operation import OperationORM, get_or_create_operation

        engine = create_engine(
            f"sqlite:///{tmp_path / 'test_op.db'}",
            connect_args={"check_same_thread": False},
        )
        sf = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        Base.metadata.create_all(bind=engine)

        db = sf()
        try:
            ev = EventORM(
                source="cursor",
                event_type="coding_proposal",
                payload=json.dumps(_make_proposal().model_dump()),
                original_goal="Test",
            )
            db.add(ev)
            db.commit()
            db.refresh(ev)

            event_create = _make_event()
            operation, is_new = get_or_create_operation(
                db=db,
                event=event_create,
                event_id=ev.id,
                agent_identity="cursor-agent-1",
            )
            assert is_new
            assert operation.source == "cursor"
            assert operation.action_fingerprint
            assert len(operation.action_fingerprint) == 64
        finally:
            db.close()

    def test_build_canonical_action_for_coding_proposal(self):
        proposal = _make_proposal(relative_path="src/status.py")
        event = _make_event(proposal=proposal)
        canonical = build_canonical_action(event, "cursor-agent-1")
        assert canonical.source == "cursor"
        assert canonical.action_type == "coding_proposal"
        assert canonical.target == "src/status.py"
        assert canonical.normalized_parameters.get("action_type") == "file_write"
        assert canonical.normalized_parameters.get("relative_path") == "src/status.py"

    def test_build_canonical_action_for_non_coding_unchanged(self):
        event = EventCreate(
            source="cursor",
            event_type="plan_execution",
            payload={"target": "src/a.py", "steps": [{"type": "file_write"}]},
            original_goal="Test",
        )
        canonical = build_canonical_action(event)
        assert canonical.action_type == "plan_execution"
        assert canonical.target == "src/a.py"
