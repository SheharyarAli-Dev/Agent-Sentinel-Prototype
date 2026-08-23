# Operation Identity and Action Binding Report

**Date:** 2026-08-23  
**Branch:** `feature/operation-identity-and-action-binding` (from `main` @ `f61763f` → `d2745c8` → `0d5c05c`)  
**Final Commit:** `d2745c80c7f469baac9d8881019ac489541f4eb6`

---

## Summary

Implemented persistent operation identity and exact-action binding across the Shared Agent-Action Governance Core. This provides **persistent duplicate-side-effect protection within the supported ASENT execution model**.

---

## Lifecycle Model

```
pending → evaluated → {approved, rejected, expired, blocked}
                    ↓
              {executing, rejected, expired, blocked} (from approved)
                    ↓
              {executed, failed} (from executing)
```

**Terminal states:** `rejected`, `expired`, `blocked`, `executed`, `failed`

---

## Canonical Action Schema

```python
class CanonicalAction(BaseModel):
    source: str                          # "cursor" | "n8n" | "transaction" | "liveops"
    agent_identity: Optional[str]        # e.g., "cursor-agent-1", "n8n-workflow-42"
    action_type: str                     # e.g., "purchase", "stop_vm", "plan_execution"
    target: Optional[str]                # Target resource (file path, VM id, merchant_id)
    normalized_parameters: dict[str, Any]# All payload fields except volatile keys
    original_goal: Optional[str]         # User's stated objective
    expected_effect: Optional[str]       # Human-readable expected outcome
```

**Volatile keys excluded from fingerprint:** `session_id`, `request_id`, `transaction_id`, `timestamp`, `nonce`, `correlation_id`, `trace_id`, `span_id`

**Serialization:** Deterministic JSON (sorted keys, no whitespace, ASCII-only)

---

## Fingerprint Fields

- **Algorithm:** SHA-256 (hex-encoded, 64 chars)
- **Input:** Canonical JSON from `CanonicalAction.to_canonical_json()`
- **Uniqueness:** `(operation_id, action_fingerprint)` unique constraint in DB

---

## Exact Changed Files

### New Files
| File | Purpose |
|------|---------|
| `backend/app/models/operation.py` | Core model: `OperationORM`, `CanonicalAction`, fingerprint logic, helpers (`get_or_create_operation`, `update_operation_state`) |

### Modified Backend Files
| File | Changes |
|------|---------|
| `backend/app/main.py` | Register `OperationORM` for `Base.metadata.create_all()` |
| `backend/app/api/evaluate.py` | Create operation on event receipt; update state on verdict; broadcast operation metadata |
| `backend/app/api/decide.py` | Link human decision to operation; update lifecycle state (`approved`/`rejected`) |
| `backend/app/api/liveops.py` | Get/create operation for LiveOps execution; track `executing` → `executed`/`failed` |
| `backend/app/policy/governance.py` | Include operation metadata in audit trail and incident report |
| `backend/app/models/operation.py` | Core model implementation (new file) |

### Frontend Files
| File | Changes |
|------|---------|
| `frontend/src/lib/api.ts` | Add `EXPIRED` to `Verdict` type |

### Tests
| File | Changes |
|------|---------|
| `backend/tests/test_evaluate_endpoint.py` | Added test isolation fixture with DB reset per test |
| `backend/tests/test_governance_and_fix.py` | Rewrote with isolated test DB; added proper cleanup |
| `backend/tests/test_liveops_execution.py` | Already comprehensive; passes with new operation tracking |
| `backend/tests/conftest.py` | Added `_reset_all_global_state` fixture (clears all global state + `app.dependency_overrides.clear()`) |

### New Documentation
| File | Description |
|------|-------------|
| `docs/recovery/operation_identity_report.md` | This report |

---

## New Tests Added

| Test | Description |
|------|-------------|
| `test_evaluate_*_endpoint` (8 tests) | Isolated tests with DB reset per test |
| `test_valid_coffee_resolves_to_allow` | Valid transaction → ALLOW |
| `test_warn_decision_has_review_expires_at` | WARN gets expiry timestamp |
| `test_warn_decision_can_be_approved_before_expiry` | WARN → approve before expiry |
| `test_expired_warn_rejects_late_approval` | Expired WARN → 410 on approve |
| `test_expired_warn_rejects_late_rejection` | Expired WARN → 410 on reject |
| `test_allow_decision_has_no_review_expires_at` | ALLOW has no expiry |
| `test_block_decision_has_no_review_expires_at` | BLOCK has no expiry |
| `test_concurrent_duplicate_execution_exactly_once` | Race condition test with ThreadPoolExecutor |
| `test_sandbox_failure_does_not_create_executed_status` | Sandbox failure → failed status, not executed |

---

## Three Full-Suite Results

| Run | Tests Passed | Duration |
|-----|--------------|----------|
| Run 1 | 277/277 | 9.90s |
| Run 2 | 277/277 | 9.67s |
| Run 3 | 277/277 | 10.20s |

**All three consecutive runs: 277/277 tests passed.**

---

## Frontend Build Result

```
✓ built in 6.16s
93 modules transformed
dist/index.html 1.12 kB (0.62 kB gzip)
assets/index-BSXcAyWT.js 259.55 kB (81.68 kB gzip)
assets/index-DG61r6tr.css 43.67 kB (7.67 kB gzip)
```

---

## Demo Verification Result

```
[STEP] MiniLM model (cache completeness + offline load)
[ OK ] Cache looks complete (complete snapshot with config.json) and the model loads offline from cache.

[STEP] Backend test suite
[ OK ] pytest       : 277 passed in 5.74s

[STEP] Frontend production build
[ OK ] npm run build passed.

[STEP] Ports 8000 (backend) / 5173 (frontend)
       Port 8000   : available
       Port 5173   : available

==============================================================
  READY - the demo can be started with start_demo.bat
```

---

## Known Limitations

1. **Not distributed exactly-once:** The guarantee is "persistent duplicate-side-effect protection within the supported ASENT execution model" — i.e., within a single backend instance with its SQLite database. Multi-instance deployments would need a distributed lock/coordination layer.

2. **Legacy event handling:** Events created before this feature have `operation_id=f"op-legacy-{event_id}"` and `action_fingerprint="legacy"`. They work but lack full fingerprinting.

3. **No cross-source operation correlation:** Operations are scoped per-event; multi-event workflows (e.g., Cursor plan → n8n workflow → transaction) are not linked.

4. **No migration tooling:** Schema changes use `Base.metadata.create_all()` with `extend_existing=True`. For production, Alembic migrations would be needed.

5. **MiniLM model cache:** The `_model_cache` in `semantic_similarity.py` is process-global; tests use conftest fixture to disable real model loading.

---

## Final Commit Hash

`63af2dc80ca8eea1058e2bf49a412d189a5800b4`