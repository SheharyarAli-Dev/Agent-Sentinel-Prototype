"""
app/adapters/liveops_adapter.py
────────────────────────────────
LiveOps adapter — normalises raw LiveOps agent proposals into the canonical
EventCreate schema before handing them to the policy engine.

Accepted tools (deny-by-default: anything else is rejected):
  list_resources, start_vm, stop_vm, create_snapshot, delete_snapshot

Normalised fields:
  - source            "liveops" (caller-supplied source is never honoured)
  - event_type        the requested tool
  - original_goal     the user's stated goal (required, non-empty)
  - payload.tool      the requested tool
  - payload.capability  same value as tool (used by least-privilege)
  - payload.target    the target resource id (required except list_resources)
  - payload.resource  the resource id (defaults to target)
  - payload.description human-readable description of the proposed action
  - payload.session_id  caller session id (required, non-empty)

The adapter is a pure normaliser: it never executes filesystem paths, shell
commands, or any simulated cloud operation.
"""
from __future__ import annotations

from typing import Any

from app.models.event import EventCreate

# Deny-by-default allowlist of liveops tools.
ALLOWED_TOOLS = frozenset(
    {"list_resources", "start_vm", "stop_vm", "create_snapshot", "delete_snapshot"}
)


class LiveOpsAdapterError(ValueError):
    """Raised when a raw LiveOps payload cannot be normalised."""


def normalise_liveops_event(raw_payload: dict) -> EventCreate:
    """
    Convert a raw LiveOps proposal into a normalised EventCreate.

    Args:
        raw_payload: Dict from the LiveOps agent, e.g.
            {"tool": "stop_vm", "target": "prod-api-01",
             "original_goal": "...", "session_id": "...", ...}

    Returns:
        Normalised EventCreate ready for policy engine evaluation.

    Raises:
        LiveOpsAdapterError if the input is not a dict, the tool is not
        allowlisted, or required fields are missing.
    """
    if not isinstance(raw_payload, dict):
        raise LiveOpsAdapterError("raw_payload must be a dictionary.")

    tool = raw_payload.get("tool")
    if tool not in ALLOWED_TOOLS:
        raise LiveOpsAdapterError(
            f"Unsupported liveops tool {tool!r}. "
            f"Allowed tools: {sorted(ALLOWED_TOOLS)}."
        )

    goal = raw_payload.get("original_goal")
    if not goal or not str(goal).strip():
        raise LiveOpsAdapterError("original_goal is required and must be non-empty.")

    session_id = raw_payload.get("session_id")
    if not session_id or not str(session_id).strip():
        raise LiveOpsAdapterError("session_id is required and must be non-empty.")

    target = raw_payload.get("target")
    if tool != "list_resources":
        if not target or not str(target).strip():
            raise LiveOpsAdapterError(f"target is required for tool {tool!r}.")

    target_s = str(target).strip() if target is not None else ""
    resource = raw_payload.get("resource")
    resource_s = str(resource).strip() if resource else target_s

    payload: dict[str, Any] = {
        "tool": tool,
        "capability": tool,
        "target": target_s,
        "resource": resource_s,
        "description": str(raw_payload.get("description") or ""),
        "session_id": str(session_id).strip(),
    }

    return EventCreate(
        source="liveops",
        event_type=tool,
        payload=payload,
        original_goal=str(goal).strip(),
    )