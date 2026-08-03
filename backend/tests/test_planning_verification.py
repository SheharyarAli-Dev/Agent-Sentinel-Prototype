"""
tests/test_planning_verification.py
──────────────────────────────────────
Unit tests for Module 7 — Planning Verification Engine (policy/planning_verification.py).
"""
import pytest

from app.models.event import EventCreate
from app.policy.planning_verification import evaluate_plan


def _make_cursor_event(steps: list[dict]) -> EventCreate:
    return EventCreate(
        source="cursor",
        event_type="plan_execution",
        payload={"steps": steps},
        original_goal="Refactor the user authentication module.",
    )


def _make_n8n_event(steps: list[dict]) -> EventCreate:
    return EventCreate(
        source="n8n",
        event_type="workflow_execution",
        payload={"steps": steps},
        original_goal="Send weekly summary email to subscribers.",
    )


def test_clean_plan_allows():
    steps = [
        {"type": "file_write", "target": "src/auth.py", "description": "Refactor auth module"},
    ]
    decision = evaluate_plan(_make_cursor_event(steps))
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0


def test_protected_path_warns_or_blocks():
    steps = [
        {"type": "file_write", "target": ".env", "description": "Update env file"},
    ]
    decision = evaluate_plan(_make_cursor_event(steps))
    assert decision.verdict in ("WARN", "BLOCK")
    assert decision.suggested_fix.strip() != ""
    assert any("Protected resource" in r or ".env" in r for r in decision.reasons)


def test_destructive_command_blocks():
    steps = [
        {"type": "shell_command", "target": "rm -rf /src", "description": "Delete source files"},
    ]
    decision = evaluate_plan(_make_cursor_event(steps))
    assert decision.verdict == "BLOCK"
    assert decision.suggested_fix.strip() != ""
    assert any("Destructive pattern" in r for r in decision.reasons)


def test_over_scope_warns():
    steps = [
        {"type": "file_write", "target": f"src/file_{i}.py", "description": f"Edit file {i}"}
        for i in range(25)
    ]
    decision = evaluate_plan(_make_cursor_event(steps))
    assert decision.verdict in ("WARN", "BLOCK")
    assert decision.suggested_fix.strip() != ""


def test_step_contradiction_warns():
    steps = [
        {"type": "file_write", "target": "src/temp.py", "description": "Create temp file"},
        {"type": "file_delete", "target": "src/temp.py", "description": "Delete temp file"},
    ]
    decision = evaluate_plan(_make_cursor_event(steps))
    assert decision.verdict in ("WARN", "BLOCK")
    assert decision.suggested_fix.strip() != ""
    assert any("contradiction" in r for r in decision.reasons)


def test_bubble_sort_code_warns_for_cursor():
    steps = [
        {
            "type": "file_write",
            "target": "src/sort.py",
            "description": "Add sorting function",
            "code": """
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr
""",
        }
    ]
    decision = evaluate_plan(_make_cursor_event(steps))
    assert decision.verdict in ("WARN", "BLOCK")
    assert decision.suggested_fix.strip() != ""
    # Verify either the manual-sort or nested-loop pattern name appears in reasons
    assert any("manual-sort" in r or "nested-loop" in r or "sort" in r.lower() for r in decision.reasons)


def test_bubble_sort_code_ignored_for_n8n():
    """Code quality checks are not applied to n8n events."""
    steps = [
        {
            "type": "node_execution",
            "target": "SortNode",
            "description": "Sort results",
            "code": "def bubble_sort(arr): pass",
        }
    ]
    decision = evaluate_plan(_make_n8n_event(steps))
    assert decision.verdict == "ALLOW"
