"""
tests/test_intent_verification.py
────────────────────────────────────
Unit tests for Module 6 — Intent Verification Engine (policy/intent_verification.py).

Semantic-drift tests here are deterministic and OFFLINE: they never instantiate a
SentenceTransformer, never touch Hugging Face, and never access the network.
The semantic-backend boundary (app.policy.semantic_similarity.compute_embedding_drift)
is monkeypatched with fakes so the suite drives compute_semantic_drift() without
loading any model.

Increment 2C target contract verified here:
    compute_semantic_drift(goal_text, action_text)
        -> app.policy.semantic_similarity.compute_embedding_drift(goal_text, action_text)
"""
import pytest

from app.models.event import EventCreate
from app.policy import semantic_similarity
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


def _stub_backend_drift(monkeypatch, drift: float) -> None:
    """Install a deterministic fake semantic backend -- never loads a model."""
    monkeypatch.setattr(
        semantic_similarity,
        "compute_embedding_drift",
        lambda goal_text, action_text: float(drift),
    )


# ── compute_semantic_drift: delegation to the semantic backend ─────────────────

def test_compute_semantic_drift_delegates_to_semantic_backend(monkeypatch):
    """
    Contract: compute_semantic_drift(goal, action) must delegate to
    semantic_similarity.compute_embedding_drift(goal, action): the fake backend
    must be called exactly once, with both strings passed through unchanged, and
    the returned drift must match the backend value.
    """
    recorded: list[tuple[str, str]] = []

    def fake_backend(goal_text: str, action_text: str) -> float:
        recorded.append((goal_text, action_text))
        return 0.42

    monkeypatch.setattr(semantic_similarity, "compute_embedding_drift", fake_backend)

    result = compute_semantic_drift("the real goal", "the real action")

    assert recorded == [("the real goal", "the real action")]
    assert result == 0.42


@pytest.mark.parametrize(
    "goal_text,action_text",
    [
        ("", "some action text"),
        ("   ", "some action text"),
        ("\t", "some action text"),
        ("a stated goal", ""),
        ("a stated goal", "   "),
        ("", ""),
        ("  ", "  "),
    ],
)
def test_compute_semantic_drift_empty_inputs_return_one_and_do_not_call_backend(
    monkeypatch, goal_text, action_text
):
    """
    Contract: empty or whitespace goal/action inputs must short-circuit to full
    drift (1.0) and must NOT invoke the semantic backend at all.
    """
    calls = {"n": 0}

    def fake_backend(goal_text: str, action_text: str) -> float:
        calls["n"] += 1
        return 0.5

    monkeypatch.setattr(semantic_similarity, "compute_embedding_drift", fake_backend)

    drift = compute_semantic_drift(goal_text, action_text)

    assert drift == 1.0
    assert calls["n"] == 0, "semantic backend must not be called for empty inputs"


def test_compute_semantic_drift_clamps_backend_result_below_zero(monkeypatch):
    """A backend result below 0.0 is clamped to 0.0."""
    _stub_backend_drift(monkeypatch, -0.5)
    assert compute_semantic_drift("the goal", "the action") == 0.0


def test_compute_semantic_drift_clamps_backend_result_above_one(monkeypatch):
    """A backend result above 1.0 is clamped to 1.0."""
    _stub_backend_drift(monkeypatch, 1.7)
    assert compute_semantic_drift("the goal", "the action") == 1.0


def test_compute_semantic_drift_always_returns_float_in_bounds(monkeypatch):
    """compute_semantic_drift always returns a float inside [0.0, 1.0]."""
    _stub_backend_drift(monkeypatch, 0.6)
    result = compute_semantic_drift("the goal", "the action")
    assert isinstance(result, float)
    assert 0.0 <= result <= 1.0


# ── evaluate_intent: decision behaviour ────────────────────────────────────────

def test_aligned_action_allows(monkeypatch):
    _stub_backend_drift(monkeypatch, 0.05)
    event = _make_event(
        goal="Refactor the user authentication module to use JWT tokens.",
        action_description="Refactor user authentication token validation in auth module",
        action_target="src/auth.py",
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0


def test_unrelated_action_warns(monkeypatch):
    _stub_backend_drift(monkeypatch, 0.95)
    event = _make_event(
        goal="Refactor the user authentication module to use JWT tokens.",
        action_description="Delete all backup logs and drop analytics database table",
        action_target="/var/log/analytics.db",
    )
    decision = evaluate_intent(event)
    assert decision.verdict in ("WARN", "BLOCK")
    assert decision.suggested_fix.strip() != ""
    assert any("Intent drift" in r for r in decision.reasons)


def test_semantic_backend_failure_falls_back_to_lexical(monkeypatch):
    """
    Contract: when the semantic backend raises (model unavailable at request
    time), evaluate_intent must not propagate the exception, must return a valid
    DecisionCreate computed with the lexical (Jaccard) fallback, and must say so
    in at least one reason.
    """
    def broken_backend(goal_text: str, action_text: str) -> float:
        raise RuntimeError("semantic model unavailable")

    monkeypatch.setattr(semantic_similarity, "compute_embedding_drift", broken_backend)

    event = _make_event(
        goal="Refactor the user authentication module to use JWT tokens.",
        action_description="Refactor user authentication token validation in auth module",
        action_target="src/auth.py",
    )
    decision = evaluate_intent(event)
    assert decision.verdict in ("ALLOW", "WARN")
    assert decision.module == "intent_verification"
    assert any(
        "fallback" in r.lower() or "lexical" in r.lower() for r in decision.reasons
    ), f"expected a fallback/lexical reason, got: {decision.reasons}"


def test_empty_goal_handled_gracefully():
    event = _make_event(
        goal="",
        action_description="Some action",
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "ALLOW"
    assert "No original session goal provided" in decision.reasons[0]


# ── Semantic action-text construction ──────────────────────────────────────────

def test_semantic_action_text_excludes_event_type_and_target(monkeypatch):
    """
    Contract: the semantic action text sent to the backend must be the clean
    natural-language description, NOT polluted by the generic event_type
    (file_write) or the technical target path (src/auth.py) when a meaningful
    description exists. Records the exact goal/action passed to
    semantic_similarity.compute_embedding_drift.
    """
    recorded: list[tuple[str, str]] = []

    def recording_backend(goal_text: str, action_text: str) -> float:
        recorded.append((goal_text, action_text))
        return 0.275

    monkeypatch.setattr(semantic_similarity, "compute_embedding_drift", recording_backend)

    event = _make_event(
        goal="Fix the login issue",
        action_description="Repair the authentication problem",
        action_target="src/auth.py",
    )
    decision = evaluate_intent(event)

    # The backend was consulted exactly once, with the clean semantic text.
    assert len(recorded) == 1
    sent_goal, sent_action = recorded[0]
    assert sent_goal == "Fix the login issue"
    assert "Repair the authentication problem" in sent_action
    assert "file_write" not in sent_action
    assert "src/auth.py" not in sent_action

    # And that clean semantic evidence (drift 0.275 <= 0.38) aligns the action.
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0


def test_clean_semantic_text_allows_live_defect_scenario(monkeypatch):
    """
    Regression for the live verification defect: goal "Fix the login issue" vs
    clean description "Repair the authentication problem" (drift 0.275) must be
    ALLOW with risk 0.0, mirroring the polluted full-text WARN bug.
    """
    _stub_backend_drift(monkeypatch, 0.275)
    event = _make_event(
        goal="Fix the login issue",
        action_description="Repair the authentication problem",
        action_target="src/auth.py",
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0