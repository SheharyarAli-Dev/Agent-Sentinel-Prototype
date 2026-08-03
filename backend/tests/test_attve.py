"""
tests/test_attve.py
────────────────────
Unit tests for Module 2 — ATTVE (policy/attve.py).
"""
import pytest

from app.models.event import EventCreate
from app.policy.attve import evaluate_transaction, clear_seen_transactions


@pytest.fixture(autouse=True)
def reset_seen_transactions():
    """Reset duplicate tracking before every test."""
    clear_seen_transactions()


def _make_event(
    merchant_id: str,
    amount: float,
    transaction_id: str = "TXN_001",
    merchant_name: str = "Test Merchant",
) -> EventCreate:
    return EventCreate(
        source="transaction",
        event_type="purchase",
        payload={
            "merchant_id": merchant_id,
            "merchant_name": merchant_name,
            "amount": amount,
            "currency": "USD",
            "transaction_id": transaction_id,
            "item": "Coffee",
        },
        original_goal="Order a coffee.",
    )


def test_valid_transaction_allows():
    """A well-formed transaction from a trusted merchant within limit should ALLOW."""
    event = _make_event(merchant_id="MERCH_001", amount=4.50, transaction_id="TXN_VALID_1")
    decision = evaluate_transaction(event)
    assert decision.verdict == "ALLOW"
    assert decision.risk_score == 0.0
    assert decision.suggested_fix == ""


def test_untrusted_merchant_warns_or_blocks():
    """An untrusted merchant should produce WARN or BLOCK with a non-empty suggested_fix."""
    event = _make_event(merchant_id="MERCH_006", amount=4.50, transaction_id="TXN_UNTRUSTED_1")
    decision = evaluate_transaction(event)
    assert decision.verdict in ("WARN", "BLOCK")
    assert decision.suggested_fix.strip() != ""
    assert "Untrusted merchant" in decision.reasons[0]


def test_tampered_invoice_blocks():
    """A negative or zero amount should BLOCK with a suggested_fix."""
    event_neg = _make_event(merchant_id="MERCH_001", amount=-1.00, transaction_id="TXN_NEG_1")
    decision_neg = evaluate_transaction(event_neg)
    assert decision_neg.verdict == "BLOCK"
    assert decision_neg.suggested_fix.strip() != ""

    event_zero = _make_event(merchant_id="MERCH_001", amount=0.00, transaction_id="TXN_ZERO_1")
    decision_zero = evaluate_transaction(event_zero)
    assert decision_zero.verdict == "BLOCK"
    assert decision_zero.suggested_fix.strip() != ""


def test_over_threshold_warns():
    """An amount exceeding the configured limit ($50) should WARN with a suggested_fix."""
    event = _make_event(merchant_id="MERCH_001", amount=75.00, transaction_id="TXN_HIGH_1")
    decision = evaluate_transaction(event)
    assert decision.verdict == "WARN"
    assert decision.suggested_fix.strip() != ""
    assert any("exceeds" in r for r in decision.reasons)


def test_duplicate_transaction_id_blocks():
    """First use of transaction ID passes, second attempt blocks with duplicate error."""
    event1 = _make_event(merchant_id="MERCH_001", amount=4.50, transaction_id="TXN_DUP_123")
    decision1 = evaluate_transaction(event1)
    assert decision1.verdict == "ALLOW"

    event2 = _make_event(merchant_id="MERCH_001", amount=4.50, transaction_id="TXN_DUP_123")
    decision2 = evaluate_transaction(event2)
    assert decision2.verdict == "BLOCK"
    assert decision2.suggested_fix.strip() != ""
    assert any("Duplicate transaction ID" in r for r in decision2.reasons)


def test_unknown_merchant_warns_or_blocks():
    """An unknown merchant ID should trigger WARN or BLOCK with suggested_fix."""
    event = _make_event(merchant_id="MERCH_UNKNOWN_999", amount=4.50, transaction_id="TXN_UNK_1")
    decision = evaluate_transaction(event)
    assert decision.verdict in ("WARN", "BLOCK")
    assert decision.suggested_fix.strip() != ""
    assert any("Unknown merchant" in r for r in decision.reasons)
