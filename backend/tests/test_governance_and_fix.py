"""
Tests for:
  - Module 4 governance endpoints (audit + incident report)
  - Module 11 explanation presence on every decision
  - The intent-advisory fix: a valid transaction must resolve to ALLOW even
    when its wording differs from the goal (the bug that made ALLOW not work).
  - Review timeout / EXPIRED state for WARN decisions
"""
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.database import SessionLocal, engine
from app.models.decision import DecisionORM
from app.models.event import EventCreate, EventORM
from app.policy.attve import clear_seen_transactions
from app.policy.intent_verification import evaluate_intent

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_state():
    """Clear ATTVE state and truncate tables before each test for isolation."""
    clear_seen_transactions()
    # Truncate tables to ensure clean state
    from sqlalchemy import text
    with SessionLocal() as db:
        db.execute(text("DELETE FROM decisions"))
        db.execute(text("DELETE FROM events"))
        db.execute(text("DELETE FROM liveops_executions"))
        db.commit()


def _valid_coffee():
    return {
        "source": "transaction",
        "event_type": "purchase",
        "payload": {
            "merchant_id": "MERCH_001",
            "merchant_name": "Good Beans Coffee",
            "amount": 4.50,
            "currency": "USD",
            "transaction_id": f"TXN_{time.time()}_{uuid.uuid4().hex[:8]}",
            "item": "Flat White",
            "session_id": f"test_{time.time()}_{uuid.uuid4().hex[:8]}",
        },
        "original_goal": "Order a coffee from a nearby shop.",
    }


def _warn_coffee():
    """A coffee order that triggers WARN (over threshold)."""
    return {
        "source": "transaction",
        "event_type": "purchase",
        "payload": {
            "merchant_id": "MERCH_001",
            "merchant_name": "Good Beans Coffee",
            "amount": 75.00,  # over $50 limit -> WARN
            "currency": "USD",
            "transaction_id": f"TXN_WARN_{time.time()}_{uuid.uuid4().hex[:8]}",
            "item": "Coffee Catering",
            "session_id": f"test_{time.time()}_{uuid.uuid4().hex[:8]}",
        },
        "original_goal": "Order coffee for the team.",
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


# ── Review Timeout / EXPIRED State ──────────────────────────────────────────────

def test_warn_decision_has_review_expires_at():
    """A WARN decision should have review_expires_at set based on config."""
    resp = client.post("/api/evaluate", json=_warn_coffee())
    assert resp.status_code == 200
    decision = resp.json()["decision"]
    assert decision["verdict"] == "WARN"
    assert decision["review_expires_at"] is not None
    # Should be approximately review_timeout_seconds in the future
    # review_expires_at is stored as naive UTC in the API response
    expires = datetime.fromisoformat(decision["review_expires_at"]).replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = expires - now
    assert delta.total_seconds() > 0
    assert delta.total_seconds() <= settings.review_timeout_seconds + 5  # small buffer


def test_warn_decision_can_be_approved_before_expiry():
    """A WARN decision can be approved before the review expires."""
    resp = client.post("/api/evaluate", json=_warn_coffee())
    assert resp.status_code == 200
    event_id = resp.json()["event"]["id"]

    # Approve the WARN
    approve_resp = client.post(f"/api/decide/{event_id}", json={"decision": "approved"})
    assert approve_resp.status_code == 200
    # The endpoint returns the DecisionResponse directly
    decision = approve_resp.json()
    assert decision["human_decision"] == "approved"
    assert decision["verdict"] == "WARN"  # verdict stays WARN, human_decision changes


def test_expired_warn_rejects_late_approval():
    """An expired WARN decision (EXPIRED) rejects late approval with 410."""
    from app.database import SessionLocal
    from app.models.decision import DecisionORM

    # Create a WARN decision directly with an expired review_expires_at
    db = SessionLocal()
    try:
        from app.models.event import EventORM
        event = EventORM(
            source="transaction",
            event_type="purchase",
            payload='{"merchant_id": "MERCH_001", "amount": 75.0, "item": "Test"}',
            original_goal="Test expiry",
            timestamp=datetime.now(timezone.utc),
        )
        db.add(event)
        db.commit()
        db.refresh(event)

        expired_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        decision = DecisionORM(
            event_id=event.id,
            verdict="WARN",
            reasons='["Test reason"]',
            suggested_fix="Test fix",
            module="attve",
            risk_score=0.5,
            timestamp=datetime.now(timezone.utc),
            review_expires_at=expired_time,
        )
        db.add(decision)
        db.commit()
        db.refresh(decision)
        expired_event_id = event.id
    finally:
        db.close()

    # Try to approve the expired WARN - should get 410 GONE
    approve_resp = client.post(f"/api/decide/{expired_event_id}", json={"decision": "approved"})
    assert approve_resp.status_code == 410
    assert "expired" in approve_resp.json()["detail"].lower()


def test_expired_warn_rejects_late_rejection():
    """An expired WARN decision (EXPIRED) rejects late rejection with 410."""
    from app.database import SessionLocal
    from app.models.decision import DecisionORM

    db = SessionLocal()
    try:
        from app.models.event import EventORM
        event = EventORM(
            source="transaction",
            event_type="purchase",
            payload='{"merchant_id": "MERCH_001", "amount": 75.0, "item": "Test"}',
            original_goal="Test expiry",
            timestamp=datetime.now(timezone.utc),
        )
        db.add(event)
        db.commit()
        db.refresh(event)

        expired_time = datetime.now(timezone.utc) - timedelta(seconds=10)
        decision = DecisionORM(
            event_id=event.id,
            verdict="WARN",
            reasons='["Test reason"]',
            suggested_fix="Test fix",
            module="attve",
            risk_score=0.5,
            timestamp=datetime.now(timezone.utc),
            review_expires_at=expired_time,
        )
        db.add(decision)
        db.commit()
        db.refresh(decision)
        expired_event_id = event.id
    finally:
        db.close()

    # Try to reject the expired WARN - should get 410 GONE
    reject_resp = client.post(f"/api/decide/{expired_event_id}", json={"decision": "rejected"})
    assert reject_resp.status_code == 410
    assert "expired" in reject_resp.json()["detail"].lower()


def test_allow_decision_has_no_review_expires_at():
    """ALLOW decisions should not have review_expires_at."""
    resp = client.post("/api/evaluate", json=_valid_coffee())
    assert resp.status_code == 200
    decision = resp.json()["decision"]
    assert decision["verdict"] == "ALLOW"
    assert decision["review_expires_at"] is None


def test_block_decision_has_no_review_expires_at():
    """BLOCK decisions should not have review_expires_at."""
    # Use an untrusted merchant to trigger BLOCK
    block_coffee = _valid_coffee()
    block_coffee["payload"]["merchant_id"] = "MERCH_006"  # ShiftyCafe - untrusted
    block_coffee["payload"]["session_id"] = f"test_{time.time()}_{uuid.uuid4().hex[:8]}"

    resp = client.post("/api/evaluate", json=block_coffee)
    assert resp.status_code == 200
    decision = resp.json()["decision"]
    assert decision["verdict"] in ("WARN", "BLOCK")  # untrusted merchant -> WARN or BLOCK
    if decision["verdict"] == "BLOCK":
        assert decision["review_expires_at"] is None
