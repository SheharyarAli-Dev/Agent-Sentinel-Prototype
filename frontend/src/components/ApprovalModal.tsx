/**
 * src/components/ApprovalModal.tsx
 * ────────────────────────────────
 * Modal component for human review of WARN events.
 * Contains fixed header/footer and scrollable body so buttons are always visible.
 */
import React, { useState } from 'react'
import { submitDecision, type EventRecord, type DecisionRecord } from '../lib/api'

interface ApprovalModalProps {
  event: EventRecord
  decision: DecisionRecord
  onClose: () => void
  onDecisionSubmitted: (eventId: number, newDecision: DecisionRecord) => void
}

export const ApprovalModal: React.FC<ApprovalModalProps> = ({
  event,
  decision,
  onClose,
  onDecisionSubmitted,
}) => {
  const [notes, setNotes] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAction = async (humanChoice: 'approved' | 'rejected') => {
    setIsSubmitting(true)
    setError(null)
    try {
      const updated = await submitDecision(event.id, humanChoice, notes)
      onDecisionSubmitted(event.id, updated)
      onClose()
    } catch (err: any) {
      const msg = err.response?.data?.detail || err.message || 'Failed to record decision.'
      setError(msg)
    } finally {
      setIsSubmitting(false)
    }
  }

  const riskPct = Math.round(decision.risk_score * 100)

  return (
    <div className="modal-backdrop animate-fade-in font-lexend z-50 p-4" onClick={onClose}>
      <div
        className="w-full max-w-2xl max-h-[88vh] bg-slate-900 border border-slate-700/70 shadow-2xl rounded-3xl p-6 relative flex flex-col overflow-hidden animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Accent warning top strip */}
        <div className="absolute top-0 left-0 right-0 h-1.5 bg-gradient-to-r from-amber-400 via-amber-500 to-orange-400" />

        {/* ── Fixed Header ─────────────────────────────────────────────────── */}
        <div className="flex items-start justify-between pb-3 border-b border-slate-700/50 flex-shrink-0">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="px-2.5 py-0.5 text-xs font-semibold uppercase rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/30">
                Human Review Required
              </span>
              <span className="text-xs text-slate-500 font-mono">Event #{event.id}</span>
            </div>
            <h2 className="text-xl font-bold text-slate-100">
              Action Held Pending Approval
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-300 text-xl font-bold p-1.5 rounded-lg hover:bg-slate-800 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* ── Scrollable Body Content ───────────────────────────────────────── */}
        <div className="overflow-y-auto my-3 pr-1.5 space-y-4 flex-1">
          {/* Overview Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div className="p-3 rounded-xl bg-slate-800/60 border border-slate-700/50">
              <div className="text-[11px] font-semibold text-slate-500 uppercase">Source</div>
              <div className="text-sm font-semibold text-slate-200 capitalize mt-0.5">
                {event.source}
              </div>
            </div>
            <div className="p-3 rounded-xl bg-slate-800/60 border border-slate-700/50">
              <div className="text-[11px] font-semibold text-slate-500 uppercase">Event Type</div>
              <div className="text-sm font-mono text-amber-400 mt-0.5 truncate">
                {event.event_type}
              </div>
            </div>
            <div className="p-3 rounded-xl bg-slate-800/60 border border-slate-700/50 col-span-2 sm:col-span-1">
              <div className="text-[11px] font-semibold text-slate-500 uppercase">Risk Score</div>
              <div className="text-sm font-mono font-bold text-amber-400 mt-0.5">
                {riskPct}%
              </div>
            </div>
          </div>

          {/* Original Goal */}
          {event.original_goal && (
            <div className="p-3 rounded-xl bg-slate-800/40 border border-slate-700/40">
              <div className="text-xs font-semibold text-slate-400 uppercase mb-1">
                Original User Goal:
              </div>
              <p className="text-xs italic text-slate-300">"{event.original_goal}"</p>
            </div>
          )}

          {/* Explainable Reasoning (Module 11) */}
          {decision.explanation && decision.explanation.trim() !== '' && (
            <div className="p-3 rounded-xl bg-cyan-500/5 border border-cyan-500/20 text-cyan-200">
              <div className="text-xs font-semibold text-cyan-300 mb-1 flex items-center gap-1.5">
                Why this decision:
              </div>
              <p className="text-xs text-cyan-200/80 leading-relaxed">{decision.explanation}</p>
            </div>
          )}

          {/* Policy Evaluation Reasons */}
          <div>
            <h4 className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
              Risk Assessment Reasons:
            </h4>
            <ul className="space-y-1 bg-slate-800/50 p-3 rounded-xl border border-slate-700/50 text-xs text-slate-300 list-disc list-inside">
              {decision.reasons.map((r, i) => (
                <li key={i} className="leading-relaxed">{r}</li>
              ))}
            </ul>
          </div>

          {/* Suggested Fix */}
          {decision.suggested_fix && decision.suggested_fix.trim() !== '' && (
            <div className="p-3.5 rounded-xl bg-amber-500/5 border border-amber-500/20 text-amber-200">
              <div className="text-xs font-semibold text-amber-300 mb-1 flex items-center gap-1.5">
                💡 Suggested Remediation / Measure:
              </div>
              <p className="text-xs font-mono text-amber-200/80 leading-relaxed">
                {decision.suggested_fix}
              </p>
            </div>
          )}

          {/* Payload JSON Inspector */}
          <details className="group">
            <summary className="text-xs text-slate-500 hover:text-slate-300 cursor-pointer select-none font-mono">
              ▶ Inspect Raw Event Payload
            </summary>
            <pre className="mt-2 p-3 rounded-xl bg-slate-950/70 text-emerald-400 text-[11px] font-mono overflow-x-auto max-h-36 border border-slate-800">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </details>

          {/* Reviewer Notes Input */}
          <div>
            <label className="block text-xs font-semibold text-slate-300 mb-1">
              Reviewer Notes (Optional):
            </label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Add context or rationale for your approval/rejection..."
              className="w-full px-3.5 py-2 text-xs rounded-xl bg-slate-800/60 border border-slate-700/60 text-slate-200 placeholder-slate-500 focus:outline-none focus:border-slate-600 focus:ring-1 focus:ring-slate-600"
            />
          </div>

          {error && (
            <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs">
              {error}
            </div>
          )}
        </div>

        {/* ── Fixed Footer Action Buttons ──────────────────────────────────── */}
        <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-700/50 flex-shrink-0">
          <button
            onClick={onClose}
            disabled={isSubmitting}
            className="px-4 py-2 text-xs font-semibold rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700/60 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => handleAction('rejected')}
            disabled={isSubmitting}
            className="px-4.5 py-2 text-xs font-semibold rounded-xl bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 border border-rose-500/30 transition-all shadow-sm hover:shadow-md"
          >
            ✕ Reject Action
          </button>
          <button
            onClick={() => handleAction('approved')}
            disabled={isSubmitting}
            className="px-4.5 py-2 text-xs font-semibold rounded-xl bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 border border-emerald-500/30 transition-all shadow-sm hover:shadow-md"
          >
            ✓ Approve Action
          </button>
        </div>
      </div>
    </div>
  )
}
