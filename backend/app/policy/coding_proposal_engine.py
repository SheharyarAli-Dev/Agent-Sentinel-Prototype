"""
app/policy/coding_proposal_engine.py
────────────────────────────────────
Coding Proposal Evaluation Engine

Evaluates a coding proposal (file-write action) against the bounded
coding-demo contract. Integrates with the existing ASENT policy pipeline
by returning a DecisionCreate that feeds into the aggregation layer.

Checks (in order):
  1. Contract validation (hash mismatch, malformed hashes)
  2. Structural path rejection (absolute, traversal, symlink)
  3. Protected path match -> BLOCK
  4. Sensitive path match -> WARN
  5. Content size limit -> WARN
  6. Test profile validity -> WARN

Aggregation: most severe verdict wins (BLOCK > WARN > ALLOW).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.decision import DecisionCreate
from app.models.event import EventCreate
from app.models.coding_proposal import (
    CodingProposal,
    PathSafetyRejection,
    classify_path,
    validate_coding_path,
    _load_path_rules,
)


def evaluate_coding_proposal(
    event: EventCreate,
    proposal: CodingProposal,
) -> DecisionCreate:
    """
    Evaluate a coding proposal against the bounded coding contract.

    Args:
        event: The original event (must be source="cursor", event_type="coding_proposal")
        proposal: The validated CodingProposal from the event payload.

    Returns:
        DecisionCreate with verdict (ALLOW, WARN, BLOCK), reasons, suggested_fix,
        module="coding_proposal_engine", and risk_score.
    """
    reasons: list[str] = []
    fixes: list[str] = []
    verdict = "ALLOW"
    risk_scores: list[float] = []

    rules = _load_path_rules()
    max_content_size = rules.get("max_content_size_chars", 50_000)

    # ── 1. Contract validation: hash mismatch ──────────────────────────────────
    if not proposal.verify_new_hash():
        verdict = "BLOCK"
        reasons.append(
            "Contract violation: expected_new_hash does not match "
            "the SHA-256 hash of new_content."
        )
        fixes.append(
            "Recalculate the expected_new_hash to match the actual "
            "SHA-256 digest of the proposed new_content."
        )
        risk_scores.append(1.0)
        return _finalize(verdict, reasons, fixes, risk_scores, module="coding_proposal_engine")

    # ── 2. Structural path validation ──────────────────────────────────────────
    # Use the ASENT project root as the repo root for containment checks.
    # The coding-demo directory is at the same level as backend/.
    repo_root = Path(__file__).resolve().parent.parent.parent / "coding-demo"
    try:
        tier = validate_coding_path(proposal.relative_path, repo_root)
    except PathSafetyRejection as exc:
        verdict = "BLOCK"
        reasons.append(f"Path safety rejection: {exc.reason}")
        fixes.append(
            f"The path '{proposal.relative_path}' was rejected by the "
            f"safety rule '{exc.rule}'. Use a valid relative path within "
            f"the coding-demo repository."
        )
        risk_scores.append(1.0)
        return _finalize(verdict, reasons, fixes, risk_scores, module="coding_proposal_engine")

    # ── 3. Protected path match ────────────────────────────────────────────────
    if tier == "protected":
        verdict = "BLOCK"
        reasons.append(
            f"Protected resource targeted: '{proposal.relative_path}' "
            f"matches a protected path rule."
        )
        fixes.append(
            f"The path '{proposal.relative_path}' is in a protected "
            f"directory. This file cannot be modified."
        )
        risk_scores.append(1.0)

    # ── 4. Sensitive path match ────────────────────────────────────────────────
    elif tier == "sensitive":
        if verdict != "BLOCK":
            verdict = "WARN"
        reasons.append(
            f"Sensitive resource targeted: '{proposal.relative_path}' "
            f"requires human review."
        )
        fixes.append(
            f"Review the proposed change to '{proposal.relative_path}' "
            f"before proceeding."
        )
        risk_scores.append(0.70)

    # ── 5. Content size limit ──────────────────────────────────────────────────
    if len(proposal.new_content) > max_content_size:
        if verdict != "BLOCK":
            verdict = "WARN"
        reasons.append(
            f"Content size {len(proposal.new_content)} chars exceeds "
            f"limit of {max_content_size} chars."
        )
        fixes.append("Reduce the content size to below the limit.")
        risk_scores.append(0.50)

    # ── 6. Test profile validity ───────────────────────────────────────────────
    valid_profiles = rules.get("valid_test_profiles", ["unit", "none"])
    if proposal.test_profile not in valid_profiles:
        if verdict != "BLOCK":
            verdict = "WARN"
        reasons.append(
            f"Invalid test_profile '{proposal.test_profile}'. "
            f"Valid profiles: {valid_profiles}."
        )
        fixes.append(
            f"Set test_profile to one of: {', '.join(valid_profiles)}."
        )
        risk_scores.append(0.40)

    return _finalize(verdict, reasons, fixes, risk_scores, module="coding_proposal_engine")


def _finalize(
    verdict: str,
    reasons: list[str],
    fixes: list[str],
    risk_scores: list[float],
    module: str = "coding_proposal_engine",
) -> DecisionCreate:
    """Build the final DecisionCreate from collected evidence."""
    if verdict == "ALLOW":
        reasons.append(
            "Coding proposal passed all checks: path is allowed, "
            "hash contract verified, content within limits."
        )
        suggested_fix = ""
        risk_score = 0.0
    else:
        suggested_fix = " | ".join(fixes)
        risk_score = round(max(risk_scores) if risk_scores else 0.5, 4)

    return DecisionCreate(
        verdict=verdict,  # type: ignore[arg-type]
        reasons=reasons,
        suggested_fix=suggested_fix,
        module=module,
        risk_score=risk_score,
    )
