"""
tests/test_liveops_adapter.py
──────────────────────────────
Unit tests — LiveOps adapter normalisation.

Covers backend/app/adapters/liveops_adapter.py. The adapter only normalises
raw LiveOps proposals into an EventCreate; it never executes anything. All
tests are offline and deterministic.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.adapters.liveops_adapter import ALLOWED_TOOLS, normalise_liveops_event
from app.models.event import EventCreate

_GOAL = "Clean unused development resources to reduce cost."


def _raw(tool="stop_vm", target="dev-unused-01", **_overrides):
    payload = {
        "tool": tool,
        "target": target,
        "resource": target,
        "description": f"{tool} {target}",
        "original_goal": _GOAL,
        "session_id": "liveops-sess-01",
    }
    payload.update(_overrides)
    return payload


# ── All five allowed tools normalise ───────────────────────────────────────────

@pytest.mark.parametrize("tool", sorted(ALLOWED_TOOLS))
def test_allowed_tools_normalise(tool):
    target = None if tool == "list_resources" else "dev-unused-01"
    ev = normalise_liveops_event(_raw(tool=tool, target=target))
    assert ev.source == "liveops"
    assert ev.event_type == tool
    assert ev.original_goal == _GOAL
    assert ev.payload["tool"] == tool
    assert ev.payload["capability"] == tool
    assert ev.payload["session_id"] == "liveops-sess-01"


# ── Tool allowlist validation ───────────────────────────────────────────────────

def test_unknown_tool_rejected():
    with pytest.raises(ValueError):
        normalise_liveops_event(_raw(tool="format_disk", target="x"))


def test_missing_tool_rejected():
    raw = _raw()
    del raw["tool"]
    with pytest.raises(ValueError):
        normalise_liveops_event(raw)


# ── Goal / session requirements ────────────────────────────────────────────────

def test_missing_goal_rejected():
    raw = _raw()
    del raw["original_goal"]
    with pytest.raises(ValueError):
        normalise_liveops_event(raw)


def test_empty_goal_rejected():
    with pytest.raises(ValueError):
        normalise_liveops_event(_raw(original_goal="   "))


def test_missing_session_id_rejected():
    raw = _raw()
    del raw["session_id"]
    with pytest.raises(ValueError):
        normalise_liveops_event(raw)


def test_empty_session_id_rejected():
    with pytest.raises(ValueError):
        normalise_liveops_event(_raw(session_id=""))


# ── Target requirements ────────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["start_vm", "stop_vm", "create_snapshot", "delete_snapshot"])
def test_missing_target_rejected_for_targeted_tools(tool):
    with pytest.raises(ValueError):
        normalise_liveops_event(_raw(tool=tool, target=None))


def test_list_resources_may_omit_target():
    ev = normalise_liveops_event(_raw(tool="list_resources", target=None))
    assert ev.payload["tool"] == "list_resources"
    assert ev.payload["target"] == ""
    assert ev.payload["resource"] == ""


# ── Source forcing ─────────────────────────────────────────────────────────────

def test_source_forced_to_liveops():
    ev = normalise_liveops_event(_raw(source="cursor"))
    assert ev.source == "liveops"


def test_caller_supplied_source_never_overrides():
    ev = normalise_liveops_event(_raw(source="n8n", source2="evil"))
    assert ev.source == "liveops"


# ── Payload preservation ───────────────────────────────────────────────────────

def test_payload_fields_preserved():
    ev = normalise_liveops_event(
        _raw(
            tool="create_snapshot",
            target="prod-api-01",
            resource="prod-backup-weekly",
            description="Weekly backup snapshot",
            session_id="liveops-sess-77",
        )
    )
    p = ev.payload
    assert p["tool"] == "create_snapshot"
    assert p["capability"] == "create_snapshot"
    assert p["target"] == "prod-api-01"
    assert p["resource"] == "prod-backup-weekly"
    assert p["description"] == "Weekly backup snapshot"
    assert p["session_id"] == "liveops-sess-77"
    assert ev.event_type == "create_snapshot"


# ── Input type validation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, ["x"], "stop_vm", 42])
def test_non_dict_input_rejected(bad):
    with pytest.raises(ValueError):
        normalise_liveops_event(bad)


# ── EventCreate compatibility ──────────────────────────────────────────────────

def test_adapter_output_is_eventcreate():
    ev = normalise_liveops_event(_raw())
    assert isinstance(ev, EventCreate)


def test_liveops_source_accepted_by_eventcreate():
    ev = EventCreate(
        source="liveops",
        event_type="stop_vm",
        payload={"tool": "stop_vm", "capability": "stop_vm", "target": "dev-unused-01"},
        original_goal=_GOAL,
    )
    assert ev.source == "liveops"


@pytest.mark.parametrize("src", ["cursor", "n8n", "transaction"])
def test_previous_sources_remain_accepted(src):
    ev = EventCreate(source=src, event_type="x", payload={})
    assert ev.source == src


def test_invalid_source_still_rejected():
    with pytest.raises(ValidationError):
        EventCreate(source="bogus", event_type="x", payload={})


# ── Adapter never executes anything ────────────────────────────────────────────

def test_adapter_only_normalizes_data():
    """Path/command-looking strings must not raise and must not be executed."""
    ev = normalise_liveops_event(
        _raw(tool="stop_vm", target="../../etc/shadow", description="rm -rf /")
    )
    assert ev.payload["target"] == "../../etc/shadow"
    assert ev.payload["description"] == "rm -rf /"
    assert ev.source == "liveops"