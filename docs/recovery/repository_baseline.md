# Repository Baseline — Agent Sentinel (FYP Prototype)

**Generated:** 2026-08-23  
**Branch:** `main` (HEAD at 39eecb0)  
**HEAD Commit:** `39eecb01c38d5984f8c65d43f6b7d69de6abc396` — "Merge pull request #4 from SheharyarAli-Dev/feature/demo-portability"

---

## Git Status Summary

```
 M .gitignore
 M demo/README.md
 M demo/scripts/demo_common.ps1
 M demo/scripts/setup_demo.ps1
 M demo/scripts/start_demo.ps1
 M demo/scripts/stop_demo.ps1
 M demo/scripts/verify_demo.ps1
 M frontend/vite.config.ts
```

- Working tree has 8 modified files (all demo/launcher-related or Vite proxy config).
- No untracked source files beyond build artefacts (`frontend/dist`, `backend/.venv`, `backend/.pytest_cache`, `project.zip`, large `project` binary).
- `docs/recovery/` newly created for this audit.

---

## Backend Framework

- **Runtime:** Python 3.12 (venv at `backend/.venv/`)
- **Web Framework:** FastAPI 0.111.1 + Uvicorn 0.30.1
- **Database:** SQLAlchemy 2.0.31 (SQLite, file `backend/data/risk_gatekeeper.db`)
- **Validation:** Pydantic 2.7.4 + pydantic-settings 2.3.4
- **Testing:** pytest 8.2.2 + pytest-asyncio 0.23.7 (271 tests collected)
- **Key Dependencies:**
  - `sentence-transformers==5.7.0` (MiniLM embeddings for intent verification)
  - `websockets==12.0` (WebSocket support)
  - `httpx==0.27.0` (outbound HTTP for n8n adapter)

---

## Frontend Framework

- **Runtime:** Node.js 18+ (uses Vite dev server)
- **Build Tool:** Vite 5.4.21 (React 18.3.1 + TypeScript 5.5.3)
- **Styling:** TailwindCSS 3.4.6 + PostCSS
- **Package Manager:** npm (lockfile v3, requires npm 9+)
- **Build Output:** `frontend/dist/` (production bundle)

---

## Database Configuration

- **Engine:** SQLite (file-based, `sqlite:///./data/risk_gatekeeper.db`)
- **ORM:** SQLAlchemy DeclarativeBase with three tables:
  - `events` — normalised agent actions
  - `decisions` — policy engine output per event (verdict, reasons, suggested_fix, risk_score, latency_ms, explanation, human_decision)
  - `liveops_executions` — LiveOps demonstration state
- **Migration:** None (prototype uses `Base.metadata.create_all()` at startup)
- **Session:** `SessionLocal` dependency injected per-request

---

## Startup & Verification Scripts

### Demo Launcher (Windows PowerShell)
Located under `demo/scripts/`:
- `setup_demo.ps1` — one-time setup: venv, pip install, npm ci, MiniLM download, backend tests, frontend build. Logs to `demo/logs/`.
- `start_demo.ps1` — launches backend (uvicorn on 127.0.0.1:8000) + frontend (Vite on 127.0.0.1:5173), polls both `/health` and `/`, opens browser only after both ready. Writes PID files to `demo/state/`.
- `stop_demo.ps1` — kills only launcher-owned processes via PID + command-line verification; reports STOP INCOMPLETE if any survive.
- `verify_demo.ps1` — read-only environment check: Git (optional), Python/venv, Node/npm, torch + sentence-transformers imports, MiniLM offline load, backend tests, frontend build, port ownership. Returns READY/NOT READY.
- Batch wrappers (`*.bat`) invoke the `.ps1` scripts.

### Adapters / Simulators
- `backend/app/adapters/transaction_adapter.py` — CLI simulation of 8 coffee-ordering scenarios against `/api/evaluate`.
- `backend/app/adapters/cursor_adapter.py` — normalises Cursor hooks.json → EventCreate.
- `backend/app/adapters/n8n_adapter.py` — normalises n8n workflow payload → EventCreate.
- `n8n-integration/custom-nodes/risk-gatekeeper-node/` — custom n8n node (TypeScript scaffold) that calls `/api/evaluate` before risky actions.

---

## MiniLM Model & Thresholds

| Item | Value |
|------|-------|
| **Model** | `sentence-transformers/all-MiniLM-L6-v2` (384-dim, ~90 MB) |
| **Embedding Similarity** | cosine, pure-Python, clamped to [0, 1] |
| **Drift** | `1.0 - similarity` (0 = aligned, 1 = fully drifted) |
| **Semantic Aligned Boundary** | `intent_semantic_aligned_drift = 0.38` (configurable via `config.py`) — PROVISIONAL, from 30-case dev benchmark |
| **Lexical (Jaccard) Fallback Threshold** | `intent_drift_threshold = 0.15` (keyword overlap) |
| **Fallback Behaviour** | Semantic scorer primary; Jaccard only as fallback when semantic backend raises. High lexical overlap never bypasses semantic review. |

---

## Review Timeout

- **Human Decision Endpoint:** `POST /api/decide/{event_id}` — only accepts `approved` / `rejected` on WARN decisions.
- **Timeout:** Not implemented in backend (no auto-expiry on pending WARN). `human_decision` stays `NULL` until explicit human action.
- **Governance Dashboard:** `GET /api/governance/incident-report` and `GET /api/governance/audit-trail` expose pending reviews count.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check (status, version, WS connections) |
| GET | `/` | API root info |
| POST | `/api/evaluate` | Primary endpoint — normalised event → decision (ALLOW/WARN/BLOCK) |
| GET | `/api/decide/{event_id}` | Fetch current decision for an event |
| POST | `/api/decide/{event_id}` | Human approve/reject a WARN decision |
| GET | `/api/governance/audit-trail` | Filterable audit trail (source, verdict, limit) |
| GET | `/api/governance/incident-report` | Forensic summary: verdict/source breakdown, top modules, blocked incidents |
| POST | `/api/n8n/evaluate` | n8n webhook endpoint (alias of `/api/evaluate` with n8n normalisation) |
| POST | `/api/liveops/*` | LiveOps demonstration endpoints (restore, start/stop VM, manage snapshots) |
| WS | `/ws` | Live dashboard broadcast (new_decision, human_decision events) |

---

## WebSocket Use

- **Endpoint:** `/ws` (FastAPI native WebSocket)
- **Manager:** `app.websocket.manager.ConnectionManager` (singleton `manager`)
- **Broadcast Events:**
  - `new_decision` — sent on every `/api/evaluate` completion (event + decision JSON)
  - `human_decision` — sent when `/api/decide` updates a WARN decision
- **Client Behaviour:** Dashboard connects, receives broadcasts; server does not process client messages (keeps connection alive via `receive_text()`).
- **Failure Handling:** Broadcast failures are logged but never block the API response.

---

## Docker Status

- **docker-compose.yml** exists (3.9) defining `backend` and `frontend` services.
- **Dockerfiles:** NOT included in repository (comment in compose says "Dockerfiles for backend and frontend are not included in this prototype").
- **Local Dev Only:** Compose expects local build context; no published images.
- **Volumes:** `./backend/data:/app/data` for SQLite persistence.

---

## Adapter Status

| Adapter | Status | Notes |
|---------|--------|-------|
| **Cursor** | VERIFIED-IN-CODE | `cursor_adapter.py` normalises `beforeShellExecution` / `beforeMCPExecution` hooks; routes to full module suite (planning, context integrity, tool integrity, sequence, predictive defence, intent). |
| **n8n** | PARTIAL | `n8n_adapter.py` + custom node scaffold (`RiskGatekeeper.node.ts`). n8n events skip code-quality patterns. Webhook endpoint exists. Custom node is scaffold — WARN does not pause; BLOCK throws. |
| **Transaction** | VERIFIED-IN-CODE | `transaction_adapter.py` simulation script with 8 scenarios; exercises ATTVE (Module 2) + intent (advisory) + sequential behaviour. |
| **LiveOps** | VERIFIED-IN-CODE | `liveops_adapter.py` + `/api/liveops/*` endpoints + browser dashboard panel. Simulated cloud state with VMs/snapshots; exercises governance + intent + sequential behaviour. |

---

## Implemented vs Scaffolded Features

| Feature | Status | Evidence |
|---------|--------|----------|
| **Module 1 — Policy Engine** | VERIFIED-IN-CODE | `policy_engine.py` + `policies.json`; runs first for all sources; most-severe verdict wins. |
| **Module 2 — ATTVE** | VERIFIED-IN-CODE | `attve.py` + `merchant_registry.json`; 7 unit tests pass; transaction simulation works. |
| **Module 3 — (deferred)** | NOT-FOUND | No implementation; skipped in roadmap. |
| **Module 4 — Governance** | VERIFIED-IN-CODE | `governance.py` + audit/incident APIs; persists all decisions; incident report with module breakdown. |
| **Module 5 — (deferred)** | NOT-FOUND | No implementation. |
| **Module 6 — Intent Verification** | VERIFIED-IN-CODE | `intent_verification.py` + `semantic_similarity.py`; MiniLM local; semantic primary, Jaccard fallback; 0.38 aligned boundary (provisional). |
| **Module 7 — Planning Verification** | VERIFIED-IN-CODE | `planning_verification.py` + `protected_paths.json`; destructive patterns, protected paths, scope thresholds, step contradictions, code-quality (Cursor only). |
| **Least Privilege / Agency** | VERIFIED-IN-CODE | `least_privilege.py` + `agent_capabilities.json`; capability allowlist + impact tier ceiling. |
| **Context Integrity (Prompt Injection)** | VERIFIED-IN-CODE | `context_integrity.py` + `trusted_sources.json`; injection patterns (HIGH/MED), hidden unicode, source allowlist, freshness. |
| **Sequential Behaviour** | VERIFIED-IN-CODE | `sequential_behaviour.py`; in-memory session trajectory; kill-chain patterns (READ→EXFIL, PERM→NETWORK, CRED→NETWORK); burst/velocity. |
| **Uncertainty / Adaptive Threshold** | VERIFIED-IN-CODE | `uncertainty.py` (called from `_aggregate`); adjusts verdict based on module confidence + risk dispersion. |
| **Feedback Learning** | VERIFIED-IN-CODE | `feedback_learning.py`; signature-based human decision recording + future adjustment. |
| **Explainability (Module 11)** | VERIFIED-IN-CODE | `explainability.py`; builds plain-language explanation from module reasons + verdict. |
| **ATTVE (Module 2 variant)** | VERIFIED-IN-CODE | Already listed as Module 2. |
| **LiveOps Demo** | VERIFIED-IN-CODE | `liveops_adapter.py` + simulated cloud + browser panel; end-to-end demo flow. |
| **Cursor Integration** | VERIFIED-IN-CODE | `.cursor/hooks.json` + adapter; tested via unit tests. |
| **n8n Custom Node** | SCAFFOLD | TypeScript scaffold compiles; BLOCK halts workflow; WARN only logs; no pause-for-approval. |
| **Frontend Dashboard** | VERIFIED-IN-CODE | React + Vite; LiveOps panel, governance view, WebSocket listener. |
| **Demo Launcher** | VERIFIED-IN-CODE | PowerShell scripts with logging, PID tracking, readiness polling, offline MiniLM gating. |

---

## Known Limitations

1. **No Dockerfiles** — docker-compose cannot build images; local dev only.
2. **SQLite concurrency** — `check_same_thread=False` works for dev but not production-grade.
3. **In-memory state** — Sequential behaviour, duplicate transaction IDs, session tracking reset on restart.
4. **No auth / TLS** — API open, WebSocket unauthenticated, CORS wide (`localhost:5173`, `127.0.0.1:5173`).
4. **MiniLM offline gating** — `start_demo` enables `HF_HUB_OFFLINE=1` only when cache complete; first run needs internet.
5. **Intent semantic threshold (0.38)** — provisional, not scientifically calibrated.
6. **n8n node** — scaffold only; WARN does not pause; no `/decide` integration.
7. **Human decision timeout** — no auto-expiry on pending WARN.
8. **No migrations** — schema changes require manual DB recreation.
9. **No CI/CD** — no GitHub Actions, no automated test runs.
10. **Single-process** — in-memory caches (model, sessions, duplicate tracking) not shared across workers.