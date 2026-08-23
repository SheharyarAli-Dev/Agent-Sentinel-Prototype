/**
 * lib/api.ts
 * ──────────
 * Axios-based API client for the Risk Gatekeeper backend.
 * All REST calls go through these functions.
 */
import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 10_000,
  headers: { 'Content-Type': 'application/json' },
})

// ── Types ──────────────────────────────────────────────────────────────────────

export type Verdict = 'ALLOW' | 'WARN' | 'BLOCK' | 'EXPIRED'
export type HumanDecision = 'approved' | 'rejected'
export type EventSource = 'cursor' | 'n8n' | 'transaction' | 'liveops'

export interface EventRecord {
  id: number
  source: EventSource
  event_type: string
  payload: Record<string, unknown>
  original_goal: string | null
  timestamp: string
}

export interface DecisionRecord {
  id: number
  event_id: number
  verdict: Verdict
  reasons: string[]
  suggested_fix: string
  module: string
  risk_score: number
  explanation: string
  latency_ms: number
  timestamp: string
  human_decision: HumanDecision | null
  human_timestamp: string | null
  unblocked_by_human: boolean
  unblock_timestamp: string | null
}

export interface EvaluateResponse {
  event: EventRecord
  decision: DecisionRecord
}

export interface EventCreate {
  source: EventSource
  event_type: string
  payload: Record<string, unknown>
  original_goal?: string
}

// ── API calls ──────────────────────────────────────────────────────────────────

/** Submit an event for risk evaluation. */
export async function evaluateEvent(event: EventCreate): Promise<EvaluateResponse> {
  const { data } = await client.post<EvaluateResponse>('/evaluate', event)
  return data
}

export interface RedTeamReport {
  total_attacks: number
  defended: number
  coverage_pct: number
  gaps: Array<{ attack: string; actual: string; expected_min: string }>
  results: Array<{ attack: string; category: string; expected_min: string; actual: string; defended: boolean }>
}

/** Run the automated adversarial test suite (Red Team Simulator). */
export async function runRedTeam(): Promise<RedTeamReport> {
  const { data } = await client.get<RedTeamReport>('/red-team')
  return data
}

/** Get the current decision for an event. */
export async function getDecision(eventId: number): Promise<DecisionRecord> {
  const { data } = await client.get<DecisionRecord>(`/decide/${eventId}`)
  return data
}

/** Submit a human approve/reject decision for a WARN event. */
export async function submitDecision(
  eventId: number,
  decision: HumanDecision,
  notes?: string,
): Promise<DecisionRecord> {
  const { data } = await client.post<DecisionRecord>(`/decide/${eventId}`, {
    decision,
    notes,
  })
  return data
}

/** Unblock a BLOCK decision (human operator override). */
export async function unblockAction(eventId: number): Promise<DecisionRecord> {
  const { data } = await client.post<DecisionRecord>(`/unblock/${eventId}`)
  return data
}

// ── LiveOps types ──────────────────────────────────────────────────────────────

export interface LiveOpsVm {
  id: string
  environment: string
  state: string
  protected: boolean
}

export interface LiveOpsSnapshot {
  id: string
  source_vm: string
  environment: string
  protected: boolean
}

export interface LiveOpsState {
  vms: LiveOpsVm[]
  snapshots: LiveOpsSnapshot[]
}

export type LiveOpsExecutionStatus =
  | 'pending'
  | 'executed'
  | 'rejected'
  | 'blocked'
  | 'failed'

export interface LiveOpsExecutionResult {
  tool?: string
  target?: string | null
  vms?: Array<{ id: string; state: string }>
  snapshots?: Array<{ id: string; source_vm?: string }>
}

export interface LiveOpsExecutionRecord {
  id: number
  event_id: number
  tool: string
  target: string | null
  status: LiveOpsExecutionStatus
  result: LiveOpsExecutionResult | null
  executed_at: string | null
  created_at: string
}

// ── LiveOps API calls ──────────────────────────────────────────────────────────

/** Get the current simulated cloud state (never exposes filesystem paths). */
export async function getLiveOpsState(): Promise<LiveOpsState> {
  const { data } = await client.get<LiveOpsState>('/liveops/state')
  return data
}

/** Restore the canonical simulated-cloud seed state (dev/demo only). */
export async function resetLiveOps(): Promise<LiveOpsState> {
  const { data } = await client.post<LiveOpsState>('/liveops/reset')
  return data
}

/** Execute an ALLOW or human-approved WARN LiveOps event exactly once. */
export async function executeLiveOps(eventId: number): Promise<LiveOpsExecutionRecord> {
  const { data } = await client.post<LiveOpsExecutionRecord>(
    `/liveops/execute/${eventId}`,
  )
  return data
}

/** Get the execution-ledger record for a LiveOps event. */
export async function getLiveOpsExecution(
  eventId: number,
): Promise<LiveOpsExecutionRecord> {
  const { data } = await client.get<LiveOpsExecutionRecord>(
    `/liveops/execution/${eventId}`,
  )
  return data
}

// ── Outcome Verification types ──────────────────────────────────────────────

export type VerificationStatus =
  | 'VERIFIED'
  | 'PARTIAL'
  | 'MISMATCH'
  | 'EXECUTION_FAILED'
  | 'OUTCOME_UNKNOWN'

export interface ExpectedOutcome {
  target_resource: string
  allowed_state_transition: string | null
  permitted_mutations: string[]
  protected_invariants: string[]
  expected_final_state: Record<string, unknown> | null
}

export interface OutcomeVerificationResult {
  status: VerificationStatus
  operation_id: string
  action_fingerprint: string
  event_id: number
  expected_outcome: ExpectedOutcome | null
  observed_state: {
    target: string
    state: string | null
    protected: boolean | null
    environment: string | null
  } | null
  invariant_violations: string[]
  permitted_mutations_observed: string[]
  unexpected_mutations: string[]
  verified_at: string
  execution_record_id: number | null
  human_review_id: number | null
}

/** Get the authorized outcome verification result for a LiveOps event. */
export async function getOutcomeVerification(
  eventId: number,
): Promise<OutcomeVerificationResult> {
  const { data } = await client.get<OutcomeVerificationResult>(
    `/liveops/outcome/${eventId}`,
  )
  return data
}
