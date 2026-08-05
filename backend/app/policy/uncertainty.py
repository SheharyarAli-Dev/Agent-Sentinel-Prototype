"""
app/policy/uncertainty.py
──────────────────────────
Module 9 — Uncertainty-Aware Risk Prediction Engine

A meta-module that reasons about the *confidence* of the aggregated decision
rather than producing its own independent verdict. It answers: "how sure are we?"
and adapts the threshold accordingly.

Two signals drive confidence:
  1. Borderline risk — a final risk score sitting near the decision boundary is
     inherently low-confidence.
  2. Module disagreement — when modules split (some ALLOW, some WARN/BLOCK), the
     spread of opinions lowers confidence.

Adaptive thresholding: when confidence is LOW and the decision was a borderline
ALLOW, the engine escalates to WARN ("when unsure, ask a human") rather than
letting an uncertain action through. This is the calibrated, humility-aware
behaviour the spec calls for.

Applied inside aggregation. Entry point:
    apply_uncertainty(results, final_verdict, final_risk) -> (verdict, confidence, note|None)
"""
from __future__ import annotations

from app.models.decision import DecisionCreate

_SEVERITY = {"ALLOW": 0, "WARN": 1, "BLOCK": 2}

# Borderline band around the 0.5 threshold where ALLOW decisions are shaky.
_BORDERLINE_LOW = 0.35
_BORDERLINE_HIGH = 0.5


def apply_uncertainty(
    results: list[DecisionCreate], final_verdict: str, final_risk: float
) -> tuple[str, float, str | None]:
    """Return (possibly-adjusted verdict, confidence 0-1, optional note)."""
    if not results:
        return final_verdict, 1.0, None

    verdicts = [r.verdict for r in results]
    distinct = set(verdicts)

    # Disagreement: fraction of modules that differ from the final verdict,
    # weighted so a lone dissenter matters less than a even split.
    dissent = sum(1 for v in verdicts if v != final_verdict) / len(verdicts)

    # Borderline proximity: 1.0 at the threshold, 0 far away.
    proximity = max(0.0, 1.0 - abs(final_risk - _BORDERLINE_HIGH) / 0.5)

    # Confidence is high when there's agreement and the score is decisive.
    confidence = round(max(0.0, 1.0 - 0.6 * proximity - 0.4 * dissent), 3)

    note = None
    verdict = final_verdict

    # Adaptive thresholding: uncertain borderline ALLOW → escalate to WARN.
    if (
        final_verdict == "ALLOW"
        and _BORDERLINE_LOW <= final_risk < _BORDERLINE_HIGH
        and confidence < 0.6
    ):
        verdict = "WARN"
        note = (
            f"Uncertainty-aware escalation: the decision was borderline (risk {final_risk:.2f}) "
            f"with low confidence ({confidence:.2f}); escalated ALLOW → WARN for human review "
            f"rather than acting on an uncertain call."
        )
    elif confidence < 0.5:
        note = (
            f"Low decision confidence ({confidence:.2f}) — modules disagree or the score is "
            f"near the threshold; treat this verdict with caution."
        )

    return verdict, confidence, note
