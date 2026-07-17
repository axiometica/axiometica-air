/**
 * StormsDashboard
 *
 * Displays active event storms detected by the Storm Agent.
 * Each storm groups correlated incidents across multiple resources into a
 * single parent in awaiting_manual state on this page.
 */

import { useState, useEffect, useCallback, useRef, CSSProperties } from 'react'
import {
  listStorms, getStorm, releaseStorm, resolveStorm,
  StormSummary, StormDetail,
} from '../services/api'
import { StormIcon } from './IconWrappers'
import {
  IconRefresh,
  IconBrain,
  IconChevronDown,
  IconChevronRight,
  IconDatabase,
} from './icons'
import { useGlobalEvents } from '../hooks/useGlobalEvents'

// ── Design tokens ─────────────────────────────────────────────────────────────
const DS = {
  bg:      '#0d1117',
  surface: '#1a1f2e',
  raised:  '#252c3c',
  border:  '#3d4557',
  txtP:    '#e8eef5',
  txtS:    '#7a8ba3',
  txtM:    '#a0aec0',
  accent:  '#3b82f6',
  danger:  '#ef4444',
  warning: '#f59e0b',
  success: '#10b981',
  purple:  '#8b5cf6',
} as const

// ── Shared style helpers ───────────────────────────────────────────────────────
const sectionCard: CSSProperties = {
  backgroundColor: DS.surface,
  border: `1px solid ${DS.border}`,
  borderRadius: 10,
  overflow: 'hidden',
  marginBottom: '0.75rem',
}

const sectionBody: CSSProperties = {
  padding: '1rem 1.25rem 1.25rem',
}

const sectionTitle: CSSProperties = {
  margin: '0 0 0.75rem',
  fontSize: '0.85rem',
  fontWeight: 700,
  color: DS.txtP,
}

const primaryBtn: CSSProperties = {
  padding: '6px 16px',
  borderRadius: 6,
  border: 'none',
  backgroundColor: DS.success,
  color: '#fff',
  fontSize: '0.8rem',
  fontWeight: 600,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
}

const secondaryBtn: CSSProperties = {
  padding: '6px 16px',
  borderRadius: 6,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.raised,
  color: DS.txtP,
  fontSize: '0.8rem',
  fontWeight: 500,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
}

const compactBtn: CSSProperties = {
  ...secondaryBtn,
  padding: '3px 10px',
  fontSize: '0.72rem',
}

const dangerBtn: CSSProperties = {
  ...secondaryBtn,
  border: `1px solid ${DS.danger}`,
  color: DS.danger,
}

// ── Severity helpers ───────────────────────────────────────────────────────────
const SEV_COLOR: Record<string, string> = {
  critical: '#ef4444',
  high:     '#f97316',
  medium:   '#f59e0b',
  low:      '#10b981',
}
const sevColor = (s: string | null) => SEV_COLOR[s?.toLowerCase() ?? ''] ?? DS.txtS

// ── Pattern labels ─────────────────────────────────────────────────────────────
const PATTERN_LABEL: Record<string, string> = {
  network_partition:   'Network Partition',
  resource_exhaustion: 'Resource Exhaustion',
  service_cascade:     'Service Cascade',
  mixed_signal_storm:  'Mixed Signal Storm',
}

// ── Time formatting ────────────────────────────────────────────────────────────
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

// ── Hypothesis text with keyword highlighting ──────────────────────────────────
function HighlightedText({ text, terms }: { text: string; terms: string[] }) {
  if (!terms.length) return <>{text}</>
  const sorted = [...terms].sort((a, b) => b.length - a.length)
  const escaped = sorted.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const regex = new RegExp(`(${escaped.join('|')})`, 'gi')
  const parts = text.split(regex)
  return (
    <>
      {parts.map((part, i) =>
        i % 2 === 1
          ? <strong key={i} style={{ color: DS.txtP, fontWeight: 600 }}>{part}</strong>
          : <span key={i}>{part}</span>
      )}
    </>
  )
}

// ── Confidence bar ─────────────────────────────────────────────────────────────
function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color = pct >= 75 ? DS.success : pct >= 50 ? DS.warning : DS.danger
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ flex: 1, height: 5, borderRadius: 3, backgroundColor: DS.border, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', backgroundColor: color, borderRadius: 3 }} />
      </div>
      <span style={{ fontSize: '0.75rem', color: DS.txtM, minWidth: 32 }}>{pct}%</span>
    </div>
  )
}

// ── Lifecycle badge ────────────────────────────────────────────────────────────
const LIFECYCLE_COLOR: Record<string, string> = {
  waiting_approval: DS.warning,
  awaiting_manual:  '#f97316',
  storm_hold:       DS.purple,
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

// ── Storm list card ────────────────────────────────────────────────────────────
function StormCard({
  storm, selected, onClick,
}: { storm: StormSummary; selected: boolean; onClick: () => void }) {
  const sev     = sevColor(storm.severity)
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
        backgroundColor: selected ? DS.accent + '12' : DS.surface,
        cursor: 'pointer',
        transition: 'background 0.15s',
        marginBottom: '0.5rem',
      }}
    >
      {/* Title row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: sev, flexShrink: 0 }} />
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
          backgroundColor: 'transparent', color: DS.purple, border: `1px solid ${DS.purple}66`,
        }}>{pattern}</span>
        <span style={{ fontSize: '0.72rem', color: DS.txtS }}>
          {storm.child_count} incidents · {storm.affected_count} resources
        </span>
        <span style={{ fontSize: '0.72rem', color: DS.txtS, marginLeft: 'auto' }}>
          {timeAgo(storm.detected_at || storm.created_at)}
        </span>
      </div>

      {storm.confidence != null && (
        <div style={{ marginTop: 6 }}>
          <ConfidenceBar value={storm.confidence} />
        </div>
      )}
    </button>
  )
}

// ── Collapsible section wrapper ────────────────────────────────────────────────
function CollapsibleSection({
  title, subtitle, defaultOpen = true, children,
}: {
  title: string
  subtitle?: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div style={sectionCard}>
      <div
        style={{ display: 'flex', alignItems: 'center', padding: '0.8rem 1.25rem', cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setOpen(v => !v)}
      >
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {open
              ? <IconChevronDown size={15} color={DS.txtS} />
              : <IconChevronRight size={15} color={DS.txtS} />
            }
            <span style={{ fontSize: '0.9rem', fontWeight: 600, color: DS.txtP }}>{title}</span>
          </div>
          {subtitle && (
            <p style={{ fontSize: '0.72rem', color: DS.txtS, marginLeft: 23, marginTop: 2, marginBottom: 0 }}>
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {open && <div style={sectionBody}>{children}</div>}
    </div>
  )
}

// ── Storm detail panel ─────────────────────────────────────────────────────────
function StormDetailPanel({
  storm, onRefresh,
}: { storm: StormDetail; onRefresh: () => void }) {
  const [actionNotes, setActionNotes]   = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [actionMsg, setActionMsg]       = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [confirmAction, setConfirmAction] = useState<'release' | 'resolve' | null>(null)

  const handleRelease = async () => {
    setActionLoading(true)
    setActionMsg(null)
    setConfirmAction(null)
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
    setActionLoading(true)
    setActionMsg(null)
    setConfirmAction(null)
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

  const sev     = sevColor(storm.severity)
  const pattern = PATTERN_LABEL[storm.pattern ?? ''] ?? storm.pattern ?? 'Unknown Pattern'

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0, overflowY: 'auto', maxHeight: 'calc(100vh - 12rem)' }}>

      {/* ── Header card ──────────────────────────────────────────────── */}
      <div style={sectionCard}>
        <div style={sectionBody}>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, marginBottom: '0.6rem' }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', backgroundColor: sev, marginTop: 5, flexShrink: 0 }} />
            <div style={{ flex: 1 }}>
              <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 700, color: DS.txtP }}>
                {storm.incident_number ? `[${storm.incident_number}] ` : ''}{storm.title}
              </h2>
              <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                <LifecycleBadge state={storm.lifecycle_state} />
                <span style={{
                  fontSize: '0.7rem', padding: '1px 7px', borderRadius: 10,
                  backgroundColor: 'transparent', color: DS.purple, border: `1px solid ${DS.purple}66`,
                }}>{pattern}</span>
                <span style={{ fontSize: '0.75rem', color: DS.txtS }}>Detected {fmtDate(storm.detected_at)}</span>
              </div>
            </div>
            {storm.confidence != null && (
              <div style={{ textAlign: 'right', minWidth: 110 }}>
                <div style={{ fontSize: '0.7rem', color: DS.txtS, marginBottom: 3 }}>Correlation confidence</div>
                <ConfidenceBar value={storm.confidence} />
              </div>
            )}
          </div>

          {/* Affected resources + event type pills — no fill, border only */}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: '0.5rem' }}>
            {storm.affected_resources.map(r => (
              <span key={r} style={{
                fontSize: '0.72rem', padding: '2px 8px', borderRadius: 10,
                backgroundColor: 'transparent', color: DS.txtM,
                border: `1px solid ${DS.border}`,
              }}>{r}</span>
            ))}
            {storm.event_types?.map(t => (
              <span key={t} style={{
                fontSize: '0.72rem', padding: '2px 8px', borderRadius: 10,
                backgroundColor: 'transparent', color: DS.txtS,
                border: `1px solid ${DS.border}`,
                fontStyle: 'italic',
              }}>{t.replace(/_/g, ' ')}</span>
            ))}
          </div>

          {/* Analysis source badges — minimal, icon + text only */}
          <div style={{ display: 'flex', gap: 12, marginTop: '0.65rem' }}>
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: '0.7rem',
              color: storm.llm_used ? DS.success : DS.txtS,
            }}>
              <IconBrain size={12} />
              LLM {storm.llm_used ? 'used' : 'unavailable'}
            </span>
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontSize: '0.7rem',
              color: storm.neo4j_available ? DS.success : DS.txtS,
            }}>
              <IconDatabase size={12} />
              Neo4j {storm.neo4j_available ? 'used' : 'unavailable'}
            </span>
          </div>
        </div>
      </div>

      {/* ── LLM Hypothesis ───────────────────────────────────────────── */}
      {storm.hypothesis && (
        <CollapsibleSection
          title={storm.llm_used ? 'AI Root Cause Hypothesis' : 'Root Cause Hypothesis'}
          subtitle="Model-generated analysis of the correlated event pattern"
        >
          <p style={{
            margin: 0,
            fontSize: '0.855rem',
            color: DS.txtM,
            lineHeight: 1.7,
            borderLeft: `3px solid ${DS.purple}55`,
            paddingLeft: '0.9rem',
          }}>
            <HighlightedText
              text={storm.hypothesis}
              terms={[
                ...storm.affected_resources,
                ...(storm.event_types?.map(t => t.replace(/_/g, ' ')) ?? []),
              ]}
            />
          </p>
        </CollapsibleSection>
      )}

      {/* ── Root Cause Candidates ─────────────────────────────────────── */}
      {storm.root_cause_candidates.length > 0 && (
        <CollapsibleSection
          title="Root Cause Candidates"
          subtitle="Shared upstream dependencies from CMDB graph traversal"
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
            {storm.root_cause_candidates.map((c, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', gap: 12,
                padding: '0.5rem 0.75rem',
                backgroundColor: DS.raised,
                borderRadius: 7,
                border: `1px solid ${DS.border}`,
              }}>
                <span style={{
                  fontSize: '0.7rem', fontWeight: 700, color: DS.accent,
                  minWidth: 20, textAlign: 'center',
                }}>#{i + 1}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: '0.82rem', color: DS.txtP, fontWeight: 600 }}>{c.ci_name}</div>
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
        </CollapsibleSection>
      )}

      {/* ── Child Incidents Table ─────────────────────────────────────── */}
      <CollapsibleSection
        title="Child Incidents"
        subtitle={`${storm.children.length} incident${storm.children.length !== 1 ? 's' : ''} grouped into this storm`}
      >
        {storm.children.length === 0 ? (
          <p style={{ color: DS.txtS, fontSize: '0.82rem', margin: 0 }}>No child incidents found.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ borderBottom: `1px solid ${DS.border}` }}>
                  {['Resource', 'Event Type', 'Title', 'State', 'Created'].map(h => (
                    <th key={h} style={{ padding: '6px 8px', textAlign: 'left', color: DS.txtS, fontWeight: 500, whiteSpace: 'nowrap' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {storm.children.map((child) => (
                  <tr key={child.workflow_id} style={{ borderBottom: `1px solid ${DS.border}22` }}>
                    <td style={{ padding: '6px 8px', color: DS.txtP }}>{child.resource_name ?? '—'}</td>
                    <td style={{ padding: '6px 8px', color: DS.txtM }}>{child.event_type?.replace(/_/g, ' ') ?? '—'}</td>
                    <td style={{
                      padding: '6px 8px', color: DS.txtM,
                      maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{child.title ?? '—'}</td>
                    <td style={{ padding: '6px 8px' }}><LifecycleBadge state={child.lifecycle_state} /></td>
                    <td style={{ padding: '6px 8px', color: DS.txtS, whiteSpace: 'nowrap' }}>{timeAgo(child.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CollapsibleSection>

      {/* ── Storm Actions ─────────────────────────────────────────────── */}
      {!['resolved', 'closed'].includes(storm.lifecycle_state) && (
        <CollapsibleSection title="Storm Actions">
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

          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            {/* Release — two-click confirm */}
            {confirmAction === 'release' ? (
              <>
                <button
                  onClick={handleRelease}
                  disabled={actionLoading}
                  style={{ ...secondaryBtn, opacity: actionLoading ? 0.5 : 1, cursor: actionLoading ? 'not-allowed' : 'pointer' }}
                >
                  Confirm Release
                </button>
                <button
                  onClick={() => setConfirmAction(null)}
                  style={compactBtn}
                >
                  Cancel
                </button>
              </>
            ) : confirmAction === 'resolve' ? (
              <>
                <button
                  onClick={handleResolve}
                  disabled={actionLoading}
                  style={{ ...primaryBtn, opacity: actionLoading ? 0.5 : 1, cursor: actionLoading ? 'not-allowed' : 'pointer' }}
                >
                  Confirm Resolve All
                </button>
                <button
                  onClick={() => setConfirmAction(null)}
                  style={compactBtn}
                >
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button
                  onClick={() => setConfirmAction('release')}
                  disabled={actionLoading}
                  title="Dismiss the storm — each child incident resumes its own pipeline independently"
                  style={{ ...secondaryBtn, cursor: actionLoading ? 'not-allowed' : 'pointer', opacity: actionLoading ? 0.5 : 1 }}
                >
                  Release
                </button>
                <button
                  onClick={() => setConfirmAction('resolve')}
                  disabled={actionLoading}
                  title="Mark all child incidents resolved — use after the root cause has been fixed"
                  style={{ ...primaryBtn, cursor: actionLoading ? 'not-allowed' : 'pointer', opacity: actionLoading ? 0.5 : 1 }}
                >
                  Resolve all
                </button>
              </>
            )}
          </div>

          <p style={{ margin: '0.6rem 0 0', fontSize: '0.72rem', color: DS.txtS }}>
            <strong style={{ color: DS.txtM }}>Release</strong> — false positive or handle individually.&nbsp;
            <strong style={{ color: DS.txtM }}>Resolve</strong> — root cause addressed, all services recovered.
          </p>
        </CollapsibleSection>
      )}
    </div>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────────
export default function StormsDashboard() {
  const [storms, setStorms]           = useState<StormSummary[]>([])
  const [loading, setLoading]         = useState(true)
  const [error, setError]             = useState<string | null>(null)
  const [selectedId, setSelectedId]   = useState<string | null>(null)
  const [detail, setDetail]           = useState<StormDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [activeOnly, setActiveOnly]   = useState(true)

  const selectedIdRef = useRef<string | null>(null)
  selectedIdRef.current = selectedId

  const loadStorms = useCallback(async () => {
    try {
      setLoading(true)
      setError(null)
      const res = await listStorms(activeOnly)
      setStorms(res.data)
      if (res.data.length > 0 && !selectedIdRef.current) {
        setSelectedId(res.data[0].storm_id)
      }
    } catch {
      setError('Failed to load storms')
    } finally {
      setLoading(false)
    }
  }, [activeOnly])

  const refreshDetail = useCallback((id: string | null) => {
    if (!id) return
    getStorm(id).then(res => setDetail(res.data)).catch(() => {})
  }, [])

  useEffect(() => {
    loadStorms()
    const iv = setInterval(loadStorms, 30_000)
    return () => clearInterval(iv)
  }, [loadStorms])

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

      {/* ── Page header ──────────────────────────────────────────────── */}
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
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
          <button
            onClick={handleRefresh}
            style={{ ...secondaryBtn, padding: '6px 14px' }}
          >
            <IconRefresh size={14} />
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

          {/* Storm list */}
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

          {/* Detail panel */}
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
