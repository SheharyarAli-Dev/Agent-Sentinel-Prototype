"""
app/policy/intent_verification.py
-----------------------------------
Module 6 - Intent Verification Engine

Checks whether the CURRENT action still aligns with the user's ORIGINAL stated
goal for this session/task, catching intent drift - actions that are technically
safe in isolation but no longer serve what was actually asked.

Applies to: cursor (primary), n8n (primary), transaction (optional/light).

Logic (Semantic Intent Verification - Increment 2C)
---------------------------------------------------
1. Semantic evidence: compute_semantic_drift() delegates to
   app.policy.semantic_similarity.compute_embedding_drift(), which scores
   goal/action alignment using the local MiniLM sentence-embedding model
   (sentence-transformers/all-MiniLM-L6-v2).
   The ACTION text sent to MiniLM is a clean, readable sentence built by
   _build_semantic_action_text() (description first, then step descriptions,
   transaction item/merchant, command, target, and event_type only as the final
   fallback). Generic event types and technical target paths are excluded when
   a meaningful description exists, so they cannot pollute the embedding. The
   full evidence text (which includes every field) is still used for lexical
   Jaccard evidence and explanations.
2. When semantic inference succeeds, semantic drift is the PRIMARY alignment
   evidence:
       drift <= settings.intent_semantic_aligned_drift -> ALLOW
       drift >  settings.intent_semantic_aligned_drift -> WARN
   The aligned boundary (default 0.38) is PROVISIONAL and derived from the
   30-case developer-authored exploratory benchmark; it is not a scientifically
   calibrated final threshold.
3. Jaccard keyword overlap remains SUPPORTING evidence for explanations and is
   the FALLBACK decision when the semantic backend is unavailable:
       overlap >= settings.intent_drift_threshold -> ALLOW
       otherwise -> WARN
4. High lexical overlap never bypasses semantic review when semantic inference
   succeeds.
5. If semantic inference raises (model unavailable), evaluate_intent() catches
   the failure, records "lexical fallback", and uses the Jaccard decision above.
   compute_semantic_drift() itself never performs fallback and never swallows
   backend exceptions.
6. Intent Verification Version 1 never independently returns BLOCK.

Advisory mode (transactions): drift is reported informationally only - verdict
always ALLOW, risk_score always 0.0, suggested_fix always empty. Transaction
safety therefore contributes zero operational risk; ATTVE (Module 2) remains
authoritative for transaction safety.

Entry point: evaluate_intent(event: EventCreate, advisory: bool = False)
"""
from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.models.decision import DecisionCreate
from app.models.event import EventCreate
from app.policy import semantic_similarity

# Basic English stopwords list
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


def compute_semantic_drift(goal_text: str, action_text: str) -> float:
    """
    Compute semantic drift between a goal sentence and an action sentence.

    Delegates to app.policy.semantic_similarity.compute_embedding_drift(), which
    scores alignment using the local MiniLM embedding model.

    Returns:
        float in [0.0, 1.0]:
            0.0 = strongly aligned
            1.0 = fully drifted

    Empty or whitespace inputs short-circuit to 1.0 (full drift) WITHOUT invoking
    the semantic backend.  Out-of-range backend results are clamped into
    [0.0, 1.0].  Backend exceptions are deliberately NOT caught here - the caller
    (evaluate_intent) owns the lexical fallback.
    """
    if not goal_text or not goal_text.strip():
        return 1.0
    if not action_text or not action_text.strip():
        return 1.0

    drift = semantic_similarity.compute_embedding_drift(goal_text, action_text)
    drift = float(drift)
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


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace into single spaces and strip the edges."""
    return re.sub(r"\s+", " ", text).strip()


def _build_semantic_action_text(event: EventCreate) -> str:
    """
    Build the clean, readable action sentence used ONLY for MiniLM semantic
    inference. This deliberately differs from the full evidence text
    (_collect_action_texts / action_full) which keeps every field for lexical
    Jaccard evidence, explanations, and security/audit context.

    Generic event types (e.g. "file_write") and technical target paths (e.g.
    "src/auth.py") pollute the embedding sentence, so they are used only when no
    better descriptive text exists.

    Priority:
      1. non-empty payload.description
      2. combined non-empty step descriptions
      3. transaction item and merchant name
      4. payload.command when no readable description exists
      5. payload.target when no better descriptive text exists
      6. event.event_type only as the final fallback

    Whitespace is normalised. None and empty-string values are ignored.
    """
    payload = event.payload or {}

    description = str(payload.get("description") or "").strip()
    if description:
        return _normalize_whitespace(description)

    step_parts: list[str] = []
    if isinstance(payload.get("steps"), list):
        for step in payload["steps"]:
            if not isinstance(step, dict):
                continue
            text = str(step.get("description") or "").strip()
            if text:
                step_parts.append(text)
    if step_parts:
        return _normalize_whitespace(" ".join(step_parts))

    item = str(payload.get("item") or "").strip()
    merchant = str(payload.get("merchant_name") or "").strip()
    if item or merchant:
        return _normalize_whitespace(" ".join(p for p in (item, merchant) if p))

    command = str(payload.get("command") or "").strip()
    if command:
        return _normalize_whitespace(command)

    target = str(payload.get("target") or "").strip()
    if target:
        return _normalize_whitespace(target)

    return _normalize_whitespace(str(event.event_type or ""))


def evaluate_intent(event: EventCreate, advisory: bool = False) -> DecisionCreate:
    """
    Evaluate action alignment against original_goal.

    Args:
        event: Normalised agent event with original_goal.
        advisory: When True (transactions), intent drift is reported as an
            INFORMATIONAL signal only - verdict always ALLOW, risk_score always
            0.0, suggested_fix always empty. Comparing a natural-language goal
            ("order a coffee") to structured payment fields is inherently lossy,
            so ATTVE (Module 2) is authoritative for transaction safety.

    Returns:
        DecisionCreate with verdict (ALLOW or WARN; never BLOCK), reasons,
        suggested_fix, module="intent_verification", and risk_score.

    Decision when semantic inference succeeds (primary evidence):
        semantic_drift <= settings.intent_semantic_aligned_drift -> ALLOW
        semantic_drift >  settings.intent_semantic_aligned_drift -> WARN
    Jaccard keyword overlap is supporting evidence for explanations only and is
    never used to bypass semantic review. If the semantic backend raises, the
    Jaccard threshold decision is used as the lexical fallback.
    """
    goal = event.original_goal
    if not goal or not goal.strip():
        # No goal specified -> skip the drift check.
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
                    f"against goal '{goal}'. Not blocking - ATTVE (Module 2) is "
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

    # Clean semantic sentence: readable intent-bearing content only. The full
    # action_full string is kept for lexical evidence and explanations.
    semantic_action_text = _build_semantic_action_text(event)

    # Semantic evidence; Jaccard is supporting/fallback only.
    semantic_ok = True
    semantic_drift: float | None = None
    try:
        semantic_drift = compute_semantic_drift(goal, semantic_action_text)
        semantic_drift = max(0.0, min(1.0, float(semantic_drift)))
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
        if semantic_ok and semantic_drift is not None:
            info = (
                f"Intent note (advisory): semantic drift {semantic_drift:.2f} with "
                f"goal '{goal}' (keyword overlap {jaccard_sim:.1%}). Not blocking - "
                "ATTVE (Module 2) is authoritative for transaction safety."
            )
        else:
            info = (
                f"Intent note (advisory): semantic scorer unavailable; keyword "
                f"overlap {jaccard_sim:.1%} with goal '{goal}'. Not blocking - "
                "ATTVE (Module 2) is authoritative for transaction safety."
            )
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[info, *reason_notes],
            suggested_fix="",
            module="intent_verification",
            risk_score=0.0,
        )

    # Hedge against non-finite semantic results being treated as aligned.
    if semantic_ok and semantic_drift is None:
        semantic_ok = False

    if semantic_ok:
        # Semantic evidence is primary; Jaccard is supporting/explanation only.
        boundary = settings.intent_semantic_aligned_drift
        if semantic_drift <= boundary:
            return DecisionCreate(
                verdict="ALLOW",
                reasons=[
                    f"Intent verified: Action semantically aligns with original goal "
                    f"(semantic drift {semantic_drift:.2f}).",
                    f"Original Goal: '{goal}'",
                    f"Proposed Action: '{action_full[:100]}...'",
                    f"Keyword overlap was {jaccard_sim:.1%} supporting evidence.",
                    *reason_notes,
                ],
                suggested_fix="",
                module="intent_verification",
                risk_score=0.0,
            )
        return DecisionCreate(
            verdict="WARN",
            reasons=[
                f"Intent drift detected: semantic drift {semantic_drift:.2f} exceeds "
                f"aligned boundary ({boundary:.2f}) vs. original goal.",
                f"Original Goal: '{goal}'",
                f"Proposed Action: '{action_full[:100]}...'",
                f"Keyword overlap was {jaccard_sim:.1%} supporting evidence.",
                *reason_notes,
            ],
            suggested_fix=(
                f"Current action shows insufficient alignment (semantic drift "
                f"{semantic_drift:.2f}, keyword overlap {jaccard_sim:.1%}) with "
                f"stated goal '{goal}'. Confirm the action directly contributes to "
                "the original task objective before proceeding."
            ),
            module="intent_verification",
            risk_score=round(max(0.5, semantic_drift), 4),
        )

    # Semantic scorer unavailable -> lexical (Jaccard) fallback decision.
    if jaccard_sim >= threshold:
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
    return DecisionCreate(
        verdict="WARN",
        reasons=[
            f"Intent drift detected: low keyword overlap ({jaccard_sim:.1%}) vs. "
            "original goal.",
            f"Original Goal: '{goal}'",
            f"Proposed Action: '{action_full[:100]}...'",
            *reason_notes,
        ],
        suggested_fix=(
            f"Current action shows insufficient alignment ({jaccard_sim:.1%} overlap) "
            f"with stated goal '{goal}'. Confirm the action directly contributes to "
            "the original task objective before proceeding."
        ),
        module="intent_verification",
        risk_score=round(max(0.5, jaccard_drift), 4),
    )
