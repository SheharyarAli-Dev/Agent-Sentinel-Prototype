"""
Tests for:
  - Module 4 governance endpoints (audit + incident report)
  - Module 11 explanation presence on every decision
  - The intent-advisory fix: a valid transaction must resolve to ALLOW even
    when its wording differs from the goal (the bug that made ALLOW not work).
"""
import time

from fastapi.testclient import TestClient

from app.main import app
from app.models.event import EventCreate
from app.policy.intent_verification import evaluate_intent

client = TestClient(app)


def _valid_coffee():
    return {
        "source": "transaction",
        "event_type": "purchase",
        "payload": {
            "merchant_id": "MERCH_001",
            "merchant_name": "Good Beans Coffee",
            "amount": 4.50,
            "currency": "USD",
            "transaction_id": f"TXN_{time.time()}",
            "item": "Flat White",
        },
        "original_goal": "Order a coffee from a nearby shop.",
    }


# ── The ALLOW fix ───────────────────────────────────────────────────────────────

def test_valid_coffee_resolves_to_allow():
    """Regression test for the reported bug: valid coffee must be ALLOW."""
    resp = client.post("/api/evaluate", json=_valid_coffee())
    assert resp.status_code == 200
    assert resp.json()["decision"]["verdict"] == "ALLOW"


def test_intent_advisory_never_escalates_for_transactions():
    e = EventCreate(
        source="transaction",
        event_type="purchase",
        payload={"merchant_name": "Good Beans Coffee", "item": "Flat White"},
        original_goal="Order a coffee from a nearby shop.",
    )
    # advisory=True must never return WARN, even on low overlap.
    d = evaluate_intent(e, advisory=True)
    assert d.verdict == "ALLOW"


def test_intent_still_warns_for_cursor_drift():
    e = EventCreate(
        source="cursor",
        event_type="plan",
        payload={"steps": [{"target": "delete_all_user_accounts.py",
                            "description": "wipe database"}]},
        original_goal="Fix the typo in the README file.",
    )
    d = evaluate_intent(e, advisory=False)
    assert d.verdict == "WARN"


# ── Module 11 explanation ───────────────────────────────────────────────────────

def test_every_decision_has_explanation():
    resp = client.post("/api/evaluate", json=_valid_coffee())
    assert resp.json()["decision"]["explanation"].strip() != ""


# ── Module 4 governance ─────────────────────────────────────────────────────────

def test_audit_endpoint_returns_records():
    client.post("/api/evaluate", json=_valid_coffee())
    resp = client.get("/api/audit")
    assert resp.status_code == 200
    body = resp.json()
    assert "records" in body and body["count"] >= 1


def test_incident_report_structure():
    resp = client.get("/api/incident-report")
    assert resp.status_code == 200
    rep = resp.json()
    for key in ("total_decisions", "verdict_breakdown", "blocked_incidents"):
        assert key in rep
