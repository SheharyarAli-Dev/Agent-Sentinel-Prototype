"""
app/policy/explainability.py
─────────────────────────────
Module 11 — Explainable Safety Reasoning Engine

Turns the aggregated, machine-oriented decision (verdict + reasons + module
list) into a single plain-language explanation an administrator can read and
trust: what was proposed, which check decided it, why, and what happens next.

This closes the "black-box" gap — instead of just a colour and a score, every
decision carries a causal, human-readable justification suitable for audit.

Entry point: build_explanation(event, results, final) -> str
"""
from __future__ import annotations

from app.models.decision import DecisionCreate
from app.models.event import EventCreate

_VERDICT_MEANING = {
    "ALLOW": "was permitted to execute automatically",
    "WARN": "was held for human review before execution",
    "BLOCK": "was blocked from executing",
}

_SOURCE_LABEL = {
    "transaction": "an autonomous financial transaction",
    "cursor": "a coding-agent action",
    "n8n": "an automation-workflow action",
}

# Friendly names for the module identifiers that can appear in a decision.
_MODULE_LABEL = {
    "policy_engine": "Policy Engine (Module 1)",
    "attve": "Transaction Trust / ATTVE (Module 2)",
    "intent_verification": "Intent Verification (Module 6)",
    "planning_verification": "Planning Verification (Module 7)",
    "context_integrity": "Context Integrity / Injection Defense",
    "sequential_behaviour": "Sequential Behaviour Analysis",
    "code_quality": "Code-Quality Patterns (Module 7)",
    "rules_engine": "Rules Engine",
}


def _label_module(module: str) -> str:
    # module may be a comma-joined list; label the first meaningful token.
    first = module.split(",")[0].strip()
    return _MODULE_LABEL.get(first, first or "the policy engine")


def build_explanation(
    event: EventCreate,
    results: list[DecisionCreate],
    final: DecisionCreate,
) -> str:
    """
    Compose a concise, plain-language explanation of the final decision.

    Args:
        event:   the original proposed action.
        results: per-module DecisionCreate results that fed the aggregation.
        final:   the aggregated DecisionCreate.
    """
    source_label = _SOURCE_LABEL.get(event.source, "an agent action")
    meaning = _VERDICT_MEANING.get(final.verdict, "was evaluated")

    # Identify the module(s) responsible for the final (worst) verdict.
    deciders = [r for r in results if r.verdict == final.verdict]
    if final.verdict == "ALLOW":
        driver_sentence = (
            "No policy or safety check raised a concern, so the action was cleared."
        )
    else:
        names = ", ".join(dict.fromkeys(_label_module(r.module) for r in deciders))
        # Pull the single most informative reason from the deciding module(s).
        key_reason = ""
        for r in deciders:
            if r.reasons:
                key_reason = r.reasons[0]
                break
        driver_sentence = (
            f"The decision was driven by {names}. Primary reason: {key_reason}"
        )

    risk_pct = f"{round(final.risk_score * 100)}%"

    parts = [
        f"This was {source_label} of type '{event.event_type}'. "
        f"After evaluation it {meaning} (aggregate risk {risk_pct}).",
        driver_sentence,
    ]

    if final.verdict == "WARN":
        parts.append(
            "A human reviewer should confirm or reject it; "
            "the recommended remediation is shown above."
        )
    elif final.verdict == "BLOCK":
        parts.append(
            "Execution was prevented outright and cannot be auto-overridden; "
            "follow the remediation guidance to proceed safely."
        )

    return " ".join(parts)
