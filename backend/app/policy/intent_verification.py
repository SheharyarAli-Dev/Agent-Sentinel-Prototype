"""
app/policy/intent_verification.py
───────────────────────────────────
Module 6 — Intent Verification Engine

Checks whether the CURRENT action still aligns with the user's ORIGINAL stated
goal for this session/task, catching intent drift — actions that are technically
safe in isolation but no longer serve what was actually asked.

Applies to: cursor (primary), n8n (primary), transaction (optional/light).

Logic for Prototype (Rule-Based Keyword / Jaccard Similarity)
─────────────────────────────────────────────────────────────
1. Extract tokens (alphanumeric words) from original_goal and action description/target.
2. Filter out common English stopwords.
3. Compute Jaccard similarity = |Goal ∩ Action| / |Goal ∪ Action|.
4. Flag intent drift when similarity drops below settings.intent_drift_threshold (default 0.15).

Future-Work Extension Point
───────────────────────────
compute_semantic_drift() raises NotImplementedError.  A sentence-transformer
embedding model will replace the Jaccard heuristic in future work.

Entry point: evaluate_intent(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.models.decision import DecisionCreate
from app.models.event import EventCreate

# ── Basic English Stopwords List ───────────────────────────────────────────────
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "until", "while",
    "of", "at", "by", "for", "with", "about", "against", "between", "into", "through",
    "during", "before", "after", "above", "below", "to", "from", "up", "down", "in",
    "out", "on", "off", "over", "under", "again", "further", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "any", "both", "each", "few",
    "more", "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "s", "t", "can", "will", "just", "don",
    "should", "now", "i", "me", "my", "we", "our", "you", "your", "he", "him",
    "his", "she", "her", "it", "its", "they", "them", "their", "this", "that",
    "is", "am", "are", "was", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "doing", "would", "could", "make", "user", "order", "file",
}


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens, stripping stopwords."""
    if not text:
        return set()
    words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


# ── Future-Work Extension Point ────────────────────────────────────────────────
def compute_semantic_drift(goal_text: str, action_text: str) -> float:
    """
    FUTURE WORK — Phase 2 of the full project (out of scope for prototype).

    This function will compute a semantic drift score using a sentence-
    transformer model (e.g. all-MiniLM-L6-v2) by comparing the cosine
    similarity between the embedding of `goal_text` and `action_text`.

    DO NOT call this function — it always raises NotImplementedError.
    """
    raise NotImplementedError(
        "compute_semantic_drift() is a future-work extension point. "
        "Sentence-transformer embedding similarity checks will replace the "
        "Jaccard heuristic in future project phases."
    )


def evaluate_intent(event: EventCreate) -> DecisionCreate:
    """
    Evaluate action alignment against original_goal using Jaccard keyword overlap.

    Args:
        event: Normalised agent event with original_goal.

    Returns:
        DecisionCreate with verdict (ALLOW or WARN), reasons, suggested_fix,
        module="intent_verification", and risk_score.
    """
    goal = event.original_goal
    if not goal or not goal.strip():
        # If no goal specified, skip drift check
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Intent verification skipped: No original session goal provided."],
            suggested_fix="",
            module="intent_verification",
            risk_score=0.0,
        )

    # Gather action text from payload fields
    payload = event.payload or {}
    action_texts: list[str] = [
        event.event_type,
        str(payload.get("description", "")),
        str(payload.get("target", "")),
        str(payload.get("command", "")),
        str(payload.get("merchant_name", "")),
        str(payload.get("item", "")),
    ]

    # Combine step descriptions if present
    if isinstance(payload.get("steps"), list):
        for step in payload["steps"]:
            if isinstance(step, dict):
                action_texts.append(str(step.get("description", "")))
                action_texts.append(str(step.get("target", "")))

    action_full = " ".join(t for t in action_texts if t).strip()

    goal_tokens = _tokenize(goal)
    action_tokens = _tokenize(action_full)

    if not goal_tokens or not action_tokens:
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Intent verification: Token set insufficient for overlap comparison."],
            suggested_fix="",
            module="intent_verification",
            risk_score=0.0,
        )

    intersection = goal_tokens & action_tokens
    union = goal_tokens | action_tokens

    jaccard_sim = len(intersection) / len(union) if union else 1.0
    drift_score = round(1.0 - jaccard_sim, 4)

    threshold = settings.intent_drift_threshold

    if jaccard_sim < threshold:
        verdict = "WARN"
        reasons = [
            f"Intent drift detected: Current action description shows low keyword overlap ({jaccard_sim:.1%}) with original goal.",
            f"Original Goal: '{goal}'",
            f"Proposed Action: '{action_full[:100]}...'",
        ]
        suggested_fix = (
            f"Current action target/description shows low keyword alignment ({jaccard_sim:.1%}) with stated goal '{goal}'. "
            "Confirm that this action directly contributes to the original task objective before proceeding."
        )
        risk_score = round(max(0.5, drift_score), 4)
    else:
        verdict = "ALLOW"
        reasons = [
            f"Intent verified: Action aligns with original goal (keyword overlap: {jaccard_sim:.1%})."
        ]
        suggested_fix = ""
        risk_score = 0.0

    return DecisionCreate(
        verdict=verdict,
        reasons=reasons,
        suggested_fix=suggested_fix,
        module="intent_verification",
        risk_score=risk_score,
    )
