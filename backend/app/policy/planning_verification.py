"""
app/policy/planning_verification.py
──────────────────────────────────────
Module 7 — Planning Verification Engine

Validates an agent's ENTIRE multi-step plan as a single unit BEFORE any step executes.

Three responsibilities:
  a) Whole-plan safety check:
     - Protected path violations (data/protected_paths.json)
     - Destructive command/operation patterns (rm -rf, DROP TABLE, git push --force)
     - Total scope threshold (too many steps or files touched)
     - Step contradiction detection (writing then deleting same file)

  b) Code-quality pattern check [Cursor adapter only]:
     - Passes step code through code_quality_patterns.py
     - Skipped for n8n events

  c) Suggested-fix output:
     - Every WARN / BLOCK verdict carries a non-empty suggested_fix string.

Entry point: evaluate_plan(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.models.decision import DecisionCreate
from app.models.event import EventCreate
from app.policy.code_quality_patterns import check_code_quality

# ── Load Protected Paths Data ──────────────────────────────────────────────────
_PROTECTED_PATH = Path(__file__).parent.parent.parent / "data" / "protected_paths.json"


def _load_protected_paths() -> tuple[list[str], list[str]]:
    """Load exact paths and glob patterns from data/protected_paths.json."""
    if not _PROTECTED_PATH.exists():
        return [], []
    try:
        with open(_PROTECTED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("protected_paths", []), data.get("protected_patterns", [])
    except Exception:
        return [], []


def evaluate_plan(event: EventCreate) -> DecisionCreate:
    """
    Evaluate a multi-step plan event against Module 7 rules.

    Args:
        event: The normalised plan execution event.

    Returns:
        DecisionCreate with verdict (ALLOW, WARN, BLOCK), reasons, suggested_fix,
        module="planning_verification", and risk_score.
    """
    payload = event.payload or {}

    # Extract steps list from payload or wrap single action as a 1-step plan
    raw_steps = payload.get("steps")
    if isinstance(raw_steps, list):
        steps = raw_steps
    else:
        steps = [{
            "type": event.event_type,
            "target": payload.get("target") or payload.get("destination") or payload.get("file") or "",
            "description": payload.get("description") or payload.get("command") or event.event_type,
            "code": payload.get("code") or "",
        }]

    reasons: list[str] = []
    fixes: list[str] = []
    verdict = "ALLOW"
    risk_scores: list[float] = []

    exact_paths, glob_patterns = _load_protected_paths()

    # ── 1. Destructive Command Patterns ───────────────────────────────────────
    DESTRUCTIVE_PATTERNS = [
        ("rm -rf", "Recursive directory deletion ('rm -rf')"),
        ("drop table", "SQL table deletion ('DROP TABLE')"),
        ("git push --force", "Git force push ('git push --force')"),
        ("git push -f", "Git force push ('git push -f')"),
        ("chmod 777", "Insecure wide-open permissions ('chmod 777')"),
        ("delete without backup", "Unsafe file deletion"),
    ]

    for idx, step in enumerate(steps, 1):
        target = str(step.get("target", "")).lower()
        desc = str(step.get("description", "")).lower()
        code = str(step.get("code", "")).lower()
        cmd = str(step.get("command", "")).lower()
        full_text = f"{target} {desc} {code} {cmd}"

        for pat, pat_name in DESTRUCTIVE_PATTERNS:
            if pat in full_text:
                verdict = "BLOCK"
                reasons.append(f"Destructive pattern detected in step #{idx}: {pat_name}.")
                fixes.append(f"Require explicit human authorization and verify data backup before executing '{pat}'.")
                risk_scores.append(1.0)

    # ── 2. Protected Paths Violations ─────────────────────────────────────────
    for idx, step in enumerate(steps, 1):
        target = str(step.get("target", "")).strip()
        if not target:
            continue

        target_norm = target.replace("\\", "/").lower()

        # Check exact paths
        is_protected = any(p.lower() in target_norm for p in exact_paths)

        # Check glob patterns
        if not is_protected:
            filename = Path(target).name.lower()
            is_protected = any(fnmatch.fnmatch(filename, pat.lower()) for pat in glob_patterns)

        if is_protected:
            if verdict != "BLOCK":
                verdict = "WARN"
            reasons.append(f"Protected resource targeted in step #{idx}: '{target}'.")
            fixes.append(f"This step modifies a protected path ('{target}') — confirm this is intentional before proceeding.")
            risk_scores.append(0.80)

    # ── 3. Plan Scope Thresholds ──────────────────────────────────────────────
    if len(steps) > settings.plan_scope_threshold:
        if verdict != "BLOCK":
            verdict = "WARN"
        reasons.append(
            f"Broad plan scope: Plan contains {len(steps)} steps, exceeding the threshold of {settings.plan_scope_threshold}."
        )
        fixes.append(f"Decompose large plan into smaller, incremental task batches (<= {settings.plan_scope_threshold} steps).")
        risk_scores.append(0.60)

    distinct_files = {
        step.get("target") for step in steps if step.get("target") and isinstance(step.get("target"), str)
    }
    if len(distinct_files) > settings.plan_file_scope_threshold:
        if verdict != "BLOCK":
            verdict = "WARN"
        reasons.append(
            f"Broad file scope: Plan touches {len(distinct_files)} distinct files, exceeding the threshold of {settings.plan_file_scope_threshold}."
        )
        fixes.append(f"Limit the number of modified files per plan session to {settings.plan_file_scope_threshold}.")
        risk_scores.append(0.60)

    # ── 4. Step Contradiction Check ───────────────────────────────────────────
    created_files: set[str] = set()
    for idx, step in enumerate(steps, 1):
        stype = str(step.get("type", "")).lower()
        target = str(step.get("target", "")).strip()

        if ("write" in stype or "create font" in stype or "add" in stype) and target:
            created_files.add(target)
        elif ("delete" in stype or "remove" in stype) and target in created_files:
            if verdict != "BLOCK":
                verdict = "WARN"
            reasons.append(f"Step contradiction: Step #{idx} deletes file '{target}' which was created in an earlier step of this plan.")
            fixes.append(f"Remove contradictory write-delete step for file '{target}'.")
            risk_scores.append(0.70)

    # ── 5. Code Quality Pattern Check (Cursor Adapter Only) ───────────────────
    if event.source == "cursor":
        for idx, step in enumerate(steps, 1):
            code_snippet = step.get("code") or ""
            if code_snippet:
                matches = check_code_quality(code_snippet)
                for m in matches:
                    if verdict != "BLOCK":
                        verdict = "WARN"
                    reasons.append(f"Code-quality pattern '{m.name}' detected in step #{idx}: {m.description}")
                    fixes.append(m.suggested_fix)
                    risk_scores.append(0.65)

    # ── Finalise Decision ───────────────────────────────────────────────────────
    if verdict == "ALLOW":
        reasons.append("Planning verification passed: Plan steps are safe, within scope, and non-destructive.")
        suggested_fix = ""
        risk_score = 0.0
    else:
        suggested_fix = " | ".join(fixes)
        risk_score = round(max(risk_scores) if risk_scores else 0.5, 4)

    return DecisionCreate(
        verdict=verdict,
        reasons=reasons,
        suggested_fix=suggested_fix,
        module="planning_verification",
        risk_score=risk_score,
    )
