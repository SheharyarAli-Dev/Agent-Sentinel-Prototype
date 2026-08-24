/**
 * src/components/EventCard.tsx
 * ────────────────────────────
 * Card component for displaying an intercepted agent event & decision.
 * Uses Lexend font for all content in accordance with design requirements.
 * Includes smooth hover state highlighting mouse pointer focus.
 */
import React, { useState } from 'react'
import type { EventRecord, DecisionRecord } from '../lib/api'

interface EventCardProps {
  event: EventRecord
  decision: DecisionRecord
  onReview?: (event: EventRecord, decision: DecisionRecord) => void
  onDecisionUpdate?: (eventId: number, newDecision: DecisionRecord) => void
}

export const EventCard: React.FC<EventCardProps> = ({ event, decision, onReview, onDecisionUpdate }) => {
  const isAllow = decision.verdict === 'ALLOW'
  const isWarn = decision.verdict === 'WARN'
  const isBlock = decision.verdict === 'BLOCK'
  const isExpired = decision.verdict === 'EXPIRED'

  const verdictBadgeClass = isAllow
    ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 shadow-xs'
    : isWarn
    ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30 shadow-xs'
    : isExpired
    ? 'bg-slate-500/15 text-slate-400 border border-slate-500/30 shadow-xs'
    : 'bg-rose-500/15 text-rose-400 border border-rose-500/30 shadow-xs'

  const sourceIcon =
    event.source === 'cursor'
      ? 'Coding-Agent Action'
      : event.source === 'n8n'
      ? 'Workflow Action'
      : event.source === 'liveops'
      ? 'LiveOps Operation'
      : 'ATTVE Transaction'

  const formattedTime = new Date(decision.timestamp).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  const riskPct = Math.round(decision.risk_score * 100)
  const riskColorClass =
    riskPct >= 75
      ? 'bg-rose-500 text-rose-400'
      : riskPct >= 40
      ? 'bg-amber-500 text-amber-400'
      : 'bg-emerald-500 text-emerald-400'

  return (
    <div
      className={`font-lexend bg-slate-900/85 border border-slate-700/70 shadow-sm p-5 rounded-2xl transition-all duration-300 hover:bg-slate-900/95 hover:border-slate-600/70 hover:shadow-md hover:-translate-y-0.5 ${
        isWarn ? 'border-amber-500/30' : isBlock || isExpired ? 'border-rose-500/30' : ''
      }`}
    >
      {/* ── Top Bar: Source, Timestamp & Verdict ───────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-slate-800/80 text-slate-300 border border-slate-700/60">
            {sourceIcon}
          </span>
          <span className="text-xs text-slate-500 font-mono">{event.event_type}</span>
          <span className="text-xs text-slate-600">#{event.id}</span>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-slate-500 font-mono">{formattedTime}</span>
          {typeof decision.latency_ms === 'number' && decision.latency_ms > 0 && (
            <span
              className={`text-[10px] font-mono px-1.5 py-0.5 rounded-md border ${
                decision.latency_ms < 40
                  ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30'
                  : 'text-amber-400 bg-amber-500/10 border-amber-500/30'
              }`}
              title="Evaluation latency (spec target: < 40ms)"
            >
              ⚡ {decision.latency_ms.toFixed(1)}ms
            </span>
          )}
          <span
            className={`px-3 py-1 text-xs font-semibold rounded-full uppercase tracking-wider ${verdictBadgeClass}`}
          >
            {decision.verdict}
          </span>
        </div>
      </div>

      {/* ── Original Goal (if present) ──────────────────────────────────────── */}
      {event.original_goal && (
        <div className="mb-3 px-3 py-2 text-xs rounded-xl bg-slate-800/60 border border-slate-700/50 text-slate-300">
          <span className="font-medium text-slate-400">Original Goal: </span>
          <span className="italic">"{event.original_goal}"</span>
        </div>
      )}

      {/* ── Risk Score Meter ────────────────────────────────────────────────── */}
      <div className="mb-4">
        <div className="flex justify-between items-center text-xs mb-1">
          <span className="text-slate-400 font-medium">Risk Score</span>
          <span className={`font-mono font-semibold ${riskColorClass.split(' ')[1]}`}>
            {(decision.risk_score * 100).toFixed(1)}%
          </span>
        </div>
        <div className="w-full bg-slate-800 h-1.5 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              riskPct >= 75
                ? 'bg-rose-500'
                : riskPct >= 40
                ? 'bg-amber-500'
                : 'bg-emerald-500'
            }`}
            style={{ width: `${Math.max(riskPct, 4)}%` }}
          />
        </div>
      </div>

      {/* ── Reasons List ───────────────────────────────────────────────────── */}
      {decision.reasons && decision.reasons.length > 0 && (
        <div className="mb-3 space-y-1.5">
          <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
            Evaluation Reasons:
          </p>
          <ul className="space-y-1 text-xs text-slate-300 list-disc list-inside pl-1">
            {decision.reasons.map((reason, idx) => (
              <li key={idx} className="leading-relaxed">
                {reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Explainable Reasoning (Module 11) ──────────────────────────────── */}
      {decision.explanation && decision.explanation.trim() !== '' && (
        <div className="mb-3 p-3 rounded-xl bg-cyan-500/5 border border-cyan-500/20 text-cyan-200 text-xs">
          <div className="flex items-center gap-1.5 font-semibold text-cyan-300 mb-1">
            Why this decision (Explainable Reasoning):
          </div>
          <p className="leading-relaxed pl-1 text-[11px] text-cyan-200/80">
            {decision.explanation}
          </p>
        </div>
      )}

      {/* ── Suggested Fix (Mandatory for WARN / BLOCK) ────────────────────── */}
      {decision.suggested_fix && decision.suggested_fix.trim() !== '' && (
        <div className="mt-3 mb-3 p-3 rounded-xl bg-amber-500/5 border border-amber-500/20 text-amber-200 text-xs">
          <div className="flex items-center gap-1.5 font-semibold text-amber-300 mb-1">
            <svg className="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            Suggested Fix / Recommendation:
          </div>
          <p className="leading-relaxed pl-5 font-mono text-[11px] text-amber-200/80">
            {decision.suggested_fix}
          </p>
        </div>
      )}

      {/* ── Human Decision Status & Action Footer ───────────────────────────── */}
      <div className="mt-4 pt-3 border-t border-slate-700/50 flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-slate-400">
          <span>Module: </span>
          <span className="font-mono text-slate-200">{decision.module}</span>
        </div>

        <div>
          {decision.unblocked_by_human ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-violet-500/15 text-violet-400 border border-violet-500/30 flex items-center gap-1">
              Unblocked by Human
            </span>
          ) : decision.human_decision === 'approved' ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
              Human Approved
            </span>
          ) : decision.human_decision === 'rejected' ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-rose-500/15 text-rose-400 border border-rose-500/30 flex items-center gap-1">
              Human Rejected
            </span>
          ) : isWarn && onReview ? (
            <button
              onClick={() => onReview(event, decision)}
              className="px-3.5 py-1.5 text-xs font-semibold rounded-xl bg-amber-500/20 hover:bg-amber-500/30 text-amber-300 border border-amber-500/30 transition-all duration-200 flex items-center gap-1.5 shadow-sm hover:shadow-md hover:scale-[1.02]"
            >
              Review Action
            </button>
          ) : isWarn ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-amber-500/15 text-amber-400 border border-amber-500/30">
              Pending Human Review
            </span>
          ) : isExpired ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-slate-500/15 text-slate-400 border border-slate-500/30">
              Review expired — re-submit for fresh evaluation
            </span>
          ) : isBlock ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-rose-500/15 text-rose-400 border border-rose-500/30">
              Fresh evaluation required after policy, permission, or request correction.
            </span>
          ) : (
            <span className="text-xs text-slate-500">Auto-Resolved</span>
          )}
        </div>
      </div>
    </div>
  )
}
