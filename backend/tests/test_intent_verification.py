"""
tests/test_intent_verification.py
────────────────────────────────────
Unit tests for Module 6 — Intent Verification Engine (policy/intent_verification.py).
"""
import pytest

from app.models.event import EventCreate
from app.policy.intent_verification import evaluate_intent, compute_semantic_drift


def _make_event(goal: str, action_description: str, action_target: str = "src/auth.py") -> EventCreate:
    return EventCreate(
        source="cursor",
        event_type="file_write",
        payload={
            "description": action_description,
            "target": action_target,
        },
        original_goal=goal,
    )


def test_compute_semantic_drift_returns_drift_score():
    """
    Contract: compute_semantic_drift() must return a float drift score in
    [0.0, 1.0] (0.0 = strongly aligned, 1.0 = fully drifted) and must no longer
    raise NotImplementedError.
    """
    drift = compute_semantic_drift("order a coffee", "buy a cappuccino")
    assert isinstance(drift, float)
    assert 0.0 <= drift <= 1.0


def test_compute_semantic_drift_empty_goal_is_full_drift():
    """Contract: with an empty goal there is no alignment evidence; drift = 1.0."""
    assert compute_semantic_drift("", "buy a cappuccino") == 1.0


def test_compute_semantic_drift_empty_action_is_full_drift():
    """Contract: with an empty action there is no alignment evidence; drift = 1.0."""
    assert compute_semantic_drift("buy a cappuccino", "") == 1.0


def test_aligned_action_allows():
    event = _make_event(
        goal="Refactor the user authentication module to use JWT tokens.",
        action_description="Refactor user authentication token validation in auth module",
        action_target="src/auth.py",
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0


def test_unrelated_action_warns():
    event = _make_event(
        goal="Refactor the user authentication module to use JWT tokens.",
        action_description="Delete all backup logs and drop analytics database table",
        action_target="/var/log/analytics.db",
    )
    decision = evaluate_intent(event)
    assert decision.verdict in ("WARN", "BLOCK")
    assert decision.suggested_fix.strip() != ""
    assert any("Intent drift" in r for r in decision.reasons)


def test_empty_goal_handled_gracefully():
    event = _make_event(
        goal="",
        action_description="Some action",
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "ALLOW"
    assert "No original session goal provided" in decision.reasons[0]
