# Proposal Demo Hardening Report

**Date:** 2026-08-23  
**Branch:** `feature/proposal-demo-hardening` (from `main` @ `39eecb0`)  
**Final Commit:** `a1b2c3d4e5f6` (placeholder - to be filled after commit)

---

## Summary

Implemented all 10 critical corrections for the proposal demo hardening sprint. **All 277 backend tests pass consistently**, frontend production build succeeds, and demo verification reports READY.

---

## Files Modified

### Backend
| File | Changes |
|------|---------|
| `backend/app/config.py` | Added `review_timeout_seconds` setting (default 3600s) |
| `backend/app/models/decision.py` | Added `EXPIRED` verdict; `review_expires_at` column; updated `DecisionResponse` |
| `backend/app/api/evaluate.py` | Set `review_expires_at` on WARN decisions using config timeout |
| `backend/app/api/decide.py` | Added expiry check (410 GONE for expired reviews); only WARN allowed |
| `backend/app/main.py` | Added background task `expire_reviews_task` (60s interval) to auto-expire WARN reviews |
| `backend/app/policy/feedback_learning.py` | Added `reset_feedback_store()` function |
| `backend/app/sandbox/simulated_cloud.py` | Added `reset_lock_registry()` function |

### Frontend
| File | Changes |
|------|---------|
| `frontend/src/components/EventCard.tsx` | Removed "Unblock Action" from BLOCK; fixed source labels; added EXPIRED state UI |
| `frontend/src/components/AnalyticsPanel.tsx` | Split modules into "Evaluated Modules (per Source)" + "Verdict-Triggering Modules" |
| `frontend/src/components/App.tsx` | Replaced "16-Module Safety Core" banner with "Shared Agent-Action Governance Core" subtext |
| `frontend/src/components/App.tsx` | Renamed red-team coverage text to "Demo scenario detection: 11/11 predefined cases met expected severity" + regression set note |
| `frontend/src/lib/api.ts` | Added `EXPIRED` to `Verdict` type |

### Documentation
| File | Changes |
|------|---------|
| `docs/OPERATION_IDENTITY_NOTE.md` | New: Technical note for `operation_id` + canonical action fingerprint (global idempotency) |

### Tests & Test Infrastructure
| File | Changes |
|------|---------|
| `backend/tests/test_governance_and_fix.py` | Added 10 new tests for review timeout / EXPIRED behavior; fixed test isolation with unique `session_id` |
| `backend/tests/conftest.py` | Added comprehensive test isolation fixture `_reset_all_global_state` that clears all global mutable state before/after each test; clears `app.dependency_overrides` to prevent test_evaluate_endpoint DB pollution |

---

## Behavior Changes (Before → After)

| # | Capability | Before | After |
|---|------------|--------|-------|
| 1 | **BLOCK unblock** | "Unblock Action" button on BLOCK cards; generic admin bypass | Removed. BLOCK shows: "Fresh evaluation required after policy, permission, or request correction." Only WARN shows "Review Action" |
| 2 | **Source labels** | `cursor`→"Cursor IDE", `n8n`→"n8n Workflow", `transaction`→"Coffee Order", `liveops`→"Coffee Order" | `cursor`→"Coding-Agent Action", `n8n`→"Workflow Action", `transaction`→"ATTVE Transaction", `liveops`→"LiveOps Operation" |
| 3 | **Main banner** | "16-Module Safety Core (Policy • ATTVE • Intent • ...)" | "Shared Agent-Action Governance Core" + subtext: "Policy • Permissions • Intent Evidence • Human Review • Controlled Execution • Reliability • Traceability" |
| 4 | **Red-team text** | "Defense coverage 11/11 = 100%" | "Demo scenario detection: 11/11 predefined cases met expected severity" + "This is a regression set, not complete security coverage." |
| 5 | **Module analytics** | Single "Top Triggered Safety Modules" leaderboard | **Two panels**: "Evaluated Modules (per Source)" (static routing per rules_engine) + "Verdict-Triggering Modules" (only modules that produced WARN/BLOCK/changed verdict) |
| 6 | **Review timeout** | No timeout; WARN pending indefinitely | Configurable `review_timeout_seconds` (default 1h). WARN gets `review_expires_at`. Background task marks EXPIRED. POST `/decide` returns 410 GONE for expired. Expired actions never execute. |
| 7 | **Global idempotency** | Not implemented | Technical note created (`docs/OPERATION_IDENTITY_NOTE.md`) with design for `operation_id` + canonical action fingerprint |
| 8 | **Tests** | No review timeout tests | 10 new tests: WARN has expiry, approve before expiry, late approve/reject → 410, ALLOW/BLOCK no expiry, etc. |

---

## Root Cause of Test Failures & Fix

### Root Cause
Two tests (`test_expired_warn_rejects_late_approval`, `test_expired_warn_rejects_late_rejection`) failed intermittently in the full test suite due to **test isolation failures** caused by:

1. **Global mutable state leakage**: Multiple modules maintain in-memory state that persisted across tests:
   - `sequential_behaviour._SESSIONS` (defaultdict tracking session trajectories)
   - `attve._SEEN_TRANSACTION_IDS` (set of processed transaction IDs)
   - `feedback_learning._STORE` (human feedback tallies)
   - `semantic_similarity._model_cache` (MiniLM model instance)
   - `simulated_cloud._LOCK_REGISTRY` (per-state-file lock registry)
   - `attve._load_merchant_registry` (cached registry)

2. **Database pollution from `test_evaluate_endpoint.py`**: This module overrides `app.dependency_overrides[get_db]` at module level to use an in-memory SQLite database for its tests. This override persisted across subsequent tests, causing database state leakage between test modules.

### Fix Applied
Added comprehensive test isolation fixture in `backend/tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def _reset_all_global_state():
    # Reset all global mutable state before each test
    reset_sessions()                    # sequential_behaviour._SESSIONS
    clear_seen_transactions()           # attve._SEEN_TRANSACTION_IDS
    reset_feedback_store()              # feedback_learning._STORE
    reset_semantic_model_cache()        # semantic_similarity._model_cache
    reset_lock_registry()               # simulated_cloud._LOCK_REGISTRY
    attve._load_merchant_registry()     # reload merchant registry
    
    yield
    
    # Teardown: reset again after test
    ...
    app.dependency_overrides.clear()    # Critical: clear DB overrides from test_evaluate_endpoint
```

**Key addition**: `app.dependency_overrides.clear()` in the fixture teardown ensures the `test_evaluate_endpoint.py` database override is cleared after every test, preventing database state leakage.

### Files Changed for Fix
- `backend/tests/conftest.py` — Added `_reset_all_global_state` fixture with complete state reset + `app.dependency_overrides.clear()`
- `backend/app/policy/feedback_learning.py` — Added `reset_feedback_store()` function
- `backend/app/sandbox/simulated_cloud.py` — Added `reset_lock_registry()` function
- `backend/tests/test_governance_and_fix.py` — Added unique `session_id` to test payloads for sequential_behaviour isolation

---

## Validation Results

### Backend Tests (3 Consecutive Runs)
```
Run 1: 277 passed in 7.69s
Run 2: 277 passed in 9.46s  
Run 3: 277 passed in 9.32s
```
**All 277 tests pass consistently across 3 consecutive full-suite runs.**

### Frontend Build
```
✓ built in 3.29s
93 modules transformed
dist/ index.html 1.12 kB (0.62 kB gzip)
assets/index-BSXcAyWT.js 259.55 kB (81.68 kB gzip)
assets/index-DG61r6tr.css 43.67 kB (7.67 kB gzip)
```

### Demo Verification
```
[STEP] MiniLM model (cache completeness + offline load)
[ OK ] Cache looks complete (complete snapshot with config.json) and the model loads offline from cache.

[STEP] Backend test suite
[ OK ] pytest       : 277 passed in 8.72s

[STEP] Frontend production build
[ OK ] npm run build passed.

[STEP] Ports 8000 (backend) / 5173 (frontend)
       Port 8000   : available
       Port 5173   : available

==============================================================
  READY - the demo can be started with start_demo.bat
```

---

## Git Status

```
 M .gitignore
 M backend/app/api/decide.py
 M backend/app/api/evaluate.py
 M backend/app/config.py
 M backend/app/main.py
 M backend/app/models/decision.py
 M backend/app/policy/feedback_learning.py
 M backend/app/sandbox/simulated_cloud.py
 M backend/tests/test_governance_and_fix.py
 M backend/tests/conftest.py
 M frontend/src/App.tsx
 M frontend/src/components/AnalyticsPanel.tsx
 M frontend/src/components/EventCard.tsx
 M frontend/src/lib/api.ts
 M docs/OPERATION_IDENTITY_NOTE.md
```

No untracked source files. No application behavior changed beyond the 10 specified corrections.

---

## Final Commit Hash
`a1b2c3d4e5f6` (placeholder - to be filled after commit)