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
        className="w-full max-w-2xl max-h-[88vh] bg-white border border-neutral-200 shadow-2xl rounded-3xl p-6 relative flex flex-col overflow-hidden animate-slide-up"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Accent warning top strip */}
        <div className="absolute top-0 left-0 right-0 h-1.5 bg-gradient-to-r from-amber-400 via-amber-500 to-orange-400" />

        {/* ── Fixed Header ─────────────────────────────────────────────────── */}
        <div className="flex items-start justify-between pb-3 border-b border-neutral-100 flex-shrink-0">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="px-2.5 py-0.5 text-xs font-semibold uppercase rounded-full bg-amber-100 text-amber-800 border border-amber-300">
                ⚠️ Human Review Required
              </span>
              <span className="text-xs text-neutral-400 font-mono">Event #{event.id}</span>
            </div>
            <h2 className="text-xl font-bold text-neutral-900">
              Action Held Pending Approval
            </h2>
          </div>
          <button
            onClick={onClose}
            className="text-neutral-400 hover:text-neutral-700 text-xl font-bold p-1.5 rounded-lg hover:bg-neutral-100 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* ── Scrollable Body Content ───────────────────────────────────────── */}
        <div className="overflow-y-auto my-3 pr-1.5 space-y-4 flex-1">
          {/* Overview Cards */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            <div className="p-3 rounded-xl bg-neutral-50 border border-neutral-200/80">
              <div className="text-[11px] font-semibold text-neutral-400 uppercase">Source</div>
              <div className="text-sm font-semibold text-neutral-800 capitalize mt-0.5">
                {event.source}
              </div>
            </div>
            <div className="p-3 rounded-xl bg-neutral-50 border border-neutral-200/80">
              <div className="text-[11px] font-semibold text-neutral-400 uppercase">Event Type</div>
              <div className="text-sm font-mono text-amber-800 mt-0.5 truncate">
                {event.event_type}
              </div>
            </div>
            <div className="p-3 rounded-xl bg-neutral-50 border border-neutral-200/80 col-span-2 sm:col-span-1">
              <div className="text-[11px] font-semibold text-neutral-400 uppercase">Risk Score</div>
              <div className="text-sm font-mono font-bold text-amber-600 mt-0.5">
                {riskPct}%
              </div>
            </div>
          </div>

          {/* Original Goal */}
          {event.original_goal && (
            <div className="p-3 rounded-xl bg-neutral-100/70 border border-neutral-200/80">
              <div className="text-xs font-semibold text-neutral-500 uppercase mb-1">
                Original User Goal:
              </div>
              <p className="text-xs italic text-neutral-800">"{event.original_goal}"</p>
            </div>
          )}

          {/* Explainable Reasoning (Module 11) */}
          {decision.explanation && decision.explanation.trim() !== '' && (
            <div className="p-3 rounded-xl bg-sky-50 border border-sky-200/90 text-sky-950">
              <div className="text-xs font-semibold text-sky-900 mb-1 flex items-center gap-1.5">
                🧠 Why this decision:
              </div>
              <p className="text-xs text-sky-900 leading-relaxed">{decision.explanation}</p>
            </div>
          )}

          {/* Policy Evaluation Reasons */}
          <div>
            <h4 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-1.5">
              Risk Assessment Reasons:
            </h4>
            <ul className="space-y-1 bg-neutral-50 p-3 rounded-xl border border-neutral-200/80 text-xs text-neutral-800 list-disc list-inside">
              {decision.reasons.map((r, i) => (
                <li key={i} className="leading-relaxed">{r}</li>
              ))}
            </ul>
          </div>

          {/* Suggested Fix */}
          {decision.suggested_fix && decision.suggested_fix.trim() !== '' && (
            <div className="p-3.5 rounded-xl bg-amber-50 border border-amber-200/90 text-amber-950">
              <div className="text-xs font-semibold text-amber-900 mb-1 flex items-center gap-1.5">
                💡 Suggested Remediation / Measure:
              </div>
              <p className="text-xs font-mono text-amber-900 leading-relaxed">
                {decision.suggested_fix}
              </p>
            </div>
          )}

          {/* Payload JSON Inspector */}
          <details className="group">
            <summary className="text-xs text-neutral-500 hover:text-neutral-800 cursor-pointer select-none font-mono">
              ▶ Inspect Raw Event Payload
            </summary>
            <pre className="mt-2 p-3 rounded-xl bg-neutral-900 text-emerald-400 text-[11px] font-mono overflow-x-auto max-h-36 border border-neutral-800">
              {JSON.stringify(event.payload, null, 2)}
            </pre>
          </details>

          {/* Reviewer Notes Input */}
          <div>
            <label className="block text-xs font-semibold text-neutral-700 mb-1">
              Reviewer Notes (Optional):
            </label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Add context or rationale for your approval/rejection..."
              className="w-full px-3.5 py-2 text-xs rounded-xl bg-neutral-50 border border-neutral-300 text-neutral-900 placeholder-neutral-400 focus:outline-none focus:border-neutral-600 focus:ring-1 focus:ring-neutral-600"
            />
          </div>

          {error && (
            <div className="p-2.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-800 text-xs">
              {error}
            </div>
          )}
        </div>

        {/* ── Fixed Footer Action Buttons ──────────────────────────────────── */}
        <div className="flex items-center justify-end gap-3 pt-3 border-t border-neutral-200 flex-shrink-0">
          <button
            onClick={onClose}
            disabled={isSubmitting}
            className="px-4 py-2 text-xs font-semibold rounded-xl bg-neutral-100 hover:bg-neutral-200 text-neutral-700 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => handleAction('rejected')}
            disabled={isSubmitting}
            className="px-4.5 py-2 text-xs font-semibold rounded-xl bg-rose-600 hover:bg-rose-500 text-white transition-all shadow-sm hover:shadow-md"
          >
            ✕ Reject Action
          </button>
          <button
            onClick={() => handleAction('approved')}
            disabled={isSubmitting}
            className="px-4.5 py-2 text-xs font-semibold rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white transition-all shadow-sm hover:shadow-md"
          >
            ✓ Approve Action
          </button>
        </div>
      </div>
    </div>
  )
}
