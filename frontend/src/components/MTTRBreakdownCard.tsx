/**
 * MTTRBreakdownCard — clean MTTR breakdown by severity on the dashboard.
 *
 * Three resolution buckets, not two — "auto-remediated" used to be lumped
 * into one column compared against "manual / approved", and a derived
 * "human adds X delay" line subtracted the manual average from the auto
 * average. That compared two unrelated populations (manual incidents are
 * typically harder/different ones, not the same incidents minus an approval
 * step) — it wasn't actually measuring what an approval gate costs.
 * Splitting auto-remediated into "no approval" vs "with approval" (via a
 * join against `approvals`) keeps every column an honest sub-population.
 *
 * Design rules:
 *   • Two accent colours maximum: indigo (card theme) + amber (manual/warning)
 *   • All other text uses the app's standard #e8eef5 / #7a8ba3 palette
 *   • No coloured MTTR values — a single ▲ glyph flags slow manual MTTR
 *   • MTTR duration is the headline number per cell; incident count is
 *     secondary, shown in parentheses — this is an MTTR breakdown, not an
 *     incident-count breakdown
 *   • The "active" (currently open) indicator gets its own fixed-width slot
 *     so it never visually collides with the manual-cell count
 */

import { useState, useEffect, useCallback } from 'react'
import { getMttrBreakdown, MttrBreakdownResponse } from '../services/api'
import { IconClock, IconAlertTriangle } from './icons'
import { useGlobalEvents } from '../hooks/useGlobalEvents'

// ── Config ──────────────────────────────────────────────────────────────────────

const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low'] as const

const SEV_CFG: Record<string, { label: string; dot: string; manualWarnS: number }> = {
  critical: { label: 'Critical', dot: '#ef4444', manualWarnS: 900   }, // > 15 m → warn
  high:     { label: 'High',     dot: '#f59e0b', manualWarnS: 3600  }, // > 1 h  → warn
  medium:   { label: 'Medium',   dot: '#6366f1', manualWarnS: 14400 }, // > 4 h  → warn
  low:      { label: 'Low',      dot: '#7a8ba3', manualWarnS: 86400 }, // > 24 h → warn
  unknown:  { label: 'Unknown',  dot: '#4b5563', manualWarnS: 14400 },
}

const PERIOD_OPTIONS = [
  { label: '1d',  value: 1  },
  { label: '7d',  value: 7  },
  { label: '30d', value: 30 },
  { label: '90d', value: 90 },
]

const LABEL_COL_WIDTH  = '80px'
const ACTIVE_COL_WIDTH = '44px'

// ── Helpers ─────────────────────────────────────────────────────────────────────

function fmt(s: number): string {
  if (s < 60)    return `${Math.round(s)}s`
  if (s < 3600)  return `${Math.round(s / 60)}m`
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`
  return `${Math.round(s / 86400)}d`
}

function MttrCell({ count, avgS, warnS }: { count: number; avgS: number | null; warnS?: number }) {
  if (count <= 0) return <span style={{ color: '#3d4557' }}>—</span>
  const slow = warnS != null && avgS != null && avgS > warnS
  return (
    <div className="flex items-center justify-center gap-1.5">
      <span className="text-sm font-semibold" style={{ color: slow ? '#f59e0b' : '#e8eef5' }}>
        {avgS != null ? fmt(avgS) : '—'}
        {slow && ' ▲'}
      </span>
      <span className="text-xs" style={{ color: slow ? '#f59e0b' : '#7a8ba3' }}>
        ({count})
      </span>
    </div>
  )
}

// ── Component ────────────────────────────────────────────────────────────────────

interface Props { onNavigate?: (view: string) => void }

export default function MTTRBreakdownCard({ onNavigate }: Props) {
  const [data, setData]             = useState<MttrBreakdownResponse | null>(null)
  const [period, setPeriod]         = useState(30)
  const [loading, setLoading]       = useState(true)
  const [fetchError, setFetchError] = useState(false)

  const load = useCallback(async () => {
    try {
      setFetchError(false)
      const res = await getMttrBreakdown(period)
      setData(res.data)
    } catch (err) {
      console.error('[MTTRBreakdown] fetch error:', err)
      setFetchError(true)
    } finally {
      setLoading(false)
    }
  }, [period])

  useEffect(() => { setLoading(true); load() }, [load])

  useGlobalEvents(useCallback((event) => {
    if (['incident_created', 'incident_updated', 'approval_requested'].includes(event.type)) load()
  }, [load]))

  if (loading && !data) return null

  // ── Derived ──────────────────────────────────────────────────────────────────

  const bySev  = data?.by_severity ?? {}
  const byPath = data?.by_path    ?? {}
  const stuck  = data?.stuck_in_approval ?? []

  const displaySeverities: string[] = [
    ...SEVERITY_ORDER.filter(s => s in bySev),
    ...('unknown' in bySev && (bySev['unknown'].active + bySev['unknown'].resolved) > 0 ? ['unknown'] : []),
  ]

  const hasAnyData = Object.values(bySev).some(d => d.active + d.resolved > 0)

  const noApprovalTotal   = byPath['no_approval']?.count ?? 0
  const noApprovalAvg     = byPath['no_approval']?.avg_mttr_s ?? null
  const withApprovalTotal = byPath['with_approval']?.count ?? 0
  const withApprovalAvg   = byPath['with_approval']?.avg_mttr_s ?? null
  const manualTotal       = byPath['manual']?.count ?? 0
  const manualAvg         = byPath['manual']?.avg_mttr_s ?? null

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <div className="metric-card relative overflow-hidden">
      {/* Indigo top bar */}
      <div className="absolute top-0 left-0 right-0 h-1"
        style={{ background: 'linear-gradient(to right, #6366f1, #818cf8)' }} />

      <div className="relative">

        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0"
              style={{ border: '2px solid #6366f1', color: '#818cf8' }}>
              <IconClock size={20} strokeWidth={1.75} />
            </div>
            <div>
              <p className="text-sm font-semibold" style={{ color: '#e8eef5' }}>MTTR Breakdown</p>
              <p className="text-xs" style={{ color: '#7a8ba3' }}>Resolution time by severity &amp; path</p>
            </div>
          </div>
          <div className="flex gap-1">
            {PERIOD_OPTIONS.map(opt => (
              <button key={opt.value} onClick={() => setPeriod(opt.value)}
                className="px-2 py-0.5 rounded text-xs font-medium transition-colors"
                style={{
                  backgroundColor: period === opt.value ? 'rgba(99,102,241,0.18)' : 'transparent',
                  color:            period === opt.value ? '#818cf8' : '#7a8ba3',
                  border: `1px solid ${period === opt.value ? 'rgba(99,102,241,0.35)' : 'transparent'}`,
                }}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* ── Content ──────────────────────────────────────────────────────── */}
        {fetchError ? (
          <p className="text-xs text-center py-6" style={{ color: '#7a8ba3' }}>
            Could not load metrics — check backend connection
          </p>
        ) : !hasAnyData ? (
          <p className="text-xs text-center py-6" style={{ color: '#7a8ba3' }}>
            No incidents in the last {period}d
          </p>
        ) : (
          <>
            {/* Column headers */}
            <div className="flex mb-2" style={{ paddingLeft: LABEL_COL_WIDTH }}>
              <div className="flex-1 text-center">
                <span className="text-xs font-medium uppercase tracking-wide"
                  style={{ color: '#7a8ba3', letterSpacing: '0.06em' }}>
                  Auto · No Approval
                </span>
              </div>
              <div className="flex-1 text-center">
                <span className="text-xs font-medium uppercase tracking-wide"
                  style={{ color: '#7a8ba3', letterSpacing: '0.06em' }}>
                  Auto · With Approval
                </span>
              </div>
              <div className="flex-1 text-center">
                <span className="text-xs font-medium uppercase tracking-wide"
                  style={{ color: '#7a8ba3', letterSpacing: '0.06em' }}>
                  Manual
                </span>
              </div>
              <div className="flex-shrink-0" style={{ width: ACTIVE_COL_WIDTH }} />
            </div>

            {/* Severity rows */}
            <div className="space-y-1 mb-5">
              {displaySeverities.map(sev => {
                const d   = bySev[sev]
                const cfg = SEV_CFG[sev] ?? SEV_CFG['unknown']

                return (
                  <div key={sev}
                    className="flex items-center rounded-lg px-3 py-2.5"
                    style={{ backgroundColor: 'rgba(255,255,255,0.02)' }}>

                    {/* Severity label */}
                    <div className="flex items-center gap-2 flex-shrink-0" style={{ width: LABEL_COL_WIDTH }}>
                      <span className="w-2 h-2 rounded-full flex-shrink-0"
                        style={{ backgroundColor: cfg.dot }} />
                      <span className="text-xs font-medium" style={{ color: '#a0aec0' }}>
                        {cfg.label}
                      </span>
                    </div>

                    {/* Auto · No Approval */}
                    <div className="flex-1 text-center">
                      <MttrCell count={d?.no_approval_count ?? 0} avgS={d?.no_approval_avg_s ?? null} />
                    </div>

                    <div className="w-px h-6 mx-2 flex-shrink-0" style={{ backgroundColor: '#3d4557' }} />

                    {/* Auto · With Approval */}
                    <div className="flex-1 text-center">
                      <MttrCell count={d?.with_approval_count ?? 0} avgS={d?.with_approval_avg_s ?? null} />
                    </div>

                    <div className="w-px h-6 mx-2 flex-shrink-0" style={{ backgroundColor: '#3d4557' }} />

                    {/* Manual */}
                    <div className="flex-1 text-center">
                      <MttrCell count={d?.manual_count ?? 0} avgS={d?.manual_avg_s ?? null} warnS={cfg.manualWarnS} />
                    </div>

                    {/* Active (open) indicator — fixed-width slot so it never collides
                        with the manual-cell count next to it */}
                    <div className="flex items-center justify-end gap-1 flex-shrink-0" style={{ width: ACTIVE_COL_WIDTH }}>
                      {(d?.active ?? 0) > 0 && (
                        <>
                          <span className="w-1.5 h-1.5 rounded-full bg-orange-500 animate-metric-pulse" />
                          <span className="text-xs" style={{ color: '#f97316' }}>{d!.active}</span>
                        </>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>

            {/* Summary strip */}
            {(noApprovalTotal > 0 || withApprovalTotal > 0 || manualTotal > 0) && (
              <div className="flex items-center justify-between rounded-lg px-3 py-2.5"
                style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557' }}>
                <div className="flex items-center gap-1 flex-wrap" style={{ fontSize: '12px' }}>
                  {noApprovalTotal > 0 && (
                    <span>
                      <span style={{ color: '#e8eef5', fontWeight: 600 }}>{noApprovalAvg != null ? fmt(noApprovalAvg) : '—'}</span>
                      <span style={{ color: '#7a8ba3' }}> no-approval auto ({noApprovalTotal})</span>
                    </span>
                  )}
                  {noApprovalTotal > 0 && withApprovalTotal > 0 && (
                    <span style={{ color: '#3d4557', margin: '0 6px' }}>|</span>
                  )}
                  {withApprovalTotal > 0 && (
                    <span>
                      <span style={{ color: '#e8eef5', fontWeight: 600 }}>{withApprovalAvg != null ? fmt(withApprovalAvg) : '—'}</span>
                      <span style={{ color: '#7a8ba3' }}> approved auto ({withApprovalTotal})</span>
                    </span>
                  )}
                  {(noApprovalTotal > 0 || withApprovalTotal > 0) && manualTotal > 0 && (
                    <span style={{ color: '#3d4557', margin: '0 6px' }}>|</span>
                  )}
                  {manualTotal > 0 && (
                    <span>
                      <span style={{ color: '#f59e0b', fontWeight: 600 }}>{manualAvg != null ? fmt(manualAvg) : '—'}</span>
                      <span style={{ color: '#7a8ba3' }}> manual ({manualTotal})</span>
                    </span>
                  )}
                </div>
                {onNavigate && (
                  <button className="text-xs flex-shrink-0"
                    style={{ color: '#6366f1', background: 'none', border: 'none', cursor: 'pointer' }}
                    onClick={() => onNavigate('incidents')}>
                    View all →
                  </button>
                )}
              </div>
            )}
          </>
        )}

        {/* Stuck in approval */}
        {stuck.length > 0 && (
          <div className="mt-4">
            <div className="flex items-center gap-1.5 mb-2">
              <span className="w-1.5 h-1.5 rounded-full bg-warning-500 animate-metric-pulse" />
              <span className="text-xs font-semibold uppercase tracking-wide"
                style={{ color: '#f59e0b', letterSpacing: '0.06em' }}>
                Awaiting Approval — {stuck.length} incident{stuck.length > 1 ? 's' : ''}
              </span>
            </div>
            <div className="space-y-1">
              {stuck.slice(0, 3).map(item => (
                <div key={item.workflow_id}
                  className="flex items-center gap-3 rounded-lg px-3 py-2 cursor-pointer"
                  style={{ backgroundColor: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.18)' }}
                  onClick={() => onNavigate?.('incidents')}>
                  <IconAlertTriangle size={13} style={{ color: '#f59e0b', flexShrink: 0 }} />
                  <span className="flex-1 text-xs truncate" style={{ color: '#e8eef5' }}>
                    {item.incident_number && (
                      <span className="mr-1.5" style={{ color: '#818cf8' }}>{item.incident_number}</span>
                    )}
                    {item.title}
                  </span>
                  <span className="text-xs flex-shrink-0" style={{ color: '#f59e0b' }}>
                    {fmt(item.waiting_s)} waiting
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
