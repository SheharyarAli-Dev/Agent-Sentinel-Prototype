"""
tests/test_operation_outcome.py
────────────────────────────────
Focused regression tests for operation.py outcome verification defects.

Pre-fix, these tests expose:
  1. verify_outcome() crashes with NameError when expected_outcome is provided
     (undefined `target` variable used before assignment).
  2. A duplicate ExpectedOutcome class definition exists in operation.py,
     which also causes build_canonical_action to raise ValidationError
     when expected_outcome is present (type mismatch between the two classes).

Post-fix, these tests confirm:
  1. Successful dev-VM stop returns VERIFIED with correct observed state.
  2. Protected-invariant violation returns MISMATCH with violation details.
  3. Missing expected-outcome returns OUTCOME_UNKNOWN with clear reason.
  4. Exactly one ExpectedOutcome class exists in operation.py source.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.event import EventORM
from app.models.decision import DecisionORM
from app.models.operation import (
    OperationORM,
    ExpectedOutcome,
    OutcomeVerificationResult,
    VerificationStatus,
    verify_outcome,
)
from app.sandbox.simulated_cloud import SimulatedCloud

_SEED = Path(__file__).resolve().parent.parent / "data" / "simulated_cloud_seed.json"
_OPERATION_PY = Path(__file__).resolve().parent.parent / "app" / "models" / "operation.py"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test_outcome.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    sf = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return sf


def _build_canonical_json(payload_dict):
    """
    Build a canonical action JSON string directly, bypassing
    build_canonical_action() which is broken by the duplicate class.
    This isolates verify_outcome for focused testing.
    """
    canonical = {
        "source": "liveops",
        "agent_identity": "liveops-default",
        "action_type": payload_dict.get("tool", "stop_vm"),
        "target": payload_dict.get("target"),
        "normalized_parameters": {
            k: v for k, v in payload_dict.items()
            if k not in ("session_id", "request_id", "transaction_id",
                         "timestamp", "nonce", "correlation_id")
            and v is not None
        },
        "original_goal": "Test outcome verification",
        "expected_effect": payload_dict.get("description", ""),
    }
    if "expected_outcome" in payload_dict:
        canonical["expected_outcome"] = payload_dict["expected_outcome"]
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _insert_event_and_operation(session_factory, payload_dict, *,
                                 expected_outcome_dict=None):
    """Insert event + decision + operation with canonical_action_json. Returns (event_id, operation)."""
    full_payload = dict(payload_dict)
    if expected_outcome_dict is not None:
        full_payload["expected_outcome"] = expected_outcome_dict

    db = session_factory()
    try:
        ev = EventORM(
            source="liveops",
            event_type=payload_dict.get("tool", "stop_vm"),
            payload=json.dumps(payload_dict),
            original_goal="Test outcome verification",
            timestamp=datetime.now(timezone.utc),
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
        event_id = ev.id

        dec = DecisionORM(
            event_id=event_id,
            verdict="ALLOW",
            reasons="[]",
            suggested_fix="",
            module="test",
            risk_score=0.0,
            explanation="",
            latency_ms=1.0,
            timestamp=datetime.now(timezone.utc),
        )
        db.add(dec)
        db.commit()

        canonical_json = _build_canonical_json(full_payload)
        import hashlib
        fingerprint = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()

        op = OperationORM(
            operation_id=f"op-test-{event_id}",
            source="liveops",
            event_id=event_id,
            canonical_action_json=canonical_json,
            action_fingerprint=fingerprint,
            lifecycle_state="executed",
        )
        db.add(op)
        db.commit()
        db.refresh(op)
        return event_id, op
    finally:
        db.close()


def _build_execution_record(session_factory, event_id, *, status="executed"):
    from app.models.liveops_execution import LiveOpsExecutionORM
    db = session_factory()
    try:
        ledger = LiveOpsExecutionORM(
            event_id=event_id,
            tool="stop_vm",
            target="dev-unused-01",
            status=status,
            result=json.dumps({"tool": "stop_vm", "target": "dev-unused-01"}),
            executed_at=datetime.now(timezone.utc) if status == "executed" else None,
            created_at=datetime.now(timezone.utc),
        )
        db.add(ledger)
        db.commit()
        db.refresh(ledger)
        return ledger
    finally:
        db.close()


# ── Test 1: Successful dev-VM stop → VERIFIED ──────────────────────────────

class TestSuccessfulDevStopOutcome:
    """
    A successful development-VM stop with a matching expected_outcome must
    return VERIFIED with the correct observed state.

    Pre-fix: verify_outcome() crashes with NameError because `target` is used
    at line 578 before it is assigned at line 640.
    """

    def test_returns_verified_without_exception(self, tmp_path):
        session_factory = _make_db(tmp_path)
        cloud = SimulatedCloud(_SEED, tmp_path / "runtime_state.json")

        expected_outcome = {
            "target_resource": "dev-unused-01",
            "allowed_state_transition": "running -> stopped",
            "expected_final_state": {"state": "stopped"},
        }

        payload = {
            "tool": "stop_vm",
            "target": "dev-unused-01",
            "description": "Stop dev VM",
            "session_id": "test-session",
        }

        event_id, operation = _insert_event_and_operation(
            session_factory, payload, expected_outcome_dict=expected_outcome,
        )

        cloud.stop_vm("dev-unused-01")

        execution_record = _build_execution_record(session_factory, event_id)

        # Pre-fix: this raises NameError: name 'target' is not defined
        result = verify_outcome(
            db=session_factory(),
            operation=operation,
            cloud=cloud,
            execution_record=execution_record,
        )

        assert isinstance(result, OutcomeVerificationResult)
        assert result.status == VerificationStatus.VERIFIED
        assert result.observed_state is not None
        assert result.observed_state["target"] == "dev-unused-01"
        assert result.observed_state["state"] == "stopped"
        assert result.invariant_violations == []
        assert result.unexpected_mutations == []


# ── Test 2: Protected-invariant violation → MISMATCH ────────────────────────

class TestProtectedInvariantViolation:
    """
    When a declared protected invariant is violated by the observed state,
    verify_outcome must return MISMATCH with the violation listed.

    How the violation is produced:
    - expected_outcome declares protected_invariants: ["protected"]
      meaning the resource must have protected=True.
    - The cloud state is manipulated so that the target resource
      (prod-api-01) has protected=False (its natural seed value is True,
      but we overwrite it to False in the runtime state).
    - verify_outcome compares the observed protected flag against the
      invariant and reports the mismatch.

    Pre-fix: verify_outcome() crashes with NameError before reaching
    the invariant check.
    """

    def test_returns_mismatch_with_violation_detail(self, tmp_path):
        session_factory = _make_db(tmp_path)
        cloud = SimulatedCloud(_SEED, tmp_path / "runtime_state.json")

        # Tamper the cloud state: set prod-api-01 protected=False
        state = cloud.get_state()
        for vm in state["vms"]:
            if vm["id"] == "prod-api-01":
                vm["protected"] = False
        import json as _json
        state_path = tmp_path / "runtime_state.json"
        state_path.write_text(_json.dumps(state, indent=2), encoding="utf-8")
        cloud = SimulatedCloud(_SEED, state_path)

        expected_outcome = {
            "target_resource": "prod-api-01",
            "protected_invariants": ["protected"],
            "expected_final_state": {"state": "running"},
        }

        payload = {
            "tool": "stop_vm",
            "target": "prod-api-01",
            "description": "Stop prod VM",
            "session_id": "test-session",
        }

        event_id, operation = _insert_event_and_operation(
            session_factory, payload, expected_outcome_dict=expected_outcome,
        )

        execution_record = _build_execution_record(
            session_factory, event_id, status="executed",
        )

        # Pre-fix: this raises NameError: name 'target' is not defined
        result = verify_outcome(
            db=session_factory(),
            operation=operation,
            cloud=cloud,
            execution_record=execution_record,
        )

        assert isinstance(result, OutcomeVerificationResult)
        assert result.status == VerificationStatus.MISMATCH
        assert len(result.invariant_violations) > 0
        assert any("protected" in v.lower() for v in result.invariant_violations)


# ── Test 3: Missing expected-outcome → OUTCOME_UNKNOWN ──────────────────────

class TestMissingExpectedOutcome:
    """
    When no expected_outcome is defined on the canonical action,
    verify_outcome must return OUTCOME_UNKNOWN with a clear reason
    identifying the missing information.
    """

    def test_returns_outcome_unknown_with_reason(self, tmp_path):
        session_factory = _make_db(tmp_path)
        cloud = SimulatedCloud(_SEED, tmp_path / "runtime_state.json")

        payload = {
            "tool": "stop_vm",
            "target": "dev-unused-01",
            "description": "Stop dev VM",
            "session_id": "test-session",
        }

        event_id, operation = _insert_event_and_operation(
            session_factory, payload, expected_outcome_dict=None,
        )

        execution_record = _build_execution_record(session_factory, event_id)

        result = verify_outcome(
            db=session_factory(),
            operation=operation,
            cloud=cloud,
            execution_record=execution_record,
        )

        assert isinstance(result, OutcomeVerificationResult)
        assert result.status == VerificationStatus.OUTCOME_UNKNOWN
        assert result.expected_outcome is None
        assert any("no expected outcome" in r.lower() for r in result.invariant_violations)


# ── Test 4: Exactly one ExpectedOutcome class in source ────────────────────

class TestSingleExpectedOutcomeClass:
    """
    operation.py must contain exactly one class definition named
    'ExpectedOutcome'. A normal Python import cannot detect duplication
    because the later definition silently replaces the earlier one.
    This test parses the source AST to verify exactly one definition exists.
    """

    def test_source_contains_exactly_one_expected_outcome_class(self):
        source_text = _OPERATION_PY.read_text(encoding="utf-8")
        tree = ast.parse(source_text)

        class_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "ExpectedOutcome"
        ]

        assert len(class_defs) == 1, (
            f"Expected exactly one ExpectedOutcome class definition in "
            f"operation.py, found {len(class_defs)}: "
            f"{[f'line {d.lineno}' for d in class_defs]}"
        )

    def test_canonical_action_references_expected_outcome(self):
        from app.models.operation import CanonicalAction
        field = CanonicalAction.model_fields.get("expected_outcome")
        assert field is not None, "CanonicalAction must have an expected_outcome field"

    def test_outcome_verification_result_references_expected_outcome(self):
        from app.models.operation import OutcomeVerificationResult
        field = OutcomeVerificationResult.model_fields.get("expected_outcome")
        assert field is not None, "OutcomeVerificationResult must have an expected_outcome field"
