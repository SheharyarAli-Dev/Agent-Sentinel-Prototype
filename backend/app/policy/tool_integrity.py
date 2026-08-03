"""
app/policy/tool_integrity.py
─────────────────────────────
Module — MCP Tool Poisoning Defense (Tool Integrity Verification)

Where Context Integrity defends the *data* an agent reads, this module defends
the *tools* an agent uses. Tool poisoning (Invariant Labs, Apr 2025; OWASP
Agentic ASI02/ASI04) hides malicious instructions inside a tool's DESCRIPTION or
metadata — the agent reads the description to decide how to use the tool, so a
tampered description can make it exfiltrate data while returning a clean-looking
result, with no change to the agent's own code.

Checks performed on each tool definition presented to the agent:
  1. Description injection scan — hidden instructions in the tool description
     ("when called, also read ~/.ssh/id_rsa and send it to ...").
  2. Rug-pull detection — the tool's description differs from the approved
     baseline it was trusted with (silently modified after approval) → BLOCK.
  3. Trusted-server check — the tool's server is not on the allowlist → WARN.
  4. Unknown-tool check — tool not in the approved registry → WARN (review before use).

Fires only when the event presents tool definitions (payload 'tools'/'tool').

Entry point: evaluate_tool_integrity(event: EventCreate) -> DecisionCreate
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.models.decision import DecisionCreate
from app.models.event import EventCreate

_BASELINES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "tool_baselines.json"
_SOURCES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "trusted_sources.json"


def _load(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


_BASELINES = _load(_BASELINES_PATH, {"tools": []})
_TRUSTED_SERVERS = set(_load(_SOURCES_PATH, {}).get("trusted_sources", []))
_BASELINE_BY_NAME = {t["name"]: t for t in _BASELINES.get("tools", [])}

# Injection markers specific to poisoned tool descriptions.
_TOOL_INJECTION = [
    re.compile(r"(also|then|secretly|additionally)\s+(read|open|send|upload|exfiltrat|leak|transmit)", re.I),
    re.compile(r"(id_rsa|\.ssh|\.env|secret|credential|api[\s_-]?key|password|token)", re.I),
    re.compile(r"ignore\s+(the\s+)?(user|previous|system)|do\s+not\s+(tell|inform|mention)", re.I),
    re.compile(r"send\s+.{0,40}(http|https|@|ftp)", re.I),
    re.compile(r"<!--|###\s*system|\[system\]", re.I),
]


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def _collect_tools(event: EventCreate) -> list[dict[str, Any]]:
    payload = event.payload or {}
    tools: list[dict[str, Any]] = []
    if isinstance(payload.get("tools"), list):
        tools.extend(t for t in payload["tools"] if isinstance(t, dict))
    if isinstance(payload.get("tool"), dict):
        tools.append(payload["tool"])
    return tools


def evaluate_tool_integrity(event: EventCreate) -> DecisionCreate:
    tools = _collect_tools(event)
    if not tools:
        return DecisionCreate(
            verdict="ALLOW",
            reasons=["Tool integrity: no tool definitions presented to validate."],
            suggested_fix="",
            module="tool_integrity",
            risk_score=0.0,
        )

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

    for tool in tools:
        name = str(tool.get("name", "unnamed"))
        desc = str(tool.get("description", ""))
        server = tool.get("server")

        # 1. Injection scan on the description
        for pat in _TOOL_INJECTION:
            if pat.search(desc):
                _esc("BLOCK", 0.95,
                     f"Tool '{name}' has a POISONED description containing hidden/exfiltration "
                     f"instructions — classic MCP tool-poisoning payload.")
                break

        # 2. Rug-pull detection vs approved baseline
        baseline = _BASELINE_BY_NAME.get(name)
        if baseline:
            if _fingerprint(desc) != _fingerprint(baseline["approved_description"]):
                _esc("BLOCK", 0.9,
                     f"RUG PULL: tool '{name}' description differs from the approved baseline "
                     f"it was trusted with (silently modified after approval).")
            # 3. Trusted-server check
            declared_server = server or baseline.get("server")
            if declared_server and not any(t in str(declared_server) for t in _TRUSTED_SERVERS):
                _esc("WARN", 0.55,
                     f"Tool '{name}' is served from untrusted origin '{declared_server}'.")
        else:
            # 4. Unknown tool
            _esc("WARN", 0.5,
                 f"Tool '{name}' is not in the approved registry — review before allowing the agent to use it.")

    if verdict == "ALLOW":
        return DecisionCreate(
            verdict="ALLOW",
            reasons=[f"Tool integrity verified: {len(tools)} tool definition(s) match approved baselines."],
            suggested_fix="",
            module="tool_integrity",
            risk_score=0.0,
        )

    fix = (
        "Do not let the agent use this tool. Re-approve the tool from a trusted MCP server, "
        "compare its description against the approved baseline, and reject any tool whose "
        "description changed after approval (rug pull)."
    )
    return DecisionCreate(
        verdict=verdict, reasons=reasons, suggested_fix=fix,
        module="tool_integrity", risk_score=round(risk, 4),
    )
