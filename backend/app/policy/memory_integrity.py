"""
app/policy/memory_integrity.py
───────────────────────────────
Module — Memory Poisoning Defense

Agents with long-term memory can be permanently corrupted if an attacker gets
malicious content written into that memory: every future decision is then biased
by the poisoned entry. The critical rule (NIST-aligned): user/external input must
never modify core behavioural rules, and every memory write must be validated at
the ingestion layer.

This module fires when an event carries a memory write (payload 'memory_write').
It enforces:

  1. Core-rule protection — external/user-sourced writes may NOT target the
     agent's core/system memory scope → BLOCK.
  2. Injection validation — memory content is scanned for adversarial/injection
     strings before it is admitted → BLOCK.
  3. Provenance metadata — every write must carry source + timestamp; missing
     provenance → WARN (can't audit or roll back later).
  4. Untrusted origin — external-sourced writes to shared memory → WARN.

Entry point: evaluate_memory_integrity(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import re
from typing import Any

from app.models.decision import DecisionCreate
from app.models.event import EventCreate

_INJECTION = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|system)\s+instructions", re.I),
    re.compile(r"(you\s+are\s+now|new\s+(system\s+)?rule|always\s+(from\s+now|obey))", re.I),
    re.compile(r"(reveal|send|exfiltrat|leak).{0,30}(secret|key|password|credential|http)", re.I),
    re.compile(r"override\s+(safety|policy|guardrail)|disregard\s+(the\s+)?(system|rules)", re.I),
]

_CORE_SCOPES = {"core", "system", "core_rules", "system_prompt", "behavioural_rules"}
_TRUSTED_ORIGINS = {"system", "internal", "verified-admin"}


def evaluate_memory_integrity(event: EventCreate) -> DecisionCreate:
    payload = event.payload or {}
    mem = payload.get("memory_write")
    if not isinstance(mem, dict):
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Memory integrity: no memory write in this action."],
            suggested_fix="",
            module="memory_integrity",
            risk_score=0.0,
        )

    content = str(mem.get("content", ""))
    scope = str(mem.get("scope", "working")).lower()
    origin = str(mem.get("source", mem.get("origin", ""))).lower()
    has_ts = bool(mem.get("timestamp"))

    reasons: list[str] = []
    verdict = "ALLOW"
    risk = 0.0
    order = {"ALLOW": 0, "WARN": 1, "BLOCK": 2}

    def _esc(v: str, r: float, msg: str) -> None:
        nonlocal verdict, risk
        if order[v] > order[verdict]:
            verdict = v
        risk = max(risk, r)
        reasons.append(msg)

    external = origin not in _TRUSTED_ORIGINS

    # 1. Core-rule protection
    if scope in _CORE_SCOPES and external:
        _esc("BLOCK", 0.95,
             f"Attempt to write to protected '{scope}' memory from external/user origin "
             f"'{origin or 'unknown'}'. Core behavioural rules may not be modified by external input.")

    # 2. Injection validation
    for pat in _INJECTION:
        if pat.search(content):
            _esc("BLOCK", 0.9,
                 "Memory write contains an adversarial/injection string that would poison future decisions.")
            break

    # 3. Provenance metadata
    if not origin or not has_ts:
        _esc("WARN", 0.5,
             "Memory write is missing provenance metadata (source and/or timestamp); "
             "it cannot be audited or rolled back if later found malicious.")

    # 4. Untrusted origin to shared memory
    if external and scope in ("shared", "long_term", "global") and verdict == "ALLOW":
        _esc("WARN", 0.55,
             f"External-origin write to '{scope}' memory — validate before persisting to shared state.")

    if verdict == "ALLOW":
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[f"Memory write verified (scope='{scope}', origin='{origin or 'system'}')."],
            suggested_fix="",
            module="memory_integrity",
            risk_score=0.0,
        )

    fix = (
        "Reject or quarantine this memory write. Keep core/system memory physically separate from "
        "external input, scan content for injection at ingestion, and require source+timestamp "
        "provenance on every entry so corruption can be audited and rolled back."
    )
    return DecisionCreate(
        verdict=verdict, reasons=reasons, suggested_fix=fix,
        module="memory_integrity", risk_score=round(risk, 4),
    )
