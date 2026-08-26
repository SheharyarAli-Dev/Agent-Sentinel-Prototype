# Operation Outcome Defect Report

**Date:** 2026-08-26
**Branch:** `feature/coding-core-usecase`
**Scope:** `backend/app/models/operation.py` — outcome verification logic

---

## 1. Suspected Defects — Verdict

### Defect 1: Undefined `target` variable in `verify_outcome`

**Verdict: GENUINE DEFECT (confirmed)**

In `verify_outcome()`, the variable `target` is used at lines 578 and 583 before it is assigned at line 640. The correct variable name is `target_resource`, which is assigned at line 573.

**Pre-fix code (operation.py:573–585):**
```python
target_resource = expected.target_resource       # line 573

for vm in observed_state.get("vms", []):
    if vm["id"] == target:                       # line 578 — NameError
        observed_resource_state = vm
        break

for snap in observed_state.get("snapshots", []):
    if snap["id"] == target:                     # line 583 — NameError
        observed_resource_state = snap
        break
```

**Impact:** `verify_outcome()` raises `UnboundLocalError` whenever `expected_outcome` is provided on a LiveOps event. The function is dead code for its primary purpose. The bug is latent because no existing test provides `expected_outcome` in a LiveOps payload — all tests take the `OUTCOME_UNKNOWN` early-return path.

### Defect 2: Duplicate `ExpectedOutcome` class definitions

**Verdict: GENUINE DEFECT (confirmed, more severe than initially assessed)**

Two character-for-character identical `ExpectedOutcome` class definitions exist at lines 51 and 115. This causes a Pydantic V2 `ValidationError` at runtime when `build_canonical_action()` is called with an `expected_outcome` in the payload:

- `build_canonical_action()` validates `expected_outcome` using the module-level `ExpectedOutcome` name, which resolves to the **second** class (line 115).
- `CanonicalAction.expected_outcome` field annotation `Optional["ExpectedOutcome"]` resolves to the **first** class (line 51), because Pydantic resolves the forward reference at `CanonicalAction` class creation time (line 85), before the second definition exists.
- Pydantic V2 detects the type mismatch and raises `ValidationError`.

**Impact:** The LiveOps outcome verification feature is completely broken when `expected_outcome` is provided — the `build_canonical_action()` call crashes before `verify_outcome()` is ever reached.

### Additional Finding: Duplicate imports in `liveops.py`

**Verdict: CONFIRMED (harmless but sloppy)**

`liveops.py:71` imports `verify_outcome` and `OutcomeVerificationResult` twice on the same line:
```python
from app.models.operation import ..., verify_outcome, OutcomeVerificationResult, ..., verify_outcome, OutcomeVerificationResult
```

---

## 2. Pre-Fix Test Results

| Test | Result | Error |
|---|---|---|
| `test_returns_verified_without_exception` | **FAILED** | `UnboundLocalError: cannot access local variable 'target'` at `operation.py:578` |
| `test_returns_mismatch_with_violation_detail` | **FAILED** | `UnboundLocalError: cannot access local variable 'target'` at `operation.py:578` |
| `test_returns_outcome_unknown_with_reason` | **PASSED** | (No expected_outcome — takes early-return path) |
| `test_source_contains_exactly_one_expected_outcome_class` | **FAILED** | `AssertionError: found 2: ['line 51', 'line 115']` |
| `test_canonical_action_references_expected_outcome` | **PASSED** | |
| `test_outcome_verification_result_references_expected_outcome` | **PASSED** | |

---

## 3. Exact Corrections Applied

### File: `backend/app/models/operation.py`

**Edit 1 — Remove duplicate `ExpectedOutcome` class (lines 115–146)**

Removed the second `ExpectedOutcome` class definition. The first definition (lines 51–82) is retained. Both definitions were character-for-character identical, so no fields or validation behavior changed.

**Edit 2 — Fix undefined `target` variable (lines 578, 583)**

```
Line 578:  if vm["id"] == target:      →  if vm["id"] == target_resource:
Line 583:  if snap["id"] == target:    →  if snap["id"] == target_resource:
```

### File: `backend/app/api/liveops.py`

**Edit 3 — Remove duplicate imports (line 71)**

```
Before: from app.models.operation import ..., verify_outcome, OutcomeVerificationResult, ..., verify_outcome, OutcomeVerificationResult
After:  from app.models.operation import ..., verify_outcome, OutcomeVerificationResult, ...
```

---

## 4. Focused Post-Fix Results

| Test | Result |
|---|---|
| `test_returns_verified_without_exception` | **PASSED** |
| `test_returns_mismatch_with_violation_detail` | **PASSED** |
| `test_returns_outcome_unknown_with_reason` | **PASSED** |
| `test_source_contains_exactly_one_expected_outcome_class` | **PASSED** |
| `test_canonical_action_references_expected_outcome` | **PASSED** |
| `test_outcome_verification_result_references_expected_outcome` | **PASSED** |

---

## 5. LiveOps Execution Tests

All 23 existing tests in `test_liveops_execution.py` passed:
- State/reset/seed integrity (3)
- ALLOW executes once (3)
- WARN gating (4)
- BLOCK gating (2)
- Validation failures (5)
- No execution-time substitution (1)
- Persisted result (1)
- Failure handling (1)
- Concurrency (1)
- Unique constraint (1)
- Decide endpoint (1)

---

## 6. Three Consecutive Complete-Suite Results

| Run | Tests Collected | Tests Passed | Duration |
|---|---|---|---|
| Run 1 | 283 | 283 | 6.64s |
| Run 2 | 283 | 283 | 5.80s |
| Run 3 | 283 | 283 | 5.94s |

**Suite count increase:** 277 → 283 (+6 new tests in `test_operation_outcome.py`).

---

## 7. Frontend Production Build

```
✓ built in 13.24s
93 modules transformed
dist/index.html                 1.12 kB │ gzip:  0.62 kB
dist/assets/index-CZ8sB_YD.css  42.38 kB │ gzip:  7.52 kB
dist/assets/index-DzvYgpMc.js  264.83 kB │ gzip: 82.46 kB
```

---

## 8. Demo Verification (`demo\verify_demo.bat`)

| Step | Status |
|---|---|
| Git status | Branch `feature/coding-core-usecase` |
| Python 3.12.10 | OK |
| Node v24.11.0 / npm 11.6.1 | OK |
| torch 2.13.0+cpu | OK |
| sentence-transformers 5.7.0 | OK |
| MiniLM model cache | OK |
| pytest | 283 passed in 7.14s |
| npm run build | OK |
| Ports 8000/5173 | Available |
| **Result** | **READY** |

---

## 9. Limitations

1. **No test for the `ValidationError` path.** The duplicate class defect also causes `build_canonical_action()` to crash when `expected_outcome` is provided. The focused tests bypass `build_canonical_action()` by constructing `canonical_action_json` directly. A full integration test through the `/api/liveops/execute` endpoint with `expected_outcome` in the event payload would exercise this path but is out of scope for this defect fix.

2. **`verify_outcome` result is not included in the execute response.** The `POST /api/liveops/execute/{event_id}` endpoint calls `verify_outcome()` but only logs the result — it does not return it. The verification is only accessible via `GET /api/liveops/outcome/{event_id}`. This is existing behavior, not changed by this fix.

3. **Outcome verification only works for LiveOps.** `verify_outcome()` is hardcoded to check VMs and snapshots in `SimulatedCloud`. It does not support coding operations, file-system state, or any other resource type.

---

## 10. Final Commit Hash

**Commit:** `fix: verify and correct operation outcome defects`
(Pending — committed after all verification passed)

---

## Files Changed

| File | Change |
|---|---|
| `backend/app/models/operation.py` | Remove duplicate `ExpectedOutcome` class (32 lines), fix `target` → `target_resource` (2 lines) |
| `backend/app/api/liveops.py` | Remove duplicate imports (line 71) |
| `backend/tests/test_operation_outcome.py` | **NEW** — 6 focused regression tests |
| `docs/recovery/operation_outcome_defect_report.md` | **NEW** — this report |
