"""
app/policy/context_integrity.py
────────────────────────────────
Module — Context Integrity Verification Engine (Trusted Context & Retrieval Validation)

Defends against the #1 agent threat of 2026: PROMPT INJECTION, especially the
*indirect* kind, where malicious instructions are hidden inside data the agent
reads (a document, a web page, an email, a tool output, a RAG result) rather
than typed by the user. The agent then executes those instructions with its own
authority.

This module validates the CONTEXT an agent ingests *before* the agent acts on it:

  1. Injection-pattern detection — scans ingested text for known injection /
     instruction-override / exfiltration markers and hidden (zero-width) unicode.
  2. Source validation — checks the declared source of context against a
     trusted-source allowlist (data/trusted_sources.json).
  3. Freshness check — flags stale context (possible poisoned/outdated policy docs).
  4. Trust aggregation — combines the above into a verdict.

Honest framing (important for defense): prompt injection is an UNSOLVED problem
in 2026 and cannot be fully filtered away. This module RAISES THE COST of
injection and catches common cases; it is one layer in a defense-in-depth stack
alongside the human-in-the-loop and least-privilege policy gates.

Entry point: evaluate_context_integrity(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.decision import DecisionCreate
from app.models.event import EventCreate

_SOURCES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trusted_sources.json"


def _load_sources() -> dict[str, Any]:
    try:
        with open(_SOURCES_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"max_age_days": 365, "trusted_sources": []}


_SOURCES = _load_sources()

# ── Injection signature patterns ───────────────────────────────────────────────
# (severity, compiled-regex, human label). HIGH → BLOCK, MED → WARN.
_HIGH = "HIGH"
_MED = "MED"

_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    (_HIGH, re.compile(r"ignore\s+(all\s+)?(previous|prior|earlier|above)\s+instructions", re.I),
     "instruction-override ('ignore previous instructions')"),
    (_HIGH, re.compile(r"disregard\s+(the\s+)?(previous|above|system|prior)", re.I),
     "instruction-override ('disregard ...')"),
    (_HIGH, re.compile(r"(reveal|print|show|expose|leak)\s+(your|the)\s+(system\s+prompt|instructions|api[\s_-]?key|secret|password|credential)", re.I),
     "secret-exfiltration request"),
    (_HIGH, re.compile(r"(exfiltrat|send\s+.{0,40}\b(to|@)\b.{0,40}(http|https|ftp|@))", re.I),
     "data-exfiltration instruction"),
    (_HIGH, re.compile(r"you\s+are\s+now\s+(a|an|the)\b|new\s+(system\s+)?instructions?\s*:", re.I),
     "role/instruction reassignment"),
    (_HIGH, re.compile(r"curl\s+.+\|\s*(sh|bash)|base64\s+-d|wget\s+.+\|\s*(sh|bash)", re.I),
     "embedded remote-code-execution payload"),
    (_MED, re.compile(r"do\s+not\s+(tell|inform|notify|alert)\s+(the\s+)?(user|human|admin)", re.I),
     "secrecy/hidden-action instruction"),
    (_MED, re.compile(r"(act|pretend|behave)\s+as\s+(if\s+)?(a|an|the)\b|role\s*[:=]\s*system", re.I),
     "role-manipulation phrasing"),
    (_MED, re.compile(r"<!--\s*system|###\s*system|\[system\]|\{\{\s*system", re.I),
     "hidden system-directive marker"),
    (_MED, re.compile(r"override\s+(the\s+)?(safety|guardrail|policy|filter)", re.I),
     "guardrail-override phrasing"),
]

# Zero-width / invisible unicode often used to hide injected instructions.
_HIDDEN_CHARS = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u202e\u202d]")


# ── Context extraction ─────────────────────────────────────────────────────────
_CONTEXT_KEYS = (
    "context", "retrieved_context", "rag_context", "document", "documents",
    "tool_output", "email_body", "web_content", "page_content", "content",
)


def _collect_context(event: EventCreate) -> list[dict[str, Any]]:
    """
    Return a list of context items {content, source, age_days}. Handles:
      - payload['context'] as str, or list of str, or list of {content, source, timestamp/age_days}
      - any of the recognised context keys at payload top level
      - the same keys inside each step of payload['steps']
    """
    items: list[dict[str, Any]] = []
    payload = event.payload or {}

    def _add(val: Any, source: Any = None, age: Any = None) -> None:
        if isinstance(val, str) and val.strip():
            items.append({"content": val, "source": source, "age_days": age})
        elif isinstance(val, dict):
            content = val.get("content") or val.get("text") or ""
            items.append({
                "content": str(content),
                "source": val.get("source", source),
                "age_days": val.get("age_days", _age_from(val.get("timestamp"))),
            })
        elif isinstance(val, list):
            for v in val:
                _add(v, source, age)

    for key in _CONTEXT_KEYS:
        if key in payload:
            _add(payload[key])

    if isinstance(payload.get("steps"), list):
        for step in payload["steps"]:
            if isinstance(step, dict):
                for key in _CONTEXT_KEYS:
                    if key in step:
                        _add(step[key])

    return [it for it in items if it["content"].strip()]


def _age_from(timestamp: Any) -> float | None:
    if not timestamp:
        return None
    try:
        ts = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).days
    except (ValueError, TypeError):
        return None


# ── Evaluation ─────────────────────────────────────────────────────────────────

def evaluate_context_integrity(event: EventCreate) -> DecisionCreate:
    items = _collect_context(event)

    if not items:
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Context integrity: no ingested context to validate."],
            suggested_fix="",
            module="context_integrity",
            risk_score=0.0,
        )

    reasons: list[str] = []
    worst = "ALLOW"
    risk = 0.0
    trusted = set(_SOURCES.get("trusted_sources", []))
    max_age = _SOURCES.get("max_age_days", 365)

    def _escalate(v: str) -> None:
        nonlocal worst
        order = {"ALLOW": 0, "WARN": 1, "BLOCK": 2}
        if order[v] > order[worst]:
            worst = v

    for it in items:
        text = it["content"]

        # 1. Injection-pattern detection
        for severity, pat, label in _PATTERNS:
            if pat.search(text):
                if severity == _HIGH:
                    _escalate("BLOCK")
                    risk = max(risk, 0.95)
                    reasons.append(f"Prompt-injection signature detected in ingested context: {label}.")
                else:
                    _escalate("WARN")
                    risk = max(risk, 0.6)
                    reasons.append(f"Suspicious instruction pattern in ingested context: {label}.")

        # Hidden/invisible unicode
        if _HIDDEN_CHARS.search(text):
            _escalate("WARN")
            risk = max(risk, 0.6)
            reasons.append("Hidden/zero-width unicode characters detected in context (possible concealed injection).")

        # 2. Source validation
        src = it.get("source")
        if src is not None:
            src_ok = any(t in str(src) for t in trusted)
            if not src_ok:
                _escalate("WARN")
                risk = max(risk, 0.55)
                reasons.append(f"Context sourced from untrusted origin '{src}' (not on allowlist).")

        # 3. Freshness
        age = it.get("age_days")
        if isinstance(age, (int, float)) and age > max_age:
            _escalate("WARN")
            risk = max(risk, 0.5)
            reasons.append(f"Context is stale ({int(age)} days old > {max_age}); may be outdated or poisoned.")

    if worst == "ALLOW":
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[f"Context integrity verified: {len(items)} context item(s) passed injection, source and freshness checks."],
            suggested_fix="",
            module="context_integrity",
            risk_score=0.0,
        )

    if worst == "BLOCK":
        fix = (
            "Ingested context contains an active prompt-injection payload. Do NOT let the agent "
            "act on this content: quarantine the source, strip the injected instructions, and "
            "re-fetch from a trusted origin before retrying."
        )
    else:
        fix = (
            "Ingested context is untrusted, stale, or contains suspicious instruction-like text. "
            "Verify the source and content before the agent acts on it; prefer re-fetching from a "
            "trusted, current source."
        )

    return DecisionCreate(
        verdict=worst,
        reasons=reasons,
        suggested_fix=fix,
        module="context_integrity",
        risk_score=round(risk, 4),
    )
