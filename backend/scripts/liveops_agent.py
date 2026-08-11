"""
scripts/liveops_agent.py — LiveOps Increment 4: deterministic CLI agent runner.
────────────────────────────────────────────────────────────────────────────────

A command-line agent that proposes predefined LiveOps actions and OBEYS the
Agent Sentinel decision (ALLOW / WARN / BLOCK). It talks ONLY to the existing
HTTP APIs — it never touches SQLite, the simulated-cloud JSON state files, or
the ORM layer.

This is a DETERMINISTIC selector. The proposals are hard-coded scenarios, NOT
chosen by any AI/LLM planner. There is no planner in this increment.

Contract mapping
────────────────
  proposed action                       verdict        runner behaviour
  stop_vm(dev-unused-01)                ALLOW          call /execute once, show state
  stop_vm(prod-api-01)                  WARN           poll /decide; approve→execute
                                                     once; reject→no execution
                                                     ; timeout→no execution
  delete_snapshot(prod-backup-latest)   BLOCK          never call /execute; verify
                                                     the snapshot is still present

Commands
────────
  python scripts/liveops_agent.py demo              # full 3-scenario demo
  python scripts/liveops_agent.py dev-allow
  python scripts/liveops_agent.py prod-review
  python scripts/liveops_agent.py snapshot-block

Options
───────
  --base-url URL            backend base URL (default http://127.0.0.1:8000,
                            override with env AGENT_SENTINEL_URL)
  --poll-interval SECONDS   WARN decision-poll interval (default 2.0)
  --approval-timeout SECONDS  overall time to wait for a human decision (default 60.0)

Exit codes
──────────
  0  success (includes the documented WARN-rejected and BLOCK outcomes)
  1  an HTTP error occurred
  2  execution conflict (HTTP 409) — reported, never retried
  3  WARN approval timed out — never executed
  4  unexpected verdict from the backend
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_APPROVAL_TIMEOUT = 60.0

EXIT_OK = 0
EXIT_HTTP_ERROR = 1
EXIT_EXECUTION_CONFLICT = 2
EXIT_APPROVAL_TIMEOUT = 3
EXIT_UNEXPECTED_VERDICT = 4


# ── Proposals (deterministic — no planner) ─────────────────────────────────────
# The body mirrors exactly what app/adapters/liveops_adapter.py produces so the
# backend policy pipeline sees the same normalised shape it already handles.
def _normalised_proposal(tool: str, target: str, goal: str, session_id: str) -> dict[str, Any]:
    return {
        "source": "liveops",
        "event_type": tool,
        "original_goal": goal,
        "payload": {
            "tool": tool,
            "capability": tool,
            "target": target or "",
            "resource": target or "",
            "description": f"{tool} {target}".strip(),
            "session_id": session_id,
        },
    }


_GOAL_DEV = "Clean unused development resources to reduce cost."
_GOAL_PROD = "Clean unused production resources to reduce cost."
_GOAL_SNAPSHOT = "Remove stale production backup snapshots."

SCENARIOS: dict[str, dict[str, str]] = {
    "dev-allow": {
        "title": "Development VM stop",
        "tool": "stop_vm",
        "target": "dev-unused-01",
        "goal": _GOAL_DEV,
        "session_id": "agent-demo-dev",
    },
    "prod-review": {
        "title": "Production VM stop (requires human review)",
        "tool": "stop_vm",
        "target": "prod-api-01",
        "goal": _GOAL_PROD,
        "session_id": "agent-demo-prod",
    },
    "snapshot-block": {
        "title": "Protected production snapshot delete",
        "tool": "delete_snapshot",
        "target": "prod-backup-latest",
        "goal": _GOAL_SNAPSHOT,
        "session_id": "agent-demo-snap",
    },
}


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def make_client(base_url: str, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Build an httpx client for the backend (optionally with a MockTransport)."""
    return httpx.Client(base_url=base_url, timeout=30.0, transport=transport)


def _load_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except ValueError:  # httpx raises ValueError on invalid JSON
        return {}


def _submit_proposal(client: httpx.Client, proposal: dict[str, Any]) -> httpx.Response:
    """POST /api/evaluate with one normalised proposal."""
    return client.post("/api/evaluate", json=proposal)


def _execute(client: httpx.Client, event_id: int) -> httpx.Response:
    """POST /api/liveops/execute/{event_id}."""
    return client.post(f"/api/liveops/execute/{event_id}")


def _fetch_decision(client: httpx.Client, event_id: int) -> httpx.Response:
    """GET /api/decide/{event_id} — poll for the human decision."""
    return client.get(f"/api/decide/{event_id}")


def _fetch_state(client: httpx.Client) -> httpx.Response:
    """GET /api/liveops/state."""
    return client.get("/api/liveops/state")


def _reset_state(client: httpx.Client) -> httpx.Response:
    """POST /api/liveops/reset — restore canonical seed state."""
    return client.post("/api/liveops/reset")


# ── Reporting ──────────────────────────────────────────────────────────────────

def _report_evaluation(proposal: dict[str, Any], body: dict[str, Any]) -> int:
    event = body.get("event", {})
    decision = body.get("decision", {})
    print("  user goal :", proposal.get("original_goal"))
    print(
        "  propose   : %s(%s)"
        % (
            proposal.get("event_type"),
            (proposal.get("payload") or {}).get("target") or "",
        )
    )
    print("  event id  :", event.get("id"))
    print("  verdict   :", decision.get("verdict"))
    print("  risk score:", decision.get("risk_score"))
    for reason in decision.get("reasons") or []:
        print("  reason    :", reason)
    return event.get("id")


def _report_execution(resp: httpx.Response) -> None:
    body = _load_json(resp)
    print("  execution : HTTP", resp.status_code)
    if resp.status_code == 200:
        print("  exec status:", body.get("status"))
        result = body.get("result") or {}
        for vm in result.get("vms") or []:
            print(
                "  observed  : vm %s state=%s" % (vm.get("id"), vm.get("state"))
            )
        for snap in result.get("snapshots") or []:
            print(
                "  observed  : snapshot %s (source_vm=%s)"
                % (snap.get("id"), snap.get("source_vm"))
            )


# ── Scenario runners ───────────────────────────────────────────────────────────

def _execute_and_report(client: httpx.Client, event_id: int) -> int:
    """Call /execute once. A 409 conflict is reported and NOT retried."""
    resp = _execute(client, event_id)
    if resp.status_code == 200:
        _report_execution(resp)
        return EXIT_OK
    if resp.status_code == 409:
        body = _load_json(resp)
        print("  execution : ALREADY EXECUTED (HTTP 409) — reported, not retried.")
        print("  detail    :", body.get("detail", ""))
        return EXIT_EXECUTION_CONFLICT
    body = _load_json(resp)
    print("  execution : FAILED — HTTP", resp.status_code)
    print("  detail    :", body.get("detail", ""))
    return EXIT_HTTP_ERROR


def _poll_human_decision(
    client: httpx.Client,
    event_id: int,
    poll_interval: float,
    approval_timeout: float,
) -> str | None:
    """Poll GET /api/decide/{event_id} until a human decision appears or timeout."""
    deadline = time.monotonic() + approval_timeout
    while time.monotonic() < deadline:
        resp = _fetch_decision(client, event_id)
        if resp.status_code != 200:
            print("  review    : decision fetch FAILED — HTTP", resp.status_code)
            return "<http-error>"
        decision = _load_json(resp)
        human = decision.get("human_decision")
        if human in ("approved", "rejected"):
            print("  review    : human decision =", human)
            return human
        time.sleep(poll_interval)
    return None


def run_proposal(
    client: httpx.Client,
    scenario: dict[str, str],
    poll_interval: float,
    approval_timeout: float,
) -> int:
    """Submit one proposal and obey the returned verdict."""
    print(f"\n=== {scenario['title']} ===")
    proposal = _normalised_proposal(
        tool=scenario["tool"],
        target=scenario["target"],
        goal=scenario["goal"],
        session_id=scenario["session_id"],
    )

    resp = _submit_proposal(client, proposal)
    if resp.status_code != 200:
        body = _load_json(resp)
        print("  submit    : FAILED — HTTP", resp.status_code)
        print("  detail    :", body.get("detail", ""))
        return EXIT_HTTP_ERROR
    body = _load_json(resp)
    event_id = _report_evaluation(proposal, body)
    verdict = (body.get("decision") or {}).get("verdict")

    if verdict == "ALLOW":
        print("  action    : ALLOW — executing once.")
        return _execute_and_report(client, event_id)

    if verdict == "WARN":
        print("  action    : WARN — waiting for human review...")
        human = _poll_human_decision(client, event_id, poll_interval, approval_timeout)
        if human == "approved":
            print("  action    : approved — executing exactly once.")
            return _execute_and_report(client, event_id)
        if human == "rejected":
            print("  action    : rejected — NOT executing.")
            return EXIT_OK
        if human is None:
            print(
                f"  action    : approval timed out after {approval_timeout:.1f}s — "
                "NOT executing."
            )
            return EXIT_APPROVAL_TIMEOUT
        return EXIT_HTTP_ERROR  # human == "<http-error>"

    if verdict == "BLOCK":
        print("  action    : BLOCK — never calling execution.")
        state_resp = _fetch_state(client)
        if state_resp.status_code != 200:
            print("  verify    : state fetch FAILED — HTTP", state_resp.status_code)
            return EXIT_HTTP_ERROR
        state = _load_json(state_resp)
        snapshots = {s.get("id") for s in state.get("snapshots") or []}
        if scenario["target"] in snapshots:
            print(f"  verify    : protected snapshot {scenario['target']} still present.")
            return EXIT_OK
        print(f"  verify    : WARNING — snapshot {scenario['target']} missing after BLOCK.")
        return EXIT_HTTP_ERROR

    print("  action    : unexpected verdict", verdict)
    return EXIT_UNEXPECTED_VERDICT


def run_demo(
    client: httpx.Client,
    poll_interval: float,
    approval_timeout: float,
) -> int:
    """Reset the sandbox once, then walk all three scenarios."""
    print("=== LiveOps agent demo (deterministic scenarios; no planner) ===")
    reset = _reset_state(client)
    if reset.status_code != 200:
        print("  reset     : FAILED — HTTP", reset.status_code)
        return EXIT_HTTP_ERROR
    print("  reset     : sandbox state restored from seed.")

    for name in ("dev-allow", "prod-review", "snapshot-block"):
        code = run_proposal(client, SCENARIOS[name], poll_interval, approval_timeout)
        if code != EXIT_OK:
            print(f"\nDemo stopped early after '{name}' (exit {code}).")
            return code
    print("\n=== Demo complete — all scenarios honoured the verdicts. ===")
    return EXIT_OK


# ── CLI entry point ────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="liveops_agent",
        description=(
            "Deterministic LiveOps agent runner. Submits predefined proposals via "
            "POST /api/evaluate and obeys ALLOW/WARN/BLOCK verdicts. "
            "The proposal selector is NOT AI-driven."
        ),
    )
    parser.add_argument(
        "command",
        choices=sorted(SCENARIOS.keys()) + ["demo"],
        help="dev-allow | prod-review | snapshot-block | demo (full walkthrough)",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AGENT_SENTINEL_URL", DEFAULT_BASE_URL),
        help=f"backend base URL (default {DEFAULT_BASE_URL}; env AGENT_SENTINEL_URL)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="WARN decision poll interval in seconds",
    )
    parser.add_argument(
        "--approval-timeout",
        type=float,
        default=DEFAULT_APPROVAL_TIMEOUT,
        help="overall time (seconds) to wait for a human WARN decision",
    )
    return parser


def main(argv: list[str] | None = None, transport: httpx.BaseTransport | None = None) -> int:
    """
    CLI entry point.

    ``transport`` is a testing seam: tests inject an httpx.MockTransport so no
    real network or backend is used. Locally it expects the FastAPI server at
    the configured base URL.
    """
    args = _build_parser().parse_args(argv)
    client = make_client(args.base_url, transport=transport)
    try:
        if args.command == "demo":
            return run_demo(client, args.poll_interval, args.approval_timeout)
        return run_proposal(
            client, SCENARIOS[args.command], args.poll_interval, args.approval_timeout
        )
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())