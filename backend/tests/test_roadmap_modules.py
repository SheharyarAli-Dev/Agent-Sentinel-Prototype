"""Tests for the four roadmap-completion modules."""
from app.models.decision import DecisionCreate
from app.models.event import EventCreate
from app.policy.predictive_defence import evaluate_predictive_defence, reset as reset_pred
from app.policy.uncertainty import apply_uncertainty
from app.policy.feedback_learning import signature_for, record_feedback, apply_learning, _STORE
from app.policy.red_team import run_red_team


# ── Predictive Defence ──────────────────────────────────────────────────────────

def test_predictive_forecasts_developing_chain():
    reset_pred()
    e = EventCreate(source="cursor", event_type="read",
                    payload={"target": "read customer database records", "session_id": "p1"})
    d = evaluate_predictive_defence(e)
    assert d.verdict == "WARN"
    assert "forecast" in " ".join(d.reasons).lower() or "predict" in " ".join(d.reasons).lower()


def test_predictive_allows_benign():
    reset_pred()
    e = EventCreate(source="cursor", event_type="write",
                    payload={"target": "src/util.py", "session_id": "p2"})
    assert evaluate_predictive_defence(e).verdict == "ALLOW"


# ── Uncertainty ─────────────────────────────────────────────────────────────────

def _mk(v, r):
    return DecisionCreate(verdict=v, reasons=[v], suggested_fix="x" if v != "ALLOW" else "",
                          module="m", risk_score=r)


def test_uncertainty_escalates_borderline_lowconfidence_allow():
    # disagreement + borderline risk → escalate ALLOW to WARN
    results = [_mk("ALLOW", 0.4), _mk("WARN", 0.45), _mk("ALLOW", 0.4)]
    verdict, conf, note = apply_uncertainty(results, "ALLOW", 0.42)
    assert verdict == "WARN"
    assert note is not None


def test_uncertainty_keeps_confident_allow():
    results = [_mk("ALLOW", 0.0), _mk("ALLOW", 0.0)]
    verdict, conf, note = apply_uncertainty(results, "ALLOW", 0.0)
    assert verdict == "ALLOW"
    assert conf > 0.6


# ── Feedback Learning ───────────────────────────────────────────────────────────

def test_feedback_auto_clears_after_three_approvals():
    _STORE.clear()
    e = EventCreate(source="cursor", event_type="deploy",
                    payload={"capability": "write_file", "command": "./deploy.sh --force"},
                    original_goal="deploy")
    sig = signature_for(e)
    assert apply_learning(e, "WARN")[0] == "WARN"       # no history yet
    for _ in range(3):
        record_feedback(sig, "approved")
    assert apply_learning(e, "WARN")[0] == "ALLOW"      # learned safe


def test_feedback_tightens_after_rejections():
    _STORE.clear()
    e = EventCreate(source="n8n", event_type="webhook",
                    payload={"capability": "http_request", "target": "http://x"},
                    original_goal="call")
    sig = signature_for(e)
    for _ in range(3):
        record_feedback(sig, "rejected")
    assert apply_learning(e, "ALLOW")[0] == "WARN"      # learned unsafe


# ── Red Team ────────────────────────────────────────────────────────────────────

def test_red_team_full_coverage():
    report = run_red_team()
    assert report["total_attacks"] >= 10
    # the shipped defenses should catch every catalogued attack
    assert report["coverage_pct"] == 100.0, f"gaps: {report['gaps']}"
