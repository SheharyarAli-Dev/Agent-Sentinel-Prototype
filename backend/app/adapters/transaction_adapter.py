"""
app/adapters/transaction_adapter.py
──────────────────────────────────────
Transaction adapter — standalone simulation script for the coffee-ordering use case.

Simulates a coffee-ordering AI agent submitting transactions to the Risk Gatekeeper.
Runs a set of test scenarios covering:
  1. Valid coffee order from trusted merchant (Good Beans Coffee, $4.50) -> ALLOW
  2. Untrusted merchant order (ShiftyCafe, $12.00) -> BLOCK
  3. Untrusted merchant order (FakePay Ltd, $150.00) -> BLOCK
  4. Negative amount invoice (-$1.00) -> BLOCK
  5. Zero amount invoice ($0.00) -> BLOCK
  6. Over-threshold coffee order ($55.00 for 10 coffees) -> WARN
  7. Duplicate transaction ID re-submission -> BLOCK
  8. Unknown merchant ID -> WARN/BLOCK

Usage (from backend/ with venv active and server running):
    python -m app.adapters.transaction_adapter
"""
from __future__ import annotations

import httpx
import json
import time

BACKEND_URL = "http://localhost:8000/api/evaluate"

SCENARIOS = [
    {
        "name": "1. Valid Coffee Order (Trusted Merchant)",
        "merchant_id": "MERCH_001",
        "merchant_name": "Good Beans Coffee",
        "amount": 4.50,
        "transaction_id": "TXN_SIM_001",
        "item": "Latte",
        "goal": "Order a latte from a nearby trusted coffee shop.",
    },
    {
        "name": "2. Untrusted Merchant Order (ShiftyCafe)",
        "merchant_id": "MERCH_006",
        "merchant_name": "ShiftyCafe",
        "amount": 12.00,
        "transaction_id": "TXN_SIM_002",
        "item": "Coffee",
        "goal": "Order a coffee from the nearest shop.",
    },
    {
        "name": "3. Tampered Invoice (Negative Amount)",
        "merchant_id": "MERCH_001",
        "merchant_name": "Good Beans Coffee",
        "amount": -1.00,
        "transaction_id": "TXN_SIM_003",
        "item": "Coffee",
        "goal": "Order a coffee.",
    },
    {
        "name": "4. Over-Threshold Order ($55.00 > $50 Limit)",
        "merchant_id": "MERCH_001",
        "merchant_name": "Good Beans Coffee",
        "amount": 55.00,
        "transaction_id": "TXN_SIM_004",
        "item": "10 Lattes for Team",
        "goal": "Order coffee for the whole team.",
    },
    {
        "name": "5. Duplicate Transaction Replay Attack",
        "merchant_id": "MERCH_001",
        "merchant_name": "Good Beans Coffee",
        "amount": 4.50,
        "transaction_id": "TXN_SIM_001",  # Same as scenario 1!
        "item": "Latte",
        "goal": "Order a latte.",
    },
    {
        "name": "6. Unknown Merchant ID",
        "merchant_id": "MERCH_UNKNOWN_999",
        "merchant_name": "Pop-up Kiosk",
        "amount": 4.50,
        "transaction_id": "TXN_SIM_006",
        "item": "Espresso",
        "goal": "Order an espresso.",
    },
]


def build_event(sc: dict) -> dict:
    return {
        "source": "transaction",
        "event_type": "purchase",
        "payload": {
            "merchant_id": sc["merchant_id"],
            "merchant_name": sc["merchant_name"],
            "amount": sc["amount"],
            "currency": "USD",
            "transaction_id": sc["transaction_id"],
            "item": sc["item"],
            "description": f"Purchase of {sc['item']} at {sc['merchant_name']}",
        },
        "original_goal": sc["goal"],
    }


def run_simulation() -> None:
    print("=" * 70)
    print("Transaction Adapter Simulation — Coffee Ordering Agent")
    print(f"Targeting: {BACKEND_URL}")
    print("=" * 70)

    for i, sc in enumerate(SCENARIOS, 1):
        print(f"\n[{i}/{len(SCENARIOS)}] Running: {sc['name']}")
        event = build_event(sc)

        try:
            resp = httpx.post(BACKEND_URL, json=event, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            dec = data["decision"]

            verdict = dec["verdict"]
            v_color = (
                "\033[92mALLOW\033[0m" if verdict == "ALLOW"
                else "\033[93mWARN\033[0m" if verdict == "WARN"
                else "\033[91mBLOCK\033[0m"
            )

            print(f"  Verdict:      {v_color}")
            print(f"  Risk Score:   {dec['risk_score']:.4f}")
            print(f"  Module:       {dec['module']}")
            print(f"  Reasons:      {dec['reasons']}")
            if dec["suggested_fix"]:
                print(f"  Suggested Fix: {dec['suggested_fix']}")

        except Exception as exc:
            print(f"  [ERROR] Failed to post event: {exc}")

        time.sleep(0.3)

    print("\n" + "=" * 70)
    print("Simulation complete.")
    print("=" * 70)


if __name__ == "__main__":
    run_simulation()
