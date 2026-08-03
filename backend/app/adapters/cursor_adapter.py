"""
app/adapters/cursor_adapter.py
────────────────────────────────
Cursor adapter — normalises events from Cursor IDE's hooks.json into the
canonical EventCreate schema before handing them to the policy engine.

Maps Cursor hook payloads:
  - hook_type: "beforeShellExecution" | "beforeMCPExecution"
  - command / method: shell command or MCP method name
  - args / params: parameters passed to command
  - goal: original session goal (if set)
  - steps: multi-step plan array (if available)
"""
from __future__ import annotations

from typing import Any
from app.models.event import EventCreate


def normalise_cursor_event(raw_payload: dict[str, Any]) -> EventCreate:
    """
    Convert a raw Cursor hook payload into a normalised EventCreate.

    Args:
        raw_payload: Dict received from Cursor hook POST request.

    Returns:
        Normalised EventCreate ready for policy engine evaluation.
    """
    hook_type = raw_payload.get("hook_type", "shell_execution")
    goal = raw_payload.get("goal") or raw_payload.get("original_goal")

    # Map steps if present, or construct single step from command/args
    steps = raw_payload.get("steps")
    if not steps:
        cmd = raw_payload.get("command") or raw_payload.get("method") or ""
        args = raw_payload.get("args") or raw_payload.get("params") or ""
        desc = f"Execute shell command: {cmd}" if cmd else "Cursor IDE action"
        steps = [{
            "type": hook_type,
            "target": str(cmd),
            "description": desc,
            "code": str(raw_payload.get("code", "")),
            "args": args,
        }]

    payload = {
        "hook_type": hook_type,
        "command": raw_payload.get("command"),
        "args": raw_payload.get("args"),
        "steps": steps,
    }

    return EventCreate(
        source="cursor",
        event_type=hook_type,
        payload=payload,
        original_goal=goal,
    )
