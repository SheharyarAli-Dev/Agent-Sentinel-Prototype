/**
 * src/components/AnalyticsPanel.tsx
 * ───────────────────────────────────
 * Statistics & Analytics dashboard panel.
 * Renders live stats derived from the WebSocket message buffer.
 *
 * Features:
 *  - Verdict donut chart (ALLOW / WARN / BLOCK %)
 *  - Source bar chart (transaction / cursor / n8n counts)
 *  - Risk score sparkline (last 20 events)
 *  - KPI metrics row (total, block rate, avg risk, avg latency)
 *  - Top policy modules leaderboard
 *  - Collapsible with smooth animation
 *  - Dark operations-console theme
 */
import React, { useMemo, useState } from 'react'
import type { WsMessage } from '../hooks/useWebSocket'
import type { EventRecord, DecisionRecord } from '../lib/api'

interface AnalyticsPanelProps {
  messages: WsMessage[]
}

// ── Internal helpers ────────────────────────────────────────────────────────────

function extractItems(messages: WsMessage[]): { event: EventRecord; decision: DecisionRecord }[] {
  const map = new Map<number, { event: EventRecord; decision: DecisionRecord }>()
  for (let i = messages.length - 1; i >= 0; i--) {
    const msg = messages[i]
    if (msg.type === 'new_decision') {
      map.set(msg.event.id, { event: msg.event, decision: msg.decision })
    } else if (msg.type === 'human_decision' || msg.type === 'human_unblock') {
      const ex = map.get(msg.event_id)
      if (ex) map.set(msg.event_id, { event: ex.event, decision: msg.decision })
    }
  }
  return Array.from(map.values()).sort((a, b) => b.event.id - a.event.id)
}

// ── Donut chart (pure SVG) ──────────────────────────────────────────────────────

interface DonutSlice { value: number; color: string; label: string; accent: string }

function DonutChart({ slices, total }: { slices: DonutSlice[]; total: number }) {
  const r = 52
  const cx = 70
  const cy = 70
  const circumference = 2 * Math.PI * r

  let cumulativeAngle = -90 // start at top
  const paths = slices.map((s) => {
    const pct = total > 0 ? s.value / total : 0
    const angle = pct * 360
    const startAngle = cumulativeAngle
    cumulativeAngle += angle
    const endAngle = cumulativeAngle

    if (pct === 0) return null

    const startRad = (startAngle * Math.PI) / 180
    const endRad = (endAngle * Math.PI) / 180
    const x1 = cx + r * Math.cos(startRad)
    const y1 = cy + r * Math.sin(startRad)
    const x2 = cx + r * Math.cos(endRad)
    const y2 = cy + r * Math.sin(endRad)
    const largeArc = angle > 180 ? 1 : 0

    return (
      <path
        key={s.label}
        d={`M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2} Z`}
        fill={s.color}
        className="transition-opacity duration-300 hover:opacity-80"
      />
    )
  })

  return (
    <div className="flex flex-col items-center">
      <svg width="140" height="140" viewBox="0 0 140 140">
        {/* Donut hole */}
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="#1e293b" strokeWidth="28" />
        {total === 0 ? (
          <circle cx={cx} cy={cy} r={r} fill="#1e293b" />
        ) : (
          paths
        )}
        {/* Inner dark hole */}
        <circle cx={cx} cy={cy} r={r - 27} fill="#0f172a" />
        {/* Center label */}
        <text x={cx} y={cy - 6} textAnchor="middle" fontSize="18" fontWeight="700" fill="#e2e8f0" fontFamily="Lexend, sans-serif">
          {total}
        </text>
        <text x={cx} y={cy + 12} textAnchor="middle" fontSize="9" fill="#64748b" fontFamily="Lexend, sans-serif">
          TOTAL
        </text>
      </svg>
      {/* Legend */}
      <div className="flex flex-col gap-1 mt-1 w-full px-2">
        {slices.map((s) => (
          <div key={s.label} className="flex items-center justify-between text-[11px]">
            <div className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: s.color }} />
              <span className="text-slate-300 font-medium">{s.label}</span>
            </div>
            <span className="font-mono font-semibold" style={{ color: s.accent }}>
              {s.value}
              <span className="text-slate-500 font-normal ml-1">
                ({total > 0 ? Math.round((s.value / total) * 100) : 0}%)
              </span>
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Sparkline (pure SVG) ────────────────────────────────────────────────────────

function Sparkline({ scores }: { scores: number[] }) {
  const w = 220
  const h = 48
  if (scores.length < 2) {
    return (
      <div className="flex items-center justify-center h-12 text-xs text-slate-500">
        Waiting for data…
      </div>
    )
  }
  const max = Math.max(...scores, 0.1)
  const pts = scores.map((v, i) => {
    const x = (i / (scores.length - 1)) * w
    const y = h - (v / max) * (h - 4) - 2
    return `${x},${y}`
  })
  const last = scores[scores.length - 1]
  const lastColor = last >= 0.75 ? '#f87171' : last >= 0.40 ? '#fbbf24' : '#34d399'

  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="overflow-visible">
      <polyline
        points={pts.join(' ')}
        fill="none"
        stroke="#475569"
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      {/* Gradient fill under line */}
      <defs>
        <linearGradient id="sparkGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#6366f1" stopOpacity="0.3" />
          <stop offset="100%" stopColor="#6366f1" stopOpacity="0" />
        </linearGradient>
      </defs>
      <polygon
        points={`0,${h} ${pts.join(' ')} ${w},${h}`}
        fill="url(#sparkGrad)"
      />
      {/* Last dot */}
      {(() => {
        const [lx, ly] = pts[pts.length - 1].split(',').map(Number)
        return <circle cx={lx} cy={ly} r={3} fill={lastColor} />
      })()}
    </svg>
  )
}

// ── Bar chart (pure SVG) ────────────────────────────────────────────────────────

interface BarItem { label: string; value: number; color: string }

function BarChart({ bars }: { bars: BarItem[] }) {
  const max = Math.max(...bars.map((b) => b.value), 1)
  const bw = 32
  const gap = 24
  const h = 70
  const totalW = bars.length * (bw + gap) - gap + 20

  return (
    <svg width={totalW} height={h + 28} viewBox={`0 0 ${totalW} ${h + 28}`}>
      {bars.map((b, i) => {
        const barH = Math.max((b.value / max) * h, b.value > 0 ? 4 : 0)
        const x = i * (bw + gap) + 10
        const y = h - barH
        return (
          <g key={b.label}>
            <rect
              x={x} y={y} width={bw} height={barH}
              rx={6} fill={b.color} opacity="0.85"
              className="transition-all duration-500"
            />
            <text x={x + bw / 2} y={h + 14} textAnchor="middle" fontSize="9" fill="#64748b" fontFamily="Lexend, sans-serif">
              {b.label}
            </text>
            <text x={x + bw / 2} y={y - 4} textAnchor="middle" fontSize="10" fontWeight="700" fill="#e2e8f0" fontFamily="Lexend, sans-serif">
              {b.value}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ── Main Component ──────────────────────────────────────────────────────────────

export const AnalyticsPanel: React.FC<AnalyticsPanelProps> = ({ messages }) => {
  const [collapsed, setCollapsed] = useState(false)

  const items = useMemo(() => extractItems(messages), [messages])

  const stats = useMemo(() => {
    let allow = 0, warn = 0, block = 0, unblocked = 0
    let totalRisk = 0, totalLatency = 0
    const sourceCounts: Record<string, number> = { transaction: 0, cursor: 0, n8n: 0, liveops: 0 }
    const moduleCounts: Record<string, number> = {}
    const recentRisks: number[] = []

items.forEach(({ event, decision }) => {
      if (decision.verdict === 'ALLOW') allow++
      else if (decision.verdict === 'WARN') warn++
      else if (decision.verdict === 'BLOCK') {
        block++
        if (decision.unblocked_by_human) unblocked++
      }

      totalRisk += decision.risk_score
      totalLatency += decision.latency_ms ?? 0
      if (event.source in sourceCounts) sourceCounts[event.source]++

      // Count verdict-triggering modules (those that produced WARN/BLOCK or changed final verdict)
      decision.module.split(',').forEach((m) => {
        const name = m.trim()
        if (name && name !== 'policy_engine') {
          moduleCounts[name] = (moduleCounts[name] ?? 0) + 1
        }
      })

      recentRisks.push(decision.risk_score)
    })

    const total = items.length
    const avgRisk = total > 0 ? totalRisk / total : 0
    const avgLatency = total > 0 ? totalLatency / total : 0
    const blockRate = total > 0 ? (block / total) * 100 : 0

    // Top 5 verdict-triggering modules by firing count
    const topTriggeredModules = Object.entries(moduleCounts)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5)

    // Evaluated modules per source (static mapping based on rules_engine.py routing)
    const evaluatedModulesBySource: Record<string, string[]> = {
      transaction: ['policy_engine', 'least_privilege', 'memory_integrity', 'multi_agent', 'attve', 'sequential_behaviour', 'intent_verification (advisory)'],
      cursor: ['policy_engine', 'least_privilege', 'memory_integrity', 'multi_agent', 'planning_verification', 'context_integrity', 'tool_integrity', 'sequential_behaviour', 'predictive_defence', 'intent_verification'],
      n8n: ['policy_engine', 'least_privilege', 'memory_integrity', 'multi_agent', 'planning_verification', 'context_integrity', 'tool_integrity', 'sequential_behaviour', 'predictive_defence', 'intent_verification'],
      liveops: ['policy_engine', 'least_privilege', 'memory_integrity', 'multi_agent', 'planning_verification', 'sequential_behaviour', 'intent_verification'],
    }

    return {
      total, allow, warn, block, unblocked,
      avgRisk, avgLatency, blockRate,
      sourceCounts,
      topModules: topTriggeredModules,
      evaluatedModulesBySource,
      recentRisks: recentRisks.slice(-20),
    }
  }, [items])

  const donutSlices = [
    { label: 'ALLOW', value: stats.allow, color: '#86EFAC', accent: '#16A34A' },
    { label: 'WARN', value: stats.warn, color: '#FCD34D', accent: '#B45309' },
    { label: 'BLOCK', value: stats.block, color: '#FCA5A5', accent: '#DC2626' },
  ]

  const sourceBars: BarItem[] = [
    { label: 'TRANS', value: stats.sourceCounts.transaction, color: '#93C5FD' },
    { label: 'CURSOR', value: stats.sourceCounts.cursor, color: '#C4B5FD' },
    { label: 'N8N', value: stats.sourceCounts.n8n, color: '#6EE7B7' },
    { label: 'OPS', value: stats.sourceCounts.liveops, color: '#67E8F9' },
  ]

  // Module label mapping for display
  const moduleDisplayNames: Record<string, string> = {
    'policy_engine': 'Policy Engine',
    'least_privilege': 'Least Privilege',
    'memory_integrity': 'Memory Integrity',
    'multi_agent': 'Multi-Agent',
    'attve': 'ATTVE',
    'sequential_behaviour': 'Sequential Behaviour',
    'intent_verification (advisory)': 'Intent Verification (advisory)',
    'intent_verification': 'Intent Verification',
    'planning_verification': 'Planning Verification',
    'context_integrity': 'Context Integrity',
    'tool_integrity': 'Tool Integrity',
    'predictive_defence': 'Predictive Defence',
  }

  return (
    <section className="relative z-10 px-6 lg:px-12 pt-8 pb-6 font-lexend">
      {/* Panel Header — matches dashboard style */}
      <div
        className="flex items-center justify-between mb-4 cursor-pointer group"
        onClick={() => setCollapsed((c) => !c)}
      >
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-violet-400 animate-pulse" />
          <h3 className="text-lg sm:text-xl font-bold uppercase tracking-widest text-slate-100">
            Analytics & Statistics
          </h3>
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-[#0a1628] text-slate-400 border border-cyan-900/30">
            {stats.total} events
          </span>
        </div>
        <button className="text-xs text-slate-400 group-hover:text-slate-200 transition-colors flex items-center gap-1 px-3 py-1.5 rounded-full bg-[#0a1628] hover:bg-[#0f1a30] border border-cyan-900/30">
          {collapsed ? '▶ Expand' : '▼ Collapse'}
        </button>
      </div>

      {/* Collapsible Body */}
      <div
        className={`overflow-hidden transition-all duration-500 ease-in-out ${
          collapsed ? 'max-h-0 opacity-0' : 'max-h-[800px] opacity-100'
        }`}
      >
        {/* ── KPI Metrics Row ─────────────────────────────────────────────────── */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
          {[
            {
              label: 'Total Events',
              value: stats.total.toString(),
              sub: 'intercepted',
              accent: 'text-slate-100',
              bg: 'bg-[#0a1628]/60',
              border: 'border-cyan-900/20',
            },
            {
              label: 'Block Rate',
              value: `${stats.blockRate.toFixed(1)}%`,
              sub: `${stats.block} blocked`,
              accent: 'text-rose-400',
              bg: 'bg-rose-950/40',
              border: 'border-rose-800/30',
            },
            {
              label: 'Avg Risk Score',
              value: `${(stats.avgRisk * 100).toFixed(1)}%`,
              sub: 'across all events',
              accent: stats.avgRisk >= 0.5 ? 'text-amber-400' : 'text-emerald-400',
              bg: stats.avgRisk >= 0.5 ? 'bg-amber-950/40' : 'bg-emerald-950/40',
              border: stats.avgRisk >= 0.5 ? 'border-amber-800/30' : 'border-emerald-800/30',
            },
            {
              label: 'Avg Latency',
              value: `${stats.avgLatency.toFixed(1)}ms`,
              sub: stats.avgLatency < 40 ? '✓ within KPI' : '⚠ above 40ms',
              accent: stats.avgLatency < 40 ? 'text-emerald-400' : 'text-amber-400',
              bg: stats.avgLatency < 40 ? 'bg-emerald-950/40' : 'bg-amber-950/40',
              border: stats.avgLatency < 40 ? 'border-emerald-800/30' : 'border-amber-800/30',
            },
          ].map((kpi) => (
            <div
              key={kpi.label}
              className={`${kpi.bg} border ${kpi.border} rounded-2xl p-3.5 transition-all duration-300 hover:shadow-md`}
            >
              <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">
                {kpi.label}
              </div>
              <div className={`text-2xl font-extrabold font-mono ${kpi.accent}`}>
                {kpi.value}
              </div>
              <div className="text-[10px] text-slate-500 mt-0.5">{kpi.sub}</div>
            </div>
          ))}
        </div>

        {/* ── Charts Row ──────────────────────────────────────────────────────── */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">

          {/* Verdict Donut */}
          <div className="theme-card p-4">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-3">
              Verdict Breakdown
            </div>
            <DonutChart slices={donutSlices} total={stats.total} />
            {stats.unblocked > 0 && (
              <div className="mt-2 text-[10px] text-violet-400 font-medium text-center bg-violet-950/40 border border-violet-800/30 rounded-xl py-1 px-2">
                🔓 {stats.unblocked} block{stats.unblocked > 1 ? 's' : ''} manually unblocked
              </div>
            )}
          </div>

          {/* Source Bar Chart */}
          <div className="theme-card p-4">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-3">
              Actions by Source
            </div>
            {stats.total === 0 ? (
              <div className="flex items-center justify-center h-24 text-xs text-slate-500">
                No events yet
              </div>
            ) : (
              <div className="flex justify-center mt-2">
                <BarChart bars={sourceBars} />
              </div>
            )}
          </div>

          {/* Risk Score Sparkline */}
          <div className="theme-card p-4">
            <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">
              Risk Score Trend
            </div>
            <div className="text-[10px] text-slate-500 mb-3">Last 20 events</div>
            <div className="flex justify-center mt-2">
              <Sparkline scores={stats.recentRisks} />
            </div>
            {stats.recentRisks.length > 0 && (
              <div className="flex justify-between text-[10px] text-slate-500 mt-2 px-1">
                <span>oldest</span>
                <span>
                  latest:{' '}
                  <span className="font-mono font-semibold text-slate-300">
                    {(stats.recentRisks[stats.recentRisks.length - 1] * 100).toFixed(0)}%
                  </span>
                </span>
              </div>
            )}
          </div>
        </div>

        {/* ── Evaluated Modules by Source ─────────────────────────────────────────────── */}
        <div className="theme-card p-4">
          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-3">
            Evaluated Modules (per Source)
          </div>
          {stats.total === 0 ? (
            <div className="text-xs text-slate-500 text-center py-4">
              Trigger events to see evaluated modules
            </div>
          ) : (
            <div className="space-y-3">
              {Object.entries(stats.sourceCounts)
                .filter(([, count]) => count > 0)
                .map(([source]) => {
                  const modules = stats.evaluatedModulesBySource[source] ?? []
                  return (
                    <div key={source} className="space-y-1.5">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                          {source.toUpperCase()}
                        </span>
                        <span className="text-[10px] font-mono text-slate-500">
                          ({stats.sourceCounts[source]} events)
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-1.5 pl-6">
                        {modules.map((mod) => (
                          <span
                            key={mod}
                            className="px-2 py-0.5 text-[10px] font-medium rounded-full bg-[#0a1628] text-slate-400 border border-cyan-900/20"
                          >
                            {moduleDisplayNames[mod] ?? mod}
                          </span>
                        ))}
                      </div>
                    </div>
                  )
                })}
            </div>
          )}
        </div>

        {/* ── Modules Recorded in Decision Evidence ────────────────────────────── */}
        <div className="theme-card p-4">
          <div className="text-[10px] font-bold uppercase tracking-wider text-slate-400 mb-1">
            Modules Recorded in Decision Evidence
          </div>
          <div className="text-[10px] text-slate-500 mb-3">
            Includes checks recorded during evaluation; not every listed module changed the final verdict.
          </div>
          {stats.topModules.length === 0 ? (
            <div className="text-xs text-slate-500 text-center py-4">
              Trigger events to see module activity
            </div>
          ) : (
            <div className="space-y-2">
              {stats.topModules.map(([mod, count], idx) => {
                const maxCount = stats.topModules[0][1]
                const pct = (count / maxCount) * 100
                const barColors = ['bg-violet-400', 'bg-blue-400', 'bg-emerald-400', 'bg-amber-400', 'bg-rose-400']
                return (
                  <div key={mod} className="flex items-center gap-3">
                    <span className="text-[10px] font-mono text-slate-500 w-4 text-right">{idx + 1}</span>
                    <span className="text-[11px] font-medium text-slate-300 w-44 truncate">{moduleDisplayNames[mod] ?? mod}</span>
                    <div className="flex-1 bg-slate-800 rounded-full h-2 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-700 ${barColors[idx]}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="text-[10px] font-mono font-semibold text-slate-400 w-6 text-right">{count}</span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
