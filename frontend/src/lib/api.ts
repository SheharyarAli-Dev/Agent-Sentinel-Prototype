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

export type Verdict = 'ALLOW' | 'WARN' | 'BLOCK'
export type HumanDecision = 'approved' | 'rejected'
export type EventSource = 'cursor' | 'n8n' | 'transaction'

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
  timestamp: string
  human_decision: HumanDecision | null
  human_timestamp: string | null
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
