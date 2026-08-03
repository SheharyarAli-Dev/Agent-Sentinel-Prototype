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
      } else if (msg.type === 'human_decision') {
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
                ? 'bg-gradient-to-r from-[#E0DFD8] to-[#D8D6CF] text-[#1A1B1E] shadow-md border border-[#C8C6BE]'
                : 'bg-[#F4F3ED] text-[#22252A] border border-[#E4E3DB] hover:bg-[#E5E3D9] hover:scale-[1.01]'
            }`}
          >
            ALL SOURCES
          </button>

          {/* Sub-tab pills */}
          <button
            onClick={() => setSourceFilter('n8n')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              sourceFilter === 'n8n'
                ? 'bg-gradient-to-r from-[#E0DFD8] to-[#D8D6CF] text-[#1A1B1E] shadow-md border border-[#C8C6BE]'
                : 'bg-[#FAF9F4] text-[#33353A] border border-[#E6E5DF] hover:bg-[#E5E3DA] hover:text-black hover:scale-[1.01] shadow-xs'
            }`}
          >
            N8N
          </button>

          <button
            onClick={() => setSourceFilter('transaction')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              sourceFilter === 'transaction'
                ? 'bg-gradient-to-r from-[#E0DFD8] to-[#D8D6CF] text-[#1A1B1E] shadow-md border border-[#C8C6BE]'
                : 'bg-[#FAF9F4] text-[#33353A] border border-[#E6E5DF] hover:bg-[#E5E3DA] hover:text-black hover:scale-[1.01] shadow-xs'
            }`}
          >
            TRANSACTION
          </button>

          <button
            onClick={() => setSourceFilter('cursor')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              sourceFilter === 'cursor'
                ? 'bg-gradient-to-r from-[#E0DFD8] to-[#D8D6CF] text-[#1A1B1E] shadow-md border border-[#C8C6BE]'
                : 'bg-[#FAF9F4] text-[#33353A] border border-[#E6E5DF] hover:bg-[#E5E3DA] hover:text-black hover:scale-[1.01] shadow-xs'
            }`}
          >
            CURSOR
          </button>
        </div>

        {/* ── CENTER COLUMN: 2x2 COLOR STATS GRID ─────────────────────────── */}
        <div className="lg:col-span-4 grid grid-cols-2 gap-3.5">
          {/* ALLOWED */}
          <div className="p-4 rounded-3xl bg-[#D6EEFF] border border-[#BCE1FC] flex flex-col justify-between transition-all duration-300 hover:scale-[1.02] hover:bg-[#C4E5FF] hover:shadow-md cursor-pointer">
            <span className="text-[11px] font-extrabold text-[#0C4A6E] uppercase tracking-wider">
              ALLOWED
            </span>
            <span className="text-3xl font-extrabold text-[#0C4A6E] font-mono mt-2">
              {stats.allow}
            </span>
          </div>

          {/* WARNINGS */}
          <div className="p-4 rounded-3xl bg-[#D2F4E2] border border-[#B9ECCE] flex flex-col justify-between transition-all duration-300 hover:scale-[1.02] hover:bg-[#BFEFDC] hover:shadow-md cursor-pointer">
            <span className="text-[11px] font-extrabold text-[#065F46] uppercase tracking-wider">
              WARNINGS
            </span>
            <span className="text-3xl font-extrabold text-[#065F46] font-mono mt-2">
              {stats.warn}
            </span>
          </div>

          {/* PENDING APPROVAL */}
          <div className="p-4 rounded-3xl bg-[#FFE4C7] border border-[#FCD5AD] flex flex-col justify-between transition-all duration-300 hover:scale-[1.02] hover:bg-[#FDDAB6] hover:shadow-md cursor-pointer">
            <span className="text-[11px] font-extrabold text-[#9A3412] uppercase tracking-wider">
              PENDING APPROVAL
            </span>
            <span className="text-3xl font-extrabold text-[#9A3412] font-mono mt-2">
              {stats.pending}
            </span>
          </div>

          {/* BLOCKED */}
          <div className="p-4 rounded-3xl bg-[#FCD4D4] border border-[#F8BEBE] flex flex-col justify-between transition-all duration-300 hover:scale-[1.02] hover:bg-[#F9C3C3] hover:shadow-md cursor-pointer">
            <span className="text-[11px] font-extrabold text-[#991B1B] uppercase tracking-wider">
              BLOCKED
            </span>
            <span className="text-3xl font-extrabold text-[#991B1B] font-mono mt-2">
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
                ? 'bg-gradient-to-r from-[#E0DFD8] to-[#D8D6CF] text-[#1A1B1E] shadow-md border border-[#C8C6BE]'
                : 'bg-[#F4F3ED] text-[#22252A] border border-[#E4E3DB] hover:bg-[#E5E3D9] hover:scale-[1.01]'
            }`}
          >
            RISKY (WARN/BLOCK)
          </button>

          {/* Sub-tab pills */}
          <button
            onClick={() => setVerdictFilter('ALLOW')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              verdictFilter === 'ALLOW'
                ? 'bg-gradient-to-r from-[#E0DFD8] to-[#D8D6CF] text-[#1A1B1E] shadow-md border border-[#C8C6BE]'
                : 'bg-[#FAF9F4] text-[#33353A] border border-[#E6E5DF] hover:bg-[#E5E3DA] hover:text-black hover:scale-[1.01] shadow-xs'
            }`}
          >
            ALLOW
          </button>

          <button
            onClick={() => setVerdictFilter('WARN')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              verdictFilter === 'WARN'
                ? 'bg-gradient-to-r from-[#E0DFD8] to-[#D8D6CF] text-[#1A1B1E] shadow-md border border-[#C8C6BE]'
                : 'bg-[#FAF9F4] text-[#33353A] border border-[#E6E5DF] hover:bg-[#E5E3DA] hover:text-black hover:scale-[1.01] shadow-xs'
            }`}
          >
            WARN
          </button>

          <button
            onClick={() => setVerdictFilter('BLOCK')}
            className={`w-full py-3 px-6 rounded-full text-xs font-semibold uppercase tracking-wider text-center transition-all duration-200 ${
              verdictFilter === 'BLOCK'
                ? 'bg-gradient-to-r from-[#E0DFD8] to-[#D8D6CF] text-[#1A1B1E] shadow-md border border-[#C8C6BE]'
                : 'bg-[#FAF9F4] text-[#33353A] border border-[#E6E5DF] hover:bg-[#E5E3DA] hover:text-black hover:scale-[1.01] shadow-xs'
            }`}
          >
            BLOCK
          </button>
        </div>

      </div>

      {/* Clear Feed & Active Filter Indicator Toolbar */}
      <div className="flex items-center justify-between pt-2">
        <div className="text-xs text-neutral-500 font-medium">
          Showing <span className="font-bold text-neutral-900">{filteredItems.length}</span> event(s)
          {sourceFilter !== 'all' && <span> • Source: <strong className="uppercase">{sourceFilter}</strong></span>}
          {verdictFilter !== 'all' && <span> • Verdict: <strong className="uppercase">{verdictFilter}</strong></span>}
        </div>

        {items.length > 0 && (
          <button
            onClick={onClear}
            className="text-xs font-semibold text-neutral-600 hover:text-neutral-900 px-3.5 py-1.5 rounded-xl bg-neutral-100 hover:bg-neutral-200 transition-colors border border-neutral-200/80"
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
          <div className="w-12 h-12 rounded-2xl bg-neutral-100 text-neutral-600 flex items-center justify-center mx-auto mb-3 text-xl shadow-xs">
            🛡️
          </div>
          <h3 className="text-base font-semibold text-neutral-900 mb-1">
            {items.length === 0
              ? 'Awaiting Intercepted Events'
              : 'No Events Match Selected Filters'}
          </h3>
          <p className="text-xs text-neutral-500 max-w-md mx-auto leading-relaxed">
            {items.length === 0
              ? 'Trigger an action from the demo dropdown above or run an adapter simulation to see real-time risk evaluations appear live.'
              : 'Try selecting ALL SOURCES or RISKY (WARN/BLOCK) above to view all events.'}
          </p>
        </div>
      )}
    </div>
  )
}
