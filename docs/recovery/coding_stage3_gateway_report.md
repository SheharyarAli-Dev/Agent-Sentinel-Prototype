# Stage 3: Governance-Gated Coding Execution Gateway — Recovery Report

**Date:** 2026-08-26
**Branch:** `feature/coding-core-usecase-dark`
**Commit:** pending (feat: add governance-gated coding execution gateway)

## Summary

Stage 3 connects the existing ASENT policy lifecycle (DecisionORM, OperationORM) to the contained Stage 2 coding executor via two new FastAPI endpoints that enforce governance before any file mutation touches the tracked fixture.

## Files Created / Modified

| File | Status | Purpose |
|------|--------|---------|
| `backend/app/models/coding_execution.py` | NEW | `CodingExecutionORM` + `CodingExecutionResponse` |
| `backend/app/api/coding_execution.py` | NEW | POST/GET endpoints for governance-gated execution |
| `backend/app/main.py` | MODIFIED | Router registration + ORM import |
| `backend/app/models/__init__.py` | MODIFIED | Exports `CodingExecutionORM` / `CodingExecutionResponse` |
| `backend/tests/test_coding_execution.py` | NEW | 28 integration tests |

## Endpoints

### `POST /api/coding/execute/{event_id}`
Executes a governance-gated coding file-write exactly once. 14-step transaction:
1. Load EventORM → 404 if missing
2. Require source="cursor" AND event_type="coding_proposal" → 422
3. Load DecisionORM → 404
4. Load OperationORM → 404
5. Recompute canonical action + fingerprint, compare → 409 on mismatch
6. Reconstruct CodingProposal from stored event payload
7. Exactly-once pre-check (execution row) → 409
8. Lifecycle state + authorization validation
9. Reserve pending row (UNIQUE constraint)
10. Update operation: evaluated → approved (ALLOW) → executing
11. Execute through CodingWorkspace
12. Persist terminal result
13. Update operation lifecycle
14. Broadcast bounded WebSocket evidence

### `GET /api/coding/execution/{event_id}`
Returns the execution-ledger record for a coding event.

## Authorization Rules

| Verdict | Human Decision | Result |
|---------|---------------|--------|
| ALLOW | — | Proceed (no approval needed) |
| WARN | approved | Proceed |
| WARN | None | 409 |
| WARN | rejected | 409 |
| WARN | expired | 410 |
| BLOCK | — | 403 |

## Lifecycle Transitions Fixed

The `update_operation_state` function enforces a strict state machine:
- `evaluated` → `approved`, `rejected`, `expired`, `blocked`
- `approved` → `executing`, `rejected`, `expired`, `blocked`
- `executing` → `executed`, `failed`

For ALLOW verdicts, the API transitions `evaluated → approved → executing → executed` (ALLOW = implicit human approval).

The exactly-once pre-check (step 7) was placed before lifecycle validation (step 8) so retries against terminal operations receive 409 conflict rather than 422 lifecycle error.

## Test Results

```
Stage 3: 28 passed (test_coding_execution.py)
Stage 1: 51 passed (test_coding_proposal.py)
Stage 2: 43 passed, 2 skipped (test_coding_executor.py)
Full suite: 402 passed, 2 skipped × 3 consecutive runs
Frontend build: OK
Coding-demo hashes: All 4 files match seed
Working tree: 5 expected changes only
```

## Scope and Limitations

- Duplicate-side-effect protection is bounded to the supported ASENT coding
  execution model (in-process SQLite, single-process event loop).
- There is no distributed exactly-once guarantee. The UNIQUE constraint
  protects against sequential retries and in-process concurrency; it does not
  protect against independent processes writing to the same database.
- Stale reservation cleanup and filesystem/database reconciliation (e.g.
  detecting a row stuck in "pending" or "executing" after a process crash)
  remain future work.

## Seed Manifest

| File | Hash |
|------|------|
| src/status.py | `8ee28ff7...` |
| tests/test_status.py | `8d3e948b...` |
| config/app.json | `36b68ef2...` |
| protected/secrets.env | `ed19d3ab...` |
