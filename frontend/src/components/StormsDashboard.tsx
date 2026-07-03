/**
 * StormsDashboard
 *
 * Displays active event storms detected by the Storm Agent.
 * Each storm groups correlated incidents across multiple resources into a
 * single parent in awaiting_manual state on this page.
 *
 * Shows:
 *   - List of active storms (left column)
 *   - Storm detail panel (right column) with:
 *       • LLM root-cause hypothesis
 *       • Neo4j root-cause candidates
 *       • Affected resources / event types
 *       • Child incidents table
 *       • Release (dismiss) / Resolve action buttons
 */

import { useState, useEffect, useCallback, useRef } from 'react'
import {
  listStorms, getStorm, releaseStorm, resolveStorm,
  StormSummary, StormDetail,
} from '../services/api'
import { StormIcon } from './IconWrappers'
import { useGlobalEvents } from '../hooks/useGlobalEvents'

// ── Design tokens (match platform palette) ────────────────────────────────────
const DS = {
  bg:       '#0d1117',
  surface:  '#1a1f2e',
  raised:   '#252c3c',
  border:   '#3d4557',
  txtP:     '#e8eef5',
  txtS:     '#7a8ba3',
  txtM:     '#a0aec0',
  accent:   '#3b82f6',
  danger:   '#ef4444',
  warning:  '#f59e0b',
  success:  '#10b981',
  purple:   '#8b5cf6',
}

// ── Severity helpers ──────────────────────────────────────────────────────────
const SEV_COLOR: Record<string, string> = {
  critical: '#ef4444',
  high:     '#f97316',
  medium:   '#f59e0b',
  low:      '#10b981',
}

const sevColor = (s: string | null) => SEV_COLOR[s?.toLowerCase() ?? ''] ?? DS.txtS

// ── Pattern labels ────────────────────────────────────────────────────────────
const PATTERN_LABEL: Record<string, string> = {
  network_partition:   'Network Partition',
  resource_exhaustion: 'Resource Exhaustion',
  service_cascade:     'Service Cascade',
  mixed_signal_storm:  'Mixed Signal Storm',
}

// ── Time formatting ───────────────────────────────────────────────────────────
/** Parse server timestamps as UTC regardless of whether they carry a 'Z' suffix */
function parseUTC(iso: string): Date {
  return /Z$|[+-]\d{2}:\d{2}$/.test(iso.trim()) ? new Date(iso) : new Date(iso + 'Z')
}

function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const diff = Math.floor((Date.now() - parseUTC(iso).getTime()) / 1000)
  if (diff < 0)     return 'just now'
  if (diff < 60)    return `${diff}s ago`
  if (diff < 3600)  return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) {
    const h = Math.floor(diff / 3600)
    const m = Math.floor((diff % 3600) / 60)
    return m > 0 ? `${h}h ${m}m ago` : `${h}h ago`
  }
  return `${Math.floor(diff / 86400)}d ago`
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return parseUTC(iso).toLocaleString()
}

// ── Confidence bar ────────────────────────────────────────────────────────────
function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 75 ? DS.success : pct >= 50 ? DS.warning : DS.danger
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{
        flex: 1, height: 5, borderRadius: 3,
        backgroundColor: DS.border, overflow: 'hidden',
      }}>
        <div style={{ width: `${pct}%`, height: '100%', backgroundColor: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: '0.75rem', color: DS.txtM, minWidth: 32 }}>{pct}%</span>
    </div>
  )
}

// ── Lifecycle badge ───────────────────────────────────────────────────────────
const LIFECYCLE_COLOR: Record<string, string> = {
  waiting_approval: DS.warning,   // amber
  awaiting_manual:  '#f97316',    // orange — human owns it
  storm_hold:       DS.purple,    // violet — child held by storm
  resolved:         DS.success,
  closed:           DS.txtS,
  failed:           DS.danger,
  rejected:         DS.danger,
}

const LIFECYCLE_LABEL: Record<string, string> = {
  waiting_approval: 'Waiting Approval',
  awaiting_manual:  'Awaiting Manual',
  storm_hold:       'Storm Hold',
  resolved:         'Resolved',
  closed:           'Closed',
  failed:           'Failed',
  rejected:         'Rejected',
  open:             'Open',
  executing:        'Executing',
  approved:         'Approved',
}

function LifecycleBadge({ state }: { state: string }) {
  const color = LIFECYCLE_COLOR[state] ?? DS.accent
  const label = LIFECYCLE_LABEL[state] ?? state.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      borderRadius: 12,
      fontSize: '0.7rem',
      fontWeight: 600,
      backgroundColor: color + '22',
      color,
      border: `1px solid ${color}44`,
    }}>{label}</span>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Storm list card
// ─────────────────────────────────────────────────────────────────────────────
function StormCard({
  storm, selected, onClick,
}: { storm: StormSummary; selected: boolean; onClick: () => void }) {
  const sev = sevColor(storm.severity)
  const pattern = PATTERN_LABEL[storm.pattern ?? ''] ?? storm.pattern ?? 'Unknown Pattern'

  return (
    <button
      onClick={onClick}
      style={{
        width: '100%',
        textAlign: 'left',
        padding: '0.85rem 1rem',
        borderRadius: 8,
        border: selected ? `1px solid ${DS.accent}55` : `1px solid ${DS.border}`,
        backgroundColor: selected ? DS.accent + '12' : DS.raised,
        cursor: 'pointer',
        transition: 'background 0.15s',
        marginBottom: '0.5rem',
      }}
    >
      {/* Top row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{
          width: 8, height: 8, borderRadius: '50%',
          backgroundColor: sev, flexShrink: 0,
        }} />
        <span style={{
          fontSize: '0.82rem', fontWeight: 600, color: DS.txtP,
          flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {storm.incident_number ? `[${storm.incident_number}] ` : ''}{storm.title}
        </span>
        <LifecycleBadge state={storm.lifecycle_state} />
      </div>

      {/* Pattern + counts */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{
          fontSize: '0.7rem', padding: '1px 7px', borderRadius: 10,
          backgroundColor: DS.purple + '22', color: DS.purple,
          border: `1px solid ${DS.purple}44`,
        }}>{pattern}</span>
        <span style={{ fontSize: '0.72rem', color: DS.txtS }}>
          {storm.child_count} incidents · {storm.affected_count} resources
        </span>
        <span style={{ fontSize: '0.72rem', color: DS.txtS, marginLeft: 'auto' }}>
          {timeAgo(storm.detected_at || storm.created_at)}
        </span>
      </div>

      {/* Confidence */}
      {storm.confidence != null && (
        <div style={{ marginTop: 6 }}>
          <ConfidenceBar value={storm.confidence} />
        </div>
      )}
    </button>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Storm detail panel
// ─────────────────────────────────────────────────────────────────────────────
function StormDetailPanel({
  storm, onRefresh,
}: { storm: StormDetail; onRefresh: () => void }) {
  const [actionNotes, setActionNotes] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [actionMsg, setActionMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  const handleRelease = async () => {
    if (!window.confirm(`Release ${storm.child_count} child incidents back to their individual pipelines?`)) return
    setActionLoading(true)
    setActionMsg(null)
    try {
      const res = await releaseStorm(storm.storm_id, actionNotes || undefined)
      setActionMsg({ type: 'ok', text: `Released ${res.data.children_released} incidents. ${res.data.message}` })
      onRefresh()
    } catch {
      setActionMsg({ type: 'err', text: 'Failed to release storm — see console for details.' })
    } finally {
      setActionLoading(false)
    }
  }

  const handleResolve = async () => {
    if (!window.confirm(`Mark all ${storm.child_count} child incidents as resolved?`)) return
    setActionLoading(true)
    setActionMsg(null)
    try {
      const res = await resolveStorm(storm.storm_id, actionNotes || undefined)
      setActionMsg({ type: 'ok', text: `Resolved ${res.data.children_resolved} incidents. ${res.data.message}` })
      onRefresh()
    } catch {
      setActionMsg({ type: 'err', text: 'Failed to resolve storm — see console for details.' })
    } finally {
      setActionLoading(false)
    }
  }

  const sev  = sevColor(storm.severity)
  const pattern = PATTERN_LABEL[storm.pattern ?? ''] ?? storm.pattern ?? 'Unknown Pattern'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem', overflowY: 'auto', maxHeight: 'calc(100vh - 12rem)' }}>

      {/* ── Header ─────────────────────────────────────────────────── */}
      <div style={{
        backgroundColor: DS.raised,
        borderRadius: 10,
        border: `1px solid ${DS.border}`,
        padding: '1rem 1.25rem',
      }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: '0.6rem' }}>
          <span style={{ width: 10, height: 10, borderRadius: '50%', backgroundColor: sev, marginTop: 5, flexShrink: 0 }} />
          <div style={{ flex: 1 }}>
            <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: DS.txtP }}>
              {storm.incident_number ? `[${storm.incident_number}] ` : ''}{storm.title}
            </h2>
            <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              <LifecycleBadge state={storm.lifecycle_state} />
              <span style={{ fontSize: '0.7rem', color: DS.purple, backgroundColor: DS.purple + '22',
                border: `1px solid ${DS.purple}44`, padding: '1px 7px', borderRadius: 10 }}>
                {pattern}
              </span>
              <span style={{ fontSize: '0.75rem', color: DS.txtS }}>Detected {fmtDate(storm.detected_at)}</span>
            </div>
          </div>
          {storm.confidence != null && (
            <div style={{ textAlign: 'right', minWidth: 100 }}>
              <div style={{ fontSize: '0.7rem', color: DS.txtS, marginBottom: 3 }}>Correlation confidence</div>
              <ConfidenceBar value={storm.confidence} />
            </div>
          )}
        </div>

        {/* Affected resources + event types pills */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: '0.5rem' }}>
          {storm.affected_resources.map(r => (
            <span key={r} style={{
              fontSize: '0.72rem', padding: '2px 8px', borderRadius: 10,
              backgroundColor: DS.accent + '18', color: DS.accent,
              border: `1px solid ${DS.accent}33`,
            }}>{r}</span>
          ))}
          {storm.event_types?.map(t => (
            <span key={t} style={{
              fontSize: '0.72rem', padding: '2px 8px', borderRadius: 10,
              backgroundColor: DS.warning + '18', color: DS.warning,
              border: `1px solid ${DS.warning}33`,
            }}>{t.replace(/_/g, ' ')}</span>
          ))}
        </div>

        {/* Analysis source badges */}
        <div style={{ display: 'flex', gap: 8, marginTop: '0.6rem' }}>
          <span style={{
            fontSize: '0.68rem', padding: '2px 8px', borderRadius: 8,
            backgroundColor: storm.llm_used ? DS.success + '22' : DS.border + '55',
            color: storm.llm_used ? DS.success : DS.txtS,
          }}>✦ LLM {storm.llm_used ? 'used' : 'unavailable'}</span>
          <span style={{
            fontSize: '0.68rem', padding: '2px 8px', borderRadius: 8,
            backgroundColor: storm.neo4j_available ? DS.success + '22' : DS.border + '55',
            color: storm.neo4j_available ? DS.success : DS.txtS,
          }}>⬡ Neo4j {storm.neo4j_available ? 'used' : 'unavailable'}</span>
        </div>
      </div>

      {/* ── LLM Hypothesis ─────────────────────────────────────────── */}
      {storm.hypothesis && (
        <div style={{
          backgroundColor: DS.raised,
          borderRadius: 10,
          border: `1px solid ${DS.purple}44`,
          padding: '1rem 1.25rem',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '0.6rem' }}>
            <span style={{ fontSize: '0.85rem', color: DS.purple }}>✦</span>
            <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 700, color: DS.purple }}>
              {storm.llm_used ? 'AI Root Cause Hypothesis' : 'Root Cause Hypothesis'}
            </h3>
          </div>
          <p style={{
            margin: 0,
            fontSize: '0.855rem',
            color: DS.txtP,
            lineHeight: 1.7,
            borderLeft: `3px solid ${DS.purple}55`,
            paddingLeft: '0.9rem',
          }}>
            {storm.hypothesis}
          </p>
        </div>
      )}

      {/* ── Root Cause Candidates (Neo4j) ─────────────────────────── */}
      {storm.root_cause_candidates.length > 0 && (
        <div style={{
          backgroundColor: DS.raised,
          borderRadius: 10,
          border: `1px solid ${DS.border}`,
          padding: '1rem 1.25rem',
        }}>
          <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.85rem', fontWeight: 700, color: DS.txtP }}>
            Root Cause Candidates
            <span style={{ fontWeight: 400, color: DS.txtS, marginLeft: 6, fontSize: '0.75rem' }}>
              (shared upstream dependencies)
            </span>
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {storm.root_cause_candidates.map((c, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '0.5rem 0.75rem',
                backgroundColor: DS.surface,
                borderRadius: 7,
                border: `1px solid ${DS.border}`,
              }}>
                <span style={{
                  fontSize: '0.7rem', fontWeight: 700, color: DS.accent,
                  minWidth: 20, textAlign: 'center',
                }}>#{i + 1}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.82rem', color: DS.txtP, fontWeight: 600 }}>
                    {c.ci_name}
                  </div>
                  {c.type && (
                    <div style={{ fontSize: '0.72rem', color: DS.txtS, marginTop: 1 }}>
                      {c.type} · shared by {c.shared_count ?? '?'} resources
                    </div>
                  )}
                </div>
                {c.confidence != null && (
                  <div style={{ minWidth: 80 }}>
                    <ConfidenceBar value={c.confidence} />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Child Incidents Table ──────────────────────────────────── */}
      <div style={{
        backgroundColor: DS.raised,
        borderRadius: 10,
        border: `1px solid ${DS.border}`,
        padding: '1rem 1.25rem',
      }}>
        <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.85rem', fontWeight: 700, color: DS.txtP }}>
          Child Incidents
          <span style={{ fontWeight: 400, color: DS.txtS, marginLeft: 6, fontSize: '0.75rem' }}>
            ({storm.children.length})
          </span>
        </h3>
        {storm.children.length === 0 ? (
          <p style={{ color: DS.txtS, fontSize: '0.82rem', margin: 0 }}>No child incidents found.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${DS.border}` }}>
                  {['Resource', 'Event Type', 'Title', 'State', 'Created'].map(h => (
                    <th key={h} style={{ padding: '6px 8px', textAlign: 'left', color: DS.txtS, fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {storm.children.map((child) => (
                  <tr key={child.workflow_id} style={{ borderBottom: `1px solid ${DS.border}22` }}>
                    <td style={{ padding: '6px 8px', color: DS.txtP }}>{child.resource_name ?? '—'}</td>
                    <td style={{ padding: '6px 8px', color: DS.txtM }}>
                      {child.event_type?.replace(/_/g, ' ') ?? '—'}
                    </td>
                    <td style={{ padding: '6px 8px', color: DS.txtM, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {child.title ?? '—'}
                    </td>
                    <td style={{ padding: '6px 8px' }}>
                      <LifecycleBadge state={child.lifecycle_state} />
                    </td>
                    <td style={{ padding: '6px 8px', color: DS.txtS, whiteSpace: 'nowrap' }}>
                      {timeAgo(child.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Actions ────────────────────────────────────────────────── */}
      {!['resolved', 'closed'].includes(storm.lifecycle_state) && (
        <div style={{
          backgroundColor: DS.raised,
          borderRadius: 10,
          border: `1px solid ${DS.border}`,
          padding: '1rem 1.25rem',
        }}>
          <h3 style={{ margin: '0 0 0.75rem', fontSize: '0.85rem', fontWeight: 700, color: DS.txtP }}>
            Storm Actions
          </h3>

          {actionMsg && (
            <div style={{
              marginBottom: '0.75rem',
              padding: '0.55rem 0.85rem',
              borderRadius: 7,
              backgroundColor: actionMsg.type === 'ok' ? DS.success + '18' : DS.danger + '18',
              border: `1px solid ${actionMsg.type === 'ok' ? DS.success : DS.danger}44`,
              color: actionMsg.type === 'ok' ? DS.success : DS.danger,
              fontSize: '0.8rem',
            }}>
              {actionMsg.text}
            </div>
          )}

          <div style={{ marginBottom: '0.75rem' }}>
            <label style={{ display: 'block', fontSize: '0.75rem', color: DS.txtS, marginBottom: 4 }}>
              Notes (optional)
            </label>
            <textarea
              rows={2}
              value={actionNotes}
              onChange={e => setActionNotes(e.target.value)}
              placeholder="Reason for this action…"
              style={{
                width: '100%',
                padding: '6px 10px',
                borderRadius: 6,
                border: `1px solid ${DS.border}`,
                backgroundColor: DS.bg,
                color: DS.txtP,
                fontSize: '0.82rem',
                resize: 'vertical',
                outline: 'none',
                boxSizing: 'border-box',
              }}
            />
          </div>

          <div style={{ display: 'flex', gap: 10 }}>
            {/* Release — dismiss the storm, send children back to their pipelines */}
            <button
              onClick={handleRelease}
              disabled={actionLoading}
              title="Dismiss the storm — each child incident resumes its own pipeline independently"
              style={{
                padding: '6px 16px',
                borderRadius: 6,
                border: `1px solid ${DS.txtS}`,
                backgroundColor: 'transparent',
                color: DS.txtS,
                fontSize: '0.8rem',
                fontWeight: 600,
                cursor: actionLoading ? 'not-allowed' : 'pointer',
                opacity: actionLoading ? 0.5 : 1,
                transition: 'border-color 0.15s, color 0.15s',
              }}
              onMouseEnter={e => { if (!actionLoading) { e.currentTarget.style.borderColor = DS.txtP; e.currentTarget.style.color = DS.txtP } }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = DS.txtS; e.currentTarget.style.color = DS.txtS }}
            >
              Release
            </button>

            {/* Resolve — bulk-close all children */}
            <button
              onClick={handleResolve}
              disabled={actionLoading}
              title="Mark all child incidents resolved — use after the root cause has been fixed"
              style={{
                padding: '6px 16px',
                borderRadius: 6,
                border: `1px solid ${DS.success}`,
                backgroundColor: 'transparent',
                color: DS.success,
                fontSize: '0.8rem',
                fontWeight: 600,
                cursor: actionLoading ? 'not-allowed' : 'pointer',
                opacity: actionLoading ? 0.5 : 1,
                transition: 'background-color 0.15s',
              }}
              onMouseEnter={e => { if (!actionLoading) e.currentTarget.style.backgroundColor = DS.success + '18' }}
              onMouseLeave={e => { e.currentTarget.style.backgroundColor = 'transparent' }}
            >
              Resolve all
            </button>
          </div>

          <p style={{ margin: '0.5rem 0 0', fontSize: '0.72rem', color: DS.txtS }}>
            <strong>Release</strong> — false positive or handle individually.&nbsp;
            <strong>Resolve</strong> — root cause addressed, all services recovered.
          </p>
        </div>
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main page
// ─────────────────────────────────────────────────────────────────────────────
export default function StormsDashboard() {
  const [storms, setStorms] = useState<StormSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<StormDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [activeOnly, setActiveOnly] = useState(true)

  // Keep a ref to selectedId so loadStorms can auto-select the first storm
  // without needing selectedId in its dependency array (which would reset the
  // 30s interval every time the user clicks a different storm).
  const selectedIdRef = useRef<string | null>(null)
  selectedIdRef.current = selectedId

  const loadStorms = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const res = await listStorms(activeOnly)
      setStorms(res.data)
      // Auto-select first storm if nothing is currently selected
      if (res.data.length > 0 && !selectedIdRef.current) {
        setSelectedId(res.data[0].storm_id)
      }
    } catch {
      setError('Failed to load storms')
    } finally {
      setLoading(false)
    }
  }, [activeOnly]) // selectedId intentionally excluded — use ref instead

  const refreshDetail = useCallback((id: string | null) => {
    if (!id) return
    getStorm(id)
      .then(res => setDetail(res.data))
      .catch(() => {})
  }, [])

  // Initial load + 30s poll (stable — not reset by storm selection)
  useEffect(() => {
    loadStorms()
    const iv = setInterval(loadStorms, 30_000)
    return () => clearInterval(iv)
  }, [loadStorms])

  // Also poll the detail panel every 30s so child state changes appear
  useEffect(() => {
    if (!selectedId) { setDetail(null); return }
    setDetailLoading(true)
    getStorm(selectedId)
      .then(res => setDetail(res.data))
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false))

    const iv = setInterval(() => refreshDetail(selectedId), 30_000)
    return () => clearInterval(iv)
  }, [selectedId, refreshDetail])

  // Real-time updates via WebSocket — refresh both list and detail immediately
  // when any incident is created or updated (storm adoption, state changes, etc.)
  useGlobalEvents(useCallback((ev) => {
    if (ev.type === 'incident_created' || ev.type === 'incident_updated') {
      loadStorms()
      refreshDetail(selectedIdRef.current)
    }
  }, [loadStorms, refreshDetail]))

  const handleRefresh = () => {
    loadStorms()
    refreshDetail(selectedId)
  }

  return (
    <div style={{ maxWidth: 1300, margin: '0 auto', padding: '0 0.5rem' }}>

      {/* ── Page header ─────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ fontSize: '1.6rem', fontWeight: 700, color: DS.txtP, margin: 0, marginBottom: '0.3rem', display: 'flex', alignItems: 'center', gap: 10 }}>
            <StormIcon size={26} strokeWidth={1.75} />
            Event Storms
          </h1>
          <p style={{ color: DS.txtM, margin: 0, fontSize: '0.85rem' }}>
            Correlated incident bursts detected by the Storm Agent
          </p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* Active / All pill filter */}
          <div style={{ display: 'flex', gap: '0.35rem' }}>
            {(['Active', 'All'] as const).map(label => {
              const isActive = label === 'Active' ? activeOnly : !activeOnly
              return (
                <button
                  key={label}
                  onClick={() => { setActiveOnly(label === 'Active'); setSelectedId(null) }}
                  style={{
                    padding: '4px 14px',
                    borderRadius: 20,
                    border: `1px solid ${isActive ? DS.accent : DS.border}`,
                    background: isActive ? DS.raised : 'transparent',
                    color: isActive ? DS.txtP : DS.txtM,
                    fontSize: '0.78rem',
                    fontWeight: 500,
                    cursor: 'pointer',
                    transition: 'all 150ms',
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
          <button
            onClick={handleRefresh}
            style={{
              padding: '6px 14px',
              borderRadius: 6,
              border: `1px solid ${DS.border}`,
              backgroundColor: 'transparent',
              color: DS.txtM,
              fontSize: '0.8rem',
              fontWeight: 500,
              cursor: 'pointer',
              transition: 'border-color 0.15s, color 0.15s',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = DS.txtM; e.currentTarget.style.color = DS.txtP }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = DS.border; e.currentTarget.style.color = DS.txtM }}
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div style={{
          marginBottom: '1rem', padding: '0.65rem 1rem', borderRadius: 8,
          backgroundColor: 'rgba(239,68,68,0.10)', border: '1px solid rgba(239,68,68,0.28)',
          color: '#f87171', fontSize: '0.85rem',
        }}>
          {error}
        </div>
      )}

      {loading && storms.length === 0 ? (
        <div style={{ color: DS.txtM, textAlign: 'center', padding: '4rem 0', fontSize: '0.9rem' }}>
          Loading storms…
        </div>
      ) : storms.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '4rem 0',
          backgroundColor: DS.surface, borderRadius: 12, border: `1px solid ${DS.border}`,
        }}>
          <div style={{ color: DS.txtP, fontWeight: 600, marginBottom: 4 }}>
            {activeOnly ? 'No active storms' : 'No storms found'}
          </div>
          <div style={{ color: DS.txtS, fontSize: '0.82rem' }}>
            {activeOnly
              ? 'The Storm Agent has not detected any correlated event bursts.'
              : 'No storm records exist yet.'}
          </div>
        </div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '340px 1fr', gap: '1rem', alignItems: 'start' }}>

          {/* ── Storm list ──────────────────────────────────────────── */}
          <div>
            <div style={{ fontSize: '0.75rem', color: DS.txtS, marginBottom: '0.5rem', fontWeight: 600 }}>
              {storms.length} storm{storms.length !== 1 ? 's' : ''}
            </div>
            {storms.map(s => (
              <StormCard
                key={s.storm_id}
                storm={s}
                selected={selectedId === s.storm_id}
                onClick={() => setSelectedId(s.storm_id)}
              />
            ))}
          </div>

          {/* ── Detail panel ────────────────────────────────────────── */}
          <div>
            {detailLoading ? (
              <div style={{
                backgroundColor: DS.surface, borderRadius: 12, border: `1px solid ${DS.border}`,
                padding: '2rem', textAlign: 'center', color: DS.txtM, fontSize: '0.85rem',
              }}>
                Loading storm detail…
              </div>
            ) : detail ? (
              <StormDetailPanel storm={detail} onRefresh={handleRefresh} />
            ) : (
              <div style={{
                backgroundColor: DS.surface, borderRadius: 12, border: `1px solid ${DS.border}`,
                padding: '2rem', textAlign: 'center', color: DS.txtS, fontSize: '0.85rem',
              }}>
                Select a storm to view details
              </div>
            )}
          </div>

        </div>
      )}
    </div>
  )
}
