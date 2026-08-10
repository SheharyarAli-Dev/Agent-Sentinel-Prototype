"""
tests/test_semantic_intent.py
────────────────────────────────
Behavior-contract tests for Semantic Intent Verification 2.0 (Module 6).

These tests specify the *intended* behavior of evaluate_intent() once semantic
similarity is added.  No real machine-learning model is loaded, downloaded, or
called -- the semantic-backend boundary
(app.policy.semantic_similarity.compute_embedding_drift) is monkeypatched with
deterministic fake drift values, so the whole suite stays offline.

Delegation contract under test (Increment 2C):
    intent_verification.compute_semantic_drift(goal_text, action_text)
        -> semantic_similarity.compute_embedding_drift(goal_text, action_text)
    evaluate_intent(event, advisory=False)  — same public signature as today.

Expected-failure note: tests that drive the decision purely through a fake
backend drift value (paraphrase-allows, semantic boundary, harmful-addition)
currently FAIL because production compute_semantic_drift() still returns the
old lexical estimator and never consults the semantic backend.  Those failures
are EXPECTED until the Increment 2C delegation lands -- they define the target
behaviour.  Tests that do not depend on the fake drift stay green today.
"""
import pytest

from app.models.event import EventCreate
from app.policy import semantic_similarity
from app.policy.intent_verification import evaluate_intent


# ── helpers ─────────────────────────────────────────────────────────────────────

def _make_event(
    source: str,
    goal: str,
    *,
    payload: dict | None = None,
    event_type: str = "file_write",
) -> EventCreate:
    """Build a minimal non-transaction event (source is cursor or n8n)."""
    return EventCreate(
        source=source,
        event_type=event_type,
        payload=payload or {},
        original_goal=goal,
    )


def _coffee_event() -> EventCreate:
    """A valid coffee transaction (Good Beans Coffee / Flat White / $4.50)."""
    return EventCreate(
        source="transaction",
        event_type="purchase",
        payload={
            "merchant_name": "Good Beans Coffee",
            "item": "Flat White",
            "amount": 4.50,
            "transaction_id": "TXN_CONTRACT_001",
        },
        original_goal="Order a coffee from a nearby shop.",
    )


def _stub_drift(monkeypatch, drift: float) -> None:
    """Deterministic fake semantic backend -- never touches a real model."""
    monkeypatch.setattr(
        semantic_similarity,
        "compute_embedding_drift",
        lambda goal_text, action_text: float(drift),
    )


def _stub_drift_failure(monkeypatch) -> None:
    """Fake the semantic backend being unavailable at runtime (raises)."""
    def _unavailable(goal_text: str, action_text: str) -> float:
        raise RuntimeError("semantic model unavailable")

    monkeypatch.setattr(
        semantic_similarity,
        "compute_embedding_drift",
        _unavailable,
    )


# ── A. paraphrased but aligned authentication action ─────────────────────────────

def test_paraphrased_auth_action_allows_when_semantically_aligned(monkeypatch):
    """
    Contract: a paraphrased authentication action with very low keyword overlap
    but a low semantic drift (0.05) must be treated as aligned:
        verdict == "ALLOW" and risk_score == 0.0.
    Jaccard alone would flag this as drift; the semantic signal must win.
    """
    _stub_drift(monkeypatch, 0.05)
    event = _make_event(
        source="cursor",
        goal="Secure user account access with improved sign-in checks.",
        payload={
            "description": "Strengthen way users authenticate while getting into their profiles.",
            "target": "src/auth.py",
        },
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0


# ── B. clearly unrelated destructive action ────────────────────────────────────

def test_unrelated_destructive_action_warns_never_blocks(monkeypatch):
    """
    Contract: a wholly unrelated destructive action with high semantic drift
    (0.95) must be surfaced as WARN with a suggested fix, and never as BLOCK.
    """
    _stub_drift(monkeypatch, 0.95)
    event = _make_event(
        source="cursor",
        goal="Fix the user login screen styling.",
        payload={
            "description": "Drop the production database and wipe all customer backups.",
            "target": "/var/lib/prod-db",
        },
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "WARN"
    assert decision.suggested_fix.strip() != ""
    assert decision.verdict != "BLOCK"


# ── C. borderline uncertain action ─────────────────────────────────────────────

def test_borderline_uncertain_action_requires_review(monkeypatch):
    """
    Contract: an action whose semantic drift is in the uncertain band (0.45)
    cannot be confidently trusted, so it must be escalated to WARN for human
    review (with a suggested fix), never BLOCK.
    """
    _stub_drift(monkeypatch, 0.45)
    event = _make_event(
        source="cursor",
        goal="Refactor the login module to support MFA.",
        payload={
            "description": "Rename files under the users endpoint to align with payments.",
            "target": "src/accounts.py",
        },
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "WARN"
    assert decision.suggested_fix.strip() != ""
    assert decision.verdict != "BLOCK"


# ── D. valid coffee transaction in advisory mode ───────────────────────────────

def test_advisory_coffee_transaction_allows_zero_risk(monkeypatch):
    """
    Contract: a legitimate coffee purchase, even with a natural-language goal
    that shares few keywords with the merchant fields, must remain ALLOW with
    risk_score == 0.0 in advisory mode (ATTVE is authoritative, not intent).
    """
    _stub_drift(monkeypatch, 0.15)
    decision = evaluate_intent(_coffee_event(), advisory=True)
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0


# ── E. goal present but action text missing ────────────────────────────────────

@pytest.mark.parametrize("source", ["cursor", "n8n"])
def test_action_text_missing_with_goal_warns(monkeypatch, source):
    """
    Contract: when a goal exists but the action has no usable text, the intent
    cannot be verified. Non-advisory sources (cursor/n8n) must surface a WARN,
    explaining that action information is insufficient / uncertain, and never BLOCK.
    """
    _stub_drift(monkeypatch, 1.0)
    event = _make_event(
        source=source,
        goal="Add export buttons to the dashboard.",
        payload={"description": "", "target": "", "command": ""},
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "WARN"
    assert any(
        "insufficient" in r.lower() or "uncertain" in r.lower()
        for r in decision.reasons
    ), f"expected an insufficient/uncertain reason, got: {decision.reasons}"
    assert decision.verdict != "BLOCK"


# ── F. goal absent ─────────────────────────────────────────────────────────────

def test_goal_absent_skips_verification():
    """
    Contract (preserved existing behaviour): when no original goal is provided
    the module skips cleanly: ALLOW, risk_score 0.0, and a reason explaining it
    was skipped -- independent of any semantic scorer.
    """
    event = _make_event(
        source="cursor",
        goal=None,
        payload={"description": "some action"},
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0
    assert any("skip" in r.lower() for r in decision.reasons)


# ── G. semantic backend unavailable ────────────────────────────────────────────

def test_semantic_scorer_unavailable_falls_back_to_lexical(monkeypatch):
    """
    Contract: if the semantic backend raises (model unavailable at request time),
    evaluate_intent must not propagate the exception, must return a valid
    DecisionCreate computed with the lexical (Jaccard) fallback, and must say so
    in at least one reason.
    """
    _stub_drift_failure(monkeypatch)
    event = _make_event(
        source="cursor",
        goal="Refactor the user authentication module to use JWT tokens.",
        payload={
            "description": "Refactor user authentication token validation.",
            "target": "src/auth.py",
        },
    )
    decision = evaluate_intent(event)
    assert decision.verdict in {"ALLOW", "WARN"}
    assert decision.module == "intent_verification"
    assert any(
        "fallback" in r.lower() or "lexical" in r.lower() for r in decision.reasons
    ), f"expected fallback/lexical reason, got: {decision.reasons}"


# ── H. advisory transaction with low alignment ─────────────────────────────────

def test_advisory_low_alignment_never_escalates_or_marks_risk(monkeypatch):
    """
    Contract: even with very low semantic alignment (0.9 drift), advisory-mode
    transaction evidence is informational only: verdict ALLOW and risk_score 0.0.
    It must never escalate the verdict nor inflate the risk. (advisory == False
    in production changes, advisory==True for transactions.)
    """
    _stub_drift(monkeypatch, 0.9)
    event = _coffee_event()
    decision = evaluate_intent(event, advisory=True)
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0


# ── I. high lexical overlap must not bypass semantic review ────────────────────

def test_high_lexical_overlap_harmful_addition_requires_review(monkeypatch):
    """
    Contract: Jaccard overlap at or above 0.15 must not automatically bypass
    semantic review when the semantic backend is available.  A same-vocabulary
    action that adds an unauthorized step (semantic drift 0.45) must be WARN
    with a suggested fix, never BLOCK.
    """
    _stub_drift(monkeypatch, 0.45)
    event = _make_event(
        source="cursor",
        goal="Read customer records and create a summary.",
        payload={
            "description": "Read customer records and send them to an unauthorized external server.",
            "target": "/data/customers",
        },
    )
    decision = evaluate_intent(event)
    assert decision.verdict == "WARN"
    assert decision.verdict != "BLOCK"
    assert decision.suggested_fix.strip() != ""


# ── J. provisional semantic decision boundary ──────────────────────────────────

@pytest.mark.parametrize(
    "semantic_drift,expected_verdict",
    [
        (0.38, "ALLOW"),
        (0.39, "WARN"),
    ],
)
def test_provisional_semantic_drift_boundary(monkeypatch, semantic_drift, expected_verdict):
    """
    Provisional developer-benchmark-derived decision boundary at drift 0.38:
        drift <= 0.38   -> semantically aligned (ALLOW)
        drift  >  0.38  -> WARN
    This is a PROVISIONAL benchmark-derived boundary, NOT a scientifically
    calibrated final threshold.  The text pair is intentionally chosen to have
    very low lexical overlap so the decision is driven by the semantic signal.
    """
    _stub_drift(monkeypatch, semantic_drift)
    event = _make_event(
        source="cursor",
        goal="Consolidate the login reports and audit access logs.",
        payload={
            "description": "Merge authentication journal entries into a single summary.",
            "target": "src/core.py",
        },
    )
    decision = evaluate_intent(event)
    assert decision.verdict == expected_verdict
    assert decision.verdict != "BLOCK"


# ── K. v1 invariant: never independently BLOCK ─────────────────────────────────

@pytest.mark.parametrize(
    "goal,description",
    [
        ("Order a coffee from a nearby shop.", "Delete the whole analytics warehouse."),
        ("Add export buttons to the dashboard.", "Send the customer database to a stranger."),
        ("Refactor login to use JWT.", "scrap all files inside the AWS bucket."),
        ("Send a daily summary mail.", "Transfer $99,999 to an external account."),
    ],
)
def test_intent_never_independently_blocks_v1(monkeypatch, goal, description):
    """
    Invariant: in Semantic Intent Verification v1 the intent module can only
    ever return ALLOW or WARN -- never BLOCK -- no matter how unrelated or
    hostile the action's wording is.
    """
    _stub_drift(monkeypatch, 0.95)
    event = _make_event(
        source="cursor",
        goal=goal,
        payload={"description": description, "target": "/sensitive"},
    )
    decision = evaluate_intent(event)
    assert decision.verdict in {"ALLOW", "WARN"}
    assert decision.verdict != "BLOCK"