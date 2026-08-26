/**
 * src/components/LiveOpsPanel.tsx
 * ─────────────────────────────────
 * Smallest browser-based LiveOps demonstration panel.
 *
 * The agent proposes one of three hard-coded cloud operations; Agent Sentinel
 * evaluates it through the existing backend pipeline and the panel obeys the
 * verdict:
 *
 *   - ALLOW           -> execute exactly once, refresh cloud state
 *   - WARN            -> open the existing ApprovalModal; execute only if approved
 *   - WARN approved   -> execute exactly once, refresh state
 *   - WARN rejected   -> never execute
 *   - BLOCK           -> never execute; protected resource stays unchanged
 *
 * No arbitrary tools/targets are ever submitted: the three scenarios are the
 * only payloads this component can send.
 */
import React, { useCallback, useEffect, useState } from 'react'
import {
  evaluateEvent,
  executeLiveOps,
  getLiveOpsState,
  getOutcomeVerification,
  resetLiveOps,
  type DecisionRecord,
  type EventRecord,
  type HumanDecision,
  type LiveOpsState,
  type OutcomeVerificationResult,
  type Verdict,
} from '../lib/api'
import { ApprovalModal } from './ApprovalModal'

type ScenarioKey = 'dev' | 'prod' | 'snapshot'

type OutcomeLabel =
  | 'ALLOWED AND EXECUTED'
  | 'HUMAN REVIEW REQUIRED'
  | 'APPROVED AND EXECUTED'
  | 'REJECTED, NOT EXECUTED'
  | 'BLOCKED, RESOURCE UNCHANGED'
  | 'ALREADY PROCESSED'

interface RunResult {
  goal: string
  proposed: string
  verdict: Verdict
  risk: number
  reasons: string[]
  humanDecision: HumanDecision | null
  executionStatus: string | null
  outcome: OutcomeLabel
  observed: string
}

interface ScenarioDef {
  key: ScenarioKey
  label: string
  event_type: string
  target: string
  original_goal: string
  description: string
  resourceKind: string
}

const SCENARIOS: ScenarioDef[] = [
  {
    key: 'dev',
    label: 'Stop development VM',
    event_type: 'stop_vm',
    target: 'dev-unused-01',
    original_goal: 'Clean unused development resources to reduce cost.',
    description: 'Stop unused development VM dev-unused-01 to reduce cost',
    resourceKind: 'Development VM',
  },
  {
    key: 'prod',
    label: 'Stop production VM',
    event_type: 'stop_vm',
    target: 'prod-api-01',
    original_goal: 'Clean unused production resources to reduce cost.',
    description: 'Stop the production API virtual machine prod-api-01 to reduce cost.',
    resourceKind: 'Production VM',
  },
  {
    key: 'snapshot',
    label: 'Delete protected snapshot',
    event_type: 'delete_snapshot',
    target: 'prod-backup-latest',
    original_goal: 'Remove stale production backup snapshots.',
    description: 'Delete the latest production backup snapshot prod-backup-latest.',
    resourceKind: 'Production backup snapshot',
  },
]

function freshSessionId(): string {
  return `liveops-ui-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function friendlyError(err: unknown): string {
  if (err && typeof err === 'object') {
    const e = err as { response?: { data?: { detail?: string } }; message?: string }
    if (typeof e.response?.data?.detail === 'string') {
      return e.response.data.detail
    }
    if (typeof e.message === 'string') {
      return e.message
    }
  }
  return 'Something went wrong while talking to Agent Sentinel.'
}

function isHttpError(err: unknown, status: number): boolean {
  return (
    !!err &&
    typeof err === 'object' &&
    (err as { response?: { status?: number } }).response?.status === status
  )
}

function describeObserved(state: LiveOpsState | null, target: string): string {
  if (!state) return ''
  const vm = state.vms.find((v) => v.id === target)
  if (vm) return `vm ${vm.id} is ${vm.state}`
  const snap = state.snapshots.find((s) => s.id === target)
  if (snap) return `snapshot ${snap.id} is present`
  return `${target} is absent from state`
}

export const LiveOpsPanel: React.FC = () => {
  const [state, setState] = useState<LiveOpsState | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<ScenarioKey | null>(null)
  const [restoring, setRestoring] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<RunResult | null>(null)
  const [reviewTarget, setReviewTarget] = useState<{
    event: EventRecord
    decision: DecisionRecord
  } | null>(null)
  const [verification, setVerification] = useState<OutcomeVerificationResult | null>(null)
  const [verificationLoading, setVerificationLoading] = useState(false)
  const [verificationEventId, setVerificationEventId] = useState<number | null>(null)
  const [verificationError, setVerificationError] = useState<string | null>(null)

  const refreshState = useCallback(async (): Promise<LiveOpsState | null> => {
    try {
      const s = await getLiveOpsState()
      setState(s)
      return s
    } catch (err) {
      setError(friendlyError(err))
      return null
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const s = await getLiveOpsState()
        if (!cancelled) setState(s)
      } catch (err) {
        if (!cancelled) setError(friendlyError(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  // ── Fetch outcome verification when an event ID is set ────────────────────
  useEffect(() => {
    if (verificationEventId === null) return
    let cancelled = false
    ;(async () => {
      setVerificationLoading(true)
      setVerificationError(null)
      try {
        const v = await getOutcomeVerification(verificationEventId)
        if (!cancelled) setVerification(v)
      } catch {
        if (!cancelled) {
          setVerification(null)
          setVerificationError('Outcome evidence unavailable')
        }
      } finally {
        if (!cancelled) setVerificationLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [verificationEventId])

  // ── Execution helper (ALLOW / approved WARN) ─────────────────────────────
  const runExecution = useCallback(
    async (
      event: EventRecord,
      decision: DecisionRecord,
      scenario: ScenarioDef,
      outcome: OutcomeLabel,
    ): Promise<void> => {
      try {
        const exec = await executeLiveOps(event.id)
        const s = await refreshState()
        setResult({
          goal: scenario.original_goal,
          proposed: `${scenario.event_type}(${scenario.target})`,
          verdict: decision.verdict,
          risk: decision.risk_score,
          reasons: decision.reasons ?? [],
          humanDecision: decision.human_decision ?? null,
          executionStatus: exec.status,
          outcome,
          observed: describeObserved(s, scenario.target),
        })
        setVerificationEventId(event.id)
      } catch (err) {
        if (isHttpError(err, 409)) {
          // Exactly-once guard: already processed. Never retry.
          const s = await refreshState()
          setResult({
            goal: scenario.original_goal,
            proposed: `${scenario.event_type}(${scenario.target})`,
            verdict: decision.verdict,
            risk: decision.risk_score,
            reasons: decision.reasons ?? [],
            humanDecision: decision.human_decision ?? null,
            executionStatus: 'already processed (409)',
            outcome: 'ALREADY PROCESSED',
            observed: describeObserved(s, scenario.target),
          })
          setVerificationEventId(event.id)
        } else {
          setError(friendlyError(err))
        }
      }
    },
    [refreshState],
  )

  // ── Scenario runner ───────────────────────────────────────────────────────
  const runScenario = useCallback(
    async (scenario: ScenarioDef) => {
      setBusy(scenario.key)
      setError(null)
      setResult(null)
      setVerification(null)
      setVerificationEventId(null)
      setVerificationError(null)
      try {
        const resp = await evaluateEvent({
          source: 'liveops',
          event_type: scenario.event_type,
          original_goal: scenario.original_goal,
          payload: {
            tool: scenario.event_type,
            capability: scenario.event_type,
            target: scenario.target,
            resource: scenario.target,
            description: scenario.description,
            session_id: freshSessionId(),
          },
        })
        const { event, decision } = resp

        if (decision.verdict === 'ALLOW') {
          await runExecution(event, decision, scenario, 'ALLOWED AND EXECUTED')
        } else if (decision.verdict === 'WARN') {
          setResult({
            goal: scenario.original_goal,
            proposed: `${scenario.event_type}(${scenario.target})`,
            verdict: 'WARN',
            risk: decision.risk_score,
            reasons: decision.reasons ?? [],
            humanDecision: null,
            executionStatus: null,
            outcome: 'HUMAN REVIEW REQUIRED',
            observed: describeObserved(state, scenario.target),
          })
          setReviewTarget({ event, decision })
        } else if (decision.verdict === 'BLOCK') {
          const s = await refreshState()
          setResult({
            goal: scenario.original_goal,
            proposed: `${scenario.event_type}(${scenario.target})`,
            verdict: 'BLOCK',
            risk: decision.risk_score,
            reasons: decision.reasons ?? [],
            humanDecision: null,
            executionStatus: null,
            outcome: 'BLOCKED, RESOURCE UNCHANGED',
            observed: describeObserved(s, scenario.target),
          })
        }
      } catch (err) {
        setError(friendlyError(err))
      } finally {
        setBusy(null)
      }
    },
    [runExecution, refreshState, state],
  )

  // ── ApprovalModal result (approved -> execute once; rejected -> never) ────
  const handleDecisionSubmitted = useCallback(
    async (eventId: number, newDecision: DecisionRecord) => {
      const scenario = SCENARIOS.find((s) => s.target === reviewTarget?.event?.payload?.target)
      setReviewTarget(null)
      if (!scenario) return

      if (newDecision.human_decision === 'approved') {
        const syntheticEvent: EventRecord = reviewTarget!.event
        await runExecution(
          syntheticEvent,
          newDecision,
          scenario,
          'APPROVED AND EXECUTED',
        )
      } else {
        // Rejected — never execute. No execution-ledger row is expected (the
        // backend only records one when /execute is called), so skip the lookup.
        const s = await refreshState()
        setResult({
          goal: scenario.original_goal,
          proposed: `${scenario.event_type}(${scenario.target})`,
          verdict: 'WARN',
          risk: newDecision.risk_score,
          reasons: newDecision.reasons ?? [],
          humanDecision: 'rejected',
          executionStatus: null,
          outcome: 'REJECTED, NOT EXECUTED',
          observed: describeObserved(s, scenario.target),
        })
      }
    },
    [refreshState, reviewTarget, runExecution],
  )

  // ── Restore Demo State ────────────────────────────────────────────────────
  const handleRestore = useCallback(async () => {
    setRestoring(true)
    setError(null)
    setResult(null)
    setVerification(null)
    setVerificationEventId(null)
    setVerificationError(null)
    try {
      const s = await resetLiveOps()
      setState(s)
    } catch (err) {
      setError(friendlyError(err))
    } finally {
      setRestoring(false)
    }
  }, [])

  const vmState = (id: string): string =>
    state?.vms.find((v) => v.id === id)?.state ?? 'unknown'

  const snapshotPresent = state?.snapshots.some((s) => s.id === 'prod-backup-latest') ?? false

  const outcomeStyles: Record<OutcomeLabel, string> = {
    'ALLOWED AND EXECUTED': 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    'APPROVED AND EXECUTED': 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    'HUMAN REVIEW REQUIRED': 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    'REJECTED, NOT EXECUTED': 'bg-rose-500/15 text-rose-400 border-rose-500/30',
    'BLOCKED, RESOURCE UNCHANGED': 'bg-rose-500/15 text-rose-400 border-rose-500/30',
    'ALREADY PROCESSED': 'bg-sky-500/15 text-sky-400 border-sky-500/30',
  }

  return (
    <section className="font-lexend bg-slate-900/85 border border-slate-700/70 shadow-sm p-6 rounded-2xl">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-start justify-between gap-3 mb-2">
        <div>
          <h2 className="text-xl font-extrabold tracking-tight text-slate-100 uppercase">
            Agent Sentinel LiveOps
          </h2>
          <p className="text-xs text-slate-400 mt-1 max-w-xl">
            An agent proposes a cloud operation. Agent Sentinel decides whether
            it may run.
          </p>
        </div>
        <button
          onClick={handleRestore}
          disabled={restoring || busy !== null}
          className="px-3.5 py-2 text-xs font-semibold rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {restoring ? 'Restoring…' : '↺ Restore Demo State'}
        </button>
      </div>
      <p className="text-[11px] text-slate-500 mb-5">
        Restore resets the demo cloud to its starting state. Audit and decision
        history is retained.
      </p>

      {/* ── Simulated cloud state ──────────────────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
        <div className="rounded-2xl border border-cyan-500/20 bg-slate-800/60 p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="font-mono text-sm font-bold text-slate-100">dev-unused-01</span>
          </div>
          <div className="text-[11px] text-slate-400">Development VM</div>
          <div className="mt-2">
            {loading ? (
              <span className="text-xs text-slate-500">loading…</span>
            ) : (
              <span
                className={`px-2.5 py-1 text-xs font-semibold rounded-full border ${
                  vmState('dev-unused-01') === 'running'
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                    : 'bg-slate-700/50 text-slate-400 border-slate-600/40'
                }`}
              >
                {vmState('dev-unused-01') === 'running' ? 'Running' : 'Stopped'}
              </span>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-amber-500/20 bg-slate-800/60 p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="font-mono text-sm font-bold text-slate-100">prod-api-01</span>
            <span className="px-2 py-0.5 text-[10px] font-bold uppercase rounded-full bg-violet-500/15 text-violet-400 border border-violet-500/30">
              Protected
            </span>
          </div>
          <div className="text-[11px] text-slate-400">Production VM</div>
          <div className="mt-2">
            {loading ? (
              <span className="text-xs text-slate-500">loading…</span>
            ) : (
              <span
                className={`px-2.5 py-1 text-xs font-semibold rounded-full border ${
                  vmState('prod-api-01') === 'running'
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                    : 'bg-slate-700/50 text-slate-400 border-slate-600/40'
                }`}
              >
                {vmState('prod-api-01') === 'running' ? 'Running' : 'Stopped'}
              </span>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-violet-500/20 bg-slate-800/60 p-4">
          <div className="flex items-center justify-between mb-2">
            <span className="font-mono text-sm font-bold text-slate-100">prod-backup-latest</span>
            <span className="px-2 py-0.5 text-[10px] font-bold uppercase rounded-full bg-violet-500/15 text-violet-400 border border-violet-500/30">
              Protected
            </span>
          </div>
          <div className="text-[11px] text-slate-400">Production backup snapshot</div>
          <div className="mt-2">
            {loading ? (
              <span className="text-xs text-slate-500">loading…</span>
            ) : (
              <span
                className={`px-2.5 py-1 text-xs font-semibold rounded-full border ${
                  snapshotPresent
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                    : 'bg-slate-700/50 text-slate-400 border-slate-600/40'
                }`}
              >
                {snapshotPresent ? 'Present' : 'Absent'}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* ── Scenario buttons ───────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
        {SCENARIOS.map((s) => (
          <button
            key={s.key}
            onClick={() => runScenario(s)}
            disabled={busy !== null}
            className={`px-4 py-3 text-xs font-semibold rounded-2xl transition-colors shadow-sm disabled:opacity-50 disabled:cursor-not-allowed text-left ${
              s.key === 'dev'
                ? 'bg-cyan-500/15 hover:bg-cyan-500/25 text-cyan-300 border border-cyan-500/30'
                : s.key === 'prod'
                ? 'bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30'
                : 'bg-rose-500/15 hover:bg-rose-500/25 text-rose-300 border border-rose-500/30'
            }`}
          >
            {busy === s.key ? 'Evaluating…' : `▶ ${s.label}`}
          </button>
        ))}
      </div>

      {/* ── Error state ────────────────────────────────────────────────────── */}
      {error && (
        <div className="mb-4 p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs">
          <span className="font-semibold">Something went wrong:</span>{' '}
          {error}
        </div>
      )}

      {/* ── Latest result ──────────────────────────────────────────────────── */}
      {result && (
        <div className="rounded-2xl border border-slate-700/70 bg-slate-800/60 p-4">
          <span
            className={`inline-block px-3 py-1 text-xs font-extrabold uppercase tracking-wider rounded-full border ${outcomeStyles[result.outcome]}`}
          >
            {result.outcome}
          </span>
          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs">
            <div>
              <span className="text-slate-500">Goal: </span>
              <span className="text-slate-200">{result.goal}</span>
            </div>
            <div>
              <span className="text-slate-500">Proposed action: </span>
              <span className="font-mono text-slate-200">{result.proposed}</span>
            </div>
            <div>
              <span className="text-slate-500">Verdict: </span>
              <span
                className={`font-semibold ${
                  result.verdict === 'ALLOW'
                    ? 'text-emerald-400'
                    : result.verdict === 'WARN'
                    ? 'text-amber-400'
                    : 'text-rose-400'
                }`}
              >
                {result.verdict}
              </span>
            </div>
            <div>
              <span className="text-slate-500">Risk score: </span>
              <span className="font-mono font-semibold text-slate-200">
                {(result.risk * 100).toFixed(1)}%
              </span>
            </div>
            {result.humanDecision && (
              <div>
                <span className="text-slate-500">Human decision: </span>
                <span className="font-semibold text-slate-200">
                  {result.humanDecision === 'approved' ? 'Approved' : 'Rejected'}
                </span>
              </div>
            )}
            {result.executionStatus && (
              <div>
                <span className="text-slate-500">Execution status: </span>
                <span className="font-mono font-semibold text-slate-200">
                  {result.executionStatus}
                </span>
              </div>
            )}
            {result.observed && (
              <div className="sm:col-span-2">
                <span className="text-slate-500">Observed resource state: </span>
                <span className="font-semibold text-slate-200">{result.observed}</span>
              </div>
            )}
          </div>

          {/* ── Authorized Outcome Verification ─────────────────────────────── */}
          {(verificationLoading || verification || verificationError) && (
            <div className="mt-3 rounded-xl border border-slate-700/60 bg-slate-800/50 p-3.5">
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-2">
                Authorized Outcome Verification
              </div>
              {verificationLoading && !verification && (
                <div className="text-xs text-slate-400">Verifying outcome…</div>
              )}
              {verificationError && !verification && (
                <div className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg px-2.5 py-1.5">
                  {verificationError}
                </div>
              )}
              {verification && (
                <div className="space-y-2.5 text-xs">
                  {/* Operation */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
                    <div>
                      <span className="text-slate-500">Operation ID: </span>
                      <span className="font-mono text-cyan-400">{verification.operation_id}</span>
                    </div>
                    <div>
                      <span className="text-slate-500">Fingerprint: </span>
                      <span className="font-mono text-cyan-400">
                        {verification.action_fingerprint.slice(0, 12)}…
                      </span>
                    </div>
                  </div>

                  {/* Expected outcome */}
                  {verification.expected_outcome && (
                    <div className="rounded-lg bg-slate-900/60 border border-slate-700/50 p-2.5">
                      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                        Expected
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
                        <div>
                          <span className="text-slate-500">Target: </span>
                          <span className="font-mono font-semibold text-slate-200">
                            {verification.expected_outcome.target_resource}
                          </span>
                        </div>
                        {verification.expected_outcome.allowed_state_transition && (
                          <div>
                            <span className="text-slate-500">Transition: </span>
                            <span className="font-mono text-slate-300">
                              {verification.expected_outcome.allowed_state_transition}
                            </span>
                          </div>
                        )}
                        {verification.expected_outcome.expected_final_state && (
                          <div className="sm:col-span-2">
                            <span className="text-slate-500">Final state: </span>
                            <span className="font-mono text-slate-300">
                              {JSON.stringify(verification.expected_outcome.expected_final_state)}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Observed state */}
                  {verification.observed_state && (
                    <div className="rounded-lg bg-slate-900/60 border border-slate-700/50 p-2.5">
                      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                        Observed
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
                        <div>
                          <span className="text-slate-500">Target: </span>
                          <span className="font-mono font-semibold text-slate-200">
                            {verification.observed_state.target}
                          </span>
                        </div>
                        <div>
                          <span className="text-slate-500">State: </span>
                          <span className="font-mono text-slate-300">
                            {verification.observed_state.state ?? 'Not available'}
                          </span>
                        </div>
                        <div>
                          <span className="text-slate-500">Protected: </span>
                          <span className="font-mono text-slate-300">
                            {verification.observed_state.protected !== null
                              ? String(verification.observed_state.protected)
                              : 'Not available'}
                          </span>
                        </div>
                        {verification.observed_state.environment !== null && (
                          <div>
                            <span className="text-slate-500">Environment: </span>
                            <span className="font-mono text-slate-300">
                              {verification.observed_state.environment}
                            </span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Verification status */}
                  <div>
                    <span className="text-slate-500">Verification: </span>
                    <span
                      className={`font-extrabold uppercase tracking-wider ${
                        verification.status === 'VERIFIED'
                          ? 'text-emerald-400'
                          : verification.status === 'PARTIAL'
                          ? 'text-amber-400'
                          : verification.status === 'MISMATCH'
                          ? 'text-rose-400'
                          : verification.status === 'EXECUTION_FAILED'
                          ? 'text-rose-400'
                          : 'text-slate-400'
                      }`}
                    >
                      {verification.status}
                    </span>
                  </div>

                  {/* Evidence lists */}
                  {(verification.invariant_violations.length > 0 ||
                    verification.unexpected_mutations.length > 0 ||
                    verification.permitted_mutations_observed.length > 0) && (
                    <div className="rounded-lg bg-slate-900/60 border border-slate-700/50 p-2.5">
                      <div className="text-[10px] font-bold uppercase tracking-wider text-slate-500 mb-1.5">
                        Evidence
                      </div>
                      <div className="space-y-1.5">
                        {verification.invariant_violations.length > 0 && (
                          <div>
                            <span className="text-[10px] font-semibold text-rose-400 uppercase">
                              Invariant violations:
                            </span>
                            <ul className="mt-0.5 space-y-0.5 pl-3 list-disc list-inside text-rose-300">
                              {verification.invariant_violations.map((v, i) => (
                                <li key={i}>{v}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {verification.unexpected_mutations.length > 0 && (
                          <div>
                            <span className="text-[10px] font-semibold text-rose-400 uppercase">
                              Unexpected mutations:
                            </span>
                            <ul className="mt-0.5 space-y-0.5 pl-3 list-disc list-inside text-rose-300">
                              {verification.unexpected_mutations.map((m, i) => (
                                <li key={i}>{m}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {verification.permitted_mutations_observed.length > 0 && (
                          <div>
                            <span className="text-[10px] font-semibold text-emerald-400 uppercase">
                              Permitted mutations observed:
                            </span>
                            <ul className="mt-0.5 space-y-0.5 pl-3 list-disc list-inside text-emerald-300">
                              {verification.permitted_mutations_observed.map((m, i) => (
                                <li key={i}>{m}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
          {result.reasons.length > 0 && (
            <details className="mt-3">
              <summary className="text-xs text-slate-500 hover:text-slate-300 cursor-pointer select-none font-mono">
                ▶ View evaluation reasons
              </summary>
              <ul className="mt-2 space-y-1 text-xs text-slate-300 list-disc list-inside pl-1">
                {result.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      {/* ── Human review modal (reuses the existing ApprovalModal) ─────────── */}
      {reviewTarget && (
        <ApprovalModal
          event={reviewTarget.event}
          decision={reviewTarget.decision}
          onClose={() => setReviewTarget(null)}
          onDecisionSubmitted={handleDecisionSubmitted}
        />
      )}
    </section>
  )
}

export default LiveOpsPanel
