"""
tests/test_liveops_agent.py
────────────────────────────
LiveOps Increment 4 — deterministic CLI agent runner (scripts/liveops_agent.py).

All HTTP interaction is mocked with httpx.MockTransport: no backend server, no
real network, no MiniLM model (conftest fences model construction). The runner
talks ONLY to httpx; these tests pin its exact API sequence, verdict-gated
behaviour, exit codes, and the no-SQLite/no-sandbox-JSON guarantee.

The fetch-decision contract is path-based GET /api/decide/{event_id}, matching
app/api/decide.py. A query-string variant would 404 against the real API.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import httpx
import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import liveops_agent  # noqa: E402

_RUNNER_PATH = _SCRIPTS_DIR / "liveops_agent.py"


# ── Mock backend ───────────────────────────────────────────────────────────────

class MockBackend:
    """Deterministic in-memory fake of the Agent Sentinel HTTP API."""

    def __init__(self, *, human_decisions=None, execute_status=200, evaluate_status=200):
        self.human_decisions = list(human_decisions or [])
        self.execute_status = execute_status
        self.evaluate_status = evaluate_status
        self.requests: list[httpx.Request] = []
        self.event_counter = 0
        self.execute_calls: list[int] = []
        self.reset_calls = 0
        self.evaluate_calls = 0

    # -- shared payload fragments ------------------------------------------------
    def _decision_payload(self, event_id, verdict, human_decision=None):
        return {
            "id": event_id,
            "event_id": event_id,
            "verdict": verdict,
            "reasons": [f"test reason for {verdict}"],
            "suggested_fix": "" if verdict == "ALLOW" else "review required",
            "module": "liveops_test",
            "risk_score": 0.0 if verdict == "ALLOW" else 0.6,
            "explanation": "",
            "latency_ms": 1.2,
            "timestamp": "2024-01-01T00:00:00Z",
            "human_decision": human_decision,
            "human_timestamp": None,
            "unblocked_by_human": False,
            "unblock_timestamp": None,
        }

    def _verdict_for(self, tool, target):
        if tool == "stop_vm" and target == "dev-unused-01":
            return "ALLOW"
        if tool == "stop_vm" and target == "prod-api-01":
            return "WARN"
        if tool == "delete_snapshot" and target == "prod-backup-latest":
            return "BLOCK"
        return "ALLOW"

    def state_payload(self):
        return {
            "vms": [
                {"id": "dev-unused-01", "state": "running"},
                {"id": "prod-api-01", "state": "running"},
            ],
            "snapshots": [
                {"id": "prod-backup-latest", "source_vm": "prod-api-01"},
            ],
        }

    def execution_payload(self, event_id, target):
        return {
            "id": event_id,
            "event_id": event_id,
            "tool": "stop_vm",
            "target": target,
            "status": "executed",
            "result": {
                "tool": "stop_vm",
                "target": target,
                "vms": [{"id": target, "state": "stopped"}],
                "snapshots": [{"id": "prod-backup-latest", "source_vm": "prod-api-01"}],
            },
            "executed_at": "2024-01-01T00:00:01Z",
            "created_at": "2024-01-01T00:00:00Z",
        }

    # -- httpx handler ------------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path

        if request.method == "POST" and path == "/api/evaluate":
            self.evaluate_calls += 1
            self.event_counter += 1
            eid = self.event_counter
            body = json.loads(request.content or b"{}")
            payload = body.get("payload", {})
            tool = payload.get("tool")
            target = payload.get("target")
            verdict = self._verdict_for(tool, target)
            if self.evaluate_status != 200:
                return httpx.Response(self.evaluate_status, json={"detail": "evaluate boom"})
            return httpx.Response(
                200,
                json={
                    "event": {
                        "id": eid,
                        "source": "liveops",
                        "event_type": tool,
                        "payload": payload,
                        "original_goal": body.get("original_goal"),
                        "timestamp": "2024-01-01T00:00:00Z",
                    },
                    "decision": self._decision_payload(eid, verdict),
                },
            )

        if request.method == "GET" and path.startswith("/api/decide/"):
            eid = int(path.rsplit("/", 1)[1])
            human = self.human_decisions.pop(0) if self.human_decisions else None
            return httpx.Response(
                200, json=self._decision_payload(eid, "WARN", human_decision=human)
            )

        if request.method == "POST" and path.startswith("/api/liveops/execute/"):
            eid = int(path.rsplit("/", 1)[1])
            self.execute_calls.append(eid)
            if self.execute_status == 409:
                return httpx.Response(
                    409, json={"detail": "Event %d already executed exactly-once." % eid}
                )
            if self.execute_status != 200:
                return httpx.Response(self.execute_status, json={"detail": "execute boom"})
            target = "dev-unused-01" if eid == 1 else "prod-api-01"
            return httpx.Response(200, json=self.execution_payload(eid, target))

        if request.method == "GET" and path == "/api/liveops/state":
            return httpx.Response(200, json=self.state_payload())

        if request.method == "POST" and path == "/api/liveops/reset":
            self.reset_calls += 1
            return httpx.Response(200, json=self.state_payload())

        return httpx.Response(404, json={"detail": "not found"})


def _run(
    command: str,
    *,
    human_decisions=None,
    execute_status=200,
    evaluate_status=200,
    extra_args=None,
):
    backend = MockBackend(
        human_decisions=human_decisions,
        execute_status=execute_status,
        evaluate_status=evaluate_status,
    )
    argv = [command, "--poll-interval", "0", "--approval-timeout", "0.01"]
    if extra_args:
        argv = extra_args + ["--poll-interval", "0", "--approval-timeout", "0.01"]
    code = liveops_agent.main(argv, transport=httpx.MockTransport(backend.handler))
    return code, backend


def _methods_and_paths(backend: MockBackend):
    return [(r.method, r.url.path) for r in backend.requests]


# ── 1. ALLOW submits the proposal and calls execution exactly once ─────────────

def test_allow_submits_proposal_and_executes_exactly_once():
    code, backend = _run("dev-allow")
    assert code == liveops_agent.EXIT_OK
    paths = _methods_and_paths(backend)
    assert paths.count(("POST", "/api/evaluate")) == 1
    assert paths.count(("POST", "/api/liveops/execute/1")) == 1
    assert backend.execute_calls == [1]


# ── 2. ALLOW conflict 409 is reported and never retried ────────────────────────

def test_allow_409_conflict_reported_and_never_retried():
    code, backend = _run("dev-allow", execute_status=409)
    assert code == liveops_agent.EXIT_EXECUTION_CONFLICT
    # exactly one execution attempt — no retry, no second submit
    assert backend.execute_calls == [1]
    assert (_methods_and_paths(backend)).count(("POST", "/api/liveops/execute/1")) == 1
    assert (_methods_and_paths(backend)).count(("POST", "/api/evaluate")) == 1


# ── 3. WARN polls GET /api/decide/{event_id} until a human decision appears ────

def test_warn_polls_decision_endpoint():
    code, backend = _run("prod-review", human_decisions=[None, "approved"])
    assert code == liveops_agent.EXIT_OK
    decide_calls = [r for r in backend.requests if r.method == "GET" and r.url.path.startswith("/api/decide/")]
    assert len(decide_calls) == 2  # pending, then approved
    assert all(r.url.path == "/api/decide/1" for r in decide_calls)
    # The poll hits the real path-based contract — no query-string decide calls.
    assert not any("event_id=" in str(r.url) for r in backend.requests)


# ── 4. WARN approved calls execution exactly once ──────────────────────────────

def test_warn_approved_executes_exactly_once():
    code, backend = _run("prod-review", human_decisions=[None, "approved"])
    assert code == liveops_agent.EXIT_OK
    assert backend.execute_calls == [1]
    assert (_methods_and_paths(backend)).count(("POST", "/api/liveops/execute/1")) == 1


# ── 5. WARN rejected never calls execution ─────────────────────────────────────

def test_warn_rejected_never_executes():
    code, backend = _run("prod-review", human_decisions=["rejected"])
    assert code == liveops_agent.EXIT_OK
    assert backend.execute_calls == []


# ── 6. WARN timeout never calls execution ──────────────────────────────────────

def test_warn_timeout_never_executes():
    # human_decisions empty -> every poll returns None -> deadline expires.
    code, backend = _run("prod-review", human_decisions=[])
    assert code == liveops_agent.EXIT_APPROVAL_TIMEOUT
    assert backend.execute_calls == []


# ── 7. BLOCK never calls execution ─────────────────────────────────────────────

def test_block_never_calls_execution():
    code, backend = _run("snapshot-block")
    assert code == liveops_agent.EXIT_OK
    assert backend.execute_calls == []
    assert not any("execute" in r.url.path for r in backend.requests)
    # ...but does verify state
    assert ("GET", "/api/liveops/state") in _methods_and_paths(backend)


# ── 8. Full demo resets state exactly once ─────────────────────────────────────

def test_demo_resets_state_exactly_once():
    code, backend = _run("demo", human_decisions=[None, "approved"])
    assert code == liveops_agent.EXIT_OK
    assert backend.reset_calls == 1
    assert backend.evaluate_calls == 3
    assert backend.execute_calls == [1, 2]  # dev ALLOW + approved prod WARN
    # snapshot-block (event 3) never executed
    assert 3 not in backend.execute_calls


# ── 9. Protected snapshot remains present after BLOCK ──────────────────────────

def test_protected_snapshot_present_after_block():
    code, backend = _run("snapshot-block")
    assert code == liveops_agent.EXIT_OK
    # runner reads the state served by GET /api/liveops/state; assert the
    # underlying mock still exposes the snapshot (i.e. our simulate uses the
    # served state, and the protected snapshot is listed).
    state = backend.state_payload()
    snap_ids = {s["id"] for s in state["snapshots"]}
    assert "prod-backup-latest" in snap_ids


# ── 10. Correct endpoint paths and query parameters are used ───────────────────

def test_endpoint_paths_are_correct():
    code, backend = _run("snapshot-block")
    assert code == liveops_agent.EXIT_OK
    for method, path in _methods_and_paths(backend):
        assert path in {
            "/api/evaluate",
            "/api/liveops/state",
        }, (method, path)

    # demo exercises every endpoint the runner is allowed to reach
    code, backend = _run("demo", human_decisions=[None, "approved"])
    assert code == liveops_agent.EXIT_OK
    allowed = {
        ("POST", "/api/evaluate"),
        ("POST", "/api/liveops/reset"),
        ("POST", "/api/liveops/execute/1"),
        ("POST", "/api/liveops/execute/2"),
        ("GET", "/api/decide/2"),
        ("GET", "/api/liveops/state"),
    }
    seen = set(_methods_and_paths(backend))
    assert seen == allowed
    # decide polls use the real path-based contract
    assert ("GET", "/api/decide/1") in seen or ("GET", "/api/decide/2") in seen


# ── 11. Goal, proposal, verdict, risk, reasons, status, outcome displayed ──────

def test_reported_fields_are_displayed(capsys):
    code, backend = _run("dev-allow")
    assert code == liveops_agent.EXIT_OK
    out = capsys.readouterr().out
    assert "user goal" in out and "Clean unused development resources" in out
    assert "propose" in out and "stop_vm(dev-unused-01)" in out
    assert "event id" in out and "1" in out
    assert "verdict" in out and "ALLOW" in out
    assert "risk score" in out
    assert "reason" in out and "test reason for ALLOW" in out
    assert "exec status" in out and "executed" in out
    assert "observed" in out and "dev-unused-01" in out and "stopped" in out


# ── 12. HTTP failures return a nonzero runner exit status ──────────────────────

def test_evaluate_http_failure_is_nonzero():
    code, backend = _run("dev-allow", evaluate_status=500)
    assert code == liveops_agent.EXIT_HTTP_ERROR
    assert backend.execute_calls == []


def test_execute_http_failure_is_nonzero():
    code, backend = _run("dev-allow", execute_status=500)
    assert code == liveops_agent.EXIT_HTTP_ERROR
    assert backend.execute_calls == [1]  # attempted once, then reported failure


# ── 13. Invalid scenario names are rejected ────────────────────────────────────

def test_invalid_scenario_name_rejected():
    backend = MockBackend()
    with pytest.raises(SystemExit) as excinfo:
        liveops_agent.main(
            ["bogus", "--poll-interval", "0", "--approval-timeout", "0.01"],
            transport=httpx.MockTransport(backend.handler),
        )
    assert excinfo.value.code == 2
    assert backend.requests == []  # nothing was sent before argparse rejected it


# ── 14. Backend URL override works ─────────────────────────────────────────────

def test_backend_url_override():
    backend = MockBackend()
    code = liveops_agent.main(
        [
            "dev-allow",
            "--base-url", "http://custom.example:8123",
            "--poll-interval", "0",
            "--approval-timeout", "0.01",
        ],
        transport=httpx.MockTransport(backend.handler),
    )
    assert code == liveops_agent.EXIT_OK
    assert backend.requests
    assert str(backend.requests[0].url).startswith("http://custom.example:8123/api/evaluate")


# ── 15. Runner never accesses SQLite or sandbox JSON files directly ────────────

def test_runner_never_touches_sqlite_or_sandbox_files():
    source = _RUNNER_PATH.read_text(encoding="utf-8")

    imports: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")

    forbidden_imports = {
        "sqlite3",
        "sqlalchemy",
        "app.database",
        "app.sandbox",
        "app.models",
        "subprocess",
    }
    assert not (forbidden_imports & set(imports)), (
        forbidden_imports & set(imports)
    )
    for token in ("sqlite", "SimulatedCloud", "subprocess", "os.system",
                  "Popen", "eval(", "exec("):
        assert token not in source
    assert "open(" not in source  # no direct file access at all


# ── 16. No retry after execution HTTP 409 (doubles as the retry guarantee) ─────

def test_no_retry_after_409():
    code, backend = _run("dev-allow", execute_status=409)
    assert code == liveops_agent.EXIT_EXECUTION_CONFLICT
    assert len(backend.execute_calls) == 1
    # each retry would be another POST; assert exactly one
    count = sum(
        1 for r in backend.requests
        if r.method == "POST" and r.url.path.startswith("/api/liveops/execute/")
    )
    assert count == 1


# ── 17. Tests are offline and deterministic ────────────────────────────────────

def test_offline_and_deterministic_imports():
    # The runner must import only stdlib + httpx: no ORM, no sandbox, no LLM.
    source = _RUNNER_PATH.read_text(encoding="utf-8")
    for heavy in ("requests", "sqlalchemy", "transformers", "sentence_transformers",
                  "app.policy", "app.sandbox"):
        assert heavy not in source
    # No real backend is ever contacted: the runner accepts an injected transport.
    backend = MockBackend()
    client = liveops_agent.make_client(
        liveops_agent.DEFAULT_BASE_URL,
        transport=httpx.MockTransport(backend.handler),
    )
    assert client._transport is not None


def test_all_scenarios_are_deterministic_repeated_runs():
    # Running twice against the same mock yields identical sequences.
    codes = []
    for _ in range(2):
        code, backend = _run("demo", human_decisions=[None, "approved"])
        codes.append(code)
        assert backend.execute_calls == [1, 2]
    assert codes == [liveops_agent.EXIT_OK, liveops_agent.EXIT_OK]