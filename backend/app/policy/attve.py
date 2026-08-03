"""
app/policy/attve.py
────────────────────
Module 2 — Autonomous Transaction Trust & Verification Engine (ATTVE)
Scoped to the coffee-ordering use case.

Verifies a proposed transaction before payment executes:
  a) Merchant verification — look up merchant against data/merchant_registry.json
     (unknown or untrusted merchant -> BLOCK/WARN with suggested fix).
  b) Invoice integrity — amount must be positive, no duplicate transaction ID seen.
  c) Transaction-limit policy — configurable maximum spend threshold
     (settings.transaction_limit_usd); if exceeded, require human approval (WARN).

Entry point: evaluate_transaction(event: EventCreate, db_session = None) -> DecisionCreate
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.models.decision import DecisionCreate
from app.models.event import EventCreate

# ── Load Merchant Registry ─────────────────────────────────────────────────────
_REGISTRY_PATH = Path(__file__).parent.parent.parent / "data" / "merchant_registry.json"

# Global in-memory set to track processed transaction IDs for duplicate detection
_SEEN_TRANSACTION_IDS: set[str] = set()


def _load_merchant_registry() -> dict[str, dict[str, Any]]:
    """Load merchant registry indexed by merchant_id."""
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            merchants = data.get("merchants", [])
            return {m["merchant_id"]: m for m in merchants if "merchant_id" in m}
    except Exception:
        return {}


def clear_seen_transactions() -> None:
    """Helper for testing to reset duplicate transaction ID tracking."""
    _SEEN_TRANSACTION_IDS.clear()


def evaluate_transaction(event: EventCreate) -> DecisionCreate:
    """
    Evaluate a coffee order transaction against Module 2 (ATTVE) rules.

    Args:
        event: The normalised transaction event.

    Returns:
        DecisionCreate with verdict (ALLOW, WARN, BLOCK), reasons, suggested_fix,
        module="attve", and risk_score.
    """
    payload = event.payload or {}
    merchant_id = payload.get("merchant_id")
    merchant_name = payload.get("merchant_name", "Unknown Merchant")
    amount = payload.get("amount")
    transaction_id = payload.get("transaction_id")

    reasons: list[str] = []
    fixes: list[str] = []
    verdict = "ALLOW"
    risk_scores: list[float] = []

    # ── Check 1: Invoice Integrity (Amount & Duplicates) ──────────────────────
    if amount is None or not isinstance(amount, (int, float)):
        verdict = "BLOCK"
        reasons.append("Invoice error: Transaction amount is missing or not a valid number.")
        fixes.append("Provide a valid numeric transaction amount.")
        risk_scores.append(1.0)
    elif amount <= 0:
        verdict = "BLOCK"
        reasons.append(f"Invoice integrity violation: Transaction amount ${amount:.2f} must be positive.")
        fixes.append("Correct the invoice amount to a valid positive dollar value before payment.")
        risk_scores.append(1.0)

    if transaction_id:
        if transaction_id in _SEEN_TRANSACTION_IDS:
            verdict = "BLOCK"
            reasons.append(f"Duplicate transaction ID '{transaction_id}' detected. Possible replay attack or re-billing.")
            fixes.append("Generate a new unique transaction ID or verify if payment was already completed.")
            risk_scores.append(1.0)
        else:
            # Track transaction ID (only if valid amount)
            if amount is not None and isinstance(amount, (int, float)) and amount > 0:
                _SEEN_TRANSACTION_IDS.add(transaction_id)

    # ── Check 2: Merchant Verification ─────────────────────────────────────────
    registry = _load_merchant_registry()

    if not merchant_id:
        verdict = "BLOCK"
        reasons.append("Merchant verification failed: Merchant ID is missing from transaction payload.")
        fixes.append("Specify a valid merchant_id in the transaction payload.")
        risk_scores.append(0.9)
    elif merchant_id not in registry:
        # Unknown merchant
        verdict = "WARN" if verdict != "BLOCK" else "BLOCK"
        reasons.append(f"Unknown merchant: ID '{merchant_id}' ({merchant_name}) was not found in the trusted merchant registry.")
        fixes.append("Verify merchant credentials and register merchant in data/merchant_registry.json prior to transaction execution.")
        risk_scores.append(0.75)
    else:
        merchant_record = registry[merchant_id]
        if not merchant_record.get("trusted", False):
            verdict = "BLOCK"
            reasons.append(f"Untrusted merchant: Merchant '{merchant_record.get('name')}' (ID: {merchant_id}) is flagged as UNTRUSTED.")
            fixes.append("Do not execute automated payments to untrusted merchants. Select an alternative trusted merchant.")
            risk_scores.append(0.90)

    # ── Check 3: Transaction Limit Policy ──────────────────────────────────────
    limit = settings.transaction_limit_usd
    if isinstance(amount, (int, float)) and amount > limit:
        if verdict != "BLOCK":
            verdict = "WARN"
        reasons.append(f"Transaction limit exceeded: Amount ${amount:.2f} exceeds configured automated spend threshold of ${limit:.2f}.")
        fixes.append(f"Require explicit human review and approval for transactions exceeding ${limit:.2f}.")
        risk_scores.append(0.65)

    # ── Finalise Decision ───────────────────────────────────────────────────────
    if verdict == "ALLOW":
        reasons.append("Transaction verified: Merchant trusted, invoice valid, and amount within limit.")
        suggested_fix = ""
        risk_score = 0.0
    else:
        suggested_fix = " | ".join(fixes)
        risk_score = round(max(risk_scores) if risk_scores else 0.5, 4)

    return DecisionCreate(
        verdict=verdict,
        reasons=reasons,
        suggested_fix=suggested_fix,
        module="attve",
        risk_score=risk_score,
    )
