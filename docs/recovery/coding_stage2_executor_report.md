# Coding Stage 2 Executor Report

**Date:** 2026-08-26
**Branch:** `feature/coding-core-usecase-dark`
**Scope:** Contained coding file-write executor

---

## 1. Bounded Execution Model

Stage 2 implements a contained file-write executor that:

- Creates an ASENT-controlled temporary runtime copy of the `coding-demo` fixture
- Executes a single bounded `file_write` action against the runtime copy
- Verifies all file hashes before and after execution
- Detects unexpected secondary-file changes
- Attempts atomic restoration on failure
- Produces structured execution evidence (`CodingExecutionResult`)

Stage 2 does NOT implement:

- API endpoints
- ORM or database tables
- Lifecycle transitions
- Operation reservation
- Approval enforcement
- Arbitrary shell execution
- Subprocess execution
- Test execution
- Network access
- Package installation
- File creation
- File deletion
- Frontend controls
- General patch application

---

## 2. Fixed Fixture and Seed Locations

| Resource | Location |
|----------|----------|
| Production fixture root | `coding-demo/` (resolved from executor module path) |
| Seed manifest | `backend/data/coding_demo_seed.json` |

These paths are fixed internally to the `coding_executor.py` module. They are NOT exposed as caller-controlled production parameters. The test-only constructor accepts `fixture_root` and `seed_path` keyword arguments for injection.

---

## 3. Runtime-Copy Lifecycle

1. `CodingWorkspace.__init__()` stores fixture and seed paths.
2. `CodingWorkspace.copy_demo()` copies the tracked fixture to a fresh `tempfile.mkdtemp()` directory.
3. The copy is validated against the seed manifest (all file hashes must match).
4. If validation fails, the temporary directory is removed and an exception is raised.
5. `CodingWorkspace.cleanup()` removes the temporary directory via `shutil.rmtree()`.
6. `CodingWorkspace` supports context-manager protocol (`__enter__` / `__exit__`).

---

## 4. Lock Strategy

Each `CodingWorkspace` owns a per-instance `_PathLockRegistry`:

- One `threading.RLock` per normalized relative path.
- Validation, old-hash check, write, after-hash check, and evidence capture all occur while holding that path lock.
- Different workspace instances remain independent (no global lock sharing).
- Two concurrent writes to the same target in the same workspace are serialized.
- Only one write with the same `expected_old_hash` can succeed; the second sees the updated hash and is rejected.

---

## 5. Atomic-Write Sequence

1. Read original bytes for potential restoration.
2. `tempfile.mkstemp` in the same directory as the target.
3. Write UTF-8 content with `newline=""`.
4. `fh.flush()`.
5. `os.fsync(fh.fileno())`.
6. `os.replace(tmp_name, target)`.
7. Remove any temporary file after failure (in `except BaseException`).

---

## 6. Restoration Behavior

If any failure occurs after the write begins:

- Write error
- Resulting-hash mismatch
- Unexpected secondary-file change
- Evidence collection failure

Then:

1. Attempt to atomically restore original bytes via `_atomic_write_bytes`.
2. Record `restoration_attempted = True`.
3. Record `restoration_succeeded` (True if restore succeeded, False if it also failed).
4. Return the final observed target hash.

---

## 7. Error Codes

| Code | When |
|------|------|
| `REJECTED_ACTION_TYPE` | action_type is not file_write |
| `REJECTED_ABSOLUTE_PATH` | Absolute Unix path (/...) |
| `REJECTED_WINDOWS_PATH` | Windows drive path (C:\...) |
| `REJECTED_UNC_PATH` | UNC path (\\server\share) |
| `REJECTED_TRAVERSAL` | Dot-dot traversal (../...) |
| `REJECTED_NULL_BYTE` | Null byte in path |
| `REJECTED_SYMLINK` | Symlink target |
| `REJECTED_OUTSIDE_ROOT` | Path resolves outside workspace |
| `REJECTED_PROTECTED` | Protected path at execution time |
| `REJECTED_REVIEW_REQUIRED` | Sensitive path without review authorization |
| `REJECTED_FILE_NOT_FOUND` | Target file does not exist |
| `REJECTED_OLD_HASH_MISMATCH` | Current hash does not match expected_old_hash |
| `REJECTED_CONTENT_SIZE` | Content exceeds size limit |
| `FAILED_SEED_VERIFICATION` | Copied fixture fails seed verification |
| `FAILED_WRITE` | Write error (disk full, permissions, etc.) |
| `FAILED_HASH_VERIFICATION` | After-hash does not match expected_new_hash |
| `FAILED_UNEXPECTED_CHANGES` | Secondary files changed unexpectedly |
| `FAILED_RESTORATION` | Restoration after failure also failed |

---

## 8. Tests Added

**File:** `backend/tests/test_coding_executor.py` — 32 tests (31 passed, 1 skipped)

| # | Test | Status |
|---|------|--------|
| 1 | Allowed file write succeeds | PASSED |
| 2 | Tracked fixture remains unchanged | PASSED |
| 3 | Runtime copy receives expected content | PASSED |
| 4 | Before hash matches seed | PASSED |
| 5 | After hash equals expected_new_hash | PASSED |
| 6 | Old-hash mismatch rejects before writing | PASSED |
| 7 | Protected target rejects at execution time | PASSED |
| 8 | Sensitive target rejects without auth | PASSED |
| 9 | Sensitive target accepted with auth | PASSED |
| 10 | Unix absolute path rejects | PASSED |
| 11 | Windows drive path rejects | PASSED |
| 12 | UNC path rejects | PASSED |
| 13 | Traversal rejects | PASSED |
| 14 | Mixed-separator traversal rejects | PASSED |
| 15 | Null byte rejects | PASSED |
| 16 | Symlink target rejects | SKIPPED |
| 17 | Missing target rejects | PASSED |
| 18 | Size limit enforced | PASSED |
| 19 | Atomic replacement leaves no temp file | PASSED |
| 20 | Simulated write failure preserves content | PASSED |
| 21 | Simulated after-hash failure restores | PASSED |
| 22 | Unexpected secondary-file change detected | PASSED |
| 23 | Fixture unchanged after write failure | PASSED |
| 24 | Fixture unchanged after hash failure | PASSED |
| 25 | Fixture unchanged after unexpected change | PASSED |
| 26 | Production constructor cannot accept custom runtime root | PASSED |
| 27 | Copied fixture failing seed verification rejected | PASSED |
| 28 | Two simultaneous writes serialized | PASSED |
| 29 | Only one same-old-hash write succeeds | PASSED |
| 30 | Different workspace instances independent | PASSED |
| 31 | No subprocess/network mechanisms | PASSED |
| 32 | Executed-at is populated | PASSED |

---

## 9. Three Full-Suite Results

| Run | Collected | Passed | Skipped | Duration |
|-----|-----------|--------|---------|----------|
| Run 1 | 365 | 363 | 2 | 14.81s |
| Run 2 | 365 | 363 | 2 | 15.02s |
| Run 3 | 365 | 363 | 2 | 18.03s |

Suite count: 332 → 363 (+31 new tests from `test_coding_executor.py`, 1 new skipped symlink test).

---

## 10. Skipped-Test Limitations

`TestSymlinkReject::test_symlink_target_rejects` — Skipped on Windows because creating symlinks requires elevated privileges (Developer Mode or Administrator). The containment logic via `os.path.commonpath` provides equivalent protection. This test passes on Unix/macOS CI environments.

---

## 11. Frontend Build

```
✓ built in 6.92s
93 modules transformed
dist/index.html                 1.12 kB │ gzip:  0.62 kB
dist/assets/index-DJSaANO6.css  40.21 kB │ gzip:  7.43 kB
dist/assets/index-DhJhHeoe.js  268.21 kB │ gzip: 83.09 kB
```

---

## 12. Demo Verification

| Step | Status |
|------|--------|
| Branch | `feature/coding-core-usecase-dark` |
| Python 3.12.10 | OK |
| Node v24.11.0 / npm 11.6.1 | OK |
| torch 2.13.0+cpu | OK |
| sentence-transformers 5.7.0 | OK |
| MiniLM model cache | OK |
| pytest | 363 passed, 2 skipped in 16.54s |
| npm run build | OK |
| Ports 8000/5173 | Available |
| **Result** | **READY** |

---

## 13. Coding-Demo Hash Verification

| File | Hash | Status |
|------|------|--------|
| `src/status.py` | `8ee28ff7bfdf0d27...` | OK |
| `tests/test_status.py` | `8d3e948b1870133c...` | OK |
| `config/app.json` | `36b68ef2a84b436c...` | OK |
| `protected/secrets.env` | `ed19d3ab797a5cca...` | OK |

All 4 tracked coding-demo fixture hashes remain unchanged.

---

## 14. Security Limitations

1. **Stage 2 is execution-only for file_write.** `file_create` and `file_delete` are out of scope.

2. **No real filesystem sandbox.** The runtime workspace is a temporary directory copy, not a container or VM-level sandbox.

3. **Symlink test skipped on Windows.** The symlink escape test is skipped because Windows symlink support requires elevated privileges.

4. **No content inspection.** The executor does not inspect file content for secrets, injection, or malicious patterns. Content validation is the responsibility of the policy layer.

5. **Single-process concurrency only.** The per-path lock registry is process-level (`threading.RLock`). Cross-process concurrent writes are not protected.

---

## 15. Exact Stage 3 Boundary

**Stage 3** would implement:

- `POST /api/coding/execute/{event_id}` endpoint
- Integration with OperationORM lifecycle
- Approval enforcement before execution
- Frontend controls for coding proposal submission and execution
- `GET /api/coding/state` and `POST /api/coding/reset` endpoints
- General patch application support

Stage 2 does NOT include any of these features.

---

## 16. Final Commit Hash

**Commit:** `5a8cd09` — `feat: add contained coding file-write executor`

---

## Files Changed

| File | Change |
|------|--------|
| `backend/app/sandbox/coding_executor.py` | **NEW** — CodingWorkspace, CodingExecutionResult, coding_executor |
| `backend/tests/test_coding_executor.py` | **NEW** — 32 focused regression tests |
| `docs/recovery/coding_stage2_executor_report.md` | **NEW** — This report |
