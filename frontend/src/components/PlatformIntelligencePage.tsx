/**
 * Platform Intelligence — dedicated page
 *
 * Four tabs:
 *   Recommendations          — pending / accepted / rejected AI-generated tuning suggestions
 *   Key Performance Indicators — KPI cards with trend vs. the prior run, sourced from the
 *                               kpis snapshot persisted on every analysis run (not just the
 *                               live /health endpoint, which has no history to trend against)
 *   Run History              — every analysis pass (scheduled/manual/force-refresh) with its
 *                               raw LLM response and KPI snapshot, for auditing why a cycle
 *                               behaved a certain way
 *   Config History           — timeline of recommendations that were accepted and applied
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import {
  listRecommendations,
  acceptRecommendation,
  rejectRecommendation,
  getPlatformHealth,
  getConfigHistory,
  triggerAnalysis,
  getAnalysisStatus,
  listRuns,
  getKpiSeries,
} from '../services/api'
import type { PlatformIntelRun, KpiSeriesPoint } from '../services/api'
import type {
  OptimizationRecommendation,
  PlatformHealthMetrics,
  ConfigHistoryEntry,
} from '../types'

interface Props {
  darkMode?: boolean
}

// ── card / section styles ────────────────────────────────────────────────────

const CARD: CSSProperties = {
  backgroundColor: '#1a1f2e',
  border: '1px solid #3d4557',
  borderRadius: '12px',
  overflow: 'hidden',
}
const CARD_HEADER: CSSProperties = {
  padding: '10px 16px',
  borderBottom: '1px solid #3d4557',
}
const LABEL: CSSProperties = {
  fontSize: '10px',
  fontWeight: 600,
  color: '#a0aec0',
  letterSpacing: '0.07em',
  textTransform: 'uppercase',
}
const INNER: CSSProperties = {
  backgroundColor: '#252c3c',
  border: '1px solid #3d4557',
  borderRadius: '8px',
  padding: '12px 14px',
}

// ── severity color palette ───────────────────────────────────────────────────
// Muted enterprise scale — matches EventTypesPage.tsx's SEVERITY_COLORS
// (info/warning/critical), replacing the brighter red/orange/blue/green mix
// this page used to draw from. NOTE: ApprovalQueue.tsx still uses the older,
// more saturated palette (#dc2626/#10b981/...) — not yet migrated to this one.
const SEVERITY = {
  critical: '#a04848', // worst
  high:     '#9a7030',
  medium:   '#4070a0', // neutral / informational
  low:      '#4a8a63', // best
} as const

// ── helpers ──────────────────────────────────────────────────────────────────

function priorityColor(p: string): string {
  if (p === 'high')   return '#a04848'
  if (p === 'medium') return '#9a7030'
  return '#6b7280'
}

function confidenceBar(c: number) {
  const pct = Math.round(c * 100)
  const col = pct >= 80 ? '#4a8a63' : pct >= 60 ? '#4070a0' : '#9a7030'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
      <div style={{ flex: 1, height: '4px', backgroundColor: '#252c3c', borderRadius: '999px', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', backgroundColor: col, borderRadius: '999px' }} />
      </div>
      <span style={{ fontSize: '11px', color: col, width: '30px', textAlign: 'right' }}>{pct}%</span>
    </div>
  )
}

function fmtPct(n: number | null | undefined) { return n == null ? '—' : `${(n * 100).toFixed(1)}%` }
function fmtHours(h: number | null) { return h == null ? '—' : `${h.toFixed(1)}h` }

const categoryLabel: Record<string, string> = {
  threshold:        'Threshold',
  factor_weight:    'Factor Weight',
  event_multiplier: 'Event Multiplier',
  missing_data:     'Missing Data Policy',
  resource_specific:'Resource Override',
  governance:       'Governance',
  runbook_step:     'Runbook Step',
  general:          'General',
}

// ── Recommendation card ──────────────────────────────────────────────────────

function RecommendationCard({
  rec,
  onAccept,
  onReject,
}: {
  rec: OptimizationRecommendation
  onAccept: (id: string) => Promise<void>
  onReject: (id: string) => void
}) {
  const [loading, setLoading] = useState(false)
  const priColor = priorityColor(rec.priority)

  const handleAccept = async () => {
    setLoading(true)
    try { await onAccept(rec.id) } finally { setLoading(false) }
  }

  const statusBadge =
    rec.status === 'accepted'     ? { label: 'Accepted',     color: '#4a8a63', bg: 'rgba(74,138,99,0.12)' }
    : rec.status === 'rejected'   ? { label: 'Rejected',     color: '#6b7280', bg: 'rgba(107,114,128,0.12)' }
    : rec.status === 'expired'    ? { label: 'Expired',      color: '#4b5563', bg: 'rgba(75,85,99,0.12)' }
    : rec.status === 'auto_applied' ? { label: '⚡ Auto-Applied', color: '#8a6fa8', bg: 'rgba(138,111,168,0.14)' }
    : null

  return (
    <div style={{
      ...CARD,
      borderLeft: `3px solid ${priColor}`,
      opacity: rec.status === 'expired' ? 0.5 : 1,
    }}>
      {/* Header */}
      <div style={{ padding: '14px 16px', borderBottom: '1px solid #2a3145' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px', flexWrap: 'wrap' }}>
              <span style={{
                fontSize: '9px', fontWeight: 700, letterSpacing: '0.06em',
                color: priColor, backgroundColor: `${priColor}18`,
                border: `1px solid ${priColor}30`, borderRadius: '4px', padding: '1px 6px',
                textTransform: 'uppercase',
              }}>
                {rec.priority}
              </span>
              <span style={{
                fontSize: '9px', fontWeight: 600, color: '#64748b',
                backgroundColor: 'rgba(100,116,139,0.12)', border: '1px solid #2a3145',
                borderRadius: '4px', padding: '1px 6px', textTransform: 'uppercase',
              }}>
                {categoryLabel[rec.category] ?? rec.category}
              </span>
              {statusBadge && (
                <span style={{
                  fontSize: '9px', fontWeight: 700, color: statusBadge.color,
                  backgroundColor: statusBadge.bg, borderRadius: '4px', padding: '1px 6px',
                  textTransform: 'uppercase',
                }}>
                  {statusBadge.label}
                  {rec.applied && rec.status === 'accepted' ? ' · Applied' : ''}
                </span>
              )}
            </div>
            <h3 style={{ fontSize: '14px', fontWeight: 600, color: '#e8eef5', margin: 0 }}>
              {rec.title}
            </h3>
          </div>

          {/* Accept/Reject buttons — only on pending */}
          {rec.status === 'pending' && (
            <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
              <button
                onClick={handleAccept}
                disabled={loading}
                style={{
                  fontSize: '11px', fontWeight: 600, padding: '4px 12px',
                  backgroundColor: 'rgba(74,138,99,0.15)', color: '#4a8a63',
                  border: '1px solid rgba(74,138,99,0.35)', borderRadius: '6px',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  opacity: loading ? 0.6 : 1,
                }}
              >
                {loading ? '…' : 'Accept'}
              </button>
              <button
                onClick={() => onReject(rec.id)}
                disabled={loading}
                style={{
                  fontSize: '11px', fontWeight: 600, padding: '4px 12px',
                  backgroundColor: 'rgba(107,114,128,0.12)', color: '#9ca3af',
                  border: '1px solid #2a3145', borderRadius: '6px',
                  cursor: loading ? 'not-allowed' : 'pointer',
                }}
              >
                Dismiss
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: '12px 16px' }} className="space-y-3">
        {/* Rationale */}
        <p style={{ fontSize: '12px', color: '#a0aec0', lineHeight: 1.5, margin: 0 }}>
          {rec.rationale}
        </p>

        {/* Parameter + value change */}
        <div>
          {(() => {
            // Multi-parameter change — evidence.parameter_changes is a list
            const multiChanges = rec.evidence?.parameter_changes as
              Array<{ parameter: string; current_value: any; suggested_value: any; label?: string }> | undefined

            if (multiChanges && multiChanges.length > 0) {
              return (
                <div className="space-y-2">
                  {multiChanges.map((change) => (
                    <div key={change.parameter} style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <code style={{
                        fontSize: '10px', color: '#8a6fa8',
                        backgroundColor: 'rgba(138,111,168,0.1)',
                        border: '1px solid rgba(138,111,168,0.2)',
                        borderRadius: '4px', padding: '2px 7px',
                        fontFamily: 'ui-monospace, monospace',
                        whiteSpace: 'nowrap', flexShrink: 0, minWidth: '200px',
                      }}>
                        {change.parameter}
                      </code>
                      <div style={{ ...INNER, padding: '4px 10px', flexShrink: 0 }}>
                        <span style={{ fontSize: '12px', fontWeight: 700, color: '#9ca3af' }}>
                          {JSON.stringify(change.current_value)}
                        </span>
                      </div>
                      <span style={{ color: '#3d4557', fontSize: '14px', flexShrink: 0 }}>→</span>
                      <div style={{ ...INNER, padding: '4px 10px', borderColor: '#4a8a63', borderLeftWidth: '2px', flexShrink: 0 }}>
                        <span style={{ fontSize: '12px', fontWeight: 700, color: '#4a8a63' }}>
                          {JSON.stringify(change.suggested_value)}
                        </span>
                      </div>
                      {change.label && (
                        <span style={{ fontSize: '10px', color: '#4b5563' }}>{change.label}</span>
                      )}
                    </div>
                  ))}
                </div>
              )
            }

            // Single-parameter change
            if (rec.suggested_value !== null) {
              return (
                <div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                    <span style={LABEL}>Parameter</span>
                    <code style={{
                      fontSize: '11px', color: '#8a6fa8',
                      backgroundColor: 'rgba(138,111,168,0.1)',
                      border: '1px solid rgba(138,111,168,0.2)',
                      borderRadius: '4px', padding: '2px 7px',
                      fontFamily: 'ui-monospace, monospace',
                    }}>
                      {rec.parameter}
                    </code>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div style={{ ...INNER, flex: 1 }}>
                      <p style={LABEL}>Current value</p>
                      <p style={{ fontSize: '15px', fontWeight: 700, color: '#e8eef5', marginTop: '2px' }}>
                        {JSON.stringify(rec.current_value)}
                      </p>
                    </div>
                    <span style={{ color: '#3d4557', fontSize: '18px' }}>→</span>
                    <div style={{ ...INNER, flex: 1, borderColor: '#4a8a63', borderLeftWidth: '2px' }}>
                      <p style={LABEL}>Suggested value</p>
                      <p style={{ fontSize: '15px', fontWeight: 700, color: '#4a8a63', marginTop: '2px' }}>
                        {JSON.stringify(rec.suggested_value)}
                      </p>
                    </div>
                  </div>
                </div>
              )
            }

            // Informational — no config change
            return (
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                  <span style={LABEL}>Parameter</span>
                  <code style={{
                    fontSize: '11px', color: '#8a6fa8',
                    backgroundColor: 'rgba(138,111,168,0.1)',
                    border: '1px solid rgba(138,111,168,0.2)',
                    borderRadius: '4px', padding: '2px 7px',
                    fontFamily: 'ui-monospace, monospace',
                  }}>
                    {rec.parameter}
                  </code>
                </div>
                <p style={{ fontSize: '11px', color: '#4b5563', fontStyle: 'italic', margin: 0 }}>
                  Informational — no automatic config change. Accept to acknowledge and track.
                </p>
              </div>
            )
          })()}
        </div>

        {/* Impact + confidence */}
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
          {rec.impact && (
            <span style={{ fontSize: '11px', color: '#7a8ba3', flex: 1 }}>
              💡 {rec.impact}
            </span>
          )}
          <div style={{ width: '160px' }}>
            {confidenceBar(rec.confidence)}
          </div>
        </div>

        {/* Review note */}
        {rec.status === 'rejected' && rec.review_reason && (
          <p style={{ fontSize: '11px', color: '#6b7280', fontStyle: 'italic', margin: 0 }}>
            Dismissed: {rec.review_reason}
          </p>
        )}
        {rec.reviewed_at && (
          <p style={{ fontSize: '10px', color: '#4b5563', margin: 0 }}>
            {rec.status === 'accepted' ? 'Accepted' : 'Dismissed'} by {rec.reviewed_by || 'admin'} ·{' '}
            {new Date(rec.reviewed_at).toLocaleString()}
          </p>
        )}
      </div>
    </div>
  )
}

// ── Reject modal ─────────────────────────────────────────────────────────────

function RejectModal({
  recId,
  onConfirm,
  onCancel,
}: {
  recId: string
  onConfirm: (id: string, reason: string) => void
  onCancel: () => void
}) {
  const [reason, setReason] = useState('')
  return (
    <div style={{
      position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }}>
      <div style={{ ...CARD, width: '420px', padding: '20px' }}>
        <h3 style={{ color: '#e8eef5', fontSize: '15px', fontWeight: 600, marginBottom: '12px' }}>
          Dismiss recommendation
        </h3>
        <p style={{ color: '#a0aec0', fontSize: '12px', marginBottom: '12px' }}>
          Optionally add a reason — this helps the agent learn.
        </p>
        <textarea
          value={reason}
          onChange={e => setReason(e.target.value)}
          placeholder="e.g. We intentionally keep a high threshold for this environment…"
          style={{
            width: '100%', backgroundColor: '#252c3c', border: '1px solid #3d4557',
            borderRadius: '6px', color: '#e8eef5', fontSize: '12px',
            padding: '8px 10px', resize: 'vertical', minHeight: '80px',
          }}
        />
        <div style={{ display: 'flex', gap: '8px', marginTop: '14px', justifyContent: 'flex-end' }}>
          <button onClick={onCancel} style={{
            fontSize: '12px', padding: '6px 14px', backgroundColor: 'rgba(107,114,128,0.12)',
            color: '#9ca3af', border: '1px solid #2a3145', borderRadius: '6px', cursor: 'pointer',
          }}>
            Cancel
          </button>
          <button onClick={() => onConfirm(recId, reason)} style={{
            fontSize: '12px', padding: '6px 14px', backgroundColor: 'rgba(160,72,72,0.15)',
            color: '#a04848', border: '1px solid rgba(160,72,72,0.3)', borderRadius: '6px', cursor: 'pointer',
          }}>
            Dismiss
          </button>
        </div>
      </div>
    </div>
  )
}

function ConfirmModal({
  title,
  message,
  confirmLabel = 'Confirm',
  onConfirm,
  onCancel,
}: {
  title: string
  message: string
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <div style={{
      position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.7)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
    }}>
      <div style={{ ...CARD, width: '420px', padding: '20px' }}>
        <h3 style={{ color: '#e8eef5', fontSize: '15px', fontWeight: 600, marginBottom: '12px' }}>
          {title}
        </h3>
        <p style={{ color: '#a0aec0', fontSize: '12px', marginBottom: '0', lineHeight: 1.5 }}>
          {message}
        </p>
        <div style={{ display: 'flex', gap: '8px', marginTop: '18px', justifyContent: 'flex-end' }}>
          <button onClick={onCancel} style={{
            fontSize: '12px', padding: '6px 14px', backgroundColor: 'rgba(107,114,128,0.12)',
            color: '#9ca3af', border: '1px solid #2a3145', borderRadius: '6px', cursor: 'pointer',
          }}>
            Cancel
          </button>
          <button onClick={onConfirm} style={{
            fontSize: '12px', padding: '6px 14px', backgroundColor: 'rgba(91,106,160,0.15)',
            color: '#5b6aa0', border: '1px solid rgba(91,106,160,0.35)', borderRadius: '6px', cursor: 'pointer',
          }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Sparkline ────────────────────────────────────────────────────────────────
// Lightweight inline-SVG trend line — no charting dependency needed for a
// per-tile sparkline. Plots the KPI's value across every persisted analysis
// run in the trend window, so a tile shows a shape over time, not just one
// number with an up/down arrow against the single prior run.
function Sparkline({ values, color, width = 120, height = 32 }: { values: number[]; color: string; width?: number; height?: number }) {
  const pts = values.filter(v => typeof v === 'number' && !Number.isNaN(v))
  if (pts.length < 2) return null

  const min = Math.min(...pts)
  const max = Math.max(...pts)
  const range = max - min || 1
  const pad = 3
  const stepX = (width - pad * 2) / (pts.length - 1)
  const coords = pts.map((v, i) => {
    const x = pad + i * stepX
    const y = pad + (height - pad * 2) * (1 - (v - min) / range)
    return [x, y]
  })
  const linePath = coords.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  const areaPath = `${linePath} L${coords[coords.length - 1][0].toFixed(1)},${height - pad} L${coords[0][0].toFixed(1)},${height - pad} Z`
  const [lastX, lastY] = coords[coords.length - 1]

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: 'block' }}>
      <path d={areaPath} fill={color} opacity={0.12} stroke="none" />
      <path d={linePath} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lastX} cy={lastY} r={2.2} fill={color} />
    </svg>
  )
}

// ── Tooltip (info icon + native title attr) ─────────────────────────────────
// Keeps the label row uncluttered — hover the ⓘ to see what the metric
// measures and how it's calculated, instead of needing separate docs.
function InfoTip({ text }: { text: string }) {
  return (
    <span
      title={text}
      style={{
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        width: '12px', height: '12px', borderRadius: '50%',
        fontSize: '9px', lineHeight: 1, color: '#7a8ba3',
        border: '1px solid #4b5563', cursor: 'help', marginLeft: '5px',
        flexShrink: 0,
      }}
    >
      i
    </span>
  )
}

// ── Loading overlay ──────────────────────────────────────────────────────────
// Full-page spinner shown for the duration of an analysis run (now a Celery
// job that can take a while on a large dataset) — without this, "Run Analysis
// Now" gave no feedback beyond a disabled button until the request returned.
function LoadingOverlay({ message }: { message: string }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, backgroundColor: 'rgba(10,14,22,0.72)',
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: '16px', zIndex: 2000,
    }}>
      <style>{'@keyframes pi-spin { to { transform: rotate(360deg); } }'}</style>
      <div style={{
        width: '44px', height: '44px', borderRadius: '50%',
        border: '3px solid rgba(91,106,160,0.2)', borderTopColor: '#5b6aa0',
        animation: 'pi-spin 0.8s linear infinite',
      }} />
      <p style={{ color: '#e8eef5', fontSize: '13px', fontWeight: 600 }}>{message}</p>
    </div>
  )
}

// ── Metric tile ──────────────────────────────────────────────────────────────

function MetricTile({
  label, value, sub, accent, trend, tooltip, series, sectionColor,
}: {
  label: string; value: string; sub?: string; accent?: string; trend?: ReactNode
  tooltip?: string; series?: number[]
  // Left border denotes which theme/section this tile belongs to (constant per
  // section) — kept separate from `accent`, which still colors the value text
  // and sparkline by that metric's own health/severity.
  sectionColor?: string
}) {
  return (
    <div style={{ ...INNER, borderLeft: `3px solid ${sectionColor || accent || '#3d4557'}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ display: 'flex', alignItems: 'center' }}>
          <p style={LABEL}>{label}</p>
          {tooltip && <InfoTip text={tooltip} />}
        </span>
        {trend}
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: '8px', marginTop: '4px' }}>
        <div>
          <p style={{ fontSize: '22px', fontWeight: 800, color: accent || '#e8eef5', lineHeight: 1 }}>
            {value}
          </p>
          {sub && <p style={{ fontSize: '10px', color: '#7a8ba3', marginTop: '2px' }}>{sub}</p>}
        </div>
        {series && series.length >= 2 && (
          <Sparkline values={series} color={accent || '#64748b'} />
        )}
      </div>
    </div>
  )
}

/**
 * Small trend indicator comparing the current KPI snapshot against the
 * previous analysis run — the only way to show "is this improving" rather
 * than just the instantaneous value, which is all the old /health endpoint
 * could ever show (it has no history to compare against).
 */
function KpiTrend({
  current, previous, higherIsBetter, pct = false,
}: { current: number | null; previous: number | null; higherIsBetter: boolean; pct?: boolean }) {
  if (current == null || previous == null || previous === current) return null
  const delta = current - previous
  const improved = higherIsBetter ? delta > 0 : delta < 0
  const arrow = delta > 0 ? '▲' : '▼'
  const color = improved ? '#4a8a63' : '#a04848'
  const deltaStr = pct
    ? `${Math.abs(delta * 100).toFixed(1)}pp`
    : Math.abs(delta).toFixed(Math.abs(delta) < 1 ? 2 : 1)
  return (
    <span
      style={{ fontSize: '10px', fontWeight: 700, color, display: 'inline-flex', alignItems: 'baseline', gap: '3px' }}
      title="Change vs. the immediately previous analysis run — may differ from the whole-window trend described below"
    >
      <span>{arrow} {deltaStr}</span>
      <span style={{ fontSize: '9px', fontWeight: 500, color: '#5b6472' }}>last run</span>
    </span>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

type TabId = 'recommendations' | 'health' | 'runs' | 'history'

export default function PlatformIntelligencePage({ darkMode: _darkMode }: Props) {
  const [tab, setTab] = useState<TabId>('health')
  const [filterStatus, setFilterStatus] = useState<string>('pending')
  const [recs, setRecs] = useState<OptimizationRecommendation[]>([])
  const [health, setHealth] = useState<PlatformHealthMetrics | null>(null)
  const [history, setHistory] = useState<ConfigHistoryEntry[]>([])
  const [kpiSeries, setKpiSeries] = useState<KpiSeriesPoint[]>([])
  const [runs, setRuns] = useState<PlatformIntelRun[]>([])
  const [expandedRunId, setExpandedRunId] = useState<string | null>(null)
  const [loadingRecs, setLoadingRecs] = useState(false)
  const [loadingHealth, setLoadingHealth] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeResult, setAnalyzeResult] = useState<string | null>(null)
  const [analyzeSource, setAnalyzeSource] = useState<string | null>(null)
  const [rejectTarget, setRejectTarget] = useState<string | null>(null)
  const [showForceRefreshConfirm, setShowForceRefreshConfirm] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadRecs = useCallback(async () => {
    setLoadingRecs(true)
    setError(null)
    try {
      const res = await listRecommendations(filterStatus || undefined)
      setRecs(res.data)
    } catch (e: any) {
      setError('Failed to load recommendations')
    } finally {
      setLoadingRecs(false)
    }
  }, [filterStatus])

  const loadHealth = useCallback(async () => {
    setLoadingHealth(true)
    try {
      const [healthRes, kpiRes] = await Promise.all([
        getPlatformHealth(30),
        getKpiSeries(90),
      ])
      setHealth(healthRes.data)
      setKpiSeries(kpiRes.data.points)
    } catch { /* ignore */ }
    finally { setLoadingHealth(false) }
  }, [])

  const loadHistory = useCallback(async () => {
    setLoadingHistory(true)
    try {
      const res = await getConfigHistory(30)
      setHistory(res.data)
    } catch { /* ignore */ }
    finally { setLoadingHistory(false) }
  }, [])

  const loadRuns = useCallback(async () => {
    setLoadingRuns(true)
    try {
      const res = await listRuns(20, 0)
      setRuns(res.data.runs)
    } catch { /* ignore */ }
    finally { setLoadingRuns(false) }
  }, [])

  useEffect(() => { loadRecs() }, [loadRecs])
  useEffect(() => { if (tab === 'health') loadHealth() }, [tab, loadHealth])
  useEffect(() => { if (tab === 'history') loadHistory() }, [tab, loadHistory])
  useEffect(() => { if (tab === 'runs') loadRuns() }, [tab, loadRuns])

  const handleAccept = async (id: string) => {
    await acceptRecommendation(id)
    loadRecs()
  }

  const handleRejectConfirm = async (id: string, reason: string) => {
    setRejectTarget(null)
    await rejectRecommendation(id, 'admin', reason)
    loadRecs()
  }

  // Run Analysis / Force Refresh now executes as a background Celery job rather
  // than a synchronous HTTP request — a large incident window (LLM call +
  // aggregation) can take long enough that holding the request open risks a
  // gateway timeout. This dispatches the job, then polls for completion while
  // the page-level spinner (analyzing=true) stays up.
  const pollAttemptsRef = useRef(0)
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])

  const POLL_INTERVAL_MS = 2000
  const MAX_POLL_ATTEMPTS = 600 // 20 minutes — matches the Celery task's hard time limit

  const finishAnalyze = (d: {
    recommendations_generated?: number; incidents_analysed?: number
    recommendations_skipped_duplicate?: number; recommendations_expired?: number
    min_incidents_needed?: number; reason?: string; source?: string
  }) => {
    const generated: number = d.recommendations_generated ?? 0
    const analysed: number  = d.incidents_analysed ?? 0
    const skipped: number   = d.recommendations_skipped_duplicate ?? 0
    const expired: number   = d.recommendations_expired ?? 0
    const minNeeded: number = d.min_incidents_needed ?? 5
    const reason: string    = d.reason ?? 'ok'
    const source: string    = d.source ?? reason

    setAnalyzeSource(source)

    let message = `Analysis complete — ${analysed} incident${analysed !== 1 ? 's' : ''} analysed`

    if (generated > 0) {
      message += `, ${generated} new recommendation${generated !== 1 ? 's' : ''} generated`
      if (skipped > 0) message += ` (${skipped} suppressed — cooldown/dedup)`
      if (expired > 0) message += `, ${expired} stale expired`
    } else {
      if (reason === 'insufficient_data') {
        message += ` — need at least ${minNeeded} resolved incidents (${analysed} found)`
      } else if (reason === 'all_pending') {
        message += ` — ${skipped} recommendation${skipped !== 1 ? 's' : ''} already pending review (accept or reject them first)`
      } else if (reason === 'healthy') {
        message += ` — all metrics within healthy thresholds, no tuning needed`
        if (expired > 0) message += ` (${expired} stale recs expired)`
      } else if (reason === 'suppressed') {
        message += ` — ${skipped} recommendation${skipped !== 1 ? 's' : ''} suppressed (recently accepted/rejected — cooldown active)`
      } else {
        message += `, 0 new recommendations`
        if (skipped > 0) message += ` (${skipped} suppressed — cooldown/dedup)`
      }
    }

    setAnalyzeResult(message)
    loadRecs()
    if (tab === 'health') loadHealth()
    if (tab === 'runs') loadRuns()
  }

  const pollJob = (jobId: string) => {
    if (!mountedRef.current) return
    pollAttemptsRef.current += 1
    getAnalysisStatus(jobId).then(res => {
      if (!mountedRef.current) return
      const { state, result } = res.data
      if (state === 'SUCCESS') {
        finishAnalyze((result as any) ?? {})
        setAnalyzing(false)
      } else if (state === 'FAILURE') {
        setAnalyzeSource('error')
        setAnalyzeResult('Analysis failed: ' + ((result as any)?.reason ?? 'unknown error'))
        setAnalyzing(false)
      } else if (pollAttemptsRef.current >= MAX_POLL_ATTEMPTS) {
        setAnalyzeSource('error')
        setAnalyzeResult('Analysis is taking longer than expected — check Run History shortly.')
        setAnalyzing(false)
      } else {
        setTimeout(() => pollJob(jobId), POLL_INTERVAL_MS)
      }
    }).catch(() => {
      if (!mountedRef.current) return
      if (pollAttemptsRef.current >= MAX_POLL_ATTEMPTS) {
        setAnalyzeSource('error')
        setAnalyzeResult('Lost track of the analysis job — check Run History shortly.')
        setAnalyzing(false)
      } else {
        setTimeout(() => pollJob(jobId), POLL_INTERVAL_MS)
      }
    })
  }

  const handleAnalyze = async (ignoreCooldown = false) => {
    setAnalyzing(true)
    setAnalyzeResult(null)
    setAnalyzeSource(null)
    pollAttemptsRef.current = 0
    try {
      const res = await triggerAnalysis(30, ignoreCooldown)
      pollJob(res.data.job_id)
    } catch (e: any) {
      setAnalyzeSource('error')
      setAnalyzeResult('Failed to start analysis: ' + (e?.response?.data?.detail ?? e.message))
      setAnalyzing(false)
    }
  }

  const tabs: { id: TabId; label: string }[] = [
    { id: 'health',          label: 'Key Performance Indicators' },
    { id: 'recommendations', label: 'Recommendations' },
    { id: 'runs',            label: 'Run History' },
    { id: 'history',         label: 'Config History' },
  ]

  return (
    <div className="space-y-6">
      {analyzing && <LoadingOverlay message="Running Platform Intelligence analysis…" />}
      {/* Page title */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '22px', fontWeight: 800, color: '#e8eef5', margin: 0 }}>
            Platform Intelligence
          </h1>
          <p style={{ fontSize: '13px', color: '#7a8ba3', marginTop: '4px' }}>
            Observe outcomes · Identify patterns · Tune configuration
          </p>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button
            onClick={() => handleAnalyze(false)}
            disabled={analyzing}
            style={{
              fontSize: '12px', fontWeight: 600, padding: '8px 16px',
              backgroundColor: 'rgba(91,106,160,0.15)', color: '#5b6aa0',
              border: '1px solid rgba(91,106,160,0.35)', borderRadius: '8px',
              cursor: analyzing ? 'not-allowed' : 'pointer',
              opacity: analyzing ? 0.7 : 1,
            }}
          >
            {analyzing ? '⟳ Analysing…' : '⟳ Run Analysis Now'}
          </button>
          <button
            onClick={() => setShowForceRefreshConfirm(true)}
            disabled={analyzing}
            title="Bypasses the 30-day accepted / 14-day rejected cooldown for this run only. Decision history is preserved — nothing is deleted."
            style={{
              fontSize: '12px', fontWeight: 600, padding: '8px 16px',
              backgroundColor: 'rgba(107,114,128,0.12)', color: '#9ca3af',
              border: '1px solid #2a3145', borderRadius: '8px',
              cursor: analyzing ? 'not-allowed' : 'pointer',
              opacity: analyzing ? 0.7 : 1,
            }}
          >
            ⟳ Force Refresh
          </button>
        </div>
      </div>

      {analyzeResult && (() => {
        const isError = analyzeResult.startsWith('Analysis failed')
        const sourceMeta: Record<string, { label: string; color: string; bg: string }> = {
          llm:               { label: '✦ AI Analysis',    color: '#8a6fa8', bg: 'rgba(138,111,168,0.15)' },
          rules:             { label: '⚙ Rule-based',     color: '#4070a0', bg: 'rgba(64,112,160,0.12)' },
          healthy:           { label: '✓ Healthy',        color: '#4a8a63', bg: 'rgba(74,138,99,0.12)' },
          suppressed:        { label: '⏸ Cooldown',       color: '#9a7030', bg: 'rgba(154,112,48,0.12)' },
          insufficient_data: { label: '⚠ Low Data',      color: '#9a7030', bg: 'rgba(154,112,48,0.12)' },
          error:             { label: '✕ Error',          color: '#a04848', bg: 'rgba(160,72,72,0.12)'  },
        }
        const meta = isError ? sourceMeta.error : (sourceMeta[analyzeSource ?? ''] ?? sourceMeta.rules)
        return (
          <div style={{
            backgroundColor: isError ? 'rgba(160,72,72,0.1)' : 'rgba(74,138,99,0.1)',
            border: `1px solid ${isError ? 'rgba(160,72,72,0.3)' : 'rgba(74,138,99,0.3)'}`,
            borderRadius: '8px', padding: '10px 14px',
            fontSize: '12px', display: 'flex', alignItems: 'center', gap: '10px',
            color: isError ? '#a04848' : '#4a8a63',
          }}>
            <span style={{
              fontSize: '11px', fontWeight: 700, padding: '2px 8px',
              borderRadius: '999px', whiteSpace: 'nowrap',
              color: meta.color, backgroundColor: meta.bg,
              border: `1px solid ${meta.color}40`,
            }}>
              {meta.label}
            </span>
            <span>{analyzeResult}</span>
          </div>
        )
      })()}

      {/* Tabs — sticky below the app's own sticky header (4rem/64px tall) so
          switching tabs from partway down a long KPI page doesn't require
          scrolling back to the top first. */}
      <div style={{
        display: 'flex', gap: '4px', borderBottom: '1px solid #2a3145', paddingBottom: '0',
        position: 'sticky', top: '64px', zIndex: 40, backgroundColor: '#1a1f2e',
      }}>
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              fontSize: '13px', fontWeight: 600,
              padding: '8px 16px',
              color: tab === t.id ? '#5b6aa0' : '#64748b',
              borderBottom: tab === t.id ? '2px solid #5b6aa0' : '2px solid transparent',
              backgroundColor: 'transparent',
              cursor: 'pointer',
              transition: 'color 0.15s',
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Recommendations tab ─────────────────────────────────────────────── */}
      {tab === 'recommendations' && (
        <div className="space-y-4">
          {/* Filter bar */}
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            {(['pending', 'accepted', 'rejected', ''].map((s) => (
              <button
                key={s}
                onClick={() => setFilterStatus(s)}
                style={{
                  fontSize: '11px', fontWeight: 600, padding: '4px 12px',
                  borderRadius: '6px', cursor: 'pointer',
                  backgroundColor: filterStatus === s
                    ? 'rgba(91,106,160,0.2)' : 'rgba(107,114,128,0.1)',
                  color: filterStatus === s ? '#5b6aa0' : '#6b7280',
                  border: filterStatus === s ? '1px solid rgba(91,106,160,0.4)' : '1px solid #2a3145',
                }}
              >
                {s === '' ? 'All' : s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            )))}
          </div>

          {error && (
            <p style={{ color: '#a04848', fontSize: '12px' }}>{error}</p>
          )}

          {loadingRecs ? (
            <p style={{ color: '#64748b', fontSize: '13px' }}>Loading…</p>
          ) : recs.length === 0 ? (
            <div style={{ ...CARD, padding: '32px', textAlign: 'center' }}>
              {filterStatus === 'pending' ? (
                <div>
                  <p style={{ color: '#64748b', fontSize: '14px', marginBottom: '12px' }}>
                    No pending recommendations.
                  </p>
                  <p style={{ color: '#4b5563', fontSize: '12px', lineHeight: 1.6 }}>
                    Click <strong style={{ color: '#5b6aa0' }}>⟳ Run Analysis Now</strong> to scan the last 30 days of incident data.<br />
                    The analysis needs at least <strong style={{ color: '#a0aec0' }}>5 resolved incidents</strong> and detects patterns like
                    high false-positive rates, low automation, or poor CMDB coverage.<br />
                    If all metrics are healthy — or existing pending recs haven't been reviewed yet — 0 recommendations are generated.
                  </p>
                </div>
              ) : (
                <p style={{ color: '#64748b', fontSize: '14px' }}>
                  No recommendations found for this filter.
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-3">
              {recs.map(rec => (
                <RecommendationCard
                  key={rec.id}
                  rec={rec}
                  onAccept={handleAccept}
                  onReject={(id) => setRejectTarget(id)}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Key Performance Indicators tab ──────────────────────────────────── */}
      {tab === 'health' && (
        <div className="space-y-4">
          {loadingHealth ? (
            <p style={{ color: '#64748b', fontSize: '13px' }}>Loading…</p>
          ) : health ? (
            <>
              {(() => {
                const latest = kpiSeries.length ? kpiSeries[kpiSeries.length - 1].kpis : null
                const prev   = kpiSeries.length > 1 ? kpiSeries[kpiSeries.length - 2].kpis : null
                const oldest = kpiSeries.length ? kpiSeries[0].kpis : null
                const num = (k: Record<string, any> | null, key: string): number | null => {
                  const v = k?.[key]
                  return typeof v === 'number' ? v : null
                }
                // Full-window series for sparklines — every persisted run, not just current vs. prior.
                const series = (key: string): number[] =>
                  kpiSeries.map(p => p.kpis?.[key]).filter((v): v is number => typeof v === 'number')

                // Prefer the persisted KPI snapshot (has trend history); fall back to the
                // live /health endpoint until the first run after this feature lands.
                const automationRate = num(latest, 'automation_rate') ?? health.automation_rate
                const falsePositive  = num(latest, 'false_positive_rate') ?? health.false_positive_rate
                const mttrAll        = num(latest, 'mttr_all_hours') ?? health.avg_mttr_hours
                const mttrP1P2       = num(latest, 'mttr_p1p2_hours') ?? health.p1p2_avg_mttr_hours
                const cmdbCoverage   = num(latest, 'cmdb_coverage') ?? health.avg_cmdb_coverage
                const qualificationRate = num(latest, 'qualification_rate')
                const meanTimeToApproval = num(latest, 'mean_time_to_approval_minutes')
                const recAcceptanceRate = num(latest, 'recommendation_acceptance_rate')
                const autoApplyTrust    = num(latest, 'auto_apply_trust_coverage')
                const govBypassRate     = num(latest, 'governance_bypass_rate')
                const remediationFail   = num(latest, 'remediation_failure_rate')
                const runbookStepFail   = num(latest, 'runbook_step_failure_rate')
                const runbookStepCats: Record<string, number> =
                  (latest?.['runbook_step_failure_categories'] as Record<string, number>) || {}

                const findRec = (param: string) =>
                  recs.find(r => r.parameter === param && r.status === 'pending')

                const recLink = (param: string) => {
                  const rec = findRec(param)
                  if (!rec) return null
                  return (
                    <button
                      onClick={() => { setFilterStatus('pending'); setTab('recommendations') }}
                      style={{
                        marginLeft: '6px', fontSize: '11px', color: '#5b6aa0',
                        background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline', padding: 0,
                      }}
                    >
                      → See recommendation
                    </button>
                  )
                }

                // Trend over the *whole* window (oldest persisted run → latest), not just
                // vs. the single prior run — gives the narrative real context ("up 4 runs
                // in a row from 62% to 81%") instead of a bare arrow.
                const windowTrend = (key: string, higherIsBetter: boolean) => {
                  const first = num(oldest, key)
                  const last  = num(latest, key)
                  if (first == null || last == null || kpiSeries.length < 2) return null
                  const delta = last - first
                  let direction: 'flat' | 'improving' | 'regressing'
                  if (Math.abs(delta) < 1e-6) {
                    direction = 'flat'
                  } else {
                    direction = (higherIsBetter ? delta > 0 : delta < 0) ? 'improving' : 'regressing'
                  }
                  return { direction, first, last, delta }
                }

                const tip = {
                  automation: 'Share of resolved incidents closed entirely by automated remediation (no human action). Calculated as automated resolutions ÷ resolved incidents over the window.',
                  falsePositive: 'Share of resolved incidents whose resolution was won’t-fix, noise, or duplicate — work that paged someone but needed no real remediation. Calculated as false-positive count ÷ resolved incidents.',
                  mttrAll: 'Average wall-clock time from incident creation to resolution, across every resolved incident in the window.',
                  mttrP1P2: 'Average MTTR restricted to incidents tagged priority P1 or P2 — the subset where slow resolution has the most business impact.',
                  cmdb: 'Average confidence score behind each incident’s risk calculation, sourced from CMDB-derived qualification factors. Low coverage means risk scoring is leaning on pessimistic defaults instead of real CI data.',
                  qualification: 'Share of raw monitoring events promoted to a tracked incident, out of every event ingested in the window. Calculated as qualified events ÷ total monitoring events — distinct from false-positive rate, which only looks at events that already became incidents.',
                  approval: 'Average time a governance approval request sat in the queue before a human decided it, across governance-type approvals with a recorded decision in the window.',
                  govBypass: 'Share of resolved incidents that never hit a manual governance approval gate at all — either a confidence-gate bypass or no matching policy. Calculated as ungated resolutions ÷ resolved incidents.',
                  acceptance: 'Share of Platform Intelligence recommendations that were accepted rather than dismissed, among all decided recommendations in the window — a proxy for how well-calibrated the tuning engine’s suggestions are.',
                  autoApply: 'Share of tunable parameters that have earned auto-apply trust (3+ consecutive accepted, applied, and verified-improved cycles) vs. ones still requiring manual review every cycle.',
                  remediation: 'Share of whole remediation attempts that did not succeed, at the attempt level. Calculated as failed attempts ÷ decided attempts.',
                  runbookStep: 'Share of individual runbook steps that failed or timed out, at the step level — pinpoints exactly which step breaks, distinct from remediation failure rate which only sees the whole attempt.',
                }

                const tileGrid: CSSProperties = { display: 'grid', gap: '12px', gridTemplateColumns: 'repeat(5, 1fr)' }
                // Dynamic — these track each dimension's *current health* and drive the
                // tile value-text color and the Interpretation status dots. Intentionally
                // NOT used for the tile border below: two sections can land in the same
                // severity tier at the same time (e.g. both "high"), which would make
                // their borders collide even though they're different sections.
                const automationColor = automationRate == null ? '#4b5563' : automationRate >= 0.5 ? SEVERITY.low : automationRate >= 0.3 ? SEVERITY.high : SEVERITY.critical
                const reliabilityColor = (mttrP1P2 == null || mttrP1P2 <= 4) ? SEVERITY.low : SEVERITY.critical
                const dataQualityColor = (cmdbCoverage == null || cmdbCoverage >= 70) ? SEVERITY.low : SEVERITY.high

                // Fixed — one color per section, constant regardless of current values,
                // used only for the tile border/legend so section identity never
                // collides with (or gets confused with) a severity color.
                const sectionId = {
                  overview:     '#7a8ba3', // slate
                  automation:   '#9a7030', // amber
                  reliability:  '#4070a0', // blue
                  dataQuality:  '#4a8a63', // green
                  trust:        '#8a6fa8', // violet
                  execution:    '#a04848', // red
                }

                // Legend — since section identity now lives on each tile's left border
                // rather than a labeled chip taking up a grid slot, this is the only
                // place the section names are still spelled out.
                const legendItems: [string, string][] = [
                  ['Overview', sectionId.overview],
                  ['Automation & noise', sectionId.automation],
                  ['Reliability & governance', sectionId.reliability],
                  ['Data quality', sectionId.dataQuality],
                  ...(recAcceptanceRate != null || autoApplyTrust != null ? [['Trust & calibration', sectionId.trust]] as [string, string][] : []),
                  ...(remediationFail != null || runbookStepFail != null ? [['Execution reliability', sectionId.execution]] as [string, string][] : []),
                ]

                return (
                  <>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', marginBottom: '14px' }}>
                      {legendItems.map(([label, color]) => (
                        <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '7px' }}>
                          <span style={{ width: '3px', height: '14px', borderRadius: '1px', background: color, flexShrink: 0 }} />
                          <span style={{ fontSize: '11px', fontWeight: 600, color: '#7a8ba3' }}>{label}</span>
                        </div>
                      ))}
                    </div>
                    <div style={tileGrid}>
                      {/* Overview — quick-glance totals, not tied to a single analytical theme */}
                      <MetricTile
                        label="Total Incidents (30d)"
                        value={String(health.total_incidents)}
                        accent={SEVERITY.medium}
                        sectionColor={sectionId.overview}
                      />
                      <MetricTile
                        label="Pending Recommendations"
                        value={String(health.pending_recommendations)}
                        accent={health.pending_recommendations > 0 ? SEVERITY.high : SEVERITY.low}
                        sectionColor={sectionId.overview}
                      />

                      {/* Automation & noise */}
                      <MetricTile
                        label="Automation Rate"
                        value={fmtPct(automationRate)}
                        sub={`${health.automated_resolutions} automated / ${health.resolved_incidents} resolved`}
                        accent={automationColor}
                        sectionColor={sectionId.automation}
                        trend={<KpiTrend current={num(latest, 'automation_rate')} previous={num(prev, 'automation_rate')} higherIsBetter pct />}
                        tooltip={tip.automation}
                        series={series('automation_rate')}
                      />
                      <MetricTile
                        label="False Positive Rate"
                        value={fmtPct(falsePositive)}
                        sub={`${health.false_positive_count} incidents`}
                        accent={falsePositive == null ? '#4b5563' : falsePositive <= 0.1 ? SEVERITY.low : falsePositive <= 0.25 ? SEVERITY.high : SEVERITY.critical}
                        sectionColor={sectionId.automation}
                        trend={<KpiTrend current={num(latest, 'false_positive_rate')} previous={num(prev, 'false_positive_rate')} higherIsBetter={false} pct />}
                        tooltip={tip.falsePositive}
                        series={series('false_positive_rate')}
                      />
                      {qualificationRate != null && (
                        <MetricTile
                          label="Qualification Rate"
                          value={fmtPct(qualificationRate)}
                          sub="raw events → tracked incidents"
                          accent={qualificationRate >= 0.5 ? SEVERITY.low : SEVERITY.high}
                          sectionColor={sectionId.automation}
                          trend={<KpiTrend current={qualificationRate} previous={num(prev, 'qualification_rate')} higherIsBetter />}
                          tooltip={tip.qualification}
                          series={series('qualification_rate')}
                        />
                      )}

                      {/* Reliability & governance */}
                      <MetricTile
                        label="Avg MTTR (all)"
                        value={fmtHours(mttrAll)}
                        accent={SEVERITY.medium}
                        sectionColor={sectionId.reliability}
                        trend={<KpiTrend current={num(latest, 'mttr_all_hours')} previous={num(prev, 'mttr_all_hours')} higherIsBetter={false} />}
                        tooltip={tip.mttrAll}
                        series={series('mttr_all_hours')}
                      />
                      <MetricTile
                        label="P1/P2 Avg MTTR"
                        value={fmtHours(mttrP1P2)}
                        accent={mttrP1P2 == null ? '#4b5563' : mttrP1P2 <= 4 ? SEVERITY.low : SEVERITY.critical}
                        sectionColor={sectionId.reliability}
                        trend={<KpiTrend current={num(latest, 'mttr_p1p2_hours')} previous={num(prev, 'mttr_p1p2_hours')} higherIsBetter={false} />}
                        tooltip={tip.mttrP1P2}
                        series={series('mttr_p1p2_hours')}
                      />
                      {meanTimeToApproval != null && (
                        <MetricTile
                          label="Mean Time to Approval"
                          value={`${meanTimeToApproval.toFixed(0)}m`}
                          accent={meanTimeToApproval <= 30 ? SEVERITY.low : meanTimeToApproval <= 120 ? SEVERITY.high : SEVERITY.critical}
                          sectionColor={sectionId.reliability}
                          trend={<KpiTrend current={meanTimeToApproval} previous={num(prev, 'mean_time_to_approval_minutes')} higherIsBetter={false} />}
                          tooltip={tip.approval}
                          series={series('mean_time_to_approval_minutes')}
                        />
                      )}
                      {govBypassRate != null && (
                        <MetricTile
                          label="Governance Bypass Rate"
                          value={fmtPct(govBypassRate)}
                          sub="resolved without a manual gate"
                          // Two-sided metric (a confidence-gate bypass is earned trust, a missing
                          // policy is a gap) so this stays informational at moderate values — but a
                          // rate this high across the board is worth a look either way.
                          accent={govBypassRate <= 0.6 ? SEVERITY.medium : govBypassRate <= 0.8 ? SEVERITY.high : SEVERITY.critical}
                          sectionColor={sectionId.reliability}
                          trend={<KpiTrend current={govBypassRate} previous={num(prev, 'governance_bypass_rate')} higherIsBetter />}
                          tooltip={tip.govBypass}
                          series={series('governance_bypass_rate')}
                        />
                      )}

                      {/* Data quality */}
                      <MetricTile
                        label="CMDB Coverage"
                        value={cmdbCoverage != null ? `${cmdbCoverage}%` : '—'}
                        accent={cmdbCoverage == null ? '#4b5563' : cmdbCoverage >= 70 ? SEVERITY.low : SEVERITY.high}
                        sectionColor={sectionId.dataQuality}
                        trend={<KpiTrend current={num(latest, 'cmdb_coverage')} previous={num(prev, 'cmdb_coverage')} higherIsBetter />}
                        tooltip={tip.cmdb}
                        series={series('cmdb_coverage')}
                      />

                      {/* Trust & calibration */}
                      {recAcceptanceRate != null && (
                        <MetricTile
                          label="Recommendation Acceptance"
                          value={fmtPct(recAcceptanceRate)}
                          sub="is Platform Intel well-calibrated?"
                          accent={recAcceptanceRate >= 0.6 ? SEVERITY.low : SEVERITY.high}
                          sectionColor={sectionId.trust}
                          trend={<KpiTrend current={recAcceptanceRate} previous={num(prev, 'recommendation_acceptance_rate')} higherIsBetter />}
                          tooltip={tip.acceptance}
                          series={series('recommendation_acceptance_rate')}
                        />
                      )}
                      {autoApplyTrust != null && (
                        <MetricTile
                          label="Auto-Apply Trust Coverage"
                          value={fmtPct(autoApplyTrust)}
                          accent={SEVERITY.medium}
                          sectionColor={sectionId.trust}
                          trend={<KpiTrend current={autoApplyTrust} previous={num(prev, 'auto_apply_trust_coverage')} higherIsBetter />}
                          tooltip={tip.autoApply}
                          series={series('auto_apply_trust_coverage')}
                        />
                      )}

                      {/* Execution reliability */}
                      {remediationFail != null && (
                        <MetricTile
                          label="Remediation Failure Rate"
                          value={fmtPct(remediationFail)}
                          sub="whole-attempt level"
                          accent={remediationFail <= 0.1 ? SEVERITY.low : remediationFail <= 0.25 ? SEVERITY.high : SEVERITY.critical}
                          sectionColor={sectionId.execution}
                          trend={<KpiTrend current={remediationFail} previous={num(prev, 'remediation_failure_rate')} higherIsBetter={false} pct />}
                          tooltip={tip.remediation}
                          series={series('remediation_failure_rate')}
                        />
                      )}
                      {runbookStepFail != null && (
                        <MetricTile
                          label="Runbook Step Failure Rate"
                          value={fmtPct(runbookStepFail)}
                          sub="step level — where exactly it breaks"
                          accent={runbookStepFail <= 0.1 ? SEVERITY.low : runbookStepFail <= 0.25 ? SEVERITY.high : SEVERITY.critical}
                          sectionColor={sectionId.execution}
                          trend={<KpiTrend current={runbookStepFail} previous={num(prev, 'runbook_step_failure_rate')} higherIsBetter={false} pct />}
                          tooltip={tip.runbookStep}
                          series={series('runbook_step_failure_rate')}
                        />
                      )}
                    </div>

                    {health.last_analysis_at && (
                      <p style={{ fontSize: '10px', color: '#4b5563', fontStyle: 'italic' }}>
                        Last analysis: {new Date(health.last_analysis_at).toLocaleString()}
                        {kpiSeries.length > 0 && ` · ${kpiSeries.length} run${kpiSeries.length !== 1 ? 's' : ''} in trend window`}
                      </p>
                    )}

                    {/* Interpretation — substantive narrative per dimension, grounded in
                        actual values and the full-window trend, not a one-line threshold
                        check that just points at a recommendation. Two-column card grid
                        (not a single running column) so the page's full width gets used;
                        each card's heading stays neutral white with a small colored status
                        dot instead of coloring the heading text itself, so severity reads
                        as a status indicator rather than as emphasis on the label. */}
                    <div style={CARD}>
                      <div style={CARD_HEADER}>
                        <span style={LABEL}>Interpretation</span>
                      </div>
                      <div style={{ padding: '16px' }}>
                        <p style={{ fontSize: '13px', color: '#a0aec0', lineHeight: 1.7, margin: '0 0 14px' }}>
                          This analysis covers <strong style={{ color: '#e8eef5' }}>{health.total_incidents} incident{health.total_incidents !== 1 ? 's' : ''}</strong> over
                          the last 30 days, of which <strong style={{ color: '#e8eef5' }}>{health.resolved_incidents}</strong> have resolved.
                          {kpiSeries.length > 1
                            ? <> The trend below is drawn from <strong style={{ color: '#e8eef5' }}>{kpiSeries.length} analysis runs</strong>, the earliest on {new Date(kpiSeries[0].created_at).toLocaleDateString()}.</>
                            : <> This is the {kpiSeries.length === 1 ? 'first' : 'only'} analysis run persisted so far — trend lines will appear once a second run lands.</>}
                        </p>

                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
                          {/* Automation & noise */}
                          <div style={{ ...INNER, padding: '14px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                              <span style={{ width: '9px', height: '9px', borderRadius: '50%', flexShrink: 0, background: automationColor }} />
                              <h4 style={{ fontSize: '13px', fontWeight: 700, color: '#e8eef5', margin: 0 }}>Automation & noise</h4>
                            </div>
                            <p style={{ fontSize: '13px', color: '#a0aec0', lineHeight: 1.7, margin: 0 }}>
                              {automationRate == null || falsePositive == null ? (
                                'No incidents have resolved in this window yet — automation and false-positive rates will appear once some have.'
                              ) : (
                                <>
                                  {fmtPct(automationRate)} of resolved incidents closed without a human touching them
                                  ({health.automated_resolutions} of {health.resolved_incidents}), while {fmtPct(falsePositive)} turned out to be noise that shouldn't have paged anyone
                                  ({health.false_positive_count} incident{health.false_positive_count !== 1 ? 's' : ''}).
                                  {(() => {
                                    const t = windowTrend('automation_rate', true)
                                    if (!t) return ' Not enough history yet to say whether this is trending up or down.'
                                    if (t.direction === 'flat') return ' This has held essentially flat across the trend window.'
                                    return ` Across the trend window automation has moved from ${fmtPct(t.first)} to ${fmtPct(t.last)} — ${t.direction === 'improving' ? 'a real improvement' : 'a regression worth investigating'}.`
                                  })()}
                                  {automationRate < 0.3 && ' A rate this low usually means runbook coverage is thin for the event types actually showing up — most incidents are still falling through to a human.'}
                                  {falsePositive > 0.25 && ' A false-positive rate this high is expensive twice over: it burns on-call attention and it dilutes confidence in every other automation metric on this page, since "automated" and "actually needed automating" are not the same thing.'}
                                </>
                              )}
                              {recLink('automation_rate')}
                            </p>
                          </div>

                          {/* Reliability & governance */}
                          <div style={{ ...INNER, padding: '14px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                              <span style={{ width: '9px', height: '9px', borderRadius: '50%', flexShrink: 0, background: reliabilityColor }} />
                              <h4 style={{ fontSize: '13px', fontWeight: 700, color: '#e8eef5', margin: 0 }}>Reliability & governance</h4>
                            </div>
                            <p style={{ fontSize: '13px', color: '#a0aec0', lineHeight: 1.7, margin: 0 }}>
                              {mttrP1P2 != null
                                ? <>High-priority incidents (P1/P2) take <strong style={{ color: '#e8eef5' }}>{fmtHours(mttrP1P2)}</strong> on average to resolve, against an overall average of {fmtHours(mttrAll)} across every priority.</>
                                : <>No P1/P2 incidents resolved in this window, so there's no high-priority MTTR signal yet — the overall average sits at {fmtHours(mttrAll)}.</>}
                              {meanTimeToApproval != null && <> Approvals that do require a human sign-off spend an average of <strong style={{ color: '#e8eef5' }}>{meanTimeToApproval.toFixed(0)} minutes</strong> in the queue before being decided.</>}
                              {govBypassRate != null && <> {fmtPct(govBypassRate)} of resolved incidents never reached a manual governance gate at all — either a confidence-gate bypass earned the right to skip review, or no policy matched.</>}
                              {mttrP1P2 != null && mttrP1P2 > 4 && ' That exceeds the 4-hour target for high-priority work; if mean time to approval is also elevated, the bottleneck is more likely the review queue than the remediation itself — worth checking which one is actually driving the number before tightening either.'}
                              {recLink('mttr_p1p2')}
                            </p>
                          </div>

                          {/* Data quality */}
                          <div style={{ ...INNER, padding: '14px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                              <span style={{ width: '9px', height: '9px', borderRadius: '50%', flexShrink: 0, background: dataQualityColor }} />
                              <h4 style={{ fontSize: '13px', fontWeight: 700, color: '#e8eef5', margin: 0 }}>Data quality</h4>
                            </div>
                            <p style={{ fontSize: '13px', color: '#a0aec0', lineHeight: 1.7, margin: 0 }}>
                              {cmdbCoverage != null
                                ? <>Risk scoring is backed by an average CMDB confidence of <strong style={{ color: '#e8eef5' }}>{cmdbCoverage}%</strong> across resolved incidents.</>
                                : <>No CMDB confidence data is available for this window yet.</>}
                              {qualificationRate != null && <> Upstream of that, <strong style={{ color: '#e8eef5' }}>{fmtPct(qualificationRate)}</strong> of raw monitoring events were promoted to a tracked incident at all — the rest were filtered before ever reaching a workflow.</>}
                              {cmdbCoverage != null && cmdbCoverage < 70 && ' Coverage below 70% means a meaningful share of risk scores are leaning on pessimistic factor defaults rather than real configuration-item data, which tends to push more incidents into manual review than the underlying risk actually warrants.'}
                              {qualificationRate != null && qualificationRate < 0.3 && ' A qualification rate this low is worth a second look — either upstream monitoring is very noisy, or the qualification threshold is filtering out events that should have become incidents.'}
                              {recLink('cmdb_coverage')}
                            </p>
                          </div>

                          {/* Trust & calibration */}
                          {(recAcceptanceRate != null || autoApplyTrust != null) && (
                            <div style={{ ...INNER, padding: '14px' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                                <span style={{ width: '9px', height: '9px', borderRadius: '50%', flexShrink: 0, background: SEVERITY.medium }} />
                                <h4 style={{ fontSize: '13px', fontWeight: 700, color: '#e8eef5', margin: 0 }}>Trust & calibration</h4>
                              </div>
                              <p style={{ fontSize: '13px', color: '#a0aec0', lineHeight: 1.7, margin: 0 }}>
                                {recAcceptanceRate != null && <>Operators have accepted <strong style={{ color: '#e8eef5' }}>{fmtPct(recAcceptanceRate)}</strong> of decided Platform Intelligence recommendations in this window{recAcceptanceRate >= 0.6 ? ', a strong sign the tuning engine’s suggestions are well-calibrated to what this environment actually needs' : ', which is on the low side — worth checking whether rejected recommendations share a common pattern (wrong domain, too aggressive a change) the engine should be learning from'}.</>}
                                {autoApplyTrust != null && <> <strong style={{ color: '#e8eef5' }}>{fmtPct(autoApplyTrust)}</strong> of tunable parameters have earned enough consecutive verified-good cycles to qualify for auto-apply, meaning the rest still require a human to review every single cycle.</>}
                              </p>
                            </div>
                          )}

                          {/* Execution reliability */}
                          {(remediationFail != null || runbookStepFail != null) && (
                            <div style={{ ...INNER, padding: '14px' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                                <span style={{ width: '9px', height: '9px', borderRadius: '50%', flexShrink: 0, background: SEVERITY.high }} />
                                <h4 style={{ fontSize: '13px', fontWeight: 700, color: '#e8eef5', margin: 0 }}>Execution reliability</h4>
                              </div>
                              <p style={{ fontSize: '13px', color: '#a0aec0', lineHeight: 1.7, margin: 0 }}>
                                {remediationFail != null && <><strong style={{ color: '#e8eef5' }}>{fmtPct(remediationFail)}</strong> of whole remediation attempts did not succeed.</>}
                                {runbookStepFail != null && <> At the step level, <strong style={{ color: '#e8eef5' }}>{fmtPct(runbookStepFail)}</strong> of individual runbook steps failed or timed out — this is the more actionable number since it points at which step broke rather than just that the attempt as a whole did.</>}
                                {Object.keys(runbookStepCats).length > 0 && (
                                  <> The most common failure categor{Object.keys(runbookStepCats).length === 1 ? 'y is' : 'ies are'}: {Object.entries(runbookStepCats).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([cat, n]) => `${cat} (${n})`).join(', ')} — check Run History for the specific runbook and step.</>
                                )}
                                {runbookStepFail != null && runbookStepFail > 0.25 && ' A step failure rate above 25% usually means one specific step in one specific runbook is doing most of the damage, not that automation broadly is unreliable — worth isolating before tuning anything else.'}
                              </p>
                            </div>
                          )}
                        </div>

                        {automationRate != null && falsePositive != null
                          && automationRate >= 0.3
                          && falsePositive <= 0.2
                          && (mttrP1P2 == null || mttrP1P2 <= 4)
                          && (cmdbCoverage == null || cmdbCoverage >= 70)
                          && (remediationFail == null || remediationFail <= 0.1)
                          && (runbookStepFail == null || runbookStepFail <= 0.1) && (
                          <p style={{ fontSize: '13px', color: SEVERITY.low, lineHeight: 1.7, margin: '14px 0 0' }}>
                            ✓ Every dimension above is within its healthy range for this window — no tuning action is currently indicated.
                          </p>
                        )}
                      </div>
                    </div>
                  </>
                )
              })()}
            </>
          ) : (
            <p style={{ color: '#64748b', fontSize: '13px' }}>No data available yet.</p>
          )}
        </div>
      )}

      {/* ── Run History tab ─────────────────────────────────────────────────── */}
      {tab === 'runs' && (
        <div className="space-y-3">
          {loadingRuns ? (
            <p style={{ color: '#64748b', fontSize: '13px' }}>Loading…</p>
          ) : runs.length === 0 ? (
            <div style={{ ...CARD, padding: '32px', textAlign: 'center' }}>
              <p style={{ color: '#64748b', fontSize: '14px' }}>
                No analysis runs recorded yet — trigger one from the Recommendations tab.
              </p>
            </div>
          ) : (
            runs.map(run => {
              const isExpanded = expandedRunId === run.id
              const sourceColor: Record<string, string> = {
                llm: '#5b6aa0', rules: '#4070a0', healthy: '#4a8a63',
                suppressed: '#6b7280', insufficient_data: '#6b7280',
              }
              return (
                <div key={run.id} style={CARD}>
                  <div
                    onClick={() => setExpandedRunId(isExpanded ? null : run.id)}
                    style={{
                      ...CARD_HEADER, cursor: 'pointer', display: 'flex',
                      alignItems: 'center', justifyContent: 'space-between', gap: '12px',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{
                        fontSize: '10px', fontWeight: 700, padding: '2px 8px', borderRadius: '4px',
                        textTransform: 'uppercase', letterSpacing: '0.4px',
                        color: sourceColor[run.source] || '#9ca3af',
                        border: `1px solid ${sourceColor[run.source] || '#9ca3af'}50`,
                        backgroundColor: `${sourceColor[run.source] || '#9ca3af'}15`,
                      }}>
                        {run.source}
                      </span>
                      <span style={{ fontSize: '11px', color: '#64748b', textTransform: 'capitalize' }}>
                        {run.trigger.replace('_', ' ')}
                      </span>
                      <span style={{ fontSize: '12px', color: '#a0aec0' }}>
                        {new Date(run.created_at).toLocaleString()}
                      </span>
                    </div>
                    <span style={{ fontSize: '11px', color: '#64748b' }}>
                      {run.incidents_analysed} analysed · {run.recommendations_generated} generated
                      {run.recommendations_skipped > 0 && ` · ${run.recommendations_skipped} skipped`}
                    </span>
                  </div>
                  {isExpanded && (
                    <div style={{ padding: '14px 16px' }} className="space-y-3">
                      <div>
                        <p style={{ ...LABEL, marginBottom: '6px' }}>KPI Snapshot</p>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                          {Object.entries(run.kpis)
                            .filter(([, v]) => v !== null && typeof v !== 'object')
                            .map(([k, v]) => (
                              <span key={k} style={{
                                fontSize: '11px', padding: '3px 8px', borderRadius: '4px',
                                border: '1px solid #3d4557', color: '#a0aec0',
                              }}>
                                {k}: <strong style={{ color: '#e8eef5' }}>{String(v)}</strong>
                              </span>
                            ))}
                        </div>
                      </div>
                      {run.llm_raw_response && (
                        <div>
                          <p style={{ ...LABEL, marginBottom: '6px' }}>Raw LLM Response</p>
                          <pre style={{
                            fontSize: '11px', color: '#a0aec0', backgroundColor: '#0f1419',
                            padding: '10px', borderRadius: '6px', overflowX: 'auto',
                            whiteSpace: 'pre-wrap', maxHeight: '300px', overflowY: 'auto',
                          }}>
                            {run.llm_raw_response}
                          </pre>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })
          )}
        </div>
      )}

      {/* ── Config History tab ──────────────────────────────────────────────── */}
      {tab === 'history' && (
        <div className="space-y-3">
          {loadingHistory ? (
            <p style={{ color: '#64748b', fontSize: '13px' }}>Loading…</p>
          ) : history.length === 0 ? (
            <div style={{ ...CARD, padding: '32px', textAlign: 'center' }}>
              <p style={{ color: '#64748b', fontSize: '14px' }}>
                No configuration changes have been applied yet.
              </p>
            </div>
          ) : (
            history.map((entry) => (
              <div key={entry.id} style={{
                ...CARD,
                borderLeft: `3px solid ${priorityColor(entry.priority)}`,
              }}>
                <div style={{ padding: '12px 16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '6px' }}>
                    <span style={{ fontSize: '10px', color: '#4a8a63', fontWeight: 600 }}>
                      ✓ Applied
                    </span>
                    <span style={{ fontSize: '10px', color: '#4b5563' }}>
                      {entry.applied_at ? new Date(entry.applied_at).toLocaleString() : '—'}
                    </span>
                    <span style={{ fontSize: '10px', color: '#4b5563' }}>
                      by {entry.reviewed_by || 'admin'}
                    </span>
                  </div>
                  <p style={{ fontSize: '13px', fontWeight: 600, color: '#e8eef5', margin: '0 0 6px' }}>
                    {entry.title}
                  </p>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <div style={{ ...INNER, padding: '6px 10px' }}>
                      <p style={{ ...LABEL, marginBottom: '2px' }}>{entry.parameter}</p>
                      <p style={{ fontSize: '12px', color: '#9ca3af', margin: 0 }}>
                        <span style={{ color: '#6b7280' }}>{JSON.stringify(entry.previous_value)}</span>
                        {' → '}
                        <span style={{ color: '#4a8a63', fontWeight: 600 }}>{JSON.stringify(entry.new_value)}</span>
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      {/* Reject modal */}
      {rejectTarget && (
        <RejectModal
          recId={rejectTarget}
          onConfirm={handleRejectConfirm}
          onCancel={() => setRejectTarget(null)}
        />
      )}

      {/* Force refresh confirm modal */}
      {showForceRefreshConfirm && (
        <ConfirmModal
          title="Force a fresh analysis pass?"
          message="Ignores the accept/reject cooldown for this run only. Already-decided recommendations may resurface — nothing is deleted."
          confirmLabel="Force Refresh"
          onConfirm={() => { setShowForceRefreshConfirm(false); handleAnalyze(true) }}
          onCancel={() => setShowForceRefreshConfirm(false)}
        />
      )}
    </div>
  )
}
