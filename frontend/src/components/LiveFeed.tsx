/**
 * src/components/LiveFeed.tsx
 * ───────────────────────────
 * Main live event stream feed for the Risk Gatekeeper dashboard.
 * 100% aligned with the toolbar layout in the screenshot:
 *  - Header Pill: "LIVE RISK EVALUATION STREAM"
 *  - Left Column: Source Tabs (ALL SOURCES, N8N, TRANSACTION, CURSOR)
 *  - Center Column: 2x2 Color Stats Cards (ALLOWED, WARNINGS, PENDING APPROVAL, BLOCKED)
 *  - Right Column: Verdict Tabs (RISKY (WARN/BLOCK), ALLOW, WARN, BLOCK)
 *
 * Includes smooth hover effects (transparency decreases & turns darker on hover)
 * and full filtering functionality for the live event stream below.
 */
import React, { useState, useMemo } from 'react'
import { EventCard } from './EventCard'
import type { EventRecord, DecisionRecord, EventSource, Verdict } from '../lib/api'
import type { WsMessage } from '../hooks/useWebSocket'

interface LiveFeedProps {
  messages: WsMessage[]
  wsStatus: 'connecting' | 'connected' | 'disconnected' | 'error'
  onReviewEvent: (event: EventRecord, decision: DecisionRecord) => void
  onClear: () => void
}

export const LiveFeed: React.FC<LiveFeedProps> = ({
  messages,
  wsStatus,
  onReviewEvent,
  onClear,
}) => {
  const [sourceFilter, setSourceFilter] = useState<EventSource | 'all'>('all')
  const [verdictFilter, setVerdictFilter] = useState<Verdict | 'risky' | 'all'>('all')

  // Extract combined event+decision pairs from WebSocket message buffer
  const items = useMemo(() => {
    const map = new Map<number, { event: EventRecord; decision: DecisionRecord }>()

    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i]
      if (msg.type === 'new_decision') {
        map.set(msg.event.id, { event: msg.event, decision: msg.decision })
      } else if (msg.type === 'human_decision' || msg.type === 'human_unblock') {
        const existing = map.get(msg.event_id)
        if (existing) {
          map.set(msg.event_id, {
            event: existing.event,
            decision: msg.decision,
          })
        }
      }
    }

    return Array.from(map.values()).sort((a, b) => b.event.id - a.event.id)
  }, [messages])

  // Filtered items based on tab selection
  const filteredItems = useMemo(() => {
    return items.filter(({ event, decision }) => {
      if (sourceFilter !== 'all' && event.source !== sourceFilter) return false
      if (verdictFilter === 'risky' && decision.verdict === 'ALLOW') return false
      if (
        verdictFilter !== 'all' &&
        verdictFilter !== 'risky' &&
        decision.verdict !== verdictFilter
      )
        return false
      return true
    })
  }, [items, sourceFilter, verdictFilter])

  // Stats Counters
  const stats = useMemo(() => {
    let allow = 0
    let warn = 0
    let block = 0
    let pending = 0

    items.forEach(({ decision }) => {
      if (decision.verdict === 'ALLOW') allow++
      else if (decision.verdict === 'WARN') {
        warn++
        if (!decision.human_decision) pending++
      } else if (decision.verdict === 'BLOCK') block++
    })

    return { total: items.length, allow, warn, block, pending }
  }, [items])

  return (
    <div className="font-lexend space-y-8">
      {/* ── 3-Column Control Panel Matching Screenshot ─────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-stretch">
        
        {/* ── LEFT COLUMN: SOURCE TABS ─────────────────────────────────────── */}
        <div className="lg:col-span-4 flex flex-col justify-between space-y-2.5">
          {/* Top Active Header Pill: ALL SOURCES */}
          <button
            onClick={() => setSourceFilter('all')}
            className={`w-full py-3 px-6 rounded-full text-xs font-extrabold uppercase tracking-wider text-center transition-all duration-200 shadow-sm ${
              sourceFilter === 'all'
                ? 'bg-gradient-to-r from-cyan-700/30 to-teal-700/20 text-white shadow-md border border-cyan-600/40'
                : 'bg-[#0a1628]/70 text-slate-400 border border-cyan-900/20 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01]'
            }`}
          >
            ALL SOURCES
          </button>

          {/* Sub-tab pills */}
          <button
            onClick={() => setSourceFilter('n8n')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              sourceFilter === 'n8n'
                ? 'bg-gradient-to-r from-cyan-700/30 to-teal-700/20 text-white shadow-md border border-cyan-600/40'
                : 'bg-[#0a1628]/50 text-slate-400 border border-cyan-900/15 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01] shadow-xs'
            }`}
          >
            N8N
          </button>

          <button
            onClick={() => setSourceFilter('transaction')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              sourceFilter === 'transaction'
                ? 'bg-gradient-to-r from-cyan-700/30 to-teal-700/20 text-white shadow-md border border-cyan-600/40'
                : 'bg-[#0a1628]/50 text-slate-400 border border-cyan-900/15 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01] shadow-xs'
            }`}
          >
            TRANSACTION
          </button>

          <button
            onClick={() => setSourceFilter('cursor')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              sourceFilter === 'cursor'
                ? 'bg-gradient-to-r from-cyan-700/30 to-teal-700/20 text-white shadow-md border border-cyan-600/40'
                : 'bg-[#0a1628]/50 text-slate-400 border border-cyan-900/15 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01] shadow-xs'
            }`}
          >
            CURSOR
          </button>

          <button
            onClick={() => setSourceFilter('liveops')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              sourceFilter === 'liveops'
                ? 'bg-gradient-to-r from-cyan-700/30 to-teal-700/20 text-white shadow-md border border-cyan-600/40'
                : 'bg-[#0a1628]/50 text-slate-400 border border-cyan-900/15 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01] shadow-xs'
            }`}
          >
            LIVEOPS
          </button>
        </div>

        {/* ── CENTER COLUMN: 2x2 COLOR STATS GRID ─────────────────────────── */}
        <div className="lg:col-span-4 grid grid-cols-2 gap-3.5">
          {/* ALLOWED */}
          <div className="p-4 rounded-3xl bg-emerald-950/50 border border-emerald-800/30 flex flex-col justify-between">
            <span className="text-[11px] font-extrabold text-emerald-300 uppercase tracking-wider">
              ALLOWED
            </span>
            <span className="text-3xl font-extrabold text-emerald-300 font-mono mt-2">
              {stats.allow}
            </span>
          </div>

          {/* WARNINGS */}
          <div className="p-4 rounded-3xl bg-amber-950/50 border border-amber-800/30 flex flex-col justify-between">
            <span className="text-[11px] font-extrabold text-amber-300 uppercase tracking-wider">
              WARNINGS
            </span>
            <span className="text-3xl font-extrabold text-amber-300 font-mono mt-2">
              {stats.warn}
            </span>
          </div>

          {/* PENDING APPROVAL */}
          <div className="p-4 rounded-3xl bg-orange-950/50 border border-orange-800/30 flex flex-col justify-between">
            <span className="text-[11px] font-extrabold text-orange-300 uppercase tracking-wider">
              PENDING APPROVAL
            </span>
            <span className="text-3xl font-extrabold text-orange-300 font-mono mt-2">
              {stats.pending}
            </span>
          </div>

          {/* BLOCKED */}
          <div className="p-4 rounded-3xl bg-rose-950/50 border border-rose-800/30 flex flex-col justify-between">
            <span className="text-[11px] font-extrabold text-rose-300 uppercase tracking-wider">
              BLOCKED
            </span>
            <span className="text-3xl font-extrabold text-rose-300 font-mono mt-2">
              {stats.block}
            </span>
          </div>
        </div>

        {/* ── RIGHT COLUMN: VERDICT TABS ───────────────────────────────────── */}
        <div className="lg:col-span-4 flex flex-col justify-between space-y-2.5">
          {/* Top Active Header Pill: RISKY (WARN/BLOCK) */}
          <button
            onClick={() => setVerdictFilter('risky')}
            className={`w-full py-3 px-6 rounded-full text-xs font-extrabold uppercase tracking-wider text-center transition-all duration-200 shadow-sm ${
              verdictFilter === 'risky'
                ? 'bg-gradient-to-r from-rose-700/30 to-amber-700/20 text-white shadow-md border border-rose-600/40'
                : 'bg-[#0a1628]/70 text-slate-400 border border-cyan-900/20 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01]'
            }`}
          >
            RISKY (WARN/BLOCK)
          </button>

          {/* Sub-tab pills */}
          <button
            onClick={() => setVerdictFilter('ALLOW')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              verdictFilter === 'ALLOW'
                ? 'bg-gradient-to-r from-cyan-700/30 to-teal-700/20 text-white shadow-md border border-cyan-600/40'
                : 'bg-[#0a1628]/50 text-slate-400 border border-cyan-900/15 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01] shadow-xs'
            }`}
          >
            ALLOW
          </button>

          <button
            onClick={() => setVerdictFilter('WARN')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              verdictFilter === 'WARN'
                ? 'bg-gradient-to-r from-amber-700/30 to-yellow-700/20 text-white shadow-md border border-amber-600/40'
                : 'bg-[#0a1628]/50 text-slate-400 border border-cyan-900/15 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01] shadow-xs'
            }`}
          >
            WARN
          </button>

          <button
            onClick={() => setVerdictFilter('BLOCK')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              verdictFilter === 'BLOCK'
                ? 'bg-gradient-to-r from-rose-700/30 to-red-700/20 text-white shadow-md border border-rose-600/40'
                : 'bg-[#0a1628]/50 text-slate-400 border border-cyan-900/15 hover:bg-[#0f1a30] hover:text-white hover:scale-[1.01] shadow-xs'
            }`}
          >
            BLOCK
          </button>
        </div>

      </div>

      {/* Clear Feed & Active Filter Indicator Toolbar */}
      <div className="flex items-center justify-between pt-2">
        <div className="text-xs text-slate-400 font-medium">
          Showing <span className="font-bold text-slate-100">{filteredItems.length}</span> event(s)
          {sourceFilter !== 'all' && <span> • Source: <strong className="uppercase text-cyan-400">{sourceFilter}</strong></span>}
          {verdictFilter !== 'all' && <span> • Verdict: <strong className="uppercase text-cyan-400">{verdictFilter}</strong></span>}
        </div>

        {items.length > 0 && (
          <button
            onClick={onClear}
            className="text-xs font-semibold text-slate-400 hover:text-white px-3.5 py-1.5 rounded-xl bg-[#0a1628] hover:bg-[#0f1a30] transition-colors border border-cyan-900/20"
          >
            Clear Feed
          </button>
        )}
      </div>

      {/* ── Event Stream List ──────────────────────────────────────────────── */}
      {filteredItems.length > 0 ? (
        <div className="space-y-4">
          {filteredItems.map(({ event, decision }) => (
            <EventCard
              key={event.id}
              event={event}
              decision={decision}
              onReview={onReviewEvent}
            />
          ))}
        </div>
      ) : (
        <div className="theme-card p-12 text-center my-6">
          <div className="w-12 h-12 rounded-2xl bg-[#0a1628] text-slate-400 flex items-center justify-center mx-auto mb-3 text-xl shadow-xs border border-cyan-900/20">
            🛡️
          </div>
          <h3 className="text-base font-semibold text-slate-100 mb-1">
            {items.length === 0
              ? 'Awaiting Intercepted Events'
              : 'No Events Match Selected Filters'}
          </h3>
          <p className="text-xs text-slate-400 max-w-md mx-auto leading-relaxed">
            {items.length === 0
              ? 'Trigger an action from the demo dropdown above or run an adapter simulation to see real-time risk evaluations appear live.'
              : 'Try selecting ALL SOURCES or RISKY (WARN/BLOCK) above to view all events.'}
          </p>
        </div>
      )}
    </div>
  )
}
