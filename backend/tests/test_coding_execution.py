"""
tests/test_coding_execution.py
──────────────────────────────
Integration tests for the governance-gated coding execution gateway (Stage 3).
"""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.coding_execution import CodingExecutionORM
from app.models.decision import DecisionORM
from app.models.event import EventORM, EventCreate
from app.models.operation import (
    OperationORM,
    build_canonical_action,
    compute_action_fingerprint,
)
from app.models.coding_proposal import CodingProposal
from app.sandbox import coding_executor as executor_module
from app.sandbox.coding_executor import CodingWorkspace


_DEMO_ROOT = Path(__file__).resolve().parent.parent.parent / "coding-demo"
_SEED = Path(__file__).resolve().parent.parent / "data" / "coding_demo_seed.json"


def _sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    from sqlalchemy import select as sa_select
    db = session_factory()
    try:
        return db.scalar(
            sa_select(CodingExecutionORM).where(
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


# ── 1. ALLOW executes once ─────────────────────────────────────────────────────

class TestAllowExecutes:
    def test_safe_allow_executes_once(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "executed"
        assert body["relative_path"] == "src/status.py"
        assert body["replayed"] is False


# ── 2. ALLOW does not require human approval ───────────────────────────────────

class TestAllowNoApproval:
    def test_allow_does_not_require_human_approval(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory, verdict="ALLOW", human_decision=None
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "executed"
        db = session_factory()
        try:
            decision = (
                db.query(DecisionORM)
                .filter(DecisionORM.event_id == event_id)
                .first()
            )
            assert decision.human_decision is None
        finally:
            db.close()


# ── 3. Identical retry returns conflict ───────────────────────────────────────

class TestExecutedRetry:
    def test_identical_retry_returns_conflict(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        first = client.post(f"/api/coding/execute/{event_id}")
        assert first.status_code == 200
        first_id = first.json()["id"]
        second = client.post(f"/api/coding/execute/{event_id}")
        assert second.status_code == 409
        detail = second.json()["detail"]
        assert detail["status"] == "executed"
        assert detail["execution_id"] == first_id


# ── 4. Failed retry returns conflict ──────────────────────────────────────────

class TestFailedRetryConflict:
    def test_failed_retry_returns_conflict(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        def _fail_atomic_write(target, content):
            raise OSError("disk full")

        monkeypatch.setattr(
            executor_module, "_atomic_write", _fail_atomic_write
        )
        first = client.post(f"/api/coding/execute/{event_id}")
        assert first.status_code == 200
        assert first.json()["status"] == "failed"
        second = client.post(f"/api/coding/execute/{event_id}")
        assert second.status_code == 409
        assert second.json()["detail"]["status"] == "failed"


# ── 5. Concurrent duplicate invokes once ──────────────────────────────────────

class TestConcurrentDuplicate:
    def test_concurrent_requests_invoke_once(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        counter = {"n": 0}
        barrier = threading.Barrier(2)

        original_execute = CodingWorkspace.execute_file_write

        def _counting_execute(self, proposal, *, review_authorized=False):
            counter["n"] += 1
            barrier.wait(timeout=5)
            return original_execute(self, proposal, review_authorized=review_authorized)

        monkeypatch.setattr(
            CodingWorkspace, "execute_file_write", _counting_execute
        )

        def _fire(_):
            return client.post(f"/api/coding/execute/{event_id}").status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(_fire, range(2)))

        assert sorted(codes) == [200, 409], codes
        assert counter["n"] == 1, "executor must be called exactly once"


# ── 6. WARN without approval rejects ──────────────────────────────────────────

class TestWarnNoApproval:
    def test_warn_without_approval_rejects(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory, verdict="WARN", human_decision=None
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 409


# ── 7. Approved WARN executes ─────────────────────────────────────────────────

class TestApprovedWarn:
    def test_approved_warn_executes(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory, verdict="WARN", human_decision="approved"
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "executed"


# ── 8. Rejected WARN rejects ──────────────────────────────────────────────────

class TestRejectedWarn:
    def test_rejected_warn_rejects(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory, verdict="WARN", human_decision="rejected"
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 409


# ── 9. Expired WARN returns 410 ───────────────────────────────────────────────

class TestExpiredWarn:
    def test_expired_warn_returns_410(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory,
            verdict="WARN",
            human_decision="approved",
            review_expired=True,
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 410


# ── 10. BLOCK rejects without execution row ───────────────────────────────────

class TestBlockRejects:
    def test_block_rejects_without_execution_row(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="BLOCK")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 403
        row = _execution_row(session_factory, event_id)
        assert row is None


# ── 11. Fingerprint mismatch returns 409 ──────────────────────────────────────

class TestFingerprintMismatch:
    def test_fingerprint_mismatch_returns_409(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory, verdict="ALLOW", fingerprint_mismatch=True
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 409


# ── 12. Tampered payload fails verification ───────────────────────────────────

class TestExactActionVerification:
    def test_tampered_payload_fails_verification(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        db = session_factory()
        try:
            event = db.get(EventORM, event_id)
            payload = json.loads(event.payload)
            payload["relative_path"] = "src/tampered.py"
            event.payload = json.dumps(payload)
            db.commit()
        finally:
            db.close()
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 409


# ── 13. Empty body uses stored payload ────────────────────────────────────────

class TestCallerCannotSubstitute:
    def test_empty_body_uses_stored_payload(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["relative_path"] == "src/status.py"


# ── 14. Non-cursor event rejects ──────────────────────────────────────────────

class TestNonCursorEvent:
    def test_non_cursor_event_rejects(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory, verdict="ALLOW", source="liveops"
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 422


# ── 15. Non-coding cursor event rejects ───────────────────────────────────────

class TestNonCodingCursorEvent:
    def test_non_coding_cursor_event_rejects(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(
            session_factory, verdict="ALLOW", event_type="plan_execution"
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 422


# ── 16. Missing event returns 404 ─────────────────────────────────────────────

class TestMissingEvent:
    def test_missing_event_returns_404(self, tmp_path):
        client, _, _ = _make_test_env(tmp_path)
        resp = client.post("/api/coding/execute/99999")
        assert resp.status_code == 404


# ── 17. Execution record persists ─────────────────────────────────────────────

class TestExecutionPersists:
    def test_execution_record_persists(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        row = _execution_row(session_factory, event_id)
        assert row is not None
        assert row.status == "executed"
        assert row.event_id == event_id
        assert row.relative_path == "src/status.py"
        assert row.before_hash is not None
        assert row.after_hash is not None
        assert row.started_at is not None
        assert row.completed_at is not None


# ── 18. Operation lifecycle reaches executed ──────────────────────────────────

class TestOperationLifecycle:
    def test_operation_reaches_executed(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        op = _operation_row(session_factory, event_id)
        assert op is not None
        assert op.lifecycle_state == "executed"


# ── 19. Executor failure persists failed ──────────────────────────────────────

class TestExecutorFailure:
    def test_executor_failure_persists_failed(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        def _fail_atomic_write(target, content):
            raise OSError("simulated write failure")

        monkeypatch.setattr(
            executor_module, "_atomic_write", _fail_atomic_write
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error_code"] == "FAILED_WRITE"


# ── 20. Workspace failure persists failed ─────────────────────────────────────

class TestWorkspaceFailure:
    def test_workspace_failure_persists_failed(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        def _fail_init(self, **kwargs):
            raise RuntimeError("workspace init failed")

        monkeypatch.setattr(CodingWorkspace, "__init__", _fail_init)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error_code"] == "FAILED_WORKSPACE"


# ── 21. Restoration status persists ───────────────────────────────────────────

class TestRestorationStatus:
    def test_restoration_status_persists(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        def _fail_atomic_write(target, content):
            raise OSError("disk full")

        monkeypatch.setattr(
            executor_module, "_atomic_write", _fail_atomic_write
        )
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        row = _execution_row(session_factory, event_id)
        assert row.restoration_attempted is True


# ── 22. Protected target rejected at execution ────────────────────────────────

class TestProtectedTarget:
    def test_protected_target_rejected_at_execution(self, tmp_path):
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


# ── 23. No raw content in response ────────────────────────────────────────────

class TestNoRawContentInLedger:
    def test_no_new_content_in_response(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "new_content" not in body
        row = _execution_row(session_factory, event_id)
        assert row is not None
        assert not hasattr(row, "new_content") or getattr(row, "new_content", None) is None


# ── 24. GET returns persisted execution ───────────────────────────────────────

class TestGetExecution:
    def test_get_returns_persisted_execution(self, tmp_path):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")
        post_resp = client.post(f"/api/coding/execute/{event_id}")
        assert post_resp.status_code == 200, post_resp.text
        get_resp = client.get(f"/api/coding/execution/{event_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["event_id"] == event_id
        assert data["status"] == "executed"
        assert data["relative_path"] == "src/status.py"
        assert data["replayed"] is True


# ── 25. GET missing execution returns 404 ─────────────────────────────────────

class TestGetMissingExecution:
    def test_get_missing_returns_404(self, tmp_path):
        client, _, _ = _make_test_env(tmp_path)
        resp = client.get("/api/coding/execution/99999")
        assert resp.status_code == 404


# ── 26. Fixture remains unchanged after execution ────────────────────────────

class TestFixtureUnchanged:
    def test_fixture_remains_unchanged_after_execution(self, tmp_path):
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


# ── 27. Workspace error leaves no pending row ─────────────────────────────────

class TestHandledExceptionNoPendingRow:
    def test_workspace_error_leaves_no_pending_row(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        def _fail_init(self, **kwargs):
            raise RuntimeError("workspace init failed")

        monkeypatch.setattr(CodingWorkspace, "__init__", _fail_init)
        resp = client.post(f"/api/coding/execute/{event_id}")
        assert resp.status_code == 200, resp.text
        row = _execution_row(session_factory, event_id)
        assert row is not None
        assert row.status == "failed"
        assert row.completed_at is not None


# ── 28. Existing failed record reported honestly ──────────────────────────────

class TestStaleRecordHonest:
    def test_existing_failed_record_reported_honestly(self, tmp_path, monkeypatch):
        client, session_factory, _ = _make_test_env(tmp_path)
        event_id = _seed_coding_event(session_factory, verdict="ALLOW")

        def _fail_atomic_write(target, content):
            raise OSError("simulated failure")

        monkeypatch.setattr(
            executor_module, "_atomic_write", _fail_atomic_write
        )
        first = client.post(f"/api/coding/execute/{event_id}")
        assert first.status_code == 200
        assert first.json()["status"] == "failed"
        second = client.post(f"/api/coding/execute/{event_id}")
        assert second.status_code == 409
        assert second.json()["detail"]["status"] == "failed"
