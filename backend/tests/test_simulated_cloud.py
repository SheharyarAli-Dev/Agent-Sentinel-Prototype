"""
tests/test_simulated_cloud.py
──────────────────────────────
Unit tests — LiveOps Increment 1: the local simulated-cloud sandbox.

Covers backend/app/sandbox/simulated_cloud.py. All tests are deterministic and
offline. Every test uses pytest tmp_path for the runtime state file, so no test
ever touches the committed seed file (byte-identity is asserted explicitly).

These tests intentionally do NOT exercise ALLOW/WARN/BLOCK verdicts, policies,
or the agent: policy enforcement belongs to a later increment.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.sandbox.simulated_cloud import (
    InvalidResourceId,
    ResourceConflict,
    ResourceNotFound,
    SimulatedCloud,
    SimulatedCloudError,
)

# ── Shared paths ───────────────────────────────────────────────────────────────
_SEED = Path(__file__).resolve().parent.parent / "data" / "simulated_cloud_seed.json"


def _make_cloud(tmp_path: Path, seed: Path | None = None) -> SimulatedCloud:
    return SimulatedCloud(seed or _SEED, tmp_path / "runtime_state.json")


# ── 1. State initialization from the seed ──────────────────────────────────────

def test_state_initialized_from_seed(tmp_path):
    cloud = _make_cloud(tmp_path)
    state = cloud.get_state()

    vm_ids = {vm["id"] for vm in state["vms"]}
    assert vm_ids == {"dev-unused-01", "prod-api-01"}

    snap_ids = {snap["id"] for snap in state["snapshots"]}
    assert snap_ids == {"prod-backup-latest"}

    by_id = {vm["id"]: vm for vm in state["vms"]}
    assert by_id["dev-unused-01"]["environment"] == "development"
    assert by_id["dev-unused-01"]["state"] == "running"
    assert by_id["dev-unused-01"]["protected"] is False
    assert by_id["prod-api-01"]["environment"] == "production"
    assert by_id["prod-api-01"]["protected"] is True

    snap = state["snapshots"][0]
    assert snap["source_vm"] == "prod-api-01"
    assert snap["environment"] == "production"
    assert snap["protected"] is True


def test_missing_seed_rejected(tmp_path):
    with pytest.raises(SimulatedCloudError):
        SimulatedCloud(tmp_path / "does_not_exist.json", tmp_path / "state.json")


def test_state_file_created_only_when_missing(tmp_path):
    state_path = tmp_path / "runtime_state.json"
    assert not state_path.exists()

    cloud = _make_cloud(tmp_path)
    assert state_path.exists(), "state file must be materialised on first creation"

    # A second construction with an existing state file must NOT reset from seed.
    cloud.stop_vm("dev-unused-01")
    cloud2 = SimulatedCloud(_SEED, state_path)
    dev = next(vm for vm in cloud2.get_state()["vms"] if vm["id"] == "dev-unused-01")
    assert dev["state"] == "stopped", "existing runtime state must be preserved"


# ── 2. Seed file remains byte-identical after all operations ───────────────────

def test_seed_remains_byte_identical(tmp_path):
    before = _SEED.read_bytes()

    cloud = _make_cloud(tmp_path)
    cloud.start_vm("dev-unused-01")
    cloud.stop_vm("dev-unused-01")
    cloud.create_snapshot("prod-api-01", "snap-ephemeral")
    cloud.delete_snapshot("snap-ephemeral")
    cloud.reset()

    assert _SEED.read_bytes() == before, "seed file must never be modified"


# ── 3. Reset restores canonical state ──────────────────────────────────────────

def test_reset_restores_canonical_state(tmp_path):
    cloud = _make_cloud(tmp_path)
    cloud.stop_vm("dev-unused-01")
    cloud.stop_vm("prod-api-01")
    cloud.create_snapshot("prod-api-01", "snap-ephemeral")

    reset_state = cloud.reset()

    assert reset_state == SimulatedCloud(_SEED, tmp_path / "_probe.json").get_state()

    # And the persisted file matches after reload from disk.
    reloaded = SimulatedCloud(_SEED, tmp_path / "runtime_state.json").get_state()
    assert reloaded == reset_state


# ── 4. list_resources returns an independent copy ──────────────────────────────

def test_list_resources_returns_independent_copy(tmp_path):
    cloud = _make_cloud(tmp_path)
    view = cloud.list_resources()

    view["vms"].append({"id": "ghost-vm", "environment": "x", "state": "running", "protected": False})
    view["snapshots"].clear()

    state = cloud.get_state()
    assert "ghost-vm" not in {vm["id"] for vm in state["vms"]}
    assert len(state["snapshots"]) == 1
    assert state["snapshots"][0]["id"] == "prod-backup-latest"


# ── 5/6. Stop then start a VM ──────────────────────────────────────────────────

def test_stop_development_vm(tmp_path):
    cloud = _make_cloud(tmp_path)
    state = cloud.stop_vm("dev-unused-01")
    dev = next(vm for vm in state["vms"] if vm["id"] == "dev-unused-01")
    assert dev["state"] == "stopped"
    # prod untouched
    prod = next(vm for vm in state["vms"] if vm["id"] == "prod-api-01")
    assert prod["state"] == "running"


def test_start_stopped_vm(tmp_path):
    cloud = _make_cloud(tmp_path)
    cloud.stop_vm("dev-unused-01")
    state = cloud.start_vm("dev-unused-01")
    dev = next(vm for vm in state["vms"] if vm["id"] == "dev-unused-01")
    assert dev["state"] == "running"


# ── 7. Idempotent start/stop ───────────────────────────────────────────────────

def test_start_already_running_is_idempotent(tmp_path):
    cloud = _make_cloud(tmp_path)
    before = cloud.get_state()
    after = cloud.start_vm("dev-unused-01")
    assert after == before
    assert len(after["vms"]) == 2  # no duplicated records


def test_stop_already_stopped_is_idempotent(tmp_path):
    cloud = _make_cloud(tmp_path)
    cloud.stop_vm("dev-unused-01")
    before = cloud.get_state()
    after = cloud.stop_vm("dev-unused-01")
    assert after == before
    assert len(after["vms"]) == 2


# ── 8. Create snapshot ─────────────────────────────────────────────────────────

def test_create_snapshot(tmp_path):
    cloud = _make_cloud(tmp_path)
    state = cloud.create_snapshot("prod-api-01", "snap-before-maintenance")
    snap = next(s for s in state["snapshots"] if s["id"] == "snap-before-maintenance")
    assert snap["source_vm"] == "prod-api-01"
    assert snap["environment"] == "production"
    assert snap["protected"] is True, "snapshot inherits protected flag from VM"
    assert len(state["snapshots"]) == 2


def test_snapshot_protection_inherited_from_vm(tmp_path):
    cloud = _make_cloud(tmp_path)
    cloud.start_vm("dev-unused-01")
    state = cloud.create_snapshot("dev-unused-01", "snap-dev")
    snap = next(s for s in state["snapshots"] if s["id"] == "snap-dev")
    assert snap["protected"] is False, "dev VM is unprotected, so its snapshot is too"


# ── 9. Reject duplicate snapshot ───────────────────────────────────────────────

def test_duplicate_snapshot_rejected(tmp_path):
    cloud = _make_cloud(tmp_path)
    cloud.create_snapshot("prod-api-01", "snap-dup")
    with pytest.raises(ResourceConflict):
        cloud.create_snapshot("prod-api-01", "snap-dup")


# ── 10. Delete snapshot ────────────────────────────────────────────────────────

def test_delete_snapshot(tmp_path):
    cloud = _make_cloud(tmp_path)
    cloud.create_snapshot("prod-api-01", "snap-transient")
    state = cloud.delete_snapshot("snap-transient")
    assert "snap-transient" not in {s["id"] for s in state["snapshots"]}
    # Canonical snapshot still present.
    assert "prod-backup-latest" in {s["id"] for s in state["snapshots"]}


# ── 11/12. Unknown resources ───────────────────────────────────────────────────

def test_unknown_vm_rejected(tmp_path):
    cloud = _make_cloud(tmp_path)
    with pytest.raises(ResourceNotFound):
        cloud.start_vm("ghost-01")
    with pytest.raises(ResourceNotFound):
        cloud.stop_vm("ghost-01")
    with pytest.raises(ResourceNotFound):
        cloud.create_snapshot("ghost-01", "snap-x")


def test_unknown_snapshot_rejected(tmp_path):
    cloud = _make_cloud(tmp_path)
    with pytest.raises(ResourceNotFound):
        cloud.delete_snapshot("ghost-snapshot")


# ── 13. Invalid ids including path traversal ───────────────────────────────────

@pytest.mark.parametrize(
    "bad_id",
    [
        "",
        "   ",
        "../../etc/shadow",
        "..\\..\\windows\\system32",
        "../",
        "a/b",
        "a\\b",
        "x y",
        "path.to.file",
        "@evil",
        "/escape",
    ],
)
def test_invalid_resource_ids_rejected(tmp_path, bad_id):
    cloud = _make_cloud(tmp_path)
    for call in (
        lambda: cloud.start_vm(bad_id),
        lambda: cloud.stop_vm(bad_id),
        lambda: cloud.create_snapshot(bad_id, "snap-x"),
    ):
        with pytest.raises(InvalidResourceId):
            call()

    with pytest.raises(InvalidResourceId):
        cloud.create_snapshot("prod-api-01", bad_id)
    with pytest.raises(InvalidResourceId):
        cloud.delete_snapshot(bad_id)


def test_whitespace_only_and_traversal_snapshot_ids_rejected(tmp_path):
    cloud = _make_cloud(tmp_path)
    for bad in ("../x", "a/b", "..\\x", "snap id"):
        with pytest.raises(InvalidResourceId):
            cloud.delete_snapshot(bad)


# ── 14. Atomic writes leave valid JSON ─────────────────────────────────────────

def test_atomic_writes_leave_valid_json(tmp_path):
    state_path = tmp_path / "runtime_state.json"
    cloud = SimulatedCloud(_SEED, state_path)

    for i in range(20):
        cloud.start_vm("dev-unused-01")
        cloud.stop_vm("dev-unused-01")
        cloud.create_snapshot("prod-api-01", f"snap-{i}")
        cloud.delete_snapshot(f"snap-{i}")

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(payload.keys()) == {"vms", "snapshots"}
    assert len(payload["vms"]) == 2


def test_no_partial_tmp_files_left_behind(tmp_path):
    state_path = tmp_path / "runtime_state.json"
    cloud = SimulatedCloud(_SEED, state_path)
    for i in range(5):
        cloud.create_snapshot("prod-api-01", f"snap-{i}")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "runtime_state.json"]
    assert leftovers == [], f"unexpected leftover files: {leftovers}"


# ── 15. Two instances share a state path without corruption ────────────────────

def test_two_instances_shared_state_path_do_not_corrupt(tmp_path):
    state_path = tmp_path / "shared_state.json"
    a = SimulatedCloud(_SEED, state_path)
    b = SimulatedCloud(_SEED, state_path)

    a.stop_vm("dev-unused-01")
    b.start_vm("prod-api-01")  # already running — idempotent
    a.create_snapshot("prod-api-01", "snap-shared")

    # Both instances see the same coherent state.
    for cloud in (a, b):
        state = cloud.get_state()
        dev = next(vm for vm in state["vms"] if vm["id"] == "dev-unused-01")
        prod = next(vm for vm in state["vms"] if vm["id"] == "prod-api-01")
        assert dev["state"] == "stopped"
        assert prod["state"] == "running"
        assert "snap-shared" in {s["id"] for s in state["snapshots"]}

    # Persisted file is valid JSON, not interleaved garbage.
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(payload["vms"]) == 2
    assert len(payload["snapshots"]) == 2


# ── 15b. Concurrent instances sharing one state path lose no updates ────────────

def test_concurrent_instances_shared_state_path_lossless(tmp_path):
    """
    Regression: two SimulatedCloud instances on the SAME state_path, mutated
    from two threads in lock-step via threading.Barrier, must not lose either
    update, must leave valid JSON, and must leave no temporary files behind.

    Each round uses a fresh snapshot id so a lost update is observable: a
    missing snapshot id any round is a lost write. The dev VM stop is repeated
    each round (idempotent in the fixed build, but in a racy build the second
    thread's stale read can overwrite the stop).
    """
    state_path = tmp_path / "concurrent_state.json"
    a = SimulatedCloud(_SEED, state_path)
    b = SimulatedCloud(_SEED, state_path)

    rounds = 40
    errors: list[BaseException] = []

    for i in range(rounds):
        # Restore dev-unused-01 to running so stop_vm is a real write every round.
        a.start_vm("dev-unused-01")

        barrier = threading.Barrier(2)
        snapshot_id = f"snap-concurrent-{i}"
        results: dict[str, set[str]] = {"expected": set()}

        def _stop_dev():
            try:
                barrier.wait()
                a.stop_vm("dev-unused-01")
            except BaseException as exc:  # noqa: BLE001 — collect for assertion
                errors.append(exc)

        def _snap_from_prod():
            try:
                barrier.wait()
                b.create_snapshot("prod-api-01", snapshot_id)
            except BaseException as exc:  # noqa: BLE001 — collect for assertion
                errors.append(exc)

        t1 = threading.Thread(target=_stop_dev)
        t2 = threading.Thread(target=_snap_from_prod)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert not errors, f"iteration {i} raised: {errors}"

    # Both changes must survive across every round.
    final = b.get_state()
    dev = next(vm for vm in final["vms"] if vm["id"] == "dev-unused-01")
    assert dev["state"] == "stopped", "concurrent stop_vm was lost"
    snap_ids = {s["id"] for s in final["snapshots"]}
    expected = {"snap-concurrent-%d" % i for i in range(rounds)}
    assert expected <= snap_ids, f"lost snapshots: {expected - snap_ids}"

    # Persisted file stays valid JSON with no partial leftovers.
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert set(on_disk.keys()) == {"vms", "snapshots"}
    assert len(on_disk["vms"]) == 2
    assert len(on_disk["snapshots"]) == rounds + 1  # + prod-backup-latest

    leftovers = [p.name for p in tmp_path.iterdir() if p != state_path]
    assert leftovers == [], f"temporary files leaked: {leftovers}"


def test_lock_registry_shared_per_state_path_and_separate_across_paths(tmp_path):
    """
    Contract: instances sharing one resolved state_path use the SAME lock;
    instances on different state paths do not share one global lock.
    """
    shared = tmp_path / "same.json"
    a1 = SimulatedCloud(_SEED, shared)
    a2 = SimulatedCloud(_SEED, shared)
    other = tmp_path / "other.json"
    b = SimulatedCloud(_SEED, other)

    assert a1._lock is a2._lock, "same state path must share one process lock"
    assert a1._lock is not b._lock, "different state paths must not share one lock"


# ── 16. No operation touches files outside state_path's own project dir ────────

def test_no_operations_modify_unrelated_files(tmp_path):
    state_path = tmp_path / "runtime_state.json"
    untouched = tmp_path / "untouched.bin"
    untouched.write_bytes(b"\x00original\xff")

    cloud = SimulatedCloud(_SEED, state_path)
    cloud.start_vm("dev-unused-01")
    cloud.stop_vm("dev-unused-01")
    cloud.create_snapshot("prod-api-01", "snap-z")
    cloud.delete_snapshot("snap-z")
    cloud.reset()

    assert untouched.read_bytes() == b"\x00original\xff"


# ── 17. Returned state cannot mutate internal state ────────────────────────────

def test_returned_state_objects_are_isolated(tmp_path):
    cloud = _make_cloud(tmp_path)

    state = cloud.get_state()
    state["vms"].clear()
    state["snapshots"].append({"id": "injected", "source_vm": "dev-unused-01"})

    again = cloud.get_state()
    assert len(again["vms"]) == 2
    assert "injected" not in {s["id"] for s in again["snapshots"]}

    # Also, the seed-loaded nested dicts must not alias internal state.
    first = cloud.list_resources()
    first["vms"][0]["state"] = "hummed"
    second = cloud.list_resources()
    assert second["vms"][0]["state"] != "hummed"