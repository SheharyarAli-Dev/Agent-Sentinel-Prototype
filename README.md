# Agentic Action Risk Gatekeeper — FYP Prototype

Middleware system that intercepts AI agent actions, evaluates their risk before execution, and returns **ALLOW / WARN / BLOCK** decisions with suggested fixes.

## Three Use Cases

| Use Case | Adapter | Modules |
|----------|---------|---------|
| Cursor (coding agent) | `cursor_adapter.py` | Module 7 + Module 6 |
| n8n (automation agent) | `n8n_adapter.py` | Module 7 (plan safety) + Module 6 |
| Coffee ordering (transaction) | `transaction_adapter.py` | Module 2 (ATTVE) |

## Three Policy Modules

| Module | File | Purpose |
|--------|------|---------|
| Module 2 — ATTVE | `policy/attve.py` | Merchant verification, invoice integrity, spend limit |
| Module 6 — Intent Verification | `policy/intent_verification.py` | Detect drift from original goal |
| Module 7 — Planning Verification | `policy/planning_verification.py` | Whole-plan safety + code-quality patterns |

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
