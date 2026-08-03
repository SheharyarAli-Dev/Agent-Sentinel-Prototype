"""Tests for Module 1 — AI Policy Engine (policy/policy_engine.py)."""
from app.models.event import EventCreate
from app.policy.policy_engine import evaluate_policies


def _txn(amount, merchant_id="MERCH_001"):
    return EventCreate(
        source="transaction",
        event_type="purchase",
        payload={"merchant_id": merchant_id, "amount": amount, "item": "x"},
        original_goal="buy",
    )


def test_no_policy_matches_allows():
    d = evaluate_policies(_txn(10.0))
    assert d.verdict == "ALLOW"
    assert d.module == "policy_engine"


def test_hard_spend_ceiling_blocks():
    d = evaluate_policies(_txn(999.0))
    assert d.verdict == "BLOCK"
    assert any("POL-002" in r for r in d.reasons)
    assert d.suggested_fix  # non-empty for BLOCK


def test_blacklisted_merchant_blocks():
    d = evaluate_policies(_txn(5.0, merchant_id="MERCH_666"))
    assert d.verdict == "BLOCK"


def test_destructive_shell_pattern_blocks_cursor():
    e = EventCreate(
        source="cursor",
        event_type="plan",
        payload={"steps": [{"command": "rm -rf /", "description": "clean"}]},
        original_goal="clean temp",
    )
    d = evaluate_policies(e)
    assert d.verdict == "BLOCK"


def test_production_data_reference_warns():
    e = EventCreate(
        source="n8n",
        event_type="webhook",
        payload={"steps": [{"target": "update production-db customers"}]},
        original_goal="sync",
    )
    d = evaluate_policies(e)
    assert d.verdict == "WARN"


def test_policy_only_applies_to_declared_sources():
    # POL-003 destructive-shell applies to cursor/n8n, not transaction.
    e = EventCreate(
        source="transaction",
        event_type="purchase",
        payload={"merchant_id": "MERCH_001", "amount": 5.0, "item": "rm -rf / coffee"},
        original_goal="buy",
    )
    d = evaluate_policies(e)
    # Should NOT block on the destructive pattern for a transaction source.
    assert d.verdict == "ALLOW"
