"""
tests/test_liveops_policy.py
─────────────────────────────
Unit tests — LiveOps proposals routed through the policy pipeline.

Three verdict contracts (same user goal, three different proposals):
  1. stop_vm          dev-unused-01    -> ALLOW
  2. stop_vm          prod-api-01      -> WARN   (production-target policy)
  3. delete_snapshot  prod-backup-latest -> BLOCK (ungranted capability)

Also verifies:
  - delete_snapshot stays ungranted for liveops
  - LiveOps never invokes transaction ATTVE or cursor code-quality checks
  - evaluation is offline/deterministic and does not touch sandbox state files
"""
from __future__ import annotations

import pytest

from app.adapters.liveops_adapter import normalise_liveops_event
from app.models.event import EventCreate
from app.policy.least_privilege import _AGENTS, evaluate_least_privilege
from app.policy.rules_engine import evaluate_event

GOAL = "Clean unused development resources to reduce cost."


# ── Semantic backend fenced offline (see conftest): lexical fallback only ──────

def _event(tool, target, description=None, session_id="liveops-sess"):
    return normalise_liveops_event(
        {
            "tool": tool,
            "target": target,
            "resource": target,
            "description": description or f"{tool} {target}",
            "original_goal": GOAL,
            "session_id": session_id,
        }
    )


# ── Three verdict contracts ────────────────────────────────────────────────────

def test_development_vm_stop_allows():
    ev = _event(
        "stop_vm",
        "dev-unused-01",
        description="stop unused development VM dev-unused-01 to reduce cost",
        session_id="liveops-sess-dev",
    )
    d = evaluate_event(ev)
    assert d.verdict == "ALLOW", d.reasons


def test_production_vm_stop_warns():
    ev = _event(
        "stop_vm",
        "prod-api-01",
        description="stop production API VM prod-api-01 to reduce cost",
    )
    d = evaluate_event(ev)
    assert d.verdict == "WARN", d.reasons


def test_protected_production_snapshot_delete_blocks():
    ev = _event(
        "delete_snapshot",
        "prod-backup-latest",
        description="delete production backup snapshot prod-backup-latest",
        session_id="liveops-sess-del",
    )
    d = evaluate_event(ev)
    assert d.verdict == "BLOCK", d.reasons


# ── Capability / least-privilege contract ──────────────────────────────────────

def test_delete_snapshot_remains_ungranted():
    allowed = set(_AGENTS["liveops"].get("allowed_capabilities", []))
    assert "delete_snapshot" not in allowed
    for granted in ("list_resources", "start_vm", "stop_vm", "create_snapshot"):
        assert granted in allowed


def test_least_privilege_blocks_delete_snapshot():
    ev = _event("delete_snapshot", "prod-backup-latest")
    d = evaluate_least_privilege(ev)
    assert d.verdict == "BLOCK"


def test_least_privilege_allows_granted_liveops_tools():
    for tool in ("list_resources", "start_vm", "stop_vm", "create_snapshot"):
        target = None if tool == "list_resources" else "dev-unused-01"
        ev = normalise_liveops_event(
            {
                "tool": tool,
                "target": target,
                "original_goal": GOAL,
                "session_id": "liveops-sess-lp",
            }
        )
        d = evaluate_least_privilege(ev)
        assert d.verdict == "ALLOW", f"{tool}: {d.reasons}"


# ── Routing isolation ──────────────────────────────────────────────────────────

def test_liveops_does_not_invoke_attve(monkeypatch):
    def _fail(_):
        raise AssertionError("ATTVE must not run for liveops events")

    monkeypatch.setattr("app.policy.attve.evaluate_transaction", _fail)
    ev = _event("stop_vm", "dev-unused-01")
    d = evaluate_event(ev)
    assert d.verdict in ("ALLOW", "WARN", "BLOCK")


def test_liveops_does_not_invoke_cursor_code_quality(monkeypatch):
    def _fail(_e, _code):
        raise AssertionError("Cursor code-quality checks must not run for liveops events")

    monkeypatch.setattr(
        "app.policy.code_quality_patterns.check_code_quality", _fail
    )
    ev = _event("stop_vm", "prod-api-01", description="stop production API VM prod-api-01")
    d = evaluate_event(ev)
    assert d.verdict in ("ALLOW", "WARN", "BLOCK")


# ── Offline / deterministic / no sandbox mutation ──────────────────────────────

def test_evaluation_is_deterministic():
    a = evaluate_event(
        _event(
            "stop_vm",
            "dev-unused-01",
            description="stop unused development VM dev-unused-01 to reduce cost",
            session_id="liveops-sess-d",
        )
    )
    b = evaluate_event(
        _event(
            "stop_vm",
            "dev-unused-01",
            description="stop unused development VM dev-unused-01 to reduce cost",
            session_id="liveops-sess-d",
        )
    )
    assert a.verdict == b.verdict == "ALLOW", (a.reasons, b.reasons)


def test_policy_evaluation_does_not_touch_sandbox_state(tmp_path):
    """Evaluation must not read/write a SimulatedCloud runtime state file."""
    state_path = tmp_path / "runtime_state.json"
    state_path.write_text(
        '{"vms": [{"id": "dev-unused-01", "environment": "development", "state": "running", "protected": false}], "snapshots": []}',
        encoding="utf-8",
    )
    before = state_path.read_bytes()

    ev = _event("stop_vm", "dev-unused-01", description="stop unused dev VM dev-unused-01")
    evaluate_event(ev)

    assert state_path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["runtime_state.json"]


def test_eventcreate_accepts_liveops_source():
    ev = EventCreate(source="liveops", event_type="stop_vm", payload={})
    assert ev.source == "liveops"