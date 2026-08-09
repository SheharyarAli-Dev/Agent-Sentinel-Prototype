"""
app/policy/intent_verification.py
───────────────────────────────────
Module 6 — Intent Verification Engine

Checks whether the CURRENT action still aligns with the user's ORIGINAL stated
goal for this session/task, catching intent drift — actions that are technically
safe in isolation but no longer serve what was actually asked.

Applies to: cursor (primary), n8n (primary), transaction (optional/light).

Logic (Semantic Intent Verification — Increment 1)
────────────────────────────────────────────────────
1. Extract tokens (alphanumeric words) from original_goal and action text.
2. Filter out common English stopwords.
3. Compute Jaccard similarity = |Goal ∩ Action| / |Goal ∪ Action|.
4. If Jaccard similarity is at or above settings.intent_drift_threshold,
   the action is treated as aligned (lexical baseline).
5. Otherwise call compute_semantic_drift():
       drift <= 0.25 → semantically aligned (ALLOW)
       drift >  0.25 → WARN for non-advisory evaluation
   For this increment compute_semantic_drift() is a deterministic lexical
   estimator (1.0 - Jaccard). It does not use a machine-learning model; a
   sentence-transformer backend will replace its internals in a later increment.
6. If compute_semantic_drift() raises, fall back to the Jaccard result alone and
   record "lexical fallback" in the reasons.
7. Intent Verification Version 1 never independently returns BLOCK.

Advisory mode (transactions): drift is reported informationally only — verdict
always ALLOW, risk_score always 0.0, suggested_fix always empty. ATTVE (Module 2)
remains authoritative for transaction safety.

Entry point: evaluate_intent(event: EventCreate, advisory: bool = False)
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

# Semantic drift at or below this value is treated as semantically aligned.
_SEMANTIC_ALIGNED_DRIFT = 0.25


def _tokenize(text: str) -> set[str]:
    """Extract lowercase alphanumeric tokens, stripping stopwords."""
    if not text:
        return set()
    words = re.findall(r"\b[a-zA-Z0-9_-]+\b", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def compute_semantic_drift(goal_text: str, action_text: str) -> float:
    """
    Compute semantic drift between a goal sentence and an action sentence.

    Returns:
        float in [0.0, 1.0]:
            0.0 = strongly aligned
            1.0 = fully drifted

    Increment 1: a deterministic lexical estimator (1.0 - Jaccard similarity over
    stopword-filtered token sets). No machine-learning library is involved; a
    sentence-transformer embedding model will replace the internals in a later
    increment without changing this signature.

    If either input is empty or whitespace, returns 1.0 (full drift).
    """
    if not goal_text or not goal_text.strip() or not action_text or not action_text.strip():
        return 1.0

    goal_tokens = _tokenize(goal_text)
    action_tokens = _tokenize(action_text)
    if not goal_tokens or not action_tokens:
        return 1.0

    intersection = goal_tokens & action_tokens
    union = goal_tokens | action_tokens
    similarity = len(intersection) / len(union) if union else 0.0
    drift = round(1.0 - similarity, 4)
    return max(0.0, min(1.0, drift))


def _collect_action_texts(event: EventCreate) -> list[str]:
    """Collect all considered action text fields (with event_type first)."""
    payload = event.payload or {}
    texts: list[str] = [
        event.event_type,
        str(payload.get("description", "")),
        str(payload.get("target", "")),
        str(payload.get("command", "")),
        str(payload.get("merchant_name", "")),
        str(payload.get("item", "")),
    ]
    if isinstance(payload.get("steps"), list):
        for step in payload["steps"]:
            if isinstance(step, dict):
                texts.append(str(step.get("description", "")))
                texts.append(str(step.get("target", "")))
    return texts


def _has_meaningful_action_details(event: EventCreate) -> bool:
    """
    True when the action carries descriptive evidence beyond event.event_type.
    event_type alone (e.g. "file_write") is not considered sufficient evidence.
    """
    payload = event.payload or {}
    for key in ("description", "target", "command", "merchant_name", "item"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return True
    if isinstance(payload.get("steps"), list):
        for step in payload["steps"]:
            if not isinstance(step, dict):
                continue
            for key in ("description", "target"):
                val = step.get(key)
                if isinstance(val, str) and val.strip():
                    return True
    return False


def evaluate_intent(event: EventCreate, advisory: bool = False) -> DecisionCreate:
    """
    Evaluate action alignment against original_goal.

    Args:
        event: Normalised agent event with original_goal.
        advisory: When True (transactions), intent drift is reported as an
            INFORMATIONAL signal only — verdict always ALLOW, risk_score always
            0.0, suggested_fix always empty. Comparing a natural-language goal
            ("order a coffee") to structured payment fields is inherently lossy,
            so ATTVE (Module 2) is authoritative for transaction safety.

    Returns:
        DecisionCreate with verdict (ALLOW or WARN; never BLOCK), reasons,
        suggested_fix, module="intent_verification", and risk_score.
    """
    goal = event.original_goal
    if not goal or not goal.strip():
        # No goal specified → skip the drift check.
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Intent verification skipped: No original session goal provided."],
            suggested_fix="",
            module="intent_verification",
            risk_score=0.0,
        )

    action_texts = _collect_action_texts(event)
    action_full = " ".join(t for t in action_texts if t).strip()

    # Missing meaningful action text: the action cannot be verified.
    if not _has_meaningful_action_details(event):
        if advisory:
            return DecisionCreate(
                verdict="ALLOW",
                reasons=[
                    f"Intent note (advisory): insufficient action details to compare "
                    f"against goal '{goal}'. Not blocking — ATTVE (Module 2) is "
                    "authoritative for transaction safety."
                ],
                suggested_fix="",
                module="intent_verification",
                risk_score=0.0,
            )
        return DecisionCreate(
            verdict="WARN",
            reasons=[
                "Intent verification: insufficient action text to verify intent. "
                "The action is uncertain because there is no descriptive detail "
                "beyond the event type.",
                f"Original Goal: '{goal}'",
                f"Proposed Action: '{action_full or '(no descriptive action text)'}'",
            ],
            suggested_fix=(
                "Provide descriptive action text (description, target, or command) "
                "so the action's contribution to the stated goal can be verified."
            ),
            module="intent_verification",
            risk_score=0.5,
        )

    goal_tokens = _tokenize(goal)
    action_tokens = _tokenize(action_full)

    intersection = goal_tokens & action_tokens
    union = goal_tokens | action_tokens
    jaccard_sim = len(intersection) / len(union) if union else 0.0
    jaccard_drift = round(1.0 - jaccard_sim, 4)
    threshold = settings.intent_drift_threshold

    # ── Semantic evidence, isolated from the lexical decision ───────────────────
    semantic_ok = True
    semantic_drift: float | None = None
    try:
        semantic_score = compute_semantic_drift(goal, action_full)
        semantic_drift = max(0.0, min(1.0, float(semantic_score)))
    except Exception:
        semantic_ok = False
        semantic_drift = None

    reason_notes: list[str] = []
    if not semantic_ok:
        reason_notes.append(
            "Lexical fallback mode used (semantic scorer unavailable); Jaccard "
            "evidence used."
        )

    if advisory:
        # Informational-only output. Verdict/risk/fix never vary.
        if jaccard_sim >= threshold:
            info = (
                f"Intent note (advisory): action aligns with goal '{goal}' "
                f"(keyword overlap: {jaccard_sim:.1%})."
            )
        elif semantic_ok and semantic_drift is not None and semantic_drift <= _SEMANTIC_ALIGNED_DRIFT:
            info = (
                f"Intent note (advisory): semantically aligned with goal '{goal}' "
                f"(semantic drift {semantic_drift:.2f}; keyword overlap {jaccard_sim:.1%})."
            )
        else:
            info = (
                f"Intent note (advisory): low alignment with goal '{goal}' — "
                f"keyword overlap {jaccard_sim:.1%}, semantic drift "
                f"{semantic_drift if semantic_drift is not None else 'n/a'}. "
                "Not blocking — ATTVE (Module 2) is authoritative for transaction safety."
            )
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[info, *reason_notes],
            suggested_fix="",
            module="intent_verification",
            risk_score=0.0,
        )

    # ── Non-advisory decision ───────────────────────────────────────────────────
    if jaccard_sim >= threshold:
        # Lexical overlap is sufficient to consider the action aligned.
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[
                f"Intent verified: Action aligns with original goal "
                f"(keyword overlap: {jaccard_sim:.1%}).",
                f"Original Goal: '{goal}'",
                f"Proposed Action: '{action_full[:100]}...'",
                *reason_notes,
            ],
            suggested_fix="",
            module="intent_verification",
            risk_score=0.0,
        )

    # Jaccard is below threshold; trust the semantic signal if it is confident.
    if semantic_ok and semantic_drift is not None and semantic_drift <= _SEMANTIC_ALIGNED_DRIFT:
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[
                f"Intent verified: Action semantically aligns with original goal "
                f"(semantic drift {semantic_drift:.2f}).",
                f"Original Goal: '{goal}'",
                f"Proposed Action: '{action_full[:100]}...'",
                f"Keyword overlap was low ({jaccard_sim:.1%}) but semantic evidence shows alignment.",
                *reason_notes,
            ],
            suggested_fix="",
            module="intent_verification",
            risk_score=0.0,
        )

    # Uncertain / drifted. WARN, never BLOCK.
    drift_reported = semantic_drift if semantic_drift is not None else jaccard_drift
    return DecisionCreate(
        verdict="WARN",
        reasons=[
            f"Intent drift detected: low keyword overlap ({jaccard_sim:.1%}) and "
            f"semantic drift {drift_reported:.2f} vs. original goal.",
            f"Original Goal: '{goal}'",
            f"Proposed Action: '{action_full[:100]}...'",
            *reason_notes,
        ],
        suggested_fix=(
            f"Current action shows insufficient alignment ({jaccard_sim:.1%} "
            f"overlap, semantic drift {drift_reported:.2f}) with stated goal "
            f"'{goal}'. Confirm the action directly contributes to the original "
            "task objective before proceeding."
        ),
        module="intent_verification",
        risk_score=round(max(0.5, drift_reported), 4),
    )