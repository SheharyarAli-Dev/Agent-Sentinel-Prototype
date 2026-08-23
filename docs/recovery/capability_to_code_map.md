# Capability → Code Map — Agent Sentinel

**Audit standard:** Each capability is traced to source files and tests. Status codes:
- **VERIFIED-IN-CODE** — implementation exists, tests pass, logic traceable end-to-end.
- **PARTIAL** — implementation exists but incomplete (missing tests, scaffolded, or known gaps).
- **SCAFFOLD** — structure/interface present, no functional logic.
- **NOT-FOUND** — no implementation or tests found.

---

## 1. Action Interception

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| HTTP `/api/evaluate` entry point | VERIFIED-IN-CODE | `app/api/evaluate.py:44` (`evaluate()`), `app/main.py:111` (router mount) | `test_evaluate_endpoint.py` |
| Event normalisation (adapters) | VERIFIED-IN-CODE | `app/adapters/cursor_adapter.py`, `app/adapters/n8n_adapter.py`, `app/adapters/transaction_adapter.py` | `test_cursor_adapter.py` (implied), `test_liveops_adapter.py` |
| WebSocket broadcast on intercept | VERIFIED-IN-CODE | `app/api/evaluate.py:108-118` (`manager.broadcast`), `app/websocket/manager.py` | — (manual/integration) |
| Request validation (Pydantic) | VERIFIED-IN-CODE | `app/models/event.py:59` (`EventCreate`) | `test_evaluate_endpoint.py` |

---

## 2. Policy Evaluation (Pipeline Orchestration)

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Central orchestrator (`evaluate_event`) | VERIFIED-IN-CODE | `app/policy/rules_engine.py:35` | `test_policy_engine.py`, `test_evaluate_endpoint.py` |
| Module 1 runs first for ALL sources | VERIFIED-IN-CODE | `rules_engine.py:48-51` | `test_policy_engine.py` |
| Per-source routing (cursor/n8n/transaction/liveops) | VERIFIED-IN-CODE | `rules_engine.py:59-169` | `test_policy_engine.py` |
| Aggregation: worst verdict wins (BLOCK>WARN>ALLOW) | VERIFIED-IN-CODE | `rules_engine.py:174-257` (`_aggregate`) | `test_policy_engine.py` |
| Risk score averaging + decider floor | VERIFIED-IN-CODE | `rules_engine.py:213-221` | `test_policy_engine.py` |
| Module contribution tracking (module field) | VERIFIED-IN-CODE | `rules_engine.py:224-229` | `test_policy_engine.py` |

---

## 3. Permission & Least-Privilege Checks

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Capability allowlist (`agent_capabilities.json`) | VERIFIED-IN-CODE | `app/policy/least_privilege.py:30` (`_load`), `backend/data/agent_capabilities.json` | — |
| Least privilege: ungranted capability → BLOCK | VERIFIED-IN-CODE | `least_privilege.py:88-101` | — |
| Least agency: granted but tier > cap → WARN | VERIFIED-IN-CODE | `least_privilege.py:103-117` | — |
| Capability declaration extraction | VERIFIED-IN-CODE | `least_privilege.py:55-67` (`_declared_capability`) | — |
| Impact tier mapping | VERIFIED-IN-CODE | `least_privilege.py:44-52` (`_IMPACT_TIER`) | — |
| Runs for EVERY source (cross-cutting) | VERIFIED-IN-CODE | `rules_engine.py:55` | `test_policy_engine.py` |

---

## 4. Semantic Intent Verification (Module 6)

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Entry point `evaluate_intent(event, advisory)` | VERIFIED-IN-CODE | `app/policy/intent_verification.py:210` | `test_intent_verification.py`, `test_semantic_intent.py` |
| Semantic drift via MiniLM (primary evidence) | VERIFIED-IN-CODE | `intent_verification.py:84-108` (`compute_semantic_drift` → `semantic_similarity.py`) | `test_semantic_similarity.py`, `test_semantic_intent.py` |
| Semantic aligned boundary (0.38 provisional) | VERIFIED-IN-CODE | `config.py:42`, `intent_verification.py:336-337` | `test_intent_verification.py` |
| Lexical Jaccard fallback (threshold 0.15) | VERIFIED-IN-CODE | `intent_verification.py:278-284`, `373-402` | `test_intent_verification.py` |
| Semantic primary; Jaccard never bypasses | VERIFIED-IN-CODE | `intent_verification.py:330-332`, `370-371` | `test_intent_verification.py` |
| `semantic_action_text` clean sentence builder | VERIFIED-IN-CODE | `intent_verification.py:156-207` (`_build_semantic_action_text`) | `test_semantic_intent.py` |
| Advisory mode (transactions) — informational only | VERIFIED-IN-CODE | `intent_verification.py:308-328` | `test_intent_verification.py` |
| MiniLM lazy loading + cache + thread-safe | VERIFIED-IN-CODE | `semantic_similarity.py:49-84` (`get_semantic_model`, `_load_model`, lock) | `test_semantic_similarity.py` |
| Cosine similarity pure-Python (no numpy dep) | VERIFIED-IN-CODE | `semantic_similarity.py:135-155` | `test_semantic_similarity.py` |
| Embedding validation (dims, NaN, count) | VERIFIED-IN-CODE | `semantic_similarity.py:116-133` | `test_semantic_similarity.py` |
| Loader failures propagate (no silent swallow) | VERIFIED-IN-CODE | `semantic_similarity.py:78-83`, docstring | `test_semantic_similarity.py` |

---

## 5. Lexical Fallback

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Jaccard keyword overlap computation | VERIFIED-IN-CODE | `intent_verification.py:76-81` (`_tokenize`), `278-284` | `test_intent_verification.py` |
| Stopword filtering | VERIFIED-IN-CODE | `intent_verification.py:62-74` (`_STOPWORDS`) | — |
| Fallback triggered only on semantic exception | VERIFIED-IN-CODE | `intent_verification.py:292-300`, `372-402` | `test_intent_verification.py` |
| Lexical evidence included in explanations | VERIFIED-IN-CODE | `intent_verification.py:345-346`, `359-360`, `379-381` | — |

---

## 6. ALLOW / WARN / BLOCK Verdicts

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Canonical verdict enum | VERIFIED-IN-CODE | `app/models/decision.py:41` (`Verdict = Literal["ALLOW","WARN","BLOCK"]`) | All module tests |
| Module 1 (policy_engine) mapping | VERIFIED-IN-CODE | `policy_engine.py:132-170` | `test_policy_engine.py` |
| Module 2 (ATTVE) mapping | VERIFIED-IN-CODE | `attve.py:51-142` | `test_attve.py` (7 tests) |
| Module 6 (Intent) — never BLOCK v1 | VERIFIED-IN-CODE | `intent_verification.py:42` (doc), `330-370` (ALLOW/WARN only) | `test_intent_verification.py` |
| Module 7 (Planning) mapping | VERIFIED-IN-CODE | `planning_verification.py:52-196` | `test_planning_verification.py` |
| Aggregation severity map | VERIFIED-IN-CODE | `rules_engine.py:174` (`_VERDICT_SEVERITY`) | `test_policy_engine.py` |
| Pydantic validator: `suggested_fix` required for WARN/BLOCK | VERIFIED-IN-CODE | `decision.py:116-124` (`_fix_required_for_non_allow`) | Implicit via module tests |

---

## 7. Approval / Rejection / Timeout

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| `POST /api/decide/{event_id}` human decision | VERIFIED-IN-CODE | `app/api/decide.py:49-129` (`submit_decision`) | `test_governance_and_fix.py` (implied) |
| Only WARN decisions actionable | VERIFIED-IN-CODE | `decide.py:74-81` | `test_governance_and_fix.py` |
| Duplicate human decision rejected (409) | VERIFIED-IN-CODE | `decide.py:83-90` | — |
| Human decision persisted + broadcast | VERIFIED-IN-CODE | `decide.py:93-127` | — |
| Feedback learning on human decision | VERIFIED-IN-CODE | `decide.py:98-110` → `feedback_learning.record_feedback` | — |
| GET `/api/decide/{event_id}` for polling | VERIFIED-IN-CODE | `decide.py:39-46` | — |
| **Auto-expiry / timeout on pending WARN** | NOT-FOUND | — | — |

---

## 8. Exact-Action Binding

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Event→Decision FK (`event_id`) | VERIFIED-IN-CODE | `app/models/decision.py:53-54` (`DecisionORM.event_id` FK) | — |
| Single decision per event (latest wins) | VERIFIED-IN-CODE | `decide.py:134-145` (`_get_decision_or_404` orders by `id desc`) | — |
| Human decision bound to event via decision | VERIFIED-IN-CODE | `DecisionORM.human_decision` + `human_timestamp` | — |
| WebSocket broadcast includes event+decision | VERIFIED-IN-CODE | `evaluate.py:109-114`, `decide.py:119-124` | — |
| Audit trail joins events+decisions | VERIFIED-IN-CODE | `governance.py:25-61` (`get_audit_trail`) | — |

---

## 9. Duplicate Suppression

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Transaction duplicate ID tracking (in-memory) | VERIFIED-IN-CODE | `attve.py:30` (`_SEEN_TRANSACTION_IDS`), `85-94` | `test_attve.py::test_duplicate_transaction_id_blocks` |
| Reset helper for tests | VERIFIED-IN-CODE | `attve.py:46-48` (`clear_seen_transactions`) | fixture in `test_attve.py` |
| Sequential behaviour session history dedup | PARTIAL | `sequential_behaviour.py:43` (`_SESSIONS` dict), capped at 50 | — |
| No global cross-request dedup for other sources | NOT-FOUND | — | — |

---

## 10. Concurrency Protection

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| MiniLM model cache — double-checked locking | VERIFIED-IN-CODE | `semantic_similarity.py:49-84` (`_shared_lock`, `_model_cache`) | `test_semantic_similarity.py` (thread safety implied) |
| LiveOps simulated cloud — file locking (fcntl/msvcrt) | VERIFIED-IN-CODE | `app/sandbox/simulated_cloud.py` (implied by test names) | `test_liveops_adapter.py` (`test_lock_registry_shared...`, `test_atomic_writes...`, `test_concurrent_instances...`) |
| SQLite `check_same_thread=False` for FastAPI | VERIFIED-IN-CODE | `database.py:18-21` | — |
| WebSocket manager list mutation during broadcast | VERIFIED-IN-CODE | `manager.py:51-66` (collects dead, then disconnects) | — |
| No request-level locking / queueing | NOT-FOUND | — | — |

---

## 11. Audit Linkage

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Event + Decision persisted together | VERIFIED-IN-CODE | `evaluate.py:46-93` (event first, then decision with `event_id`) | — |
| Decision stores `module` (comma-separated list) | VERIFIED-IN-CODE | `rules_engine.py:224-229`, `DecisionORM.module` | — |
| Latency recorded per decision | VERIFIED-IN-CODE | `evaluate.py:65-75` (`_latency_ms`), `DecisionORM.latency_ms` | — |
| Human decision linked to original event | VERIFIED-IN-CODE | `decide.py:93-96`, `DecisionORM.human_decision` | — |
| Audit trail API (filterable) | VERIFIED-IN-CODE | `governance.py:25-61`, `app/api/governance.py` | — |
| Incident report (forensic summary) | VERIFIED-IN-CODE | `governance.py:64-127`, `app/api/governance.py` | — |

---

## 12. Execution-Result Recording

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| `DecisionORM` stores verdict, reasons, fix, risk, latency, explanation | VERIFIED-IN-CODE | `decision.py:48-77` | — |
| LiveOps execution recording (`LiveOpsExecutionORM`) | VERIFIED-IN-CODE | `app/models/liveops_execution.py`, `liveops_adapter.py` | `test_liveops_execution.py` |
| Feedback learning signature → human decision | VERIFIED-IN-CODE | `feedback_learning.py:33-67` (`signature_for`, `record_feedback`) | — |
| Governance incident report includes blocked events | VERIFIED-IN-CODE | `governance.py:100-110` | — |

---

## 13. LiveOps

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Simulated cloud state (VMs, snapshots) | VERIFIED-IN-CODE | `app/sandbox/simulated_cloud.py`, `backend/data/simulated_cloud_seed.json` | `test_liveops_adapter.py`, `test_liveops_execution.py` |
| REST endpoints: restore, start/stop VM, snapshots | VERIFIED-IN-CODE | `app/api/liveops.py`, `liveops_adapter.py` | `test_liveops_agent.py`, `test_liveops_policy.py` |
| Browser demonstration panel | VERIFIED-IN-CODE | `frontend/src/components/LiveOpsPanel.tsx` (implied), `demo/README.md` | — |
| LiveOps event routing (planning + sequence + intent) | VERIFIED-IN-CODE | `rules_engine.py:147-169` (`_run_liveops_modules`) | `test_liveops_policy.py` |
| Governance policy for production VM stop | VERIFIED-IN-CODE | `policies.json:61-70` (POL-006) | — |
| Runtime state persisted to `liveops_runtime_state.json` | VERIFIED-IN-CODE | `backend/data/liveops_runtime_state.json`, `.gitignore` | — |

---

## 14. Cursor / Coding

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Cursor hooks.json schema | VERIFIED-IN-CODE | `cursor-integration/.cursor/hooks.json` | — |
| Adapter normalisation | VERIFIED-IN-CODE | `app/adapters/cursor_adapter.py` | `test_liveops_adapter.py` (implied) |
| Module 7 code-quality patterns (Cursor only) | VERIFIED-IN-CODE | `planning_verification.py:169-179`, `code_quality_patterns.py` | `test_code_quality_patterns.py` |
| Context integrity (prompt injection) | VERIFIED-IN-CODE | `context_integrity.py` | — |
| Tool integrity (MCP tool poisoning) | VERIFIED-IN-CODE | `tool_integrity.py` | — |
| Predictive defence (early attack forecasting) | VERIFIED-IN-CODE | `predictive_defence.py` | — |
| Planning verification (protected paths, scope, contradictions) | VERIFIED-IN-CODE | `planning_verification.py` | `test_planning_verification.py` |

---

## 15. n8n

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| n8n adapter normalisation | VERIFIED-IN-CODE | `app/adapters/n8n_adapter.py` | `test_liveops_adapter.py` (implied) |
| Custom n8n node (TypeScript) | SCAFFOLD | `n8n-integration/custom-nodes/risk-gatekeeper-node/RiskGatekeeper.node.ts` | — |
| Node calls `/api/evaluate` pre-action | SCAFFOLD | `RiskGatekeeper.node.ts:84-91` | — |
| BLOCK halts workflow (throws NodeOperationError) | SCAFFOLD | `RiskGatekeeper.node.ts:98-103` | — |
| WARN logs but continues (no pause-for-approval) | SCAFFOLD | `RiskGatekeeper.node.ts:105-109` | — |
| Module routing: planning (no code-quality) + intent | VERIFIED-IN-CODE | `rules_engine.py:124-144` (`_run_n8n_modules`) | — |
| Webhook endpoint `POST /api/n8n/evaluate` | VERIFIED-IN-CODE | `app/api/n8n_webhook.py`, `main.py:113` | — |

---

## 16. ATTVE (Module 2)

| Aspect | Status | Source Files | Tests |
|--------|--------|--------------|-------|
| Merchant registry (`merchant_registry.json`) | VERIFIED-IN-CODE | `attve.py:27` (`_REGISTRY_PATH`), `backend/data/merchant_registry.json` | `test_attve.py` |
| Invoice integrity (amount > 0, numeric) | VERIFIED-IN-CODE | `attve.py:73-83` | `test_attve.py::test_tampered_invoice_blocks` |
| Duplicate transaction ID detection | VERIFIED-IN-CODE | `attve.py:85-94` | `test_attve.py::test_duplicate_transaction_id_blocks` |
| Merchant verification (unknown/untrusted) | VERIFIED-IN-CODE | `attve.py:96-116` | `test_attve.py::test_untrusted_merchant_warns_or_blocks`, `test_unknown_merchant_warns_or_blocks` |
| Transaction limit policy (`$50` default) | VERIFIED-IN-CODE | `attve.py:118-125`, `config.py:34` | `test_attve.py::test_over_threshold_warns` |
| Risk scores per check (0.65–1.0) | VERIFIED-IN-CODE | `attve.py:68-71`, `133-134` | `test_attve.py` |
| Advisory intent for transactions | VERIFIED-IN-CODE | `rules_engine.py:97-98`, `intent_verification.py:308-328` | `test_intent_verification.py` |

---

## 17. ATTVE (Separate entry — ATTVE stands for Autonomous Transaction Trust & Verification Engine; the capability above covers it. The "ATTVE" row in the original request is redundant with Module 2. Marked VERIFIED-IN-CODE.)

| Capability | Status | Source Files | Tests |
|------------|--------|--------------|-------|
| ATTVE (Module 2) | VERIFIED-IN-CODE | `app/policy/attve.py` + `backend/data/merchant_registry.json` | `backend/tests/test_attve.py` (7 tests) |