import { useState, useEffect, useCallback } from 'react'
import axios from 'axios'
import { useGlobalEvents } from '../hooks/useGlobalEvents'
import { parseUTC } from '../utils/dateFormatter'
import {
  IconActivityHeartbeat,
  IconAlertTriangle,
  IconRefresh,
  IconSearch,
  IconTrendingUp,
} from './icons'
import './EventsFeed.css'

export interface QualificationFactors {
  event_type_multiplier: number
  criticality_score: number
  base_event_score: number
  ci_found: boolean
  unknown_ci_policy: string
  environment: string
  environment_multiplier: number
  final_score: number
  qualification_threshold: number
  criticality_floor: number
}

export interface MonitoringEvent {
  event_id: string
  source: string
  event_type: string
  resource_name: string
  raw_criticality: string
  qualification_score: number
  qualified_as_incident: boolean
  incident_workflow_id?: string
  status: string
  detected_at: string
  created_at: string
  qualification_reason: string
  qualification_factors?: QualificationFactors | null
  confidence: number
  signal_value?: number | null
  signal_threshold?: number | null
  anomaly_process?: string | null
  payload_title?: string | null
  payload_description?: string | null
}

// DS-mapped class names — no Tailwind
const CRITICALITY_MAP: Record<string, { label: string; cls: string }> = {
  info:     { label: 'Info',     cls: 'crit-info'     },
  warning:  { label: 'Warning',  cls: 'crit-warning'  },
  high:     { label: 'High',     cls: 'crit-high'     },
  critical: { label: 'Critical', cls: 'crit-critical' },
}

const STATUS_MAP: Record<string, { label: string; cls: string }> = {
  new:       { label: 'New',       cls: 'status-new'       },
  qualified: { label: 'Incident',  cls: 'status-qualified' },
  dismissed: { label: 'Dismissed', cls: 'status-dismissed' },
}

const STATUS_FILTERS = ['all', 'new', 'qualified', 'dismissed']

// darkMode accepted but unused — component is always dark per design system
export default function EventsFeed({ darkMode: _darkMode, onViewWorkflow }: { darkMode?: boolean; onViewWorkflow?: (id: string) => void } = {}) {
  const [events, setEvents] = useState<MonitoringEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('all')
  const [refreshing, setRefreshing] = useState(false)

  const loadEvents = useCallback(async () => {
    try {
      setLoading(true)
      const params = statusFilter !== 'all' ? { status: statusFilter } : {}
      const { data } = await axios.get<MonitoringEvent[]>('/api/monitoring-events', { params })
      setEvents(data || [])
      setError(null)
    } catch {
      setError('Failed to load monitoring events')
    } finally {
      setLoading(false)
    }
  }, [statusFilter])

  // Initial load + re-fetch when filter changes
  useEffect(() => { loadEvents() }, [loadEvents])

  // 30-second poll fallback (covers cases where WS misses events)
  useEffect(() => {
    const iv = setInterval(loadEvents, 30_000)
    return () => clearInterval(iv)
  }, [loadEvents])

  // Real-time refresh via WebSocket global events
  useGlobalEvents(useCallback((ev) => {
    if (ev.type === 'incident_created' || ev.type === 'incident_updated') {
      loadEvents()
    }
  }, [loadEvents]))

  const handleRefresh = async () => {
    setRefreshing(true)
    await loadEvents()
    setRefreshing(false)
  }

  const handleDismiss = async (eventId: string) => {
    try {
      await axios.post(`/api/monitoring-events/${eventId}/dismiss`)
      await loadEvents()
    } catch {
      setError('Failed to dismiss event')
    }
  }

  const filtered = events.filter(e => {
    const q = search.toLowerCase()
    return (
      e.event_type.toLowerCase().includes(q) ||
      e.resource_name.toLowerCase().includes(q) ||
      e.source.toLowerCase().includes(q)
    )
  })

  const stats = {
    total:     events.length,
    qualified: events.filter(e => e.status === 'qualified').length,
    dismissed: events.filter(e => e.status === 'dismissed').length,
  }

  const statCards = [
    { label: 'Total Events', value: stats.total,     accentCls: 'accent-white'  },
    { label: 'Qualified',    value: stats.qualified, accentCls: 'accent-green'  },
    { label: 'Dismissed',    value: stats.dismissed, accentCls: 'accent-muted'  },
  ]

  return (
    <div className="events-feed">

      {/* Page Header */}
      <div className="ef-header">
        <div className="ef-header-left">
          <h1 className="ef-title">Monitoring Events</h1>
          <p className="ef-subtitle">
            Raw signals from Sentinel eBPF monitoring. Events are scored and qualified as incidents when they exceed the threshold.
          </p>
        </div>
        <button
          className={`ef-refresh-btn${refreshing ? ' spinning' : ''}`}
          onClick={handleRefresh}
          disabled={refreshing}
        >
          <IconRefresh size={15} />
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {/* Stats Strip */}
      <div className="ef-stats">
        {statCards.map(s => (
          <div key={s.label} className="ef-stat-card">
            <span className="ef-stat-label">{s.label}</span>
            <span className={`ef-stat-value ${s.accentCls}`}>{s.value}</span>
          </div>
        ))}
      </div>

      {/* Filter Bar */}
      <div className="ef-filter-bar">
        <div className="ef-status-pills">
          {STATUS_FILTERS.map(f => (
            <button
              key={f}
              className={`ef-pill${statusFilter === f ? ' active' : ''}`}
              onClick={() => setStatusFilter(f)}
            >
              {f.charAt(0).toUpperCase() + f.slice(1)}
            </button>
          ))}
        </div>
        <div className="ef-search-wrap">
          <span className="ef-search-icon"><IconSearch size={13} /></span>
          <input
            type="text"
            className="ef-search"
            placeholder="Search events…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* Error Banner */}
      {error && (
        <div className="ef-banner ef-banner-error">
          <IconAlertTriangle size={15} />
          {error}
        </div>
      )}

      {/* Body */}
      {loading ? (
        <div className="ef-skeletons">
          {[...Array(5)].map((_, i) => <div key={i} className="ef-skeleton" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="ef-empty">
          <IconActivityHeartbeat size={44} />
          <p className="ef-empty-title">{search ? 'No matching events' : 'No events yet'}</p>
          <p className="ef-empty-sub">
            {search
              ? 'Try a different search term'
              : 'Monitoring events will appear here as they are detected'}
          </p>
        </div>
      ) : (
        <div className="ef-list">
          {filtered.map(event => {
            const isAllClear = event.event_type === 'condition_cleared'
            const crit     = CRITICALITY_MAP[event.raw_criticality] ?? CRITICALITY_MAP.info
            const statusMeta = STATUS_MAP[event.status] ?? STATUS_MAP.new
            const highConf = event.confidence != null && event.confidence >= 70
            const time     = parseUTC(event.detected_at).toLocaleTimeString()
            const confPct  = event.confidence != null ? event.confidence.toFixed(0) : 'N/A'

            return (
              <div key={event.event_id} className={`ef-event-card${isAllClear ? ' ef-event-card--allclear' : ''}`}>

                {/* Top row: badges + score */}
                <div className="ef-event-top">
                  <div className="ef-event-badges">
                    <code className="ef-event-type">{event.event_type}</code>
                    <span className={`ef-badge ${statusMeta.cls}`}>{statusMeta.label}</span>
                    {isAllClear ? (
                      /* All-clear events are definitive observations — no probabilistic score */
                      <span className="ef-badge ef-badge--allclear">✓ Recovery Signal</span>
                    ) : (
                      <>
                        <span className={`ef-badge ${crit.cls}`}>{crit.label}</span>
                        <span className={`ef-badge ${highConf ? 'conf-high' : 'conf-low'}`}>
                          {highConf ? '✓ High Confidence' : 'Low Confidence'}
                        </span>
                      </>
                    )}
                  </div>
                  {/* Score is meaningless for all-clear events — they bypass qualification */}
                  {!isAllClear && (
                    <div className="ef-score ef-score--tip">
                      {event.qualification_factors ? (
                        <span className="ef-score-tooltip">
                          <span className="ef-qual-math">
                            {event.raw_criticality}
                            <span className="ef-qual-op">×{event.qualification_factors.event_type_multiplier}</span>
                            <span className="ef-qual-arrow">→</span>
                            <span className="ef-qual-base">{event.qualification_factors.base_event_score.toFixed(0)}</span>
                            <span className="ef-qual-op">× {event.qualification_factors.environment}</span>
                            <span className="ef-qual-op">(×{event.qualification_factors.environment_multiplier})</span>
                            <span className="ef-qual-arrow">→</span>
                            <span className="ef-qual-final">{event.qualification_factors.final_score.toFixed(1)}</span>
                            {!event.qualification_factors.ci_found && (
                              <span className="ef-qual-ci-warn"> · CI unknown</span>
                            )}
                          </span>
                          <span className="ef-qual-threshold">threshold {event.qualification_factors.qualification_threshold}</span>
                        </span>
                      ) : event.qualification_reason ? (
                        <span className="ef-score-tooltip">
                          <span className="ef-qual-reason">{event.qualification_reason}</span>
                        </span>
                      ) : null}
                      <span className="ef-score-value">
                        <IconTrendingUp size={13} />
                        {event.qualification_score.toFixed(1)}
                      </span>
                      <span className="ef-score-label">/100</span>
                    </div>
                  )}
                </div>


                {/* Title from payload (richer than event_type alone) */}
                {event.payload_title && (
                  <p className="ef-event-title">{event.payload_title}</p>
                )}

                {/* Full description from payload */}
                {event.payload_description && (
                  <p className="ef-event-reason">{event.payload_description}</p>
                )}

                {/* Meta row: resource · source · severity · signal · time */}
                <div className="ef-event-meta">
                  <span>
                    {event.resource_name} · Source: {event.source} · Severity: {crit.label}
                    {event.signal_value != null && event.signal_value > 0 && event.signal_threshold != null && event.signal_threshold > 0 && (
                      <> · {Number(event.signal_value).toLocaleString('en', { maximumFractionDigits: 1 })} / {Number(event.signal_threshold).toLocaleString('en', { maximumFractionDigits: 1 })} limit</>
                    )}
                    {event.anomaly_process && <> · Process: {event.anomaly_process}</>}
                  </span>
                  <span>
                    Detected {time}
                    {isAllClear
                      ? ' · Watcher confirmed recovery'
                      : ` · Confidence: ${confPct}%`}
                  </span>
                </div>

                {/* Actions row — only when relevant */}
                {(event.status === 'new' || event.incident_workflow_id) && (
                  <div className="ef-event-actions">
                    {event.status === 'new' && (
                      <button
                        className="ef-action-btn ef-dismiss"
                        onClick={() => handleDismiss(event.event_id)}
                      >
                        Dismiss
                      </button>
                    )}
                    {event.incident_workflow_id && (
                      <button
                        className="ef-action-btn ef-view-link"
                        onClick={() => onViewWorkflow?.(event.incident_workflow_id!)}
                      >
                        View Incident →
                      </button>
                    )}
                  </div>
                )}

              </div>
            )
          })}
        </div>
      )}

    </div>
  )
}
