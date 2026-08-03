/**
 * src/components/EventCard.tsx
 * ────────────────────────────
 * Card component for displaying an intercepted agent event & decision.
 * Uses Lexend font for all content in accordance with design requirements.
 * Includes smooth hover state highlighting mouse pointer focus.
 */
import React from 'react'
import type { EventRecord, DecisionRecord } from '../lib/api'

interface EventCardProps {
  event: EventRecord
  decision: DecisionRecord
  onReview?: (event: EventRecord, decision: DecisionRecord) => void
}

export const EventCard: React.FC<EventCardProps> = ({ event, decision, onReview }) => {
  const isAllow = decision.verdict === 'ALLOW'
  const isWarn = decision.verdict === 'WARN'
  const isBlock = decision.verdict === 'BLOCK'

  const verdictBadgeClass = isAllow
    ? 'bg-emerald-100/90 text-emerald-800 border border-emerald-300/80 shadow-xs'
    : isWarn
    ? 'bg-amber-100/90 text-amber-900 border border-amber-300/80 shadow-xs'
    : 'bg-rose-100/90 text-rose-900 border border-rose-300/80 shadow-xs'

  const sourceIcon =
    event.source === 'cursor'
      ? '⚡ Cursor IDE'
      : event.source === 'n8n'
      ? '🔄 n8n Workflow'
      : '☕ Coffee Order'

  const formattedTime = new Date(decision.timestamp).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })

  const riskPct = Math.round(decision.risk_score * 100)
  const riskColorClass =
    riskPct >= 75
      ? 'bg-rose-500 text-rose-700'
      : riskPct >= 40
      ? 'bg-amber-500 text-amber-800'
      : 'bg-emerald-500 text-emerald-800'

  return (
    <div
      className={`font-lexend theme-card p-5 transition-all duration-300 hover:bg-neutral-50/90 hover:border-neutral-300 hover:shadow-md hover:-translate-y-0.5 ${
        isWarn ? 'border-amber-300/80 bg-amber-50/30' : isBlock ? 'border-rose-200/90 bg-rose-50/30' : ''
      }`}
    >
      {/* ── Top Bar: Source, Timestamp & Verdict ───────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div className="flex items-center gap-2">
          <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-neutral-100 text-neutral-700 border border-neutral-200/80">
            {sourceIcon}
          </span>
          <span className="text-xs text-neutral-500 font-mono">{event.event_type}</span>
          <span className="text-xs text-neutral-400">#{event.id}</span>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-neutral-400 font-mono">{formattedTime}</span>
          <span
            className={`px-3 py-1 text-xs font-semibold rounded-full uppercase tracking-wider ${verdictBadgeClass}`}
          >
            {decision.verdict}
          </span>
        </div>
      </div>

      {/* ── Original Goal (if present) ──────────────────────────────────────── */}
      {event.original_goal && (
        <div className="mb-3 px-3 py-2 text-xs rounded-xl bg-neutral-100/70 border border-neutral-200/60 text-neutral-700">
          <span className="font-medium text-neutral-500">Original Goal: </span>
          <span className="italic">"{event.original_goal}"</span>
        </div>
      )}

      {/* ── Risk Score Meter ────────────────────────────────────────────────── */}
      <div className="mb-4">
        <div className="flex justify-between items-center text-xs mb-1">
          <span className="text-neutral-500 font-medium">Risk Score</span>
          <span className={`font-mono font-semibold ${riskColorClass.split(' ')[1]}`}>
            {(decision.risk_score * 100).toFixed(1)}%
          </span>
        </div>
        <div className="w-full bg-neutral-200/70 h-1.5 rounded-full overflow-hidden">
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
        <div className="mb-3 space-y-1">
          <p className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">
            Evaluation Reasons:
          </p>
          <ul className="space-y-1 text-xs text-neutral-700 list-disc list-inside pl-1">
            {decision.reasons.map((reason, idx) => (
              <li key={idx} className="leading-relaxed">
                {reason}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* ── Suggested Fix (Mandatory for WARN / BLOCK) ────────────────────── */}
      {decision.suggested_fix && decision.suggested_fix.trim() !== '' && (
        <div className="mt-3 mb-3 p-3 rounded-xl bg-amber-500/10 border border-amber-300/80 text-amber-950 text-xs">
          <div className="flex items-center gap-1.5 font-semibold text-amber-900 mb-1">
            <svg className="w-4 h-4 text-amber-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"
              />
            </svg>
            Suggested Fix / Recommendation:
          </div>
          <p className="leading-relaxed pl-5 font-mono text-[11px] text-amber-900">
            {decision.suggested_fix}
          </p>
        </div>
      )}

      {/* ── Human Decision Status & Action Footer ───────────────────────────── */}
      <div className="mt-4 pt-3 border-t border-neutral-200/70 flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-neutral-500">
          <span>Module: </span>
          <span className="font-mono text-neutral-800">{decision.module}</span>
        </div>

        <div>
          {decision.human_decision === 'approved' ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-emerald-100 text-emerald-800 border border-emerald-300 flex items-center gap-1">
              ✓ Human Approved
            </span>
          ) : decision.human_decision === 'rejected' ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-rose-100 text-rose-800 border border-rose-300 flex items-center gap-1">
              ✕ Human Rejected
            </span>
          ) : isWarn && onReview ? (
            <button
              onClick={() => onReview(event, decision)}
              className="px-3.5 py-1.5 text-xs font-semibold rounded-xl bg-amber-500 hover:bg-amber-400 text-neutral-950 transition-all duration-200 flex items-center gap-1.5 shadow-sm hover:shadow-md hover:scale-[1.02]"
            >
              ⚠️ Review Action
            </button>
          ) : isWarn ? (
            <span className="px-2.5 py-1 text-xs font-medium rounded-lg bg-amber-100 text-amber-900 border border-amber-300">
              Pending Human Review
            </span>
          ) : (
            <span className="text-xs text-neutral-400">Auto-Resolved</span>
          )}
        </div>
      </div>
    </div>
  )
}
