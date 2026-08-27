# Stage 4: Coding Outcome Verification and Bounded Diff Evidence — Recovery Report

**Date:** 2026-08-27
**Branch:** `feature/coding-core-usecase-dark`
**Commit:** fix: capture genuine protected invariant evidence

## Summary

Stage 4 adds post-execution verification that compares observed workspace state against expected proposal outcomes, generates bounded unified diffs from actual content (not proposal payloads), and persists a separate `CodingOutcomeORM` record for audit trail completeness.

## Files Created / Modified

| File | Status | Purpose |
|------|--------|---------|
| `backend/app/coding/__init__.py` | NEW | Empty package init |
| `backend/app/coding/outcome.py` | NEW | Verification logic, diff generation, persistence |
| `backend/app/models/coding_outcome.py` | NEW | `CodingOutcomeORM` + `CodingOutcomeResponse` |
| `backend/tests/test_coding_outcome.py` | MODIFIED | 42 integration tests (expanded from 35) |
| `backend/app/sandbox/coding_executor.py` | MODIFIED | `old_content`/`new_content` in result, `get_protected_invariant_hashes()` |
| `backend/app/api/coding_execution.py` | MODIFIED | Evidence capture (step 12a), outcome verification (step 14a), GET /outcome endpoint |
| `backend/app/main.py` | MODIFIED | Registered `CodingOutcomeORM` |
| `backend/app/models/__init__.py` | MODIFIED | Exports `CodingOutcomeORM`/`CodingOutcomeResponse` |
| `docs/recovery/coding_stage4_outcome_report.md` | MODIFIED | Updated with fix details |

## New Endpoints

### `GET /api/coding/outcome/{event_id}`
Returns the persisted outcome record for a coding event. Does not recompute — returns 404 if no outcome exists.

## Verification Algorithm

1. Load context (decision, operation, proposal from stored event payload, seed, path rules)
2. Short-circuit: `EXECUTION_FAILED` if execution.status == "failed"
3. Short-circuit: `OUTCOME_UNKNOWN` if execution.status != "executed"
4. Short-circuit: `OUTCOME_UNKNOWN` if proposal cannot be reconstructed
5. Classify changes (unexpected_created, unexpected_deleted, unexpected_modified)
6. Check protected invariants (compare before/after hashes)
7. Determine status:
   - Invariant violations → `MISMATCH`
   - Wrong final hash → `MISMATCH`
   - Unauthorized changes → `MISMATCH`
   - Wrong old hash → `MISMATCH`
   - Wrong path → `MISMATCH`
   - All pass → `VERIFIED`
8. Generate diff from actual old_content and new_content (not proposal)
9. Persist separate `CodingOutcomeORM` row

## Statuses

| Status | Meaning |
|--------|---------|
| `VERIFIED` | All checks pass — expected and observed outcomes match |
| `MISMATCH` | Verification failed — hash, path, or invariant mismatch |
| `EXECUTION_FAILED` | Execution itself failed (short-circuit, no recompute) |
| `OUTCOME_UNKNOWN` | Missing evidence, proposal, or execution status ≠ "executed" |
| `PARTIAL` | Reserved but not emitted in current single-file-write implementation |

## Diff Generation

- Uses `difflib.unified_diff` with normalized headers `a/{path}` / `b/{path}`
- Max 500 lines, max 100,000 characters, with `diff_truncated` flag
- Not generated for: BLOCK, rejected, expired, failed execution, protected/sensitive paths
- Sensitive paths: `diff_omitted_reason = "SENSITIVE_PATH"`
- Protected paths: `diff_omitted_reason = "PROTECTED_PATH"`

## Evidence Capture Flow (Step 12a)

**Fixed in this commit:** `protected_before` is now captured BEFORE `execute_file_write()` is called, not after.

1. `workspace.copy_demo()` — creates runtime workspace
2. `protected_before = workspace.get_protected_invariant_hashes()` — **BEFORE execution**
3. `executor_result = workspace.execute_file_write(...)` — execution mutates workspace
4. `protected_after = workspace.get_protected_invariant_hashes()` — AFTER execution, before cleanup
5. `old_content` and `new_content` captured from executor result
6. `workspace.cleanup()` — removes temporary workspace

This ensures protected invariant comparison detects execution-time tampering, not just post-execution snapshots.

## Persistence Sequence

1. Execution result persisted first (Step 13)
2. Operation lifecycle updated (Step 14)
3. Outcome verification performed and persisted separately (Step 14a)
4. WebSocket broadcast after outcome commit (Step 15)
5. Verification failure must NOT rewrite execution status

## WebSocket Broadcast Fields (Stage 4 additions)

`verification_status`, `invariant_violation_count`, `diff_truncated`, `diff_omitted_reason`

NOT broadcast: full diff, file contents, proposal content, protected hashes, secrets.

## Test Results

```
Stage 4: 42 passed (test_coding_outcome.py)
Stage 3: 28 passed (test_coding_execution.py)
Stage 1: 51 passed (test_coding_proposal.py)
Stage 2: 43 passed, 2 skipped (test_coding_executor.py)
Full suite: 444 passed, 2 skipped × 3 consecutive runs
Frontend build: OK
Coding-demo hashes: All 4 files match seed
Seed file: Unchanged (no diff)
Working tree: 8 expected changes only
```

## Scope and Limitations

- Diff is generated from `old_content`/`new_content` captured at runtime — not from the proposal payload. This ensures the diff reflects what actually happened on disk.
- Protected and sensitive paths suppress diff output entirely.
- `PARTIAL` status is reserved for future multi-file-write implementations.
- Outcome verification failure does not rewrite the execution record status — this is intentional to preserve execution ledger integrity.
- `GET /api/coding/outcome/{event_id}` is idempotent and does not recompute.
- Protected invariant hashes are captured before and after execution to detect tampering during the write operation.

## Seed Manifest

| File | Hash |
|------|------|
| src/status.py | `8ee28ff7...` |
| tests/test_status.py | `8d3e948b...` |
| config/app.json | `36b68ef2...` |
| protected/secrets.env | `ed19d3ab...` |
