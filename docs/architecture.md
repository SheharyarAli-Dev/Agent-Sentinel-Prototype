# Architecture — Agentic Action Risk Gatekeeper

## System Overview

The Risk Gatekeeper is a middleware layer that sits between AI agents and the real-world actions they propose. Every action is intercepted, normalised, evaluated, and either allowed, held for human review, or blocked — before any side effect occurs.

```
┌─────────────────────────────────────────────────────────────────┐
│                        AI Agent Layer                           │
│   ┌──────────┐    ┌──────────┐    ┌─────────────────────────┐  │
│   │  Cursor  │    │   n8n    │    │  Transaction Simulator  │  │
│   │  (IDE)   │    │(Workflow)│    │   (Coffee Ordering)     │  │
│   └────┬─────┘    └────┬─────┘    └────────────┬────────────┘  │
└────────┼───────────────┼───────────────────────┼───────────────┘
         │               │                       │
         ▼               ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Adapter Layer                               │
│   cursor_adapter.py   n8n_adapter.py   transaction_adapter.py  │
│               ↓ normalise to EventCreate schema ↓              │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼  POST /api/evaluate
┌─────────────────────────────────────────────────────────────────┐
│                   FastAPI Backend (app/)                        │
│                                                                 │
│  api/evaluate.py                                                │
│    └── policy/rules_engine.py  (orchestrator)                   │
│         ├── policy/attve.py            (Module 2 — transaction) │
│         ├── policy/intent_verification.py (Module 6 — all)      │
│         └── policy/planning_verification.py (Module 7 — cursor, n8n)
│              └── policy/code_quality_patterns.py  (cursor only) │
│                                                                 │
│  → DecisionCreate (verdict, reasons, suggested_fix, risk_score) │
│  → Persist to SQLite (events + decisions tables)                │
│  → Broadcast over WebSocket /ws                                 │
│  → Return EvaluateResponse                                      │
└─────────────────────────────────────────────────────────────────┘
         │                           │
         ▼                           ▼
┌────────────────┐       ┌───────────────────────────────────────┐
│   SQLite DB    │       │        React Dashboard (frontend/)     │
│  (audit log)   │       │                                       │
│  events table  │       │  LiveFeed.tsx  ← useWebSocket.ts      │
│  decisions     │       │  EventCard.tsx (color-coded verdict)  │
│  table         │       │  ApprovalModal.tsx → POST /decide/{id}│
└────────────────┘       └───────────────────────────────────────┘
```

## Module Routing

| Source      | Module 2 (ATTVE) | Module 6 (Intent) | Module 7 (Planning) | Code Quality |
|-------------|-----------------|-------------------|---------------------|--------------|
| transaction | ✅ Primary       | ⚡ Optional        | ❌                   | ❌            |
| cursor      | ❌               | ✅ Primary         | ✅ Primary            | ✅ Yes        |
| n8n         | ❌               | ✅ Primary         | ✅ (plan-safety only) | ❌            |

## Decision Schema

Every decision, regardless of source module, has the same shape:

```json
{
  "verdict": "ALLOW | WARN | BLOCK",
  "reasons": ["Human-readable reason strings"],
  "suggested_fix": "Short remediation suggestion (non-empty for WARN/BLOCK)",
  "module": "attve | intent_verification | planning_verification",
  "risk_score": 0.0,
  "human_decision": null
}
```

## Data Flow — Verdict Aggregation

When multiple modules run (e.g. cursor triggers both Module 7 and Module 6):
1. Each module returns its own `DecisionCreate`
2. `rules_engine._aggregate()` takes the **worst verdict** (BLOCK > WARN > ALLOW)
3. All reasons are concatenated
4. All suggested_fix strings are joined with ` | `
5. Risk scores are averaged

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Backend API | FastAPI 0.111 |
| ORM | SQLAlchemy 2.0 |
| Validation | Pydantic v2 |
| Database | SQLite (dev) |
| WebSocket | FastAPI built-in |
| Frontend | React 18 + TypeScript + Vite |
| Styling | Tailwind CSS |
| HTTP client | Axios |
| Tests | pytest + FastAPI TestClient |
