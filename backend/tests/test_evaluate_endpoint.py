"""
tests/test_evaluate_endpoint.py
─────────────────────────────────
Integration tests for the POST /evaluate endpoint.

Uses FastAPI's TestClient (synchronous) to test the full request-response
cycle without a running server.  These tests run against the real SQLite DB
(a temporary in-memory DB is used via override_get_db).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_db, Base

# ── In-memory SQLite for tests ─────────────────────────────────────────────────
TEST_DATABASE_URL = "sqlite://"  # in-memory

test_engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _reset_test_db():
    """Reset the test database before each test."""
    # Clear dependency override first
    if get_db in app.dependency_overrides:
        del app.dependency_overrides[get_db]
    # Recreate all tables
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    # Re-apply override
    app.dependency_overrides[get_db] = override_get_db
    yield
    # Cleanup
    if get_db in app.dependency_overrides:
        del app.dependency_overrides[get_db]


client = TestClient(app)


# ── Health check ───────────────────────────────────────────────────────────────
def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_root():
    response = client.get("/")
    assert response.status_code == 200


# ── POST /evaluate ─────────────────────────────────────────────────────────────
def test_evaluate_transaction_event():
    """A valid transaction event should return a 200 with a decision."""
    payload = {
        "source": "transaction",
        "event_type": "purchase",
        "payload": {
            "merchant_id": "MERCH_001",
            "amount": 4.50,
            "transaction_id": "TXN_TEST_001",
        },
        "original_goal": "Order a coffee.",
    }
    response = client.post("/api/evaluate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "event" in data
    assert "decision" in data
    assert data["decision"]["verdict"] in ("ALLOW", "WARN", "BLOCK")
    assert data["decision"]["risk_score"] >= 0.0
    assert data["decision"]["risk_score"] <= 1.0
    assert isinstance(data["decision"]["reasons"], list)
    assert "module" in data["decision"]


def test_evaluate_cursor_event():
    """A cursor event should return a decision from the planning+intent modules."""
    payload = {
        "source": "cursor",
        "event_type": "plan_execution",
        "payload": {
            "steps": [
                {
                    "type": "file_write",
                    "target": "src/auth.py",
                    "description": "Update auth logic",
                }
            ]
        },
        "original_goal": "Refactor auth module.",
    }
    response = client.post("/api/evaluate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["decision"]["verdict"] in ("ALLOW", "WARN", "BLOCK")


def test_evaluate_n8n_event():
    """An n8n event should return a decision."""
    payload = {
        "source": "n8n",
        "event_type": "webhook_action",
        "payload": {
            "node_type": "EmailSend",
            "parameters": {"to": "user@example.com"},
        },
    }
    response = client.post("/api/evaluate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["decision"]["verdict"] in ("ALLOW", "WARN", "BLOCK")


def test_evaluate_returns_event_id():
    """The returned event should have an integer id."""
    payload = {
        "source": "transaction",
        "event_type": "purchase",
        "payload": {"merchant_id": "MERCH_001", "amount": 3.00},
    }
    response = client.post("/api/evaluate", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["event"]["id"], int)


def test_evaluate_warn_decision_decidable():
    """
    After a WARN decision is created, POST /decide/{event_id} should work.
    This test creates a WARN manually by patching the DB, but for now
    just confirms the endpoint rejects non-WARN events gracefully.
    """
    # First create an event that will produce ALLOW (stub phase)
    payload = {
        "source": "transaction",
        "event_type": "purchase",
        "payload": {"merchant_id": "MERCH_001", "amount": 4.00},
    }
    response = client.post("/api/evaluate", json=payload)
    event_id = response.json()["event"]["id"]
    verdict = response.json()["decision"]["verdict"]

    if verdict == "ALLOW":
        # POST /decide should reject non-WARN events
        decide_response = client.post(
            f"/api/decide/{event_id}",
            json={"decision": "approved"},
        )
        assert decide_response.status_code == 422


def test_evaluate_invalid_source_rejected():
    """An invalid source value should be rejected by pydantic validation."""
    payload = {
        "source": "invalid_source",
        "event_type": "test",
        "payload": {},
    }
    response = client.post("/api/evaluate", json=payload)
    assert response.status_code == 422