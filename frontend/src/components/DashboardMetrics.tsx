import { useState, useEffect, useCallback } from 'react'
import {
  getIncidentMetrics,
  getRemediationMetrics,
  getPendingApprovals,
  listWatchers,
} from '../services/api'
import { IncidentMetricsResponse } from '../types'
import AnimatedNumber from './AnimatedNumber'
import { useGlobalEvents } from '../hooks/useGlobalEvents'
import {
  IconAlertCircle,
  IconCircleCheck,
  IconClock,
  IconShield,
  IconCheck,
  IconBell,
  IconAlertTriangle,
} from './icons'

interface DashboardMetricsProps {
  onMetricClick?: (filter: 'all' | 'active') => void
  onNavigate?: (view: string) => void
}

// ─── KPI card definition ──────────────────────────────────────────────────────
interface CardDef {
  label: string
  sublabel: string
  value: number
  display?: string
  unit?: string
  icon: React.ReactNode
  accentColor: string
  topBarColor: string
  dotColor: string
  dotPulse: boolean
  showBar?: boolean
  filterType?: 'all' | 'active'
}

// ─── Watcher / approval live state ───────────────────────────────────────────
type WatcherHealth = 'active' | 'delayed' | 'offline'

interface WatcherSummary {
  name: string
  health: WatcherHealth
  secondsAgo: number | null
}

interface SystemStatus {
  watchers: WatcherSummary[]
  pendingRegistrations: number
  pendingApprovals: number
}

// ─── Watcher health helper ────────────────────────────────────────────────────
function watcherHealth(status: string, secondsAgo: number | null): WatcherHealth {
  if (status !== 'active') return 'offline'
  if (secondsAgo === null) return 'offline'
  if (secondsAgo < 90)  return 'active'
  if (secondsAgo < 300) return 'delayed'
  return 'offline'
}

export default function DashboardMetrics({ onMetricClick, onNavigate }: DashboardMetricsProps) {
  const [cards, setCards]               = useState<CardDef[]>([])
  const [system, setSystem]             = useState<SystemStatus | null>(null)
  const [severityBreakdown, setSeverityBreakdown] = useState<Record<string, number>>({})
  const [activeTotal, setActiveTotal]   = useState(0)
  const [loading, setLoading]           = useState(true)
  const [lastUpdated, setLastUpdated]   = useState<Date | null>(null)

  useEffect(() => {
    load()
    const t = setInterval(load, 60_000)   // 60 s safety-net; WS pushes handle the rest
    return () => clearInterval(t)
    // load is stable (useCallback with [] deps) — [] is equivalent to [load] here
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Live push — reload whenever the server broadcasts any incident or approval event
  // load is stable so we don't need it in the dep array; useGlobalEvents wraps with useCallback internally
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useGlobalEvents(useCallback((event) => {
    if (
      event.type === 'incident_created' ||
      event.type === 'incident_updated' ||
      event.type === 'approval_requested'
    ) {
      load()
    }
  }, []))

  const load = useCallback(async () => {
    try {
      const [incRes, remRes, appRes, watchRes] = await Promise.all([
        getIncidentMetrics(),
        getRemediationMetrics(),
        getPendingApprovals(),
        listWatchers(),
      ])

      // ── KPI cards ────────────────────────────────────────────────────────
      const inc = incRes.data as IncidentMetricsResponse
      const rem = remRes.data as Record<string, number>

      const active        = inc.active_incidents ?? 0
      const resolvedToday = inc.resolved_today ?? 0
      const mttrSec       = inc.avg_resolution_time ?? 0
      const mttrMin       = Math.round(mttrSec / 60)
      const mttrDisplay   = mttrMin === 0 ? '—' : mttrMin >= 60 ? `${Math.round(mttrMin / 60)}h` : `${mttrMin}m`
      const remSuccessPct = Math.round((rem.remediation_success_rate ?? 0) * 100)
      const approvalPct   = Math.round((inc.approval_rate ?? 0) * 100)

      setCards([
        {
          label: 'Active Incidents',
          sublabel: active === 0 ? 'All systems clear' : 'Requires attention',
          value: active,
          icon: <IconAlertCircle size={26} strokeWidth={2} />,
          accentColor: active === 0 ? '#10b981' : active <= 3 ? '#f59e0b' : '#ef4444',
          topBarColor: active === 0
            ? 'bg-gradient-to-r from-success-500 to-success-400'
            : active <= 3
              ? 'bg-gradient-to-r from-warning-500 to-warning-400'
              : 'bg-gradient-to-r from-critical-500 to-critical-400',
          dotColor:  active === 0 ? 'bg-success-500' : active <= 3 ? 'bg-warning-500' : 'bg-critical-500',
          dotPulse:  active > 0,
          filterType: 'active',
        },
        {
          label: 'Resolved Today',
          sublabel: 'Closed in last 24 hours',
          value: resolvedToday,
          icon: <IconCircleCheck size={26} strokeWidth={2} />,
          accentColor: '#10b981',
          topBarColor: 'bg-gradient-to-r from-success-500 to-info-500',
          dotColor:    'bg-success-500',
          dotPulse:    false,
        },
        {
          label: 'Mean Time to Resolve',
          sublabel: 'Average resolution time',
          value: mttrMin,
          display: mttrDisplay,
          icon: <IconClock size={26} strokeWidth={2} />,
          accentColor: mttrMin === 0 ? '#7a8ba3' : mttrMin <= 30 ? '#10b981' : mttrMin <= 120 ? '#f59e0b' : '#ef4444',
          topBarColor: mttrMin <= 30
            ? 'bg-gradient-to-r from-success-500 to-success-400'
            : mttrMin <= 120
              ? 'bg-gradient-to-r from-warning-500 to-warning-400'
              : 'bg-gradient-to-r from-critical-500 to-critical-400',
          dotColor:  mttrMin <= 30 ? 'bg-success-500' : mttrMin <= 120 ? 'bg-warning-500' : 'bg-critical-500',
          dotPulse:  false,
        },
        {
          label: 'Remediation Success',
          sublabel: 'Auto-remediation outcomes',
          value: remSuccessPct,
          unit: '%',
          icon: <IconShield size={26} strokeWidth={2} />,
          accentColor: remSuccessPct >= 80 ? '#10b981' : remSuccessPct >= 60 ? '#f59e0b' : '#ef4444',
          topBarColor: remSuccessPct >= 80
            ? 'bg-gradient-to-r from-success-500 to-success-400'
            : remSuccessPct >= 60
              ? 'bg-gradient-to-r from-warning-500 to-warning-400'
              : 'bg-gradient-to-r from-critical-500 to-critical-400',
          dotColor:  remSuccessPct >= 80 ? 'bg-success-500' : remSuccessPct >= 60 ? 'bg-warning-500' : 'bg-critical-500',
          dotPulse:  false,
          showBar:   true,
        },
        {
          label: 'Approval Rate',
          sublabel: 'Actions approved / total',
          value: approvalPct,
          unit: '%',
          icon: <IconCheck size={26} strokeWidth={2} />,
          accentColor: approvalPct >= 80 ? '#10b981' : approvalPct >= 60 ? '#f59e0b' : '#ef4444',
          topBarColor: approvalPct >= 80
            ? 'bg-gradient-to-r from-success-500 to-success-400'
            : approvalPct >= 60
              ? 'bg-gradient-to-r from-warning-500 to-warning-400'
              : 'bg-gradient-to-r from-critical-500 to-critical-400',
          dotColor:  approvalPct >= 80 ? 'bg-success-500' : approvalPct >= 60 ? 'bg-warning-500' : 'bg-critical-500',
          dotPulse:  false,
          showBar:   true,
        },
      ])

      // ── Severity breakdown ────────────────────────────────────────────────
      setSeverityBreakdown(inc.severity_breakdown ?? {})
      setActiveTotal(active)

      // ── System status ─────────────────────────────────────────────────────
      const allWatchers = (watchRes.data as any[]) ?? []
      const approved    = allWatchers.filter(w => w.registration_status === 'approved')
      const watcherSummaries: WatcherSummary[] = approved.map(w => ({
        name:       w.display_name || w.watcher_name,
        health:     watcherHealth(w.status, w.last_seen_seconds_ago),
        secondsAgo: w.last_seen_seconds_ago ?? null,
      }))

      setSystem({
        watchers:             watcherSummaries,
        pendingRegistrations: allWatchers.filter(w => w.registration_status === 'pending').length,
        pendingApprovals:     Array.isArray(appRes.data) ? appRes.data.length : 0,
      })

      setLastUpdated(new Date())
    } catch {
      // fail silently — stale data stays visible
    } finally {
      setLoading(false)
    }
  }, [])  // no external deps — only calls setters and stable API functions

  // ── Skeleton ────────────────────────────────────────────────────────────────
  if (loading && cards.length === 0) {
    return (
      <div className="mb-8">
        <div className="mb-4">
          <h2 className="text-section-title mb-1" style={{ color: '#e8eef5' }}>Incident Metrics</h2>
          <p className="text-sm" style={{ color: '#a0aec0' }}>Real-time overview of your incident management performance</p>
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-4">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="metric-card h-40 skeleton-pulse" style={{ animationDelay: `${i * 50}ms` }} />
          ))}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[...Array(2)].map((_, i) => (
            <div key={i} className="metric-card h-20 skeleton-pulse" style={{ animationDelay: `${i * 50}ms` }} />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="mb-8">
      {/* Header */}
      <div className="mb-4">
        <h2 className="text-section-title mb-1" style={{ color: '#e8eef5' }}>Incident Metrics</h2>
        <p className="text-sm" style={{ color: '#a0aec0' }}>Real-time overview of your incident management performance</p>
      </div>

      {/* ── Row 1: 5 KPI cards ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-4">
        {cards.map((card, i) => (
          <div
            key={card.label}
            onClick={() => card.filterType && onMetricClick?.(card.filterType)}
            className={`metric-card relative overflow-hidden transition-all duration-300 hover:-translate-y-1 hover:shadow-layered-dark ${card.filterType ? 'cursor-pointer' : ''}`}
            style={{ animation: 'staggerFadeIn 0.4s ease-out forwards', animationDelay: `${i * 70}ms`, opacity: 0 }}
          >
            <div className={`absolute top-0 left-0 right-0 h-1 ${card.topBarColor}`} />
            <div className="absolute -top-12 -right-12 w-28 h-28 bg-gradient-to-br from-info-500/8 to-transparent rounded-full blur-3xl pointer-events-none" />
            <div className="relative pt-1">
              <div className="flex items-center justify-between mb-3">
                <span style={{ color: card.accentColor }}>{card.icon}</span>
                <span className={`w-2.5 h-2.5 rounded-full ${card.dotColor} ${card.dotPulse ? 'animate-metric-pulse' : ''}`} />
              </div>
              <p className="text-xs font-medium mb-0.5 uppercase tracking-wide" style={{ color: '#7a8ba3' }}>{card.label}</p>
              <p className="text-xs mb-3" style={{ color: '#4b5563' }}>{card.sublabel}</p>
              <p className="text-4xl font-bold leading-none" style={{ color: card.accentColor }}>
                {card.display != null ? card.display : (
                  <><AnimatedNumber value={card.value} />{card.unit && <span className="text-xl ml-0.5">{card.unit}</span>}</>
                )}
              </p>
              {card.showBar && (
                <div className="mt-3 h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: '#1e2535' }}>
                  <div className="h-full rounded-full transition-all duration-700"
                    style={{ width: `${Math.min(card.value, 100)}%`, backgroundColor: card.accentColor }} />
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* ── Row 2: Severity breakdown (only if there are active incidents) ─── */}
      {activeTotal > 0 && (
        <SeverityBreakdownBar breakdown={severityBreakdown} total={activeTotal} />
      )}

      {/* ── Row 3: System health ────────────────────────────────────────────── */}
      {system && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4"
          style={{ animation: 'staggerFadeIn 0.4s ease-out forwards', animationDelay: '380ms', opacity: 0 }}>
          <WatcherCard
            watchers={system.watchers}
            pendingRegistrations={system.pendingRegistrations}
            onNavigate={onNavigate}
          />
          <ApprovalsCard count={system.pendingApprovals} onMetricClick={onMetricClick} />
        </div>
      )}

      {/* Footer */}
      {lastUpdated && (
        <p className="text-xs text-center mt-3" style={{ color: '#4b5563' }}>
          Live updates via WebSocket · Last updated {lastUpdated.toLocaleTimeString()}
        </p>
      )}
    </div>
  )
}

// ─── Severity breakdown bar ───────────────────────────────────────────────────
const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'unknown'] as const
const SEVERITY_CFG: Record<string, { label: string; color: string }> = {
  critical: { label: 'Critical', color: '#ef4444' },
  high:     { label: 'High',     color: '#f59e0b' },
  medium:   { label: 'Medium',   color: '#3b82f6' },
  low:      { label: 'Low',      color: '#10b981' },
  unknown:  { label: 'Unknown',  color: '#6b7280' },
}

function SeverityBreakdownBar({ breakdown, total }: { breakdown: Record<string, number>; total: number }) {
  const entries = SEVERITY_ORDER
    .map(key => ({ key, count: breakdown[key] ?? 0, ...SEVERITY_CFG[key] }))
    .filter(e => e.count > 0)

  if (entries.length === 0) return null

  return (
    <div
      className="metric-card mb-4"
      style={{ animation: 'staggerFadeIn 0.4s ease-out forwards', animationDelay: '350ms', opacity: 0 }}
    >
      {/* Header row */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#e8eef5' }}>Active by Severity</h3>
          <p className="text-xs" style={{ color: '#7a8ba3' }}>{total} incident{total !== 1 ? 's' : ''} currently open</p>
        </div>
        <div className="flex items-center gap-4 flex-wrap justify-end">
          {entries.map(e => (
            <div key={e.key} className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: e.color }} />
              <span className="text-sm font-bold" style={{ color: e.color }}>{e.count}</span>
              <span className="text-xs" style={{ color: '#7a8ba3' }}>{e.label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Proportional stacked bar */}
      <div className="h-3 rounded-full overflow-hidden flex gap-px" style={{ backgroundColor: '#1e2535' }}>
        {entries.map(e => (
          <div
            key={e.key}
            className="h-full transition-all duration-700 first:rounded-l-full last:rounded-r-full"
            style={{ width: `${(e.count / total) * 100}%`, backgroundColor: e.color }}
            title={`${e.label}: ${e.count} (${Math.round((e.count / total) * 100)}%)`}
          />
        ))}
      </div>

      {/* Percentage labels */}
      <div className="flex mt-1.5">
        {entries.map(e => (
          <div key={e.key} style={{ width: `${(e.count / total) * 100}%` }} className="overflow-hidden">
            <span className="text-xs block text-center truncate" style={{ color: e.color }}>
              {Math.round((e.count / total) * 100)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Watcher status card ──────────────────────────────────────────────────────
const WATCHER_HEALTH_CFG: Record<WatcherHealth, {
  label: string; singularLabel: string; color: string; dotCls: string; barCls: string; pulse: boolean
}> = {
  active:  { label: 'Active',  singularLabel: 'Monitoring Active', color: '#10b981', dotCls: 'bg-success-500',  barCls: 'bg-gradient-to-r from-success-500 to-success-400', pulse: true  },
  delayed: { label: 'Delayed', singularLabel: 'Heartbeat Delayed', color: '#f59e0b', dotCls: 'bg-warning-500',  barCls: 'bg-gradient-to-r from-warning-500 to-warning-400', pulse: true  },
  offline: { label: 'Offline', singularLabel: 'Watcher Offline',   color: '#ef4444', dotCls: 'bg-critical-500', barCls: 'bg-gradient-to-r from-critical-500 to-critical-400', pulse: false },
}

function lastSeenText(secondsAgo: number | null): string {
  if (secondsAgo === null) return 'Never seen'
  if (secondsAgo < 60)   return `${secondsAgo}s ago`
  if (secondsAgo < 3600) return `${Math.round(secondsAgo / 60)}m ago`
  return `${Math.round(secondsAgo / 3600)}h ago`
}

function WatcherCard({
  watchers,
  pendingRegistrations,
  onNavigate,
}: {
  watchers: WatcherSummary[]
  pendingRegistrations: number
  onNavigate?: (view: string) => void
}) {
  const goToMonitoring = () => onNavigate?.('monitoring')
  const total = watchers.length

  // ── Zero state: nothing registered yet ──────────────────────────────────
  if (total === 0) {
    const color = '#7a8ba3'
    return (
      <div className="metric-card relative overflow-hidden cursor-pointer" onClick={goToMonitoring}>
        <div className="absolute top-0 left-0 right-0 h-1" style={{ backgroundColor: color }} />
        <div className="relative flex items-center gap-4">
          <div className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0"
            style={{ backgroundColor: 'transparent', border: `2px solid ${color}` }}>
            <IconAlertTriangle size={20} style={{ color }} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold" style={{ color }}>No Watchers Registered</p>
            <p className="text-xs" style={{ color: '#7a8ba3' }}>
              {pendingRegistrations > 0
                ? `${pendingRegistrations} pending approval — click to review`
                : 'Click to set up monitoring'}
            </p>
          </div>
        </div>
      </div>
    )
  }

  const counts: Record<WatcherHealth, number> = { active: 0, delayed: 0, offline: 0 }
  for (const w of watchers) counts[w.health]++

  const worst: WatcherHealth = counts.offline > 0 ? 'offline' : counts.delayed > 0 ? 'delayed' : 'active'
  const cfg = WATCHER_HEALTH_CFG[worst]

  const badWatchers = watchers.filter(w => w.health !== 'active')
  const singleBadWatcher = total > 1 && badWatchers.length === 1 ? badWatchers[0] : null

  const headline = total === 1
    ? cfg.singularLabel
    : (['offline', 'delayed', 'active'] as WatcherHealth[])
        .filter(h => counts[h] > 0)
        .map(h => `${counts[h]} ${WATCHER_HEALTH_CFG[h].label}`)
        .join(' · ')

  const sublabel = total === 1
    ? `${watchers[0].name} · Last heartbeat: ${lastSeenText(watchers[0].secondsAgo)}`
    : singleBadWatcher
      ? `${total} watchers · ${singleBadWatcher.name} ${singleBadWatcher.health} (${lastSeenText(singleBadWatcher.secondsAgo)})`
      : `${total} watchers monitored`

  return (
    <div className="metric-card relative overflow-hidden cursor-pointer" onClick={goToMonitoring}>
      <div className={`absolute top-0 left-0 right-0 h-1 ${cfg.barCls}`} />
      <div className="relative flex items-center gap-4">
        <div className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ backgroundColor: 'transparent', border: `2px solid ${cfg.color}` }}>
          <IconAlertTriangle size={20} style={{ color: cfg.color }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className={`w-2 h-2 rounded-full ${cfg.dotCls} ${cfg.pulse ? 'animate-metric-pulse' : ''}`} />
            <p className="text-sm font-semibold truncate" style={{ color: cfg.color }}>{headline}</p>
          </div>
          <p className="text-xs truncate" style={{ color: '#7a8ba3' }}>{sublabel}</p>
        </div>
        {pendingRegistrations > 0 && (
          <span className="text-xs font-medium px-2 py-1 rounded-md flex-shrink-0"
            style={{ backgroundColor: 'transparent', border: '1px solid #f59e0b', color: '#f59e0b' }}>
            {pendingRegistrations} PENDING
          </span>
        )}
        <span className="text-xs font-medium px-2 py-1 rounded-md flex-shrink-0"
          style={{ backgroundColor: 'transparent', border: `1px solid ${cfg.color}`, color: cfg.color }}>
          {total === 1 ? worst.toUpperCase() : `${total} WATCHERS`}
        </span>
      </div>
    </div>
  )
}

// ─── Pending approvals card ───────────────────────────────────────────────────
function ApprovalsCard({ count, onMetricClick }: { count: number; onMetricClick?: (f: 'all' | 'active') => void }) {
  const urgent  = count > 0
  const color   = urgent ? '#f59e0b' : '#10b981'
  const barCls  = urgent
    ? 'bg-gradient-to-r from-warning-500 to-warning-400'
    : 'bg-gradient-to-r from-success-500 to-success-400'

  return (
    <div className={`metric-card relative overflow-hidden ${urgent ? 'cursor-pointer' : ''}`}
      onClick={() => urgent && onMetricClick?.('active')}>
      <div className={`absolute top-0 left-0 right-0 h-1 ${barCls}`} />
      <div className="relative flex items-center gap-4">
        <div className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ backgroundColor: 'transparent', border: `2px solid ${color}` }}>
          <IconBell size={20} style={{ color }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            {urgent && <span className="w-2 h-2 rounded-full bg-warning-500 animate-metric-pulse" />}
            <p className="text-sm font-semibold" style={{ color }}>
              {urgent ? `${count} Approval${count > 1 ? 's' : ''} Pending` : 'No Pending Approvals'}
            </p>
          </div>
          <p className="text-xs" style={{ color: '#7a8ba3' }}>
            {urgent ? 'Click to view incident queue' : 'Approval queue is clear'}
          </p>
        </div>
        <span className="text-3xl font-bold flex-shrink-0" style={{ color }}>
          <AnimatedNumber value={count} />
        </span>
      </div>
    </div>
  )
}
