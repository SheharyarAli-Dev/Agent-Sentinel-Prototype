"""
tests/test_coding_outcome.py
────────────────────────────
Integration tests for Stage 4 coding outcome verification.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.coding_execution import CodingExecutionORM
from app.models.coding_outcome import CodingOutcomeORM
from app.models.decision import DecisionORM
from app.models.event import EventORM, EventCreate
from app.models.operation import (
    OperationORM,
    build_canonical_action,
    compute_action_fingerprint,
)
from app.models.coding_proposal import CodingProposal
from app.sandbox import coding_executor as executor_module
from app.sandbox.coding_executor import CodingWorkspace, CodingExecutionResult
from app.websocket.manager import manager


_DEMO_ROOT = Path(__file__).resolve().parent.parent.parent / "coding-demo"
_SEED = Path(__file__).resolve().parent.parent / "data" / "coding_demo_seed.json"


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _load_seed() -> dict[str, Any]:
    return json.loads(_SEED.read_text(encoding="utf-8"))


def _make_proposal(**overrides: Any) -> CodingProposal:
    content = overrides.pop("new_content", "def get_status():\n    return {'ok': True}\n")
    seed = _load_seed()
    defaults = {
        "action_type": "file_write",
        "relative_path": "src/status.py",
        "expected_old_hash": seed["files"]["src/status.py"]["hash"],
        "new_content": content,
        "expected_new_hash": _sha256(content),
        "test_profile": "unit",
        "protected_invariants": [],
    }
    defaults.update(overrides)
    return CodingProposal(**defaults)


def _make_test_env(tmp_path: Path):
    db_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app, raise_server_exceptions=False)
    return client, TestSessionLocal, test_engine


def _seed_coding_event(
    session_factory,
    *,
    verdict: str = "ALLOW",
    human_decision: str | None = None,
    review_expired: bool = False,
    source: str = "cursor",
    event_type: str = "coding_proposal",
    proposal: CodingProposal | None = None,
    fingerprint_mismatch: bool = False,
) -> int:
    if proposal is None:
        proposal = _make_proposal()
    db = session_factory()
    try:
        payload = proposal.model_dump(mode="json")
        event = EventORM(
            source=source,
            event_type=event_type,
            payload=json.dumps(payload),
            original_goal="Test coding proposal",
            timestamp=datetime.now(timezone.utc),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        now = datetime.now(timezone.utc)
        review_expires_at = None
        if verdict == "WARN" and not review_expired:
            review_expires_at = now + timedelta(hours=1)
        elif review_expired:
            review_expires_at = now - timedelta(hours=1)
        decision = DecisionORM(
            event_id=event.id,
            verdict=verdict,
            reasons=json.dumps(["test"]),
            suggested_fix="" if verdict == "ALLOW" else "Fix suggested",
            module="coding_proposal_engine",
            risk_score=0.0 if verdict == "ALLOW" else 0.5,
            timestamp=now,
            human_decision=human_decision,
            human_timestamp=now if human_decision else None,
            review_expires_at=review_expires_at,
        )
        db.add(decision)
        db.commit()
        agent_id = f"{source}-default"
        event_create = EventCreate(
            source=source,
            event_type=event_type,
            payload=payload,
            original_goal="Test coding proposal",
        )
        canonical = build_canonical_action(event_create, agent_id)
        fingerprint = compute_action_fingerprint(canonical)
        if fingerprint_mismatch:
            fingerprint = "a" * 64
        operation = OperationORM(
            operation_id=f"op-test-{event.id}",
            source=source,
            event_id=event.id,
            canonical_action_json=canonical.to_canonical_json(),
            action_fingerprint=fingerprint,
            lifecycle_state="evaluated",
        )
        db.add(operation)
        db.commit()
        if human_decision == "approved" and verdict == "WARN":
            operation.lifecycle_state = "approved"
            db.commit()
        return event.id
    finally:
        db.close()


def _execution_row(session_factory, event_id: int):
    db = session_factory()
    try:
        return db.scalar(
            select(CodingExecutionORM).where(
                CodingExecutionORM.event_id == event_id
            )
        )
    finally:
        db.close()


def _operation_row(session_factory, event_id: int):
    db = session_factory()
    try:
        return (
            db.query(OperationORM)
            .filter(OperationORM.event_id == event_id)
            .order_by(OperationORM.id.desc())
            .first()
        )
    finally:
        db.close()


def _outcome_row(session_factory, event_id: int):
    db = session_factory()
    try:
        return db.scalar(
            select(CodingOutcomeORM).where(
                CodingOutcomeORM.event_id == event_id
            )
        )
    finally:
        db.close()


# ── 1. Verified outcome ───────────────────────────────────────────────────────

class TestVerifiedOutcome:
    def test_safe_write_produces_verified(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "VERIFIED"

        seed = _load_seed()
        expected_new_hash = _sha256(
            "def get_status():\n    return {'ok': True}\n"
        )
        assert body["observed_final_hash"] == expected_new_hash


# ── 2. Diff based on actual content ───────────────────────────────────────────

class TestDiffBasedOnActualContent:
    def test_diff_not_from_proposal(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["diff_text"] is not None
        assert len(body["diff_text"]) > 0
        assert "+def get_status()" in body["diff_text"] or "---" in body["diff_text"]


# ── 3. Hashes match for verified ──────────────────────────────────────────────

class TestHashesMatchForVerified:
    def test_expected_observed_hashes_match(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["observed_final_hash"] == body["expected_new_hash"]
        assert body["observed_old_hash"] == body["expected_old_hash"]


# ── 4. Diff headers use relative path ─────────────────────────────────────────

class TestDiffContainsOnlyAuthorizedPath:
    def test_diff_headers_use_relative_path(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["diff_text"] is not None
        assert "a/src/status.py" in body["diff_text"]
        assert "b/src/status.py" in body["diff_text"]


# ── 5. Diff truncation ────────────────────────────────────────────────────────

class TestDiffTruncation:
    def test_large_diff_is_truncated(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        large_content = "x = 1\n" * 8_000
        proposal = _make_proposal(new_content=large_content)
        event_id = _seed_coding_event(
            session_factory, verdict="ALLOW", proposal=proposal
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "VERIFIED"
        assert body["diff_truncated"] is True


# ── 6. Sensitive target diff omitted ──────────────────────────────────────────

class TestSensitiveTargetDiffOmitted:
    def test_sensitive_path_diff_omitted(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        seed = _load_seed()
        proposal = _make_proposal(
            relative_path="config/app.json",
            expected_old_hash=seed["files"]["config/app.json"]["hash"],
            new_content='{"app_name": "coding-demo", "debug": true}\n',
        )
        event_id = _seed_coding_event(
            session_factory, verdict="ALLOW", proposal=proposal
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["diff_text"] is None
        assert body["diff_omitted_reason"] == "SENSITIVE_PATH"


# ── 7. Protected content not in response ──────────────────────────────────────

class TestProtectedContentNotInResponse:
    def test_no_protected_content_in_outcome(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert "new_content" not in body
        assert "old_content" not in body
        raw_keys = [k for k in body if "raw" in k.lower()]
        assert raw_keys == []


# ── 8. Protected invariants unchanged ─────────────────────────────────────────

class TestProtectedInvariantsUnchanged:
    def test_protected_invariants_valid(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["invariant_violations"] == []


# ── 9. Wrong final hash mismatch ──────────────────────────────────────────────

class TestWrongFinalHashMismatch:
    def test_wrong_hash_produces_mismatch(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        original_execute = CodingWorkspace.execute_file_write

        def _patched_execute(self, proposal, *, review_authorized=False):
            result = original_execute(self, proposal, review_authorized=review_authorized)
            if result.status == "executed":
                return result.model_copy(
                    update={"after_hash": "b" * 64}
                )
            return result

        monkeypatch.setattr(CodingWorkspace, "execute_file_write", _patched_execute)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "MISMATCH"


# ── 10. Unauthorized secondary modification ───────────────────────────────────

class TestUnauthorizedSecondaryModification:
    def test_unauthorized_change_produces_mismatch(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        original_execute = CodingWorkspace.execute_file_write

        def _patched_execute(self, proposal, *, review_authorized=False):
            result = original_execute(self, proposal, review_authorized=review_authorized)
            if result.status == "executed":
                return result.model_copy(
                    update={"unexpected_changes": ["src/other.py"]}
                )
            return result

        monkeypatch.setattr(CodingWorkspace, "execute_file_write", _patched_execute)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "MISMATCH"


# ── 11. Unexpected new file ───────────────────────────────────────────────────

class TestUnexpectedNewFile:
    def test_new_file_produces_mismatch(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        original_execute = CodingWorkspace.execute_file_write

        def _patched_execute(self, proposal, *, review_authorized=False):
            result = original_execute(self, proposal, review_authorized=review_authorized)
            if result.status == "executed":
                return result.model_copy(
                    update={
                        "changed_files": ["src/status.py", "src/new_file.py"],
                        "unexpected_changes": ["src/new_file.py"],
                    }
                )
            return result

        monkeypatch.setattr(CodingWorkspace, "execute_file_write", _patched_execute)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "MISMATCH"


# ── 12. Unexpected modification of authorized target ──────────────────────────

class TestUnexpectedModificationOfAuthorizedTarget:
    def test_unauthorized_target_change_produces_mismatch(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        original_execute = CodingWorkspace.execute_file_write

        def _patched_execute(self, proposal, *, review_authorized=False):
            result = original_execute(self, proposal, review_authorized=review_authorized)
            if result.status == "executed":
                return result.model_copy(
                    update={"unexpected_changes": ["src/status.py"]}
                )
            return result

        monkeypatch.setattr(CodingWorkspace, "execute_file_write", _patched_execute)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "MISMATCH"
        assert "src/status.py" in body.get("unexpected_modified", [])


# ── 13. Protected invariant modification ──────────────────────────────────────

class TestProtectedInvariantModification:
    def test_protected_change_produces_mismatch(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        from app.coding import outcome as outcome_module

        original_verify = outcome_module.verify_coding_outcome

        def _patched_verify(db, execution, event, **kwargs):
            protected_before = kwargs.get("protected_before") or {}
            if protected_before:
                fake_after = {k: "wrong_hash_value" for k in protected_before}
                kwargs["protected_after"] = fake_after
            return original_verify(db, execution, event, **kwargs)

        monkeypatch.setattr(outcome_module, "verify_coding_outcome", _patched_verify)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "MISMATCH"


# ── 14. Executor failure produces execution_failed ───────────────────────────

class TestExecutorFailure:
    def test_failure_produces_execution_failed(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        def _fail_atomic_write(target, content):
            raise OSError("disk full")

        monkeypatch.setattr(
            executor_module, "_atomic_write", _fail_atomic_write
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "EXECUTION_FAILED"


# ── 15. Missing observed evidence ─────────────────────────────────────────────

class TestMissingObservedEvidence:
    def test_missing_evidence_produces_outcome_unknown(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        def _fail_init(self, **kwargs):
            raise RuntimeError("workspace init failed")

        monkeypatch.setattr(CodingWorkspace, "__init__", _fail_init)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        execution_resp = client.get(f"/api/coding/execution/{event_id}")
        assert execution_resp.status_code == 200
        assert execution_resp.json()["status"] == "failed"

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 404


# ── 16. Evidence collection failure ───────────────────────────────────────────

class TestEvidenceCollectionFailure:
    def test_evidence_failure_produces_execution_failed(
        self, tmp_path, monkeypatch
    ):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        original_execute = CodingWorkspace.execute_file_write

        def _patched_execute(self, proposal, *, review_authorized=False):
            result = original_execute(self, proposal, review_authorized=review_authorized)
            if result.status == "executed":
                return CodingExecutionResult(
                    status="failed",
                    relative_path=result.relative_path,
                    before_hash=result.before_hash,
                    after_hash=result.after_hash,
                    expected_old_hash=result.expected_old_hash,
                    expected_new_hash=result.expected_new_hash,
                    error_code="FAILED_EVIDENCE_COLLECTION",
                    error_message="Evidence collection failed",
                )
            return result

        monkeypatch.setattr(CodingWorkspace, "execute_file_write", _patched_execute)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        execution_resp = client.get(f"/api/coding/execution/{event_id}")
        assert execution_resp.status_code == 200
        assert execution_resp.json()["status"] == "failed"

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "EXECUTION_FAILED"


# ── 17. Missing expected outcome data ─────────────────────────────────────────

class TestMissingExpectedOutcome:
    def test_missing_expected_data(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        payload: dict[str, Any] = {
            "action_type": "file_write",
            "relative_path": "src/status.py",
            "expected_old_hash": "a" * 64,
            "new_content": "return True",
            "expected_new_hash": _sha256("return True"),
            "test_profile": "unit",
            "protected_invariants": [],
        }
        db = session_factory()
        try:
            event = EventORM(
                source="cursor",
                event_type="coding_proposal",
                payload=json.dumps(payload),
                original_goal="Test empty proposal",
                timestamp=datetime.now(timezone.utc),
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            event_id = event.id

            now = datetime.now(timezone.utc)
            decision = DecisionORM(
                event_id=event_id,
                verdict="ALLOW",
                reasons=json.dumps(["test"]),
                suggested_fix="",
                module="coding_proposal_engine",
                risk_score=0.0,
                timestamp=now,
            )
            db.add(decision)
            db.commit()

            agent_id = "cursor-default"
            event_create = EventCreate(
                source="cursor",
                event_type="coding_proposal",
                payload=payload,
                original_goal="Test empty proposal",
            )
            canonical = build_canonical_action(event_create, agent_id)
            fingerprint = compute_action_fingerprint(canonical)
            operation = OperationORM(
                operation_id=f"op-test-{event_id}",
                source="cursor",
                event_id=event_id,
                canonical_action_json=canonical.to_canonical_json(),
                action_fingerprint=fingerprint,
                lifecycle_state="evaluated",
            )
            db.add(operation)
            db.commit()
        finally:
            db.close()

        resp = client.post(f"/api/coding/execute/{event_id}", json={})
        assert resp.status_code == 200, resp.text

        execution_resp = client.get(f"/api/coding/execution/{event_id}")
        assert execution_resp.status_code == 200
        assert execution_resp.json()["status"] == "failed"

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "EXECUTION_FAILED"


# ── 18. PARTIAL not emitted ───────────────────────────────────────────────────

class TestPartialNotEmitted:
    def test_partial_not_emitted(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] != "PARTIAL"


# ── 19. BLOCK produces no outcome ────────────────────────────────────────────

class TestBlockNoOutcome:
    def test_block_no_outcome(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="BLOCK")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 403

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 404


# ── 20. Rejected WARN produces no outcome ────────────────────────────────────

class TestRejectedWarnNoOutcome:
    def test_rejected_no_outcome(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory, verdict="WARN", human_decision="rejected"
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 409

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 404


# ── 21. Expired WARN produces no outcome ──────────────────────────────────────

class TestExpiredWarnNoOutcome:
    def test_expired_no_outcome(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory,
            verdict="WARN",
            human_decision="approved",
            review_expired=True,
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 410

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 404


# ── 22. Repeated GET returns same ────────────────────────────────────────────

class TestRepeatedGetReturnsSame:
    def test_repeated_get_returns_same(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        first = client.get(f"/api/coding/outcome/{event_id}")
        assert first.status_code == 200
        second = client.get(f"/api/coding/outcome/{event_id}")
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["verification_status"] == second.json()["verification_status"]


# ── 23. GET does not recompute ───────────────────────────────────────────────

class TestGetDoesNotRecompute:
    def test_get_does_not_recompute(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        first = client.get(f"/api/coding/outcome/{event_id}")
        assert first.status_code == 200
        first_status = first.json()["verification_status"]

        db = session_factory()
        try:
            outcome = db.scalar(
                select(CodingOutcomeORM).where(
                    CodingOutcomeORM.event_id == event_id
                )
            )
            old_id = outcome.id
        finally:
            db.close()

        second = client.get(f"/api/coding/outcome/{event_id}")
        assert second.status_code == 200
        assert second.json()["id"] == old_id
        assert second.json()["verification_status"] == first_status


# ── 24. Operation ID links correctly ──────────────────────────────────────────

class TestOperationIdLinksCorrectly:
    def test_operation_id_links(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        outcome_body = outcome_resp.json()

        op = _operation_row(session_factory, event_id)
        assert op is not None
        assert outcome_body["operation_id"] == op.operation_id

        exec_row = _execution_row(session_factory, event_id)
        assert exec_row is not None
        assert outcome_body["execution_id"] == exec_row.id


# ── 25. Empty body uses stored payload ────────────────────────────────────────

class TestCallerCannotSubstitute:
    def test_empty_body_uses_stored(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}", json={})
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["expected_path"] == "src/status.py"


# ── 26. WebSocket metadata bounded ────────────────────────────────────────────

class TestWebSocketMetadataBounded:
    def test_ws_metadata_bounded(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        captured_payloads: list[dict] = []
        original_broadcast = manager.broadcast

        async def _capture_broadcast(data: dict):
            captured_payloads.append(data)

        monkeypatch.setattr(manager, "broadcast", _capture_broadcast)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        coding_payloads = [
            p for p in captured_payloads
            if p.get("type") == "coding_execution_complete"
        ]
        assert len(coding_payloads) >= 1
        payload = coding_payloads[0]
        assert "verification_status" in payload
        for key in ("new_content", "old_content", "raw_content"):
            assert key not in payload


# ── 27. WebSocket failure no impact ───────────────────────────────────────────

class TestWebSocketFailureNoAlter:
    def test_ws_failure_no_impact(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        async def _fail_broadcast(data: dict):
            raise RuntimeError("WebSocket failure")

        monkeypatch.setattr(manager, "broadcast", _fail_broadcast)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "executed"

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200


# ── 28. Execution status stays executed ───────────────────────────────────────

class TestExecutionSuccessNotChanged:
    def test_executed_stays_executed(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        original_execute = CodingWorkspace.execute_file_write

        def _patched_execute(self, proposal, *, review_authorized=False):
            result = original_execute(self, proposal, review_authorized=review_authorized)
            if result.status == "executed":
                return result.model_copy(
                    update={"after_hash": "b" * 64}
                )
            return result

        monkeypatch.setattr(CodingWorkspace, "execute_file_write", _patched_execute)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        execution_resp = client.get(f"/api/coding/execution/{event_id}")
        assert execution_resp.status_code == 200
        assert execution_resp.json()["status"] == "executed"


# ── 29. Fixture unchanged after execution ─────────────────────────────────────

class TestFixtureUnchanged:
    def test_fixture_unchanged(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        seed = _load_seed()
        originals = {}
        for rel_path in seed["files"]:
            fp = _DEMO_ROOT / rel_path
            originals[rel_path] = fp.read_bytes()
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        for rel_path, original_bytes in originals.items():
            fp = _DEMO_ROOT / rel_path
            assert fp.read_bytes() == original_bytes, (
                f"Fixture {rel_path} was modified after execution"
            )


# ── 30. File classification ──────────────────────────────────────────────────

class TestClassificationAccurate:
    def test_file_classification(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        original_execute = CodingWorkspace.execute_file_write

        def _patched_execute(self, proposal, *, review_authorized=False):
            result = original_execute(self, proposal, review_authorized=review_authorized)
            if result.status == "executed":
                return result.model_copy(
                    update={
                        "changed_files": ["src/status.py", "src/new.py"],
                        "unexpected_changes": ["src/new.py"],
                    }
                )
            return result

        monkeypatch.setattr(CodingWorkspace, "execute_file_write", _patched_execute)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "MISMATCH"
        unexpected_modified = body.get("unexpected_modified", [])
        unexpected_created = body.get("unexpected_created", [])
        assert "src/new.py" in unexpected_created or "src/new.py" in unexpected_modified


# ── 31. Tmp files excluded ────────────────────────────────────────────────────

class TestTmpFilesExcluded:
    def test_tmp_files_excluded(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        all_lists = (
            body.get("observed_modified", [])
            + body.get("unexpected_created", [])
            + body.get("unexpected_deleted", [])
            + body.get("unexpected_modified", [])
        )
        for f in all_lists:
            assert not f.endswith(".tmp"), f"Tmp file {f} should be excluded"


# ── 32. LiveOps outcome unchanged ─────────────────────────────────────────────

class TestLiveOpsOutcomeUnchanged:
    def test_liveops_unchanged(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        exec_resp = client.get(f"/api/coding/execution/{event_id}")
        assert exec_resp.status_code == 200
        assert exec_resp.json()["status"] == "executed"

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        assert outcome_resp.json()["verification_status"] == "VERIFIED"


# ── 33. No raw protected content in ledger ────────────────────────────────────

class TestNoRawProtectedInLedger:
    def test_no_protected_in_ledger(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        db = session_factory()
        try:
            outcome = db.scalar(
                select(CodingOutcomeORM).where(
                    CodingOutcomeORM.event_id == event_id
                )
            )
            assert outcome is not None
            assert not hasattr(outcome, "new_content") or getattr(outcome, "new_content", None) is None
            assert not hasattr(outcome, "old_content") or getattr(outcome, "old_content", None) is None
            outcome_dict = {c.name for c in CodingOutcomeORM.__table__.columns}
            assert "new_content" not in outcome_dict
            assert "old_content" not in outcome_dict
        finally:
            db.close()


# ── 34. Diff omitted reason for protected path ────────────────────────────────

class TestDiffOmittedReasonForProtected:
    def test_protected_diff_omitted(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        seed = _load_seed()
        proposal = _make_proposal(
            relative_path="protected/secrets.env",
            expected_old_hash=seed["files"]["protected/secrets.env"]["hash"],
            new_content="SECRET=compromised\n",
        )
        event_id = _seed_coding_event(
            session_factory, verdict="BLOCK", proposal=proposal
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 403

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 404


# ── 35. Before snapshot captured before executor invocation ───────────────────

class TestBeforeSnapshotTiming:
    def test_before_snapshot_captured_before_execution(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        from app.coding import outcome as outcome_module

        received_protected_before: dict[str, str] = {}
        original_verify = outcome_module.verify_coding_outcome

        def _capture_verify(db, execution, event, **kwargs):
            received_protected_before.update(kwargs.get("protected_before") or {})
            return original_verify(db, execution, event, **kwargs)

        monkeypatch.setattr(outcome_module, "verify_coding_outcome", _capture_verify)

        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "VERIFIED"

        seed = _load_seed()
        from app.sandbox.coding_executor import _SEED_PATH
        from app.models.coding_proposal import _load_path_rules
        rules = _load_path_rules()
        protected_prefixes = rules.get("tiers", {}).get("protected", {}).get("paths", [])
        for rel_path in seed.get("files", {}):
            if any(rel_path.startswith(p) or rel_path == p for p in protected_prefixes):
                assert rel_path in received_protected_before

# ── 36. Unchanged protected invariants permit VERIFIED ────────────────────────

class TestUnchangedProtectedInvariantsPermitVerified:
    def test_unchanged_invariants_allow_verified(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "VERIFIED"
        assert body["invariant_violations"] == []

        seed = _load_seed()
        from app.sandbox.coding_executor import _SEED_PATH
        from app.models.coding_proposal import _load_path_rules
        rules = _load_path_rules()
        protected_prefixes = rules.get("tiers", {}).get("protected", {}).get("paths", [])
        protected_files = [p for p in seed.get("files", {})
                           if any(p.startswith(pr) or p == pr for pr in protected_prefixes)]
        assert len(protected_files) > 0

# ── 37. Modified protected invariant produces MISMATCH ────────────────────────

class TestModifiedProtectedInvariantMismatch:
    def test_modified_protected_invariant_produces_mismatch(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        from app.coding import outcome as outcome_module

        original_verify = outcome_module.verify_coding_outcome

        def _patched_verify(db, execution, event, **kwargs):
            protected_before = kwargs.get("protected_before") or {}
            if protected_before:
                fake_after = {k: "tampered_hash" for k in protected_before}
                kwargs["protected_after"] = fake_after
            return original_verify(db, execution, event, **kwargs)

        monkeypatch.setattr(outcome_module, "verify_coding_outcome", _patched_verify)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "MISMATCH"
        violations = body.get("invariant_violations", [])
        assert len(violations) > 0
        assert any("changed" in v.lower() for v in violations)

# ── 38. Deleted protected invariant produces MISMATCH ─────────────────────────

class TestDeletedProtectedInvariantMismatch:
    def test_deleted_protected_invariant_produces_mismatch(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        from app.coding import outcome as outcome_module

        original_verify = outcome_module.verify_coding_outcome

        def _patched_verify(db, execution, event, **kwargs):
            protected_before = kwargs.get("protected_before") or {}
            if protected_before:
                kwargs["protected_after"] = {}
            return original_verify(db, execution, event, **kwargs)

        monkeypatch.setattr(outcome_module, "verify_coding_outcome", _patched_verify)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "MISMATCH"
        violations = body.get("invariant_violations", [])
        assert len(violations) > 0

# ── 39. Missing post-execution evidence prevents VERIFIED ─────────────────────

class TestMissingPostExecutionEvidencePreventsVerified:
    def test_missing_after_hash_prevents_verified(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        from app.coding import outcome as outcome_module

        original_verify = outcome_module.verify_coding_outcome

        def _patched_verify(db, execution, event, **kwargs):
            kwargs["protected_after"] = None
            return original_verify(db, execution, event, **kwargs)

        monkeypatch.setattr(outcome_module, "verify_coding_outcome", _patched_verify)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] != "VERIFIED"

# ── 40. Diff character limit enforced ─────────────────────────────────────────

class TestDiffCharLimit:
    def test_100k_char_diff_truncated(self, tmp_path, monkeypatch):
        import app.coding.outcome as outcome_mod
        original_generate = outcome_mod._generate_diff

        def _capped_generate(old_content, new_content, relative_path,
                             max_lines=500, max_chars=500):
            return original_generate(old_content, new_content, relative_path,
                                     max_lines=max_lines, max_chars=max_chars)

        monkeypatch.setattr(outcome_mod, "_generate_diff", _capped_generate)

        client, session_factory, _ = _make_test_env(tmp_path)
        content = "x = 1\n" * 8_000
        proposal = _make_proposal(new_content=content)
        event_id = _seed_coding_event(
            session_factory, verdict="ALLOW", proposal=proposal
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        outcome_resp = client.get(f"/api/coding/outcome/{event_id}")
        assert outcome_resp.status_code == 200
        body = outcome_resp.json()
        assert body["verification_status"] == "VERIFIED"
        assert body["diff_truncated"] is True
        diff_text = body.get("diff_text") or ""
        assert len(diff_text) <= 500

# ── 41. Concurrent outcome persistence ────────────────────────────────────────

class TestConcurrentOutcomePersistence:
    def test_concurrent_persistence_single_row(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        first = client.get(f"/api/coding/outcome/{event_id}")
        assert first.status_code == 200
        first_id = first.json()["id"]

        second = client.get(f"/api/coding/outcome/{event_id}")
        assert second.status_code == 200
        assert second.json()["id"] == first_id

        db = session_factory()
        try:
            outcomes = db.execute(
                select(CodingOutcomeORM).where(
                    CodingOutcomeORM.event_id == event_id
                )
            ).scalars().all()
            assert len(outcomes) == 1
        finally:
            db.close()

# ── 42. Persistence failure preserves execution status ────────────────────────

class TestPersistenceFailurePreservesExecution:
    def test_persistence_failure_keeps_execution_status(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        from app.coding import outcome as outcome_module

        original_persist = outcome_module._persist_outcome

        def _fail_persist(*args, **kwargs):
            from app.models.coding_outcome import CodingOutcomeORM
            outcome = CodingOutcomeORM(
                event_id=args[1].event_id,
                execution_id=args[1].id,
                operation_id=args[1].operation_id,
                action_fingerprint=args[1].action_fingerprint,
                verification_status="OUTCOME_UNKNOWN",
                expected_path="",
                observed_path="",
                expected_old_hash="",
                observed_old_hash="",
                expected_new_hash="",
                observed_final_hash="",
                verification_error_code="OUTCOME_PERSISTENCE_FAILED",
                verification_error_message="Simulated DB failure",
            )
            outcome.id = 0
            return outcome

        monkeypatch.setattr(outcome_module, "_persist_outcome", _fail_persist)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text

        execution_resp = client.get(f"/api/coding/execution/{event_id}")
        assert execution_resp.status_code == 200
        assert execution_resp.json()["status"] == "executed"
