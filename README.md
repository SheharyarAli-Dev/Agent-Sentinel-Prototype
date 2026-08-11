# Agentic Action Risk Gatekeeper — FYP Prototype

Middleware system which intercepts AI agent actions, evaluates their risk before execution, and returns **ALLOW / WARN / BLOCK** decisions with suggested fixes and a plain-language explanation.

## Three Use Cases

| Use Case | Adapter | Modules |
|----------|---------|---------|
| Cursor (coding agent) | `cursor_adapter.py` | Module 1 + 7 + 6 |
| n8n (automation agent) | `n8n_adapter.py` | Module 1 + 7 (plan safety) + 6 |
| Coffee ordering (transaction) | `transaction_adapter.py` | Module 1 + 2 (ATTVE) |

## Implemented Policy Modules

| Module | File | Purpose |
|--------|------|---------|
| Module 1 — AI Policy Engine | `policy/policy_engine.py` | Declarative policy-as-code governance, evaluated **before** every module check. Rules live in `data/policies.json`. A BLOCK policy is authoritative. |
| Module 2 — ATTVE | `policy/attve.py` | Merchant verification, invoice integrity, spend limit |
| Module 4 — Decision Governance & Incident Response | `policy/governance.py` | Audit trail + forensic incident report over all decisions (`/api/audit`, `/api/incident-report`) |
| Module 6 — Intent Verification | `policy/intent_verification.py` | Detect drift from original goal (advisory for transactions, blocking for cursor/n8n) |
| Module 7 — Planning Verification | `policy/planning_verification.py` | Whole-plan safety + code-quality patterns |
| Context Integrity Verification | `policy/context_integrity.py` | **Prompt-injection defense (OWASP LLM01)** — detects injection/exfiltration patterns, hidden unicode, untrusted sources, and stale context in data the agent ingests |
| Sequential Behaviour Analysis | `policy/sequential_behaviour.py` | **Trajectory monitoring** — detects multi-step attack chains (read-sensitive → exfiltrate), risk escalation, and velocity anomalies across a session |
| Tool Poisoning Defense | `policy/tool_integrity.py` | **MCP tool-poisoning defense** — injection scan of tool descriptions, rug-pull (baseline-hash) detection, trusted-server + unknown-tool checks |
| Least-Privilege / Least-Agency | `policy/least_privilege.py` | Enforces per-agent capability grants (`data/agent_capabilities.json`); denies ungranted capabilities and caps high-impact actions |
| Memory Poisoning Defense | `policy/memory_integrity.py` | Validates long-term memory writes at ingestion — protects core memory, scans for injection, requires provenance |
| Multi-Agent Safety | `policy/multi_agent.py` | Cross-agent privilege escalation, unsafe delegation, goal conflict, shared-context poisoning |
| Module 11 — Explainable Safety Reasoning | `policy/explainability.py` | Plain-language justification attached to every decision |

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/evaluate` | Submit an agent action for risk evaluation |
| POST | `/api/decide/{event_id}` | Human approve/reject a WARN event |
| GET | `/api/decide/{event_id}` | Get the current decision for an event |
| GET | `/api/audit` | Queryable audit trail (Module 4) — filters: `source`, `verdict`, `limit` |
| GET | `/api/incident-report` | Forensic summary + blocked incidents (Module 4) |
| WS | `/ws` | Live dashboard event stream |

## Quick Start (WSL2 / Linux)

### Backend

```bash
cd risk-gatekeeper/backend

# Create and activate virtual environment (if not already done)
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy env file and configure
cp .env.example .env

# Run the server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API available at: http://localhost:8000  
Swagger docs: http://localhost:8000/docs

### Frontend

```bash
cd risk-gatekeeper/frontend
npm install
npm run dev
```

Dashboard at: http://localhost:5173

### Run tests

```bash
cd risk-gatekeeper/backend
source venv/bin/activate
pytest tests/ -v
```

### Run evaluation

```bash
cd risk-gatekeeper/backend
source venv/bin/activate
# (Backend must be running)
python scripts/run_eval.py
```

## API Reference

### `POST /api/evaluate`

Submit an agent action for risk evaluation.

```json
{
  "source": "transaction",
  "event_type": "purchase",
  "payload": {
    "merchant_id": "MERCH_001",
    "amount": 4.50,
    "transaction_id": "TXN_001"
  },
  "original_goal": "Order a coffee from a nearby shop."
}
```

Response:
```json
{
  "event": { "id": 1, "source": "transaction", ... },
  "decision": {
    "verdict": "ALLOW",
    "reasons": ["..."],
    "suggested_fix": "",
    "module": "attve",
    "risk_score": 0.0
  }
}
```

### `POST /api/decide/{event_id}`

Submit a human approve/reject decision for a WARN-status event.

```json
{ "decision": "approved" }
```

### `GET /health`

Returns backend health status and active WebSocket connection count.

## Project Structure

```
risk-gatekeeper/
├── backend/
│   ├── app/
│   │   ├── main.py              FastAPI application
│   │   ├── config.py            Pydantic-settings config
│   │   ├── database.py          SQLAlchemy engine/session
│   │   ├── models/              ORM + Pydantic schemas
│   │   ├── policy/              Rule-based policy modules
│   │   ├── adapters/            Source-specific adapters
│   │   ├── websocket/           WS connection manager
│   │   └── api/                 HTTP endpoint routers
│   ├── data/                    JSON data files + eval sets
│   ├── scripts/                 run_eval.py
│   └── tests/                   pytest test suite
├── frontend/                    React + TS + Vite dashboard
├── cursor-integration/          Cursor hooks.json
├── n8n-integration/             Custom n8n node scaffold
└── docs/                        Architecture + eval results
```

## Build Phases

- ✅ **Phase 0** — Scaffolding + folder structure
- ✅ **Phase 1** — Middleware core + API endpoints (stubs)
- ⬜ **Phase 2** — Transaction adapter + ATTVE (Module 2)
- ⬜ **Phase 3** — Dashboard + WebSocket live feed
- ⬜ **Phase 4** — Cursor adapter + Modules 7 & 6 (full implementation)
- ⬜ **Phase 5** — n8n adapter
- ⬜ **Phase 6** — Eval set + report

---

## Recent Changes (this iteration)

**Bug fix — the "Valid Coffee → ALLOW" case now works.**
Previously a valid transaction was wrongly scored WARN because Intent Verification
compared the natural-language goal ("order a coffee") against the transaction's
structured fields ("Good Beans Coffee / Flat White") via keyword overlap (12.5% —
below threshold). Intent Verification now runs in **advisory mode** for
transactions: it annotates low alignment but never escalates the verdict. ATTVE
(Module 2) remains authoritative for transaction safety. Intent Verification still
fully WARNs on drift for cursor/n8n use cases.

**New modules implemented:**
- **Module 1 — AI Policy Engine** — declarative `data/policies.json`, evaluated
  before all module checks; authoritative BLOCK. Ships with 5 demo policies.
- **Module 4 — Decision Governance & Incident Response** — `/api/audit` and
  `/api/incident-report` endpoints for accountability and forensics.
- **Module 11 — Explainable Safety Reasoning** — a plain-language `explanation`
  field on every decision, displayed in the dashboard cards and approval modal.

**Also fixed:** `npm run build` was broken by a missing CSS type declaration;
added `frontend/src/vite-env.d.ts` so the production build compiles cleanly.

**Tests:** 43 passing (was 31) — new suites `test_policy_engine.py` and
`test_governance_and_fix.py`.

## Run tests

```bash
cd backend
source venv/bin/activate   # Windows: venv\Scripts\activate
pytest tests/ -v
```

## Latest additions (security modules + latency KPI)

- **Context Integrity Verification** — defends against 2026's #1 agent threat,
  prompt injection (OWASP LLM01). Scans ingested context (documents, tool
  outputs, RAG results) for injection/exfiltration patterns, hidden unicode,
  untrusted sources (`data/trusted_sources.json`), and stale content. High-
  confidence injection → BLOCK. Honest framing: injection is unsolved in 2026;
  this raises attacker cost and catches common cases as one layer of defense-in-depth.
- **Sequential Behaviour Analysis** — trajectory monitoring with per-session
  state. Catches multi-step kill-chains (e.g. read customer DB → send externally)
  that look benign step-by-step, plus risk escalation and velocity bursts.
- **Latency KPI** — every decision is now timed; `/api/incident-report` exposes
  `avg_latency_ms` / `max_latency_ms` against the 40ms spec target, and each
  dashboard card shows its evaluation latency.

New demo scenarios in the dashboard dropdown: **Prompt Injection in Document → BLOCK**
and **Exfiltration Chain (2 steps) → BLOCK**.

Tests: 53 passing. New suite: `test_context_and_sequence.py`.

## Latest additions (4 advanced security modules)

Four more modules were added, completing a defense-in-depth security story:

- **Tool Poisoning Defense** (`tool_integrity.py`) — MCP's #1 emerging threat.
  Scans tool descriptions for hidden exfiltration instructions, and detects
  "rug pulls" by fingerprinting each tool's approved description
  (`data/tool_baselines.json`) and blocking any silent change after approval.
- **Least-Privilege / Least-Agency** (`least_privilege.py`) — enforces per-agent
  capability grants (`data/agent_capabilities.json`); an agent using a capability
  outside its role is blocked (confused-deputy defense). Only enforced when a
  capability is explicitly declared, so freeform actions are never mis-blocked.
- **Memory Poisoning Defense** (`memory_integrity.py`) — validates long-term
  memory writes: external input may not modify core memory, content is scanned
  for injection, and provenance metadata is required for auditability.
- **Multi-Agent Safety** (`multi_agent.py`) — detects cross-agent privilege
  escalation, unsafe delegated tasks, goal conflicts, and shared-context poisoning.

New dashboard demo scenarios: Poisoned MCP Tool, Privilege Violation, Memory
Poisoning, and Cross-Agent Escalation — all → BLOCK.

The agent's **input** (Context Integrity), **tools** (Tool Poisoning), **memory**
(Memory Poisoning), **privileges** (Least-Privilege), **trajectory** (Sequential),
and **collaborations** (Multi-Agent) are now all defended, then governed and explained.

Tests: 70 passing. New suite: `test_advanced_security_modules.py`.

## Roadmap completion — 4 final modules (all 16 now implemented)

- **Predictive Defence Engine** (`predictive_defence.py`) — forecasts attacks
  *before* they complete by matching the session trajectory against prefixes of
  known attack templates and predicting the next dangerous step (early WARN).
- **Uncertainty-Aware Risk Prediction** (`uncertainty.py`) — estimates decision
  confidence from module agreement + risk proximity to the threshold, and
  applies adaptive thresholding (uncertain borderline ALLOW → WARN, "when unsure,
  ask a human").
- **Human Feedback Safety Learning** (`feedback_learning.py`) — records every
  human approve/reject and adapts: a signature approved ≥3× auto-clears future
  WARNs (spec KPI: adapts ≤3), a signature rejected ≥3× tightens future ALLOWs.
- **AI Red Team Simulator** (`red_team.py`, `GET /api/red-team`) — runs a catalog
  of adversarial attacks through the live pipeline and reports defense coverage.
  Current coverage: **100% (11/11 attacks defended)**. A "Run Red-Team Self-Test"
  button on the dashboard shows coverage live.

The system now implements **16 modules** end-to-end. Tests: **77 passing**
(new suite `test_roadmap_modules.py`).
