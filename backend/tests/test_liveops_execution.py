"""
tests/test_liveops_execution.py
────────────────────────────────
Integration tests — LiveOps Increment 3: the authorised, exactly-once execution
gateway connecting Agent Sentinel decisions to the simulated cloud.

Uses FastAPI's synchronous TestClient with an isolated in-memory SQLite DB
(StaticPool) and a tmp_path runtime-state SimulatedCloud, matching the repo's
existing test patterns. No real cloud, no network, no MiniLM (conftest already
fences the semantic model).

Coverage per the increment contract:
  1.  GET  /state                    returns canonical resources
  2.  POST /reset                    restores canonical seed state
  3.  Seed file stays byte-identical
  4.  ALLOW dev stop executes → VM becomes stopped
  5.  Repeated execution of same ALLOW event → 409
  6.  Repeated execution does not call sandbox twice
  7.  WARN without review → 409, state unchanged
  8.  WARN approved executes once
  9.  WARN rejected → no execution, state unchanged
  10. Repeated approved WARN execution → 409
  11. BLOCK → 403, state byte-identical
  12. BLOCK with unblock metadata still 403 (not executable)
  13. Non-LiveOps event rejected
  14. Missing event → 404
  15. Missing decision → proper error
  16. Malformed persisted payload rejected safely
  17. Unknown tool rejected
  18. Caller cannot substitute a different tool/target at execution time
  19. Execution result persisted and queryable
  20. Sandbox failure does not create an executed status
  21. Concurrent duplicate execution → exactly one cloud operation
  22. Unique event_id constraint verified
  23. Existing decide endpoint approval/rejection remain operational
  24. Full 206-test suite stays green (run separately)
  25. Tests offline (fenced MiniLM via conftest)
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.database import get_db, Base
from app.api.liveops import get_cloud
from app.models.event import EventORM
from app.models.decision import DecisionORM
from app.models.liveops_execution import LiveOpsExecutionORM
from app.sandbox.simulated_cloud import SimulatedCloud

_SEED = Path(__file__).resolve().parent.parent / "data" / "simulated_cloud_seed.json"


# ── Isolated in-memory SQLite + tmp_path cloud ────────────────────────────────

def _make_test_env(tmp_path: Path):
    # File-backed SQLite inside tmp_path: each request thread/session gets its own
    # DBAPI connection (unlike an in-memory StaticPool, which serialises every
    # statement through one shared connection and cannot support the concurrency
    # test). The DB is still fully isolated per test via tmp_path.
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    cloud = SimulatedCloud(_SEED, tmp_path / "runtime_state.json")

    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_cloud] = lambda: cloud
    client = TestClient(app)
    return client, session_factory, cloud, tmp_path


# ── Fixture helpers ────────────────────────────────────────────────────────────

def _seed_event(session_factory, *, tool="stop_vm", target="dev-unused-01",
                source="liveops", event_type=None, verdict="ALLOW",
                human_decision=None, unblocked=False, malformed_payload=False,
                payload=None):
    """Insert a persisted event + decision directly (deterministic, offline)."""
    if payload is None:
        payload = {
            "tool": tool,
            "capability": tool,
            "target": target or "",
            "resource": target or "",
            "description": f"{tool} {target}",
            "session_id": "liveops-sess-exec",
        }
    db = session_factory()
    try:
        ev = EventORM(
            source=source,
            event_type=event_type or tool,
            payload="{" if malformed_payload else json.dumps(payload),
            original_goal="Clean unused development resources to reduce cost.",
            timestamp=datetime.now(timezone.utc),
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)

        dec = DecisionORM(
            event_id=ev.id,
            verdict=verdict,
            reasons="[]",
            suggested_fix="" if verdict == "ALLOW" else "Review required",
            module="liveops_test",
            risk_score=0.5 if verdict != "ALLOW" else 0.0,
            explanation="",
            latency_ms=1.0,
            timestamp=datetime.now(timezone.utc),
            human_decision=human_decision,
            human_timestamp=datetime.now(timezone.utc) if human_decision else None,
            unblocked_by_human=1 if unblocked else 0,
            unblock_timestamp=datetime.now(timezone.utc) if unblocked else None,
        )
        db.add(dec)
        db.commit()
        return ev.id
    finally:
        db.close()


def _vm_state(cloud: SimulatedCloud, vm_id: str) -> str:
    state = cloud.get_state()
    for vm in state["vms"]:
        if vm["id"] == vm_id:
            return vm["state"]
    raise AssertionError(f"VM {vm_id} missing from cloud state")


def _execution_row(session_factory, event_id):
    db = session_factory()
    try:
        return db.query(LiveOpsExecutionORM).filter(
            LiveOpsExecutionORM.event_id == event_id
        ).first()
    finally:
        db.close()


# ── 1/2/3. State + reset + seed integrity ─────────────────────────────────────

def test_get_state_returns_canonical_resources(tmp_path):
    client, _, cloud, _ = _make_test_env(tmp_path)
    resp = client.get("/api/liveops/state")
    assert resp.status_code == 200
    data = resp.json()
    vm_ids = {vm["id"] for vm in data["vms"]}
    assert vm_ids == {"dev-unused-01", "prod-api-01"}
    snap_ids = {s["id"] for s in data["snapshots"]}
    assert snap_ids == {"prod-backup-latest"}


def test_reset_restores_canonical_state(tmp_path):
    client, _, cloud, _ = _make_test_env(tmp_path)
    cloud.stop_vm("dev-unused-01")
    cloud.create_snapshot("prod-api-01", "snap-tmp")
    assert _vm_state(cloud, "dev-unused-01") == "stopped"

    resp = client.post("/api/liveops/reset")
    assert resp.status_code == 200
    data = resp.json()
    assert _vm_state(cloud, "dev-unused-01") == "running"
    assert {s["id"] for s in data["snapshots"]} == {"prod-backup-latest"}


def test_seed_file_remains_byte_identical(tmp_path):
    before = _SEED.read_bytes()
    client, _, cloud, _ = _make_test_env(tmp_path)
    client.post("/api/liveops/reset")
    cloud.stop_vm("dev-unused-01")
    cloud.start_vm("dev-unused-01")
    client.get("/api/liveops/state")
    assert _SEED.read_bytes() == before


# ── 4-6. ALLOW executes once ──────────────────────────────────────────────────

def test_allow_dev_stop_executes_and_changes_state(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(session_factory, tool="stop_vm", target="dev-unused-01", verdict="ALLOW")

    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "executed"
    assert body["tool"] == "stop_vm"
    assert body["target"] == "dev-unused-01"
    assert body["executed_at"] is not None
    assert _vm_state(cloud, "dev-unused-01") == "stopped"


def test_repeated_allow_execution_returns_409(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(session_factory, tool="stop_vm", target="dev-unused-01", verdict="ALLOW")

    first = client.post(f"/api/liveops/execute/{event_id}")
    assert first.status_code == 200
    second = client.post(f"/api/liveops/execute/{event_id}")
    assert second.status_code == 409
    # State still exactly once-executed.
    assert _vm_state(cloud, "dev-unused-01") == "stopped"


def test_repeated_execution_does_not_call_sandbox_twice(tmp_path, monkeypatch):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(session_factory, tool="stop_vm", target="dev-unused-01", verdict="ALLOW")
    original_stop = cloud.stop_vm
    calls = {"n": 0}

    def _counting_stop(vm_id):
        calls["n"] += 1
        return original_stop(vm_id)

    monkeypatch.setattr(cloud, "stop_vm", _counting_stop)

    assert client.post(f"/api/liveops/execute/{event_id}").status_code == 200
    assert client.post(f"/api/liveops/execute/{event_id}").status_code == 409
    assert calls["n"] == 1, "sandbox stop_vm must be called exactly once"


# ── 7-10. WARN gating ─────────────────────────────────────────────────────────

def test_warn_without_review_returns_409_and_state_unchanged(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(session_factory, tool="stop_vm", target="prod-api-01", verdict="WARN")

    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 409
    assert _vm_state(cloud, "prod-api-01") == "running"


def test_warn_approved_executes_once(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(
        session_factory, tool="stop_vm", target="prod-api-01",
        verdict="WARN", human_decision="approved",
    )
    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "executed"
    assert _vm_state(cloud, "prod-api-01") == "stopped"


def test_warn_rejected_does_not_execute(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(
        session_factory, tool="stop_vm", target="prod-api-01",
        verdict="WARN", human_decision="rejected",
    )
    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 409
    assert _vm_state(cloud, "prod-api-01") == "running"
    row = _execution_row(session_factory, event_id)
    assert row is not None and row.status == "rejected"


def test_repeated_approved_warn_execution_returns_409(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(
        session_factory, tool="stop_vm", target="prod-api-01",
        verdict="WARN", human_decision="approved",
    )
    assert client.post(f"/api/liveops/execute/{event_id}").status_code == 200
    assert client.post(f"/api/liveops/execute/{event_id}").status_code == 409


# ── 11-12. BLOCK gating ───────────────────────────────────────────────────────

def test_block_returns_403_and_state_byte_identical(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    before_state = (tmp_path / "runtime_state.json").read_bytes()
    event_id = _seed_event(
        session_factory, tool="delete_snapshot", target="prod-backup-latest", verdict="BLOCK",
    )
    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 403
    # Cloud state unchanged on disk AND in memory.
    assert (tmp_path / "runtime_state.json").read_bytes() == before_state
    assert _vm_state(cloud, "prod-api-01") == "running"


def test_block_cannot_execute_even_with_unblock_metadata(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(
        session_factory, tool="delete_snapshot", target="prod-backup-latest",
        verdict="BLOCK", unblocked=True,
    )
    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 403
    assert _vm_state(cloud, "prod-api-01") == "running"


# ── 13-17. Validation failures ────────────────────────────────────────────────

def test_non_liveops_event_rejected(tmp_path):
    client, session_factory, _, _ = _make_test_env(tmp_path)
    event_id = _seed_event(
        session_factory, tool="stop_vm", target="dev-unused-01",
        source="cursor", verdict="ALLOW",
    )
    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 422


def test_missing_event_returns_404(tmp_path):
    client, _, _, _ = _make_test_env(tmp_path)
    resp = client.post("/api/liveops/execute/99999")
    assert resp.status_code == 404


def test_missing_decision_returns_error(tmp_path):
    client, session_factory, _, _ = _make_test_env(tmp_path)
    # Event persisted but NO decision row.
    db = session_factory()
    try:
        ev = EventORM(
            source="liveops", event_type="stop_vm",
            payload=json.dumps({"tool": "stop_vm", "target": "dev-unused-01"}),
            timestamp=datetime.now(timezone.utc),
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        event_id = ev.id
    finally:
        db.close()
    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 404


def test_malformed_persisted_payload_rejected(tmp_path):
    client, session_factory, _, _ = _make_test_env(tmp_path)
    event_id = _seed_event(
        session_factory, tool="stop_vm", target="dev-unused-01",
        verdict="ALLOW", malformed_payload=True,
    )
    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 422


def test_unknown_tool_rejected(tmp_path):
    client, session_factory, _, _ = _make_test_env(tmp_path)
    event_id = _seed_event(
        session_factory, tool="format_disk", target="dev-unused-01", verdict="ALLOW",
    )
    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 422


# ── 18. No execution-time substitution ────────────────────────────────────────

def test_caller_cannot_substitute_tool_or_target(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(session_factory, tool="stop_vm", target="dev-unused-01", verdict="ALLOW")
    # Caller attempts to substitute a different tool/target via a request body —
    # the endpoint must IGNORE caller input and dispatch the persisted payload.
    resp = client.post(f"/api/liveops/execute/{event_id}", json={
        "tool": "delete_snapshot", "target": "prod-backup-latest",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tool"] == "stop_vm"
    assert body["target"] == "dev-unused-01"
    assert _vm_state(cloud, "dev-unused-01") == "stopped"
    assert "prod-backup-latest" in {s["id"] for s in cloud.get_state()["snapshots"]}


# ── 19. Persisted + queryable result ──────────────────────────────────────────

def test_execution_result_persisted_and_queryable(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(session_factory, tool="stop_vm", target="dev-unused-01", verdict="ALLOW")
    assert client.post(f"/api/liveops/execute/{event_id}").status_code == 200

    row = _execution_row(session_factory, event_id)
    assert row is not None
    assert row.status == "executed"
    assert row.executed_at is not None
    result = row.get_result()
    assert result["tool"] == "stop_vm"
    assert result["target"] == "dev-unused-01"
    product = {vm["id"]: vm["state"] for vm in result["vms"]}
    assert product["dev-unused-01"] == "stopped"
    # No filesystem paths leak into the stored result.
    assert "runtime_state" not in json.dumps(result)

    lookup = client.get(f"/api/liveops/execution/{event_id}")
    assert lookup.status_code == 200
    assert lookup.json()["status"] == "executed"
    assert lookup.json()["event_id"] == event_id


# ── 20. Failure handling ──────────────────────────────────────────────────────

def test_sandbox_failure_does_not_create_executed_status(tmp_path, monkeypatch):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(session_factory, tool="stop_vm", target="dev-unused-01", verdict="ALLOW")

    def _boom(vm_id):
        raise RuntimeError("simulated sandbox failure")

    monkeypatch.setattr(cloud, "stop_vm", _boom)

    resp = client.post(f"/api/liveops/execute/{event_id}")
    assert resp.status_code == 500
    row = _execution_row(session_factory, event_id)
    assert row is not None
    assert row.status == "failed"
    assert row.executed_at is None
    assert "simulated sandbox failure" in str(row.get_result())
    # VM not stopped — operation never succeeded.
    assert _vm_state(cloud, "dev-unused-01") == "running"


# ── 21. Concurrency ───────────────────────────────────────────────────────────

def test_concurrent_duplicate_execution_exactly_once(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)
    event_id = _seed_event(session_factory, tool="stop_vm", target="dev-unused-01", verdict="ALLOW")

    original_stop = cloud.stop_vm
    lock = threading.Lock()
    stats = {"calls": 0}

    def _counting_stop(vm_id):
        with lock:
            stats["calls"] += 1
        return original_stop(vm_id)

    cloud.stop_vm = _counting_stop

    def _fire(_):
        return client.post(f"/api/liveops/execute/{event_id}").status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(_fire, range(2)))

    assert sorted(codes) == [200, 409], codes
    assert stats["calls"] == 1, "exactly one cloud operation across the race"
    assert _vm_state(cloud, "dev-unused-01") == "stopped"


# ── 22. Unique constraint ─────────────────────────────────────────────────────

def test_unique_event_id_constraint(tmp_path):
    client, session_factory, cloud, _ = _make_test_env(tmp_path)  # noqa: F841 (cloud unused)
    db = session_factory()
    try:
        db.add(LiveOpsExecutionORM(event_id=1, tool="stop_vm", target="dev-unused-01", status="executed"))
        db.commit()
        db.add(LiveOpsExecutionORM(event_id=1, tool="stop_vm", target="dev-unused-01", status="executed"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


# ── 23. Existing decide endpoint stays operational ────────────────────────────

def test_decide_endpoint_approve_and_reject_remain_operational(tmp_path):
    client, session_factory, _, _ = _make_test_env(tmp_path)
    approved_id = _seed_event(
        session_factory, tool="stop_vm", target="prod-api-01",
        verdict="WARN", human_decision=None,
    )
    rejected_id = _seed_event(
        session_factory, tool="stop_vm", target="prod-api-01",
        verdict="WARN", human_decision=None,
    )

    approve = client.post(f"/api/decide/{approved_id}", json={"decision": "approved"})
    assert approve.status_code == 200, approve.text
    assert approve.json()["human_decision"] == "approved"

    reject = client.post(f"/api/decide/{rejected_id}", json={"decision": "rejected"})
    assert reject.status_code == 200, reject.text
    assert reject.json()["human_decision"] == "rejected"

    # Non-WARN still refused by decide endpoint (unchanged behaviour).
    allow_id = _seed_event(session_factory, tool="stop_vm", target="dev-unused-01", verdict="ALLOW")
    refuse = client.post(f"/api/decide/{allow_id}", json={"decision": "approved"})
    assert refuse.status_code == 422