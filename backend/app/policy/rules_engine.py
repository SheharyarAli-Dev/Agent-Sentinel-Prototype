"""
app/policy/rules_engine.py
───────────────────────────
Central orchestrator for the policy pipeline.

Responsibility: given a normalised EventCreate, decide which combination of
policy modules to invoke (based on event.source), aggregate their results,
and return a single DecisionCreate.

Module routing
──────────────
  cursor      → Module 7 (full: plan safety + code-quality patterns)
                Module 6 (intent drift)
  n8n         → Module 7 (plan safety only — no code-quality patterns)
                Module 6 (intent drift)
  transaction → Module 2 (ATTVE — merchant, invoice, limit checks)
                Module 6 (light / optional)

Aggregation rule (when multiple modules fire)
─────────────────────────────────────────────
  The final verdict is the *most severe* across all module results:
    BLOCK > WARN > ALLOW
  Risk scores are averaged.
  Reasons and suggested_fix strings from all modules are concatenated.
"""
from __future__ import annotations

from app.models.decision import DecisionCreate
from app.models.event import EventCreate


def evaluate_event(event: EventCreate) -> DecisionCreate:
    """
    Route the event to the appropriate policy modules and aggregate results.

    This is the single entry point called by the /evaluate endpoint.
    """
    results: list[DecisionCreate] = []

    if event.source == "transaction":
        results.extend(_run_transaction_modules(event))
    elif event.source == "cursor":
        results.extend(_run_cursor_modules(event))
    elif event.source == "n8n":
        results.extend(_run_n8n_modules(event))
    else:
        # Unrecognised source — pass through with a low risk score.
        results.append(
            DecisionCreate(
                verdict="ALLOW",
                reasons=["Unrecognised source; no policy rules applied."],
                suggested_fix="",
                module="rules_engine",
                risk_score=0.0,
            )
        )

    return _aggregate(results)


# ── Per-source routing ─────────────────────────────────────────────────────────

def _run_transaction_modules(event: EventCreate) -> list[DecisionCreate]:
    from app.policy.attve import evaluate_transaction
    from app.policy.intent_verification import evaluate_intent

    results: list[DecisionCreate] = []
    results.append(evaluate_transaction(event))

    # Module 6 is optional/light for transactions — only run if original_goal set.
    if event.original_goal:
        results.append(evaluate_intent(event))

    return results


def _run_cursor_modules(event: EventCreate) -> list[DecisionCreate]:
    from app.policy.planning_verification import evaluate_plan
    from app.policy.intent_verification import evaluate_intent

    results: list[DecisionCreate] = []
    results.append(evaluate_plan(event))

    if event.original_goal:
        results.append(evaluate_intent(event))

    return results


def _run_n8n_modules(event: EventCreate) -> list[DecisionCreate]:
    from app.policy.planning_verification import evaluate_plan
    from app.policy.intent_verification import evaluate_intent

    results: list[DecisionCreate] = []
    # For n8n, planning_verification skips the code-quality sub-module
    # (handled internally by evaluate_plan based on event.source).
    results.append(evaluate_plan(event))

    if event.original_goal:
        results.append(evaluate_intent(event))

    return results


# ── Aggregation ────────────────────────────────────────────────────────────────

_VERDICT_SEVERITY: dict[str, int] = {"ALLOW": 0, "WARN": 1, "BLOCK": 2}


def _aggregate(results: list[DecisionCreate]) -> DecisionCreate:
    """
    Merge multiple module results into a single authoritative Decision.
    """
    if not results:
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["No policy modules produced a result."],
            suggested_fix="",
            module="rules_engine",
            risk_score=0.0,
        )

    # Determine worst verdict.
    worst = max(results, key=lambda d: _VERDICT_SEVERITY[d.verdict])
    final_verdict = worst.verdict

    # Collect all reasons.
    all_reasons: list[str] = []
    for r in results:
        all_reasons.extend(r.reasons)

    # Concatenate non-empty suggested_fix strings.
    fixes = [r.suggested_fix for r in results if r.suggested_fix.strip()]
    combined_fix = "  |  ".join(fixes)

    # Average risk scores.
    avg_score = sum(r.risk_score for r in results) / len(results)

    # Modules that contributed.
    modules = ", ".join(dict.fromkeys(r.module for r in results))

    return DecisionCreate(
        verdict=final_verdict,
        reasons=all_reasons,
        suggested_fix=combined_fix,
        module=modules,
        risk_score=round(avg_score, 4),
    )
