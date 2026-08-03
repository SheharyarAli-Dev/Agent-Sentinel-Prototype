#!/usr/bin/env python3
"""
scripts/run_eval.py
────────────────────
Evaluation runner — runs all labeled examples through the /evaluate endpoint
and computes precision, recall, and false-positive rate per use case.

Usage (from backend/ with venv active and server running):
    python scripts/run_eval.py

Output:
  - Summary table printed to stdout
  - Results written to docs/eval-results.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

import httpx

BACKEND_URL = "http://localhost:8000/api/evaluate"
EVAL_DIR = Path(__file__).parent.parent / "data" / "eval_set"
DOCS_DIR = Path(__file__).parent.parent.parent / "docs"

USE_CASES = {
    "coding_agent": EVAL_DIR / "coding_agent.jsonl",
    "automation_agent": EVAL_DIR / "automation_agent.jsonl",
    "transaction_agent": EVAL_DIR / "transaction_agent.jsonl",
}

# For binary precision/recall, treat WARN and BLOCK as "risky" (positive class)
RISKY_VERDICTS = {"WARN", "BLOCK"}


def run_use_case(name: str, path: Path) -> dict:
    """Run all labeled examples for one use case and return metrics."""
    if not path.exists():
        print(f"  [SKIP] {path} not found.", file=sys.stderr)
        return {}

    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))

    tp = fp = fn = tn = 0
    errors = 0
    results = []

    for ex in examples:
        expected = ex["expected_verdict"]
        try:
            resp = httpx.post(BACKEND_URL, json=ex["event"], timeout=10.0)
            resp.raise_for_status()
            actual = resp.json()["decision"]["verdict"]
        except Exception as e:
            print(f"  [ERROR] {e}", file=sys.stderr)
            errors += 1
            continue

        expected_risky = expected in RISKY_VERDICTS
        actual_risky = actual in RISKY_VERDICTS

        if expected_risky and actual_risky:
            tp += 1
        elif not expected_risky and actual_risky:
            fp += 1
        elif expected_risky and not actual_risky:
            fn += 1
        else:
            tn += 1

        results.append({
            "expected": expected,
            "actual": actual,
            "match": expected == actual,
        })

    total = tp + fp + fn + tn
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 0.0

    return {
        "name": name,
        "total": total,
        "errors": errors,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "fpr": fpr,
        "accuracy": accuracy,
        "results": results,
    }


def print_table(metrics: list[dict]) -> None:
    header = f"{'Use Case':<22} {'Total':>6} {'Precision':>10} {'Recall':>8} {'FPR':>7} {'Accuracy':>10}"
    print("\n" + "=" * len(header))
    print(header)
    print("=" * len(header))
    for m in metrics:
        if not m:
            continue
        print(
            f"{m['name']:<22} {m['total']:>6} "
            f"{m['precision']:>10.2%} {m['recall']:>8.2%} "
            f"{m['fpr']:>7.2%} {m['accuracy']:>10.2%}"
        )
    print("=" * len(header) + "\n")


def write_markdown(metrics: list[dict]) -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DOCS_DIR / "eval-results.md"
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# Risk Gatekeeper — Evaluation Results",
        "",
        f"_Generated: {ts}_",
        "",
        "## Summary",
        "",
        "| Use Case | Total | Precision | Recall | FPR | Accuracy |",
        "|----------|-------|-----------|--------|-----|----------|",
    ]

    for m in metrics:
        if not m:
            continue
        lines.append(
            f"| {m['name']} | {m['total']} | "
            f"{m['precision']:.2%} | {m['recall']:.2%} | "
            f"{m['fpr']:.2%} | {m['accuracy']:.2%} |"
        )

    lines += [
        "",
        "## Notes",
        "",
        "- **Positive class**: WARN or BLOCK (risky actions)",
        "- **Precision**: Of all actions flagged as risky, what fraction actually were?",
        "- **Recall**: Of all truly risky actions, what fraction did we catch?",
        "- **FPR**: Of all safe actions, what fraction did we incorrectly flag?",
        "",
        "> Results reflect the current prototype implementation (rule-based, no ML).",
        "> Baseline phase stubs will show 0% precision/recall until modules are implemented.",
    ]

    out_path.write_text("\n".join(lines))
    print(f"Results written to {out_path}")


def main() -> None:
    print("Risk Gatekeeper — Evaluation Runner")
    print(f"Backend: {BACKEND_URL}\n")

    # Quick health check
    try:
        health = httpx.get("http://localhost:8000/health", timeout=5.0)
        health.raise_for_status()
        print(f"Backend health: {health.json()['status']}\n")
    except Exception as e:
        print(f"[ERROR] Backend not reachable: {e}", file=sys.stderr)
        sys.exit(1)

    all_metrics = []
    for name, path in USE_CASES.items():
        print(f"Running: {name} ({path.name}) ...")
        m = run_use_case(name, path)
        all_metrics.append(m)
        if m:
            print(f"  {m['total']} examples | errors={m['errors']}")

    print_table(all_metrics)
    write_markdown(all_metrics)


if __name__ == "__main__":
    main()
