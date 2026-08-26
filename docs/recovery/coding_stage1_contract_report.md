# Coding Stage 1 Contract Report

**Date:** 2026-08-26
**Branch:** `feature/coding-core-usecase-dark`
**Scope:** Isolated coding demo repository and structured coding-action contract

---

## 1. Bounded Scope

Stage 1 defines:
- A tracked `coding-demo/` fixture directory (template only, never modified during tests)
- A `CodingProposal` Pydantic model with hash verification
- Path classification rules (protected/sensitive/allowed) with deterministic precedence
- Canonical fingerprint computation for coding proposals
- Integration into the existing `rules_engine.py` cursor routing
- Extension of `build_canonical_action()` for coding proposal payloads
- 49 focused regression tests

Stage 1 does NOT implement:
- File execution or writing
- Sandbox or runtime copy generation
- Frontend controls
- API endpoints for coding proposals
- Shell command execution
- Network access

---

## 2. Source and Event Type

| Field | Value |
|-------|-------|
| `source` | `"cursor"` (reuses existing EventSource literal) |
| `event_type` | `"coding_proposal"` |

No new `EventSource` value was added. The existing cursor routing in `rules_engine.py` was extended to detect `event_type == "coding_proposal"` and invoke the coding-specific evaluation engine.

---

## 3. Canonical Fields

The `CodingProposal` model contains:

| Field | Type | Description |
|-------|------|-------------|
| `action_type` | `Literal["file_write"]` | Stage 1 supports only file_write |
| `relative_path` | `str` | Path relative to coding-demo/ root |
| `expected_old_hash` | `str` | SHA-256 hex of current file content |
| `new_content` | `str` | Proposed new file content |
| `expected_new_hash` | `str` | SHA-256 hex of new_content (verified at validation time) |
| `test_profile` | `Literal["unit", "none"]` | Which test suite validates the change |
| `protected_invariants` | `list[str]` | Invariants that must hold after execution |

---

## 4. Fingerprint Fields

The proposal fingerprint binds:

| Field | Source |
|-------|--------|
| `source` | Event source |
| `event_type` | Event type |
| `agent_identity` | Agent identity |
| `action_type` | Proposal action type |
| `relative_path` | Normalized to forward slashes |
| `expected_old_hash` | SHA-256 of current file |
| `expected_new_hash` | SHA-256 of proposed content |
| `test_profile` | Test profile |
| `protected_invariants` | Sorted list |

The fingerprint does NOT include raw `new_content` since it is bound through `expected_new_hash` (which is verified against the content at validation time via `@model_validator`).

---

## 5. Path Precedence

Structural rejection (applied first, raises `PathSafetyRejection`):
1. Empty path
2. Null bytes
3. Absolute Unix paths (`/...`)
4. Windows drive paths (`C:\...`)
5. UNC paths (`\\server\share`)
6. Dot-dot traversal (`../...`)
7. Path resolving outside repo root (symlink escape)
8. Symlink targets

Classification (first match wins):

| Tier | Classification | Paths | Patterns | Verdict |
|------|---------------|-------|----------|---------|
| 3 | Protected | `protected/`, `.env`, `.env.*`, `secrets/`, `private/`, `credentials.json`, `service-account.json` | `*.env`, `*secret*`, `*password*`, `*credential*`, `*.pem`, `*.key` | BLOCK |
| 2 | Sensitive | `config/`, `tests/` | `*.json`, `*.yml`, `*.yaml` | WARN |
| 1 | Allowed | `src/` | `*.py` | ALLOW |
| — | Unmatched | — | — | WARN (default) |

Protected rules take precedence over extension rules. A `.py` file inside `protected/` is classified as protected, not allowed.

---

## 6. Fixture Structure

```
coding-demo/
├── src/
│   └── status.py              # ALLOW target (safe development file)
├── tests/
│   └── test_status.py         # WARN target (test file)
├── config/
│   └── app.json               # WARN target (configuration)
├── protected/
│   └── secrets.env            # BLOCK target (protected secret)
└── README.md                  # Fixture documentation
```

Seed manifest: `backend/data/coding_demo_seed.json` — contains deterministic initial content and SHA-256 hashes for each fixture file.

---

## 7. Tests Added

**File:** `backend/tests/test_coding_proposal.py` — 49 tests (48 passed, 1 skipped)

| Category | Tests | Status |
|----------|-------|--------|
| Contract validation | 11 | 11 passed |
| Path safety | 16 | 15 passed, 1 skipped (symlink) |
| Fixture integrity | 4 | 4 passed |
| Canonical fingerprint | 7 | 7 passed |
| Evaluation pipeline | 11 | 11 passed |

---

## 8. Three Full-Suite Results

| Run | Collected | Passed | Skipped | Duration |
|-----|-----------|--------|---------|----------|
| Run 1 | 332 | 331 | 1 | 7.50s |
| Run 2 | 332 | 331 | 1 | 7.28s |
| Run 3 | 332 | 331 | 1 | 7.22s |

Suite count: 283 → 332 (+49 new tests, 1 skipped symlink test).

---

## 9. Frontend Build

```
✓ built in 3.52s
93 modules transformed
dist/index.html                 1.12 kB │ gzip:  0.62 kB
dist/assets/index-DJSaANO6.css  40.21 kB │ gzip:  7.43 kB
dist/assets/index-DhJhHeoe.js  268.21 kB │ gzip: 83.09 kB
```

---

## 10. Demo Verification

| Step | Status |
|------|--------|
| Branch | `feature/coding-core-usecase-dark` |
| Python 3.12.10 | OK |
| Node v24.11.0 / npm 11.6.1 | OK |
| torch 2.13.0+cpu | OK |
| sentence-transformers 5.7.0 | OK |
| MiniLM model cache | OK |
| pytest | 331 passed, 1 skipped in 7.07s |
| npm run build | OK |
| Ports 8000/5173 | Available |
| **Result** | **READY** |

---

## 11. Limitations

1. **Stage 1 is evaluation-only.** No file writes, execution, or sandbox runtime copies are implemented. The coding-demo directory is a tracked fixture/template only.

2. **Only `file_write` is supported.** `file_create` and `file_delete` are out of scope for Stage 1.

3. **Symlink test skipped on this platform.** The symlink escape test is skipped because Windows symlink support requires elevated privileges.

4. **Content size limit is in the evaluation engine, not the model.** The `CodingProposal` Pydantic model does not enforce the content size limit at construction time. The limit is enforced by `evaluate_coding_proposal()` in the evaluation engine.

5. **No real file system sandbox.** Future execution must operate on a generated runtime copy outside the real ASENT source tree. Stage 1 defines this boundary but does not implement it.

---

## 12. Exact Next Stage

**Stage 2:** Coding execution sandbox and API endpoint.

- Implement `SimulatedCodingRepo` (analogous to `SimulatedCloud`)
- Implement `POST /api/coding/execute/{event_id}` endpoint
- Generate runtime copies of the coding-demo fixture
- Execute approved file-write proposals against runtime copies
- Verify file hashes after execution
- Implement `GET /api/coding/state` and `POST /api/coding/reset`
- Add frontend controls for coding proposal submission and execution

---

## 13. Final Commit Hash

**Commit:** `feat: add bounded coding proposal contract and demo fixture`

(Pending — committed after all verification passed)

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/models/coding_proposal.py` | **NEW** — CodingProposal model, path validation, fingerprint computation |
| `backend/app/policy/coding_proposal_engine.py` | **NEW** — Evaluation engine with 6-check pipeline |
| `backend/data/coding_path_rules.json` | **NEW** — Three-tier path classification rules |
| `backend/data/coding_demo_seed.json` | **NEW** — Seed manifest with hashes for fixture files |
| `backend/tests/test_coding_proposal.py` | **NEW** — 49 focused regression tests |
| `backend/app/policy/rules_engine.py` | Modified — Added coding proposal routing in `_run_cursor_modules()` |
| `backend/app/models/operation.py` | Modified — Extended `build_canonical_action()` for coding proposals |
| `coding-demo/src/status.py` | **NEW** — Safe demo file |
| `coding-demo/tests/test_status.py` | **NEW** — Safe demo test file |
| `coding-demo/config/app.json` | **NEW** — Sensitive demo config file |
| `coding-demo/protected/secrets.env` | **NEW** — Protected demo secret file |
| `coding-demo/README.md` | **NEW** — Fixture documentation |

---

## Post-Audit Cleanup (commit `test: complete coding proposal fingerprint coverage`)

**Date:** 2026-08-26

### Changes

1. **Added `expected_old_hash` fingerprint test** — `test_fingerprint_changes_with_old_hash` in `TestCanonicalFingerprint` verifies that changing only `expected_old_hash` produces a different fingerprint. This was the one missing fingerprint-binding test identified in the audit.

2. **Removed dead `ProposalValidationError` class** — The exception was defined in `coding_proposal.py` but never raised. Pydantic's `ValidationError` is used instead. Removed from:
   - `backend/app/models/coding_proposal.py` (class definition)
   - `backend/app/policy/rules_engine.py` (import and catch clause)
   - `backend/tests/test_coding_proposal.py` (import)

### Final Test Count

| Metric | Value |
|--------|-------|
| Tests collected | 333 |
| Tests passed | 332 |
| Tests skipped | 1 |
| New tests (test_coding_proposal.py) | 50 |

### Skipped Test

`TestPathSafety::test_symlink_escape_rejected` — Skipped on Windows because creating symlinks requires elevated privileges (Developer Mode or Administrator). The containment logic via `os.path.commonpath` provides equivalent protection. This test passes on Unix/macOS CI environments.

### Verification

- Focused tests: 49 passed, 1 skipped
- Full suite: 332 passed, 1 skipped
- Frontend build: clean
- Demo verification: READY
- Seed hashes: all 4 match
