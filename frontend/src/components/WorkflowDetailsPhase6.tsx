import { useEffect, useState, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { getWorkflow, createRunbook, getConnector, getSnowIncidentMap, pushSnowIncident, updateSnowIncident, getStorm, releaseStorm, resolveStorm } from '../services/api'
import type { StormDetail } from '../services/api'
import { SNowIncidentMap, NoteEntry } from '../types'
import { WorkflowWebSocket, WorkflowUpdate } from '../services/websocket'
import { Workflow } from '../types'
import { getIncidentDisplayName } from '../utils/incidentFormatter'
import {
  formatDate,
  formatDuration,
  calculateDurationMinutes,
} from '../utils/workflowTransformer'
import { parseUTC } from '../utils/dateFormatter'
import { useIncidentActions } from '../hooks/useIncidentActions'
import PolicyEvaluation from './PolicyEvaluation'
import RemediationRecommendation from './RemediationRecommendation'
import ActionExecution from './ActionExecution'
import RemediationSteps from './RemediationSteps'
import IncidentTimeline from './IncidentTimeline'
import IncidentEventsPage from './IncidentEventsPage'
import RiskSummaryPage from './RiskSummaryPage'
import AuditPage from './AuditPage'
import ApprovalModal from './ApprovalModal'
import {
  IconArrowLeft,
  IconAlertTriangle,
  IconAlertCircle,
  IconShield,
  IconX,
  IconCircleCheck,
  IconRefresh,
  IconActivity,
  IconFileText,
  IconBook,
  IconHistory,
  IconClipboardList,
  IconClock,
  IconBell,
  IconTestPipe,
  IconPlus,
  IconCheck,
  IconLoader2,
  IconMessage,
  IconRobot,
} from './icons'

type TabView =
  | 'overview'
  | 'events-page'
  | 'policy'
  | 'remediation'
  | 'ai-insights'
  | 'risk-summary'
  | 'timeline'
  | 'audit'
  | 'notes'

interface WorkflowDetailsPhase6Props {
  workflowId: string
  onBack: () => void
  onViewWorkflow?: (workflowId: string) => void
  darkMode?: boolean
}

// ─── helpers ────────────────────────────────────────────────────────────────

const severityColor = (sev: string | null | undefined) => {
  switch (sev?.toLowerCase()) {
    case 'critical': return '#dc2626'
    case 'high':     return '#f97316'
    case 'medium':   return '#f59e0b'
    case 'low':      return '#10b981'
    default:         return '#a0aec0'
  }
}

const lifecycleColor = (state: string) => {
  if (['resolved', 'closed', 'deployed'].includes(state)) return '#10b981'           // Emerald — done
  if (['waiting_approval'].includes(state))               return '#f59e0b'           // Amber — decision needed
  if (['approved'].includes(state))                       return '#06b6d4'           // Cyan — decided
  if (['awaiting_manual', 'failed', 'rejected', 'rolled_back'].includes(state)) return '#f97316' // Orange — human handoff
  if (['storm_hold'].includes(state))                     return '#8b5cf6'           // Violet — storm cluster
  return '#94a3b8'                                                                   // Slate — Phase 1, system working
}

const lifecycleLabel = (state: string) =>
  state.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())

const riskColor = (score: number | null | undefined) => {
  if (score == null || score === 0) return '#a0aec0'
  if (score >= 70) return '#dc2626'   // critical
  if (score >= 50) return '#f97316'   // high
  if (score >= 30) return '#f59e0b'   // medium
  return '#10b981'                    // low
}

// ─── MetricTile ─────────────────────────────────────────────────────────────

function MetricTile({
  label,
  value,
  color,
  sub,
}: {
  label: string
  value: string | number
  color: string
  sub?: string
}) {
  return (
    <div
      style={{
        backgroundColor: '#252c3c',
        border: `1px solid ${color}30`,
        borderTop: `3px solid ${color}`,
        borderRadius: '10px',
        padding: '1rem 1.25rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.25rem',
      }}
    >
      <span
        style={{
          fontSize: '0.7rem',
          fontWeight: 600,
          color: '#7a8ba3',
          textTransform: 'uppercase',
          letterSpacing: '0.5px',
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontSize: '1.5rem',
          fontWeight: 700,
          color,
          lineHeight: 1.1,
        }}
      >
        {value}
      </span>
      {sub && (
        <span style={{ fontSize: '0.7rem', color: '#7a8ba3' }}>{sub}</span>
      )}
    </div>
  )
}

// ─── DataRow ────────────────────────────────────────────────────────────────

function DataRow({ label, value, mono = false }: { label: string; value?: string | null; mono?: boolean }) {
  if (!value) return null
  return (
    <div style={{ marginBottom: '0.875rem' }}>
      <p
        style={{
          fontSize: '0.7rem',
          fontWeight: 600,
          color: '#7a8ba3',
          textTransform: 'uppercase',
          letterSpacing: '0.5px',
          marginBottom: '0.25rem',
        }}
      >
        {label}
      </p>
      <p
        style={{
          fontSize: '0.875rem',
          color: '#c1c7d0',
          fontFamily: mono ? "'Monaco', 'Courier New', monospace" : 'inherit',
          wordBreak: 'break-word',
        }}
      >
        {value}
      </p>
    </div>
  )
}

// ─── SectionCard ────────────────────────────────────────────────────────────

function SectionCard({
  title,
  icon,
  accent,
  children,
}: {
  title: string
  icon?: React.ReactNode
  accent?: string
  children: React.ReactNode
}) {
  return (
    <div
      style={{
        backgroundColor: '#1a1f2e',
        border: '1px solid #3d4557',
        borderLeft: accent ? `4px solid ${accent}` : '1px solid #3d4557',
        borderRadius: '10px',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.625rem',
          padding: '0.875rem 1.25rem',
          borderBottom: '1px solid #3d4557',
          backgroundColor: '#1e2535',
        }}
      >
        {icon && <span style={{ color: accent || '#7a8ba3' }}>{icon}</span>}
        <h3
          style={{
            fontSize: '0.875rem',
            fontWeight: 600,
            color: '#e8eef5',
            margin: 0,
            textTransform: 'uppercase',
            letterSpacing: '0.5px',
          }}
        >
          {title}
        </h3>
      </div>
      <div style={{ padding: '1.25rem' }}>{children}</div>
    </div>
  )
}

// ─── TraceStep ──────────────────────────────────────────────────────────────

function TraceStep({ idx, text }: { idx: number; text: string }) {
  return (
    <div
      style={{
        display: 'flex',
        gap: '0.875rem',
        padding: '0.875rem',
        backgroundColor: '#252c3c',
        borderRadius: '8px',
        borderLeft: '3px solid #3b82f6',
        marginBottom: '0.625rem',
      }}
    >
      <div
        style={{
          minWidth: 24,
          width: 24,
          height: 24,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: '50%',
          backgroundColor: '#1e3a8a',
          color: '#60a5fa',
          fontSize: '0.7rem',
          fontWeight: 700,
          flexShrink: 0,
        }}
      >
        {idx + 1}
      </div>
      <pre
        style={{
          fontSize: '0.8125rem',
          color: '#c1c7d0',
          fontFamily: "'Monaco', 'Courier New', monospace",
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          margin: 0,
          flex: 1,
        }}
      >
        {text}
      </pre>
    </div>
  )
}

// ─── RunbookStepRow ─────────────────────────────────────────────────────────

function RunbookStepRow({
  step,
  accent,
}: {
  step: { order?: number; name?: string; tool?: string; description?: string; args?: Record<string, any> }
  accent: string
}) {
  return (
    <div
      style={{
        padding: '0.875rem',
        backgroundColor: '#252c3c',
        borderRadius: '8px',
        borderLeft: `3px solid ${accent}`,
        marginBottom: '0.5rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.375rem' }}>
        <span
          style={{
            fontSize: '0.7rem',
            fontWeight: 700,
            color: accent,
            fontFamily: "'Monaco', 'Courier New', monospace",
          }}
        >
          [{step.order ?? '?'}]
        </span>
        <span style={{ fontSize: '0.875rem', fontWeight: 600, color: '#e8eef5' }}>
          {step.name || step.tool || 'Unknown Step'}
        </span>
        {step.tool && (
          <span
            style={{
              fontSize: '0.7rem',
              padding: '0.125rem 0.5rem',
              backgroundColor: '#1a2744',
              color: '#60a5fa',
              borderRadius: '4px',
              fontFamily: "'Monaco', 'Courier New', monospace",
            }}
          >
            {step.tool}
          </span>
        )}
      </div>
      {step.description && (
        <p style={{ fontSize: '0.8125rem', color: '#a0aec0', margin: '0 0 0.375rem 0' }}>
          {step.description}
        </p>
      )}
      {step.args && Object.keys(step.args).length > 0 && (
        <pre
          style={{
            fontSize: '0.75rem',
            color: '#7a8ba3',
            backgroundColor: '#1a1f2e',
            borderRadius: '4px',
            padding: '0.5rem',
            margin: 0,
            whiteSpace: 'pre-wrap',
            fontFamily: "'Monaco', 'Courier New', monospace",
          }}
        >
          {JSON.stringify(step.args, null, 2)}
        </pre>
      )}
    </div>
  )
}

// ─── TechnicalDigestCard ────────────────────────────────────────────────────

/** Inline highlight rules applied to prose lines inside the technical digest. */
type HlSegment = { text: string; color?: string; bold?: boolean }

function parseHighlights(raw: string): HlSegment[] {
  const rules: Array<{ re: RegExp; color: string; bold?: boolean }> = [
    // Status tags  [SUCCESS] / [FAILED] / [WARNING]
    { re: /\[(?:SUCCESS|OK|COMPLETED|PASSED)\]/gi,       color: '#10b981', bold: true },
    { re: /\[(?:FAILED|ERROR|TIMEOUT|FAIL)\]/gi,         color: '#dc2626', bold: true },
    { re: /\[(?:WARNING|WARN|PARTIAL)\]/gi,              color: '#f59e0b', bold: true },
    // Resolution outcomes
    { re: /\b(?:manual intervention required)\b/gi,      color: '#a855f7', bold: true },
    { re: /\b(?:resolved|successful|success|passed)\b/gi,color: '#10b981' },
    { re: /\b(?:failed|failure|failing|ineffective)\b/gi,color: '#dc2626' },
    // Severity adjectives
    { re: /\bcritical\b/gi,                              color: '#dc2626', bold: true },
    { re: /\bextremely critical\b/gi,                    color: '#dc2626', bold: true },
    { re: /\bhigh risk\b/gi,                             color: '#f97316', bold: true },
    // Metrics — numbers with units
    { re: /\b\d+\.?\d*\s*(?:%|\/100|ms\b|\/s\b)/g,     color: '#f59e0b' },
  ]

  let segs: HlSegment[] = [{ text: raw }]

  for (const { re, color, bold } of rules) {
    const next: HlSegment[] = []
    for (const seg of segs) {
      if (seg.color) { next.push(seg); continue }   // already highlighted — leave it
      const parts   = seg.text.split(re)
      const matches = seg.text.match(re) || []
      parts.forEach((part, i) => {
        if (part)      next.push({ text: part })
        if (matches[i]) next.push({ text: matches[i], color, bold })
      })
    }
    segs = next
  }
  return segs
}

function HighlightedLine({ text }: { text: string }) {
  // Split on **bold** markers first, then apply colour highlights within each segment
  const boldParts = text.split(/\*\*/)
  return (
    <>
      {boldParts.map((part, idx) => {
        const isBold = idx % 2 === 1
        const segs = parseHighlights(part)
        return segs.map((s, i) =>
          s.color
            ? <span key={`${idx}-${i}`} style={{ color: s.color, fontWeight: (isBold || s.bold) ? 700 : 400 }}>{s.text}</span>
            : <span key={`${idx}-${i}`} style={{ fontWeight: isBold ? 700 : 400 }}>{s.text}</span>
        )
      })}
    </>
  )
}

/** Render the technical digest as styled prose matching the executive summary card. */
function TechnicalDigestCard({ text }: { text: string }) {
  const SECTION_RE = /^(?:#{1,3}\s*)?(?:EVENT ANALYSIS|REMEDIATION REASONING|RESOLUTION EVIDENCE|VERIFICATION|EXECUTIVE SUMMARY|TECHNICAL DIGEST)\s*:?\s*$/i

  const lines = text.split('\n')

  return (
    <div
      style={{
        backgroundColor: '#1a2035',
        border: '1px solid #6366f130',
        borderLeft: '4px solid #6366f1',
        borderRadius: '10px',
        overflow: 'hidden',
      }}
    >
      <div style={{
        padding: '0.75rem 1.5rem',
        fontSize: '0.7rem',
        fontWeight: 600,
        color: '#818cf8',
        textTransform: 'uppercase',
        letterSpacing: '0.5px',
        borderBottom: '1px solid #3b82f615',
      }}>
        Technical Digest
      </div>

      <div style={{ padding: '0.875rem 1.5rem 1.25rem' }}>
        {lines.map((line, i) => {
          const trimmed = line.trim()

          if (!trimmed) return <div key={i} style={{ height: '0.5rem' }} />

          const stripped    = trimmed.replace(/\*\*/g, '')
          const cleanHeader = stripped.replace(/^#{1,3}\s*/, '').replace(/:$/, '')
          if (SECTION_RE.test(stripped)) {
            return (
              <p
                key={i}
                style={{
                  fontSize: '0.7rem',
                  fontWeight: 700,
                  color: '#818cf8',
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                  marginTop: i > 0 ? '1rem' : 0,
                  marginBottom: '0.375rem',
                }}
              >
                {cleanHeader}
              </p>
            )
          }

          return (
            <p
              key={i}
              style={{
                fontSize: '0.875rem',
                color: '#c1c7d0',
                lineHeight: 1.7,
                margin: '0 0 0.25rem 0',
              }}
            >
              <HighlightedLine text={trimmed} />
            </p>
          )
        })}
      </div>
    </div>
  )
}

// ─── AI Insights helpers ─────────────────────────────────────────────────────

function aiConfidenceColor(c: number): string {
  if (c >= 0.8) return '#10b981'
  if (c >= 0.6) return '#f59e0b'
  if (c >= 0.4) return '#f97316'
  return '#dc2626'
}

function AIBulletList({ items, accent = '#60a5fa' }: { items: string[]; accent?: string }) {
  if (!items?.length) return null
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
      {items.map((item, i) => (
        <li key={i} style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', marginBottom: '6px' }}>
          <span style={{ color: accent, flexShrink: 0, marginTop: '3px', fontSize: '8px' }}>▶</span>
          <span style={{ fontSize: '12px', color: '#c8d6e5', lineHeight: 1.5 }}>{item}</span>
        </li>
      ))}
    </ul>
  )
}

// ─── Main Component ─────────────────────────────────────────────────────────

export default function WorkflowDetailsPhase6({
  workflowId,
  onBack,
  onViewWorkflow,
  darkMode = true,
}: WorkflowDetailsPhase6Props) {
  const { user } = useCurrentUser()
  // canAct: operator, itom_admin, admin — viewer is read-only
  const canAct = user?.role === 'admin' || user?.role === 'itom_admin' || user?.role === 'operator'
  const [workflow, setWorkflow] = useState<Workflow | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [wsConnected, setWsConnected] = useState(false)
  const [activeTab, setActiveTab] = useState<TabView>('overview')
  const insightPollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [showReviewModal, setShowReviewModal] = useState(false)
  const [showCloseModal, setShowCloseModal] = useState(false)
  const [closeSummary, setCloseSummary] = useState('')
  const [closeStepsTaken, setCloseStepsTaken] = useState('')
  const [closeOutcome, setCloseOutcome] = useState('resolved')
  const {
    approve: approveAction,
    diagnosticsOnly: diagnosticsOnlyAction,
    reject: rejectAction,
    close: closeAction,
    addNote: addNoteAction,
    fetchNotes: fetchNotesAction,
    retryRemediation: retryRemediationAction,
    loading: actionLoading,
    error: actionError,
    clearError: clearActionError,
  } = useIncidentActions()

  // ── Work Notes state ─────────────────────────────────────────────────────
  const [notes, setNotes] = useState<NoteEntry[]>([])
  const [notesLoading, setNotesLoading] = useState(false)
  const [noteBody, setNoteBody] = useState('')
  const [noteType, setNoteType] = useState<'note' | 'action' | 'escalation'>('note')
  const notesEndRef = useRef<HTMLDivElement>(null)
  const [addingToRunbook, setAddingToRunbook] = useState(false)
  const [addedToRunbook, setAddedToRunbook] = useState(false)
  const [addRunbookError, setAddRunbookError] = useState<string | null>(null)

  // ServiceNow sync state
  const [snowSyncEnabled, setSnowSyncEnabled] = useState<boolean | null>(null)
  const [snowMap, setSnowMap] = useState<SNowIncidentMap | null>(null)
  const [snowLoading, setSnowLoading] = useState(false)
  const [snowPushing, setSnowPushing] = useState(false)
  const [snowBaseUrl, setSnowBaseUrl] = useState('')

  // Storm context state
  const [stormDetail, setStormDetail] = useState<StormDetail | null>(null)
  const [stormLoading, setStormLoading] = useState(false)

  // Storm response actions (for storm parent waiting_approval)
  const [stormActionLoading, setStormActionLoading] = useState(false)
  const [stormActionNotes, setStormActionNotes] = useState('')
  const [stormActionError, setStormActionError] = useState<string | null>(null)

  useEffect(() => {
    loadWorkflow()

    const ws = new WorkflowWebSocket(workflowId)
    ws.connect()
      .then(() => {
        setWsConnected(true)
        ws.subscribe((message: WorkflowUpdate) => {
          if (message.type === 'workflow_update') {
            // Instant partial update for snappy feedback — the socket payload
            // is a lightweight summary (lifecycle_state/severity/risk_score/
            // last_trace only), so this alone can't reflect changes to nested
            // fields like context.requires_manual_approval or governance state.
            setWorkflow((prev) => {
              if (!prev) return prev
              return {
                ...prev,
                lifecycle_state: (message.lifecycle_state || prev.lifecycle_state) as any,
                severity: message.severity as any,
                risk_score: message.risk_score ?? prev.risk_score,
                reasoning_trace:
                  message.last_trace && !prev.reasoning_trace.includes(message.last_trace)
                    ? [...prev.reasoning_trace, message.last_trace]
                    : prev.reasoning_trace,
              }
            })
            // Follow up with a full silent refetch so context-derived fields
            // (approval/governance status) don't stay stale until the user
            // manually refreshes or takes their own action on this incident.
            refreshWorkflowSilently()
          }
        })
      })
      .catch(() => setWsConnected(false))

    return () => ws.disconnect()
  }, [workflowId])

  const loadWorkflow = async () => {
    try {
      setLoading(true)
      const response = await getWorkflow(workflowId)
      setWorkflow(response.data as Workflow)
      setError(null)
    } catch (err) {
      setError('Failed to load workflow')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!workflowId) return
    getConnector('servicenow')
      .then(r => {
        const enabled = r.data.configured && r.data.incident_sync?.enabled === true
        setSnowSyncEnabled(enabled)
        setSnowBaseUrl(r.data.base_url || '')
        if (enabled) {
          setSnowLoading(true)
          getSnowIncidentMap(workflowId)
            .then(m => setSnowMap(m.data))
            .catch(() => setSnowMap(null))
            .finally(() => setSnowLoading(false))
        }
      })
      .catch(() => setSnowSyncEnabled(false))
  }, [workflowId])

  // Load storm context when workflow is loaded and storm is involved
  useEffect(() => {
    if (!workflow) return
    const ctx = workflow.context || {}
    const sentCtx = ctx.sentinel || {}
    const alertPayload = ctx.alert_payload || sentCtx.alert_payload || {}
    const isParent = (
      ctx.is_storm_parent === true ||
      String(ctx.is_storm_parent).toLowerCase() === 'true' ||
      (sentCtx.anomaly_type || alertPayload.type) === 'event_storm' ||
      (workflow.title || '').startsWith('[STORM]')
    )
    const childStormId = workflow.storm_id

    if (!isParent && !childStormId) return

    const stormIdToFetch = isParent ? workflow.workflow_id : childStormId!
    setStormLoading(true)
    getStorm(stormIdToFetch)
      .then(r => setStormDetail(r.data))
      .catch(() => setStormDetail(null))
      .finally(() => setStormLoading(false))
  }, [workflow?.workflow_id, workflow?.storm_id]) // eslint-disable-line react-hooks/exhaustive-deps

  // Load notes whenever the notes tab is activated — must be here (before any early returns)
  useEffect(() => {
    if (activeTab === 'notes' && workflow) loadNotes()
  }, [activeTab]) // eslint-disable-line react-hooks/exhaustive-deps

  // Silent refresh — fetches latest workflow without triggering the loading skeleton
  const refreshWorkflowSilently = useCallback(async () => {
    try {
      const response = await getWorkflow(workflowId)
      setWorkflow(response.data as Workflow)
    } catch {
      // swallow — poll failures are non-fatal
    }
  }, [workflowId]) // eslint-disable-line react-hooks/exhaustive-deps

  // Start/stop insight polling based on tab + whether insights have arrived
  useEffect(() => {
    const ctx = workflow?.context || {}
    const isResolved = ['resolved', 'closed'].includes(workflow?.lifecycle_state ?? '')
    // Resolved incidents: wait for post-resolution executive summary.
    // Active incidents: wait for live llm_insights generated during triage.
    const insightsReady = isResolved
      ? !!workflow?.summary
      : Object.keys(ctx.llm_insights || {}).length > 0

    if (activeTab === 'ai-insights' && !insightsReady) {
      if (!insightPollRef.current) {
        insightPollRef.current = setInterval(refreshWorkflowSilently, 5000)
      }
    } else {
      if (insightPollRef.current) {
        clearInterval(insightPollRef.current)
        insightPollRef.current = null
      }
    }

    return () => {
      if (insightPollRef.current) {
        clearInterval(insightPollRef.current)
        insightPollRef.current = null
      }
    }
  }, [activeTab, workflow?.lifecycle_state, workflow?.summary, refreshWorkflowSilently]) // eslint-disable-line react-hooks/exhaustive-deps

  const handleSnowPush = async () => {
    if (!workflow) return
    setSnowPushing(true)
    try {
      const ctx = workflow.context || {}
      const alertPayload = ctx.alert_payload || ctx.sentinel?.alert_payload || {}
      const serviceName = ctx.cmdb?.resource_name || alertPayload.resource_name || workflow.service
      const payload = {
        title: workflow.title || workflowId,
        summary: workflow.summary ?? undefined,
        severity: workflow.severity ?? undefined,
        lifecycle_state: workflow.lifecycle_state,
        service_name: serviceName as string | undefined,
      }
      if (snowMap?.mapped) {
        const r = await updateSnowIncident(workflowId, payload)
        setSnowMap(prev => prev ? { ...prev, mapped: true, push_status: r.data.status, last_pushed_at: new Date().toISOString() } : null)
      } else {
        const r = await pushSnowIncident(workflowId, payload)
        setSnowMap({ mapped: true, snow_number: r.data.snow_number, snow_sys_id: r.data.snow_sys_id, push_status: r.data.status, last_pushed_at: new Date().toISOString() })
      }
    } catch {}
    setSnowPushing(false)
  }

  const handleAddToRunbook = async () => {
    if (!workflow) return
    const ctx = (workflow.context || {}) as any
    const proposal = ctx.proposal || {}
    const sentinel = ctx.sentinel || {}
    const isAiGenerated = proposal.source === 'llm_generated'
    setAddingToRunbook(true)
    setAddRunbookError(null)
    try {
      await createRunbook({
        name: proposal.runbook_name
          ? `${proposal.runbook_name} (AI-generated)`
          : `AI Runbook: ${sentinel.anomaly_type || 'unknown'}`,
        description: `AI-generated during incident ${(workflow as any).incident_number_str || workflow.workflow_id}. Review and enable before it is used in automated remediation.`,
        event_type: sentinel.anomaly_type || 'unknown',
        service: workflow.service || undefined,
        environment: ctx.cmdb?.environment || undefined,
        diagnostics: (proposal.diagnostics_steps || []).map((s: any) => ({
          order: s.order,
          name: s.name,
          description: s.description,
          tool: s.tool,
          args_json: s.args_json || s.args || {},
        })),
        actions: (proposal.remediation_steps || []).map((s: any) => ({
          order: s.order,
          name: s.name,
          description: s.description,
          tool: s.tool,
          args_json: s.args_json || s.args || {},
        })),
        verification_steps: [],
        confidence: proposal.confidence ?? 0.9,
        blast_radius: proposal.blast_radius ?? 1,
        // AI-generated runbooks start disabled — ITOM admin must review & enable
        enabled: false,
        source: isAiGenerated ? 'ai_generated' : 'operator_authored',
      })
      setAddedToRunbook(true)
      setTimeout(() => setAddedToRunbook(false), 5000)
    } catch (err: any) {
      setAddRunbookError(err?.response?.data?.detail || 'Failed to save to runbook library')
    } finally {
      setAddingToRunbook(false)
    }
  }

  const handleModalApprove = async (notes: string): Promise<boolean> => {
    if (!workflow) return false
    const success = await approveAction(workflow.workflow_id, notes || 'Approved from details view')
    if (success) await loadWorkflow()
    return success
  }

  const handleModalDiagnosticsOnly = async (notes: string): Promise<boolean> => {
    if (!workflow) return false
    const success = await diagnosticsOnlyAction(workflow.workflow_id, notes)
    if (success) await loadWorkflow()
    return success
  }

  const handleModalReject = async (notes: string): Promise<boolean> => {
    if (!workflow) return false
    const success = await rejectAction(workflow.workflow_id, notes)
    if (success) await loadWorkflow()
    return success
  }

  const handleClose = async () => {
    if (!workflow || !closeSummary.trim() || !closeStepsTaken.trim()) return
    const success = await closeAction(workflow.workflow_id, closeSummary, closeStepsTaken, closeOutcome)
    if (success) {
      setShowCloseModal(false)
      setCloseSummary('')
      setCloseStepsTaken('')
      setCloseOutcome('resolved')
      await loadWorkflow()
    }
  }

  // ── Notes handlers ────────────────────────────────────────────────────────
  const loadNotes = async () => {
    if (!workflow) return
    setNotesLoading(true)
    const fetched = await fetchNotesAction(workflow.workflow_id)
    setNotes(fetched)
    setNotesLoading(false)
    setTimeout(() => notesEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
  }

  const handleAddNote = async () => {
    if (!workflow || !noteBody.trim()) return
    const note = await addNoteAction(workflow.workflow_id, noteBody, noteType, user?.name || 'operator')
    if (note) {
      setNotes(prev => [...prev, note])
      setNoteBody('')
      setTimeout(() => notesEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50)
    }
  }

  const handleRetry = async () => {
    if (!workflow) return
    const success = await retryRemediationAction(workflow.workflow_id, 'Operator requested re-attempt from UI')
    if (success) await loadWorkflow()
  }

  const openCloseModal = (defaultOutcome = 'resolved') => {
    setCloseOutcome(defaultOutcome)
    clearActionError()
    setShowCloseModal(true)
  }

  // ── Storm response handlers ───────────────────────────────────────────────
  const handleStormRelease = async () => {
    if (!workflow) return
    setStormActionLoading(true)
    setStormActionError(null)
    try {
      await releaseStorm(workflow.workflow_id, stormActionNotes || 'Released to individual pipelines by operator')
      setStormActionNotes('')
      await loadWorkflow()
      // Refresh storm detail after release
      getStorm(workflow.workflow_id).then(r => setStormDetail(r.data)).catch(() => {})
    } catch (err: any) {
      setStormActionError(err?.response?.data?.detail || 'Failed to release storm')
    } finally {
      setStormActionLoading(false)
    }
  }

  const handleStormResolve = async () => {
    if (!workflow) return
    setStormActionLoading(true)
    setStormActionError(null)
    try {
      await resolveStorm(workflow.workflow_id, stormActionNotes || 'Resolved collectively by operator')
      setStormActionNotes('')
      await loadWorkflow()
      getStorm(workflow.workflow_id).then(r => setStormDetail(r.data)).catch(() => {})
    } catch (err: any) {
      setStormActionError(err?.response?.data?.detail || 'Failed to resolve storm')
    } finally {
      setStormActionLoading(false)
    }
  }

  const handleStormDismiss = async () => {
    if (!workflow) return
    setStormActionLoading(true)
    setStormActionError(null)
    try {
      await releaseStorm(workflow.workflow_id, `Dismissed as false positive. ${stormActionNotes}`.trim())
      setStormActionNotes('')
      await loadWorkflow()
    } catch (err: any) {
      setStormActionError(err?.response?.data?.detail || 'Failed to dismiss storm')
    } finally {
      setStormActionLoading(false)
    }
  }

  // ── Loading state ─────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '6rem 2rem',
          gap: '1rem',
        }}
      >
        <div className="spinner" />
        <p style={{ color: '#a0aec0', fontWeight: 500 }}>Loading incident details…</p>
      </div>
    )
  }

  // ── Error state ───────────────────────────────────────────────────────────
  if (error || !workflow) {
    return (
      <div style={{ maxWidth: '900px', margin: '0 auto', padding: '2rem' }}>
        <button onClick={onBack} className="btn btn-secondary gap-2 mb-6">
          <IconArrowLeft size={18} />
          Back
        </button>
        <div
          className="card"
          style={{ borderColor: '#dc262660', backgroundColor: '#7f1d1d20' }}
        >
          <div style={{ display: 'flex', gap: '0.75rem', alignItems: 'center' }}>
            <IconAlertCircle size={24} color="#dc2626" />
            <p style={{ color: '#fca5a5', fontWeight: 500 }}>{error || 'Incident not found'}</p>
          </div>
        </div>
      </div>
    )
  }

  // ── Derived values ────────────────────────────────────────────────────────

  const incNum =
    workflow.incident_number_str ||
    (workflow.incident_number
      ? `INC${String(workflow.incident_number).padStart(4, '0')}`
      : null)

  const title = getIncidentDisplayName(workflow)

  const sevColor    = severityColor(workflow.severity)
  const lifColor    = lifecycleColor(workflow.lifecycle_state)
  const rColor      = riskColor(workflow.risk_score)
  const durationMin = calculateDurationMinutes(workflow.created_at, workflow.updated_at, workflow.lifecycle_state)
  const durationStr = formatDuration(durationMin)

  // Pull context layers
  const ctx           = workflow.context || {}
  const sentCtx       = ctx.sentinel || {}
  const alertPayload  = ctx.alert_payload || sentCtx.alert_payload || {}
  const cmdb          = ctx.cmdb || ctx.cmdb_context || {}
  const resourceInfo  = cmdb.resource_info || {}
  const risk          = ctx.risk || {}
  const proposal      = ctx.proposal || {}
  const governance    = ctx.governance || {}
  const execResults   = ctx.execution_results || ctx.runbook_execution_results || []
  const verification  = ctx.verification || {}

  // Convenience
  const anomalyType    = sentCtx.anomaly_type || alertPayload.type || ctx.event_type || '—'
  const resourceName   = cmdb.resource_name || alertPayload.resource_name || workflow.service || '—'
  const environment    = cmdb.environment || resourceInfo.environment || workflow.environment || '—'
  const resourceOwner  = resourceInfo.owner || '—'
  const resourceType   = resourceInfo.type || '—'
  const blastRadius    = risk.blast_radius ?? governance.blast_radius_limit ?? proposal.blast_radius ?? null
  const runbookName    = proposal.runbook_name || ctx.selected_runbook?.name || '—'
  const approvalReqd   = governance.approval_required ?? ctx.requires_manual_approval ?? false

  // Storm linkage — detect via multiple signals; worker versions may store context differently
  const isStormParent = (
    ctx.is_storm_parent === true ||
    String(ctx.is_storm_parent).toLowerCase() === 'true' ||
    (sentCtx.anomaly_type || alertPayload.type) === 'event_storm' ||
    (workflow.title || '').startsWith('[STORM]')
  )
  const stormId       = workflow.storm_id || null
  const isStormChild  = Boolean(stormId) && !isStormParent

  // External-source tracking
  const sourceConnector        = alertPayload.source_connector as string | undefined
  const isExternalSource       = Boolean(sourceConnector)
  const isExternalDocumented   = ctx.decision_result === 'external_source_documented' && isExternalSource
  // Legacy compat — old incidents may still carry external_source_pending
  const isExternalPending      = ctx.decision_result === 'external_source_pending' && isExternalSource

  // Tab definitions
  // Storm parents always have AI content (hypothesis + pattern analysis); regular incidents check llm fields
  const hasAiContent = !!(workflow.summary || workflow.technical_summary || ctx.llm_insights || isStormParent)

  const tabs: Array<{ id: TabView; label: string; icon: React.ReactNode; badge?: boolean }> = [
    { id: 'overview',     label: 'Overview',      icon: <IconClipboardList size={15} /> },
    { id: 'events-page',  label: 'Events',        icon: <IconActivity size={15} /> },
    { id: 'policy',       label: 'Governance',    icon: <IconShield size={15} /> },
    { id: 'remediation',  label: 'Remediation',   icon: <IconRefresh size={15} /> },
    { id: 'ai-insights',  label: 'AI Insights',   icon: <IconRobot size={15} />, badge: hasAiContent },
    { id: 'risk-summary', label: 'Risk',          icon: <IconAlertTriangle size={15} /> },
    { id: 'timeline',     label: 'Timeline',      icon: <IconHistory size={15} /> },
    { id: 'audit',        label: 'Audit',         icon: <IconFileText size={15} /> },
    { id: 'notes',        label: 'Work Notes',    icon: <IconMessage size={15} /> },
  ]

  // ── Build timeline entries in lowercase LifecycleState keys ─────────────────
  // IncidentTimeline now works directly with backend state strings (no mapping needed).
  const buildTimeline = () => {
    // ── Real history path (new incidents with state_history) ────────────────
    if (workflow.state_history && workflow.state_history.length > 0) {
      // Deduplicate: keep only the first occurrence of each state (preserves order)
      const seen = new Set<string>()
      return workflow.state_history
        .filter(entry => {
          const key = entry.state?.toLowerCase()
          if (!key || seen.has(key)) return false
          seen.add(key)
          return true
        })
        .map(entry => ({
          state: entry.state.toLowerCase(),
          timestamp: entry.timestamp,
          reason: entry.reason,
        }))
    }

    // ── Legacy fallback: infer visited states from context ───────────────────
    const events: Array<{ state: string; timestamp: string; reason?: string }> = []
    const createdMs = new Date(workflow.created_at).getTime()
    const updatedMs = new Date(workflow.updated_at).getTime()

    // open — always first (real timestamp)
    events.push({
      state: 'open',
      timestamp: workflow.created_at,
      reason: anomalyType !== '—'
        ? `${anomalyType} detected on ${resourceName}`
        : 'Incident detected by monitoring',
    })

    // investigating — sentinel/risk context was populated
    if (ctx.sentinel || ctx.risk || workflow.risk_score) {
      events.push({
        state: 'investigating',
        timestamp: '',
        reason: `Risk score: ${Math.round(Number(workflow.risk_score || ctx.risk?.risk_score || 0))}/100. Severity: ${workflow.severity || 'unknown'}`,
      })
    }

    const govPolicies = ctx.governance?.matching_policies || ctx.matched_policies || []
    const govApprovalRequired = ctx.governance?.approval_required ?? ctx.requires_manual_approval

    // waiting_approval — governance says manual approval required
    if (govApprovalRequired) {
      events.push({
        state: 'waiting_approval',
        timestamp: '',
        reason: `Awaiting CAB approval. Policy: ${govPolicies[0]?.name || 'governance policy'}`,
      })
    }

    // approved — decision_result approved or governance auto-approved
    const decisionResult = ctx.decision_result
    const autoApproved = ctx.governance && !ctx.governance.approval_required
    if (decisionResult === 'approved' || autoApproved) {
      const govAllowed = ctx.governance?.allowed_actions || ctx.approved_actions || []
      events.push({
        state: 'approved',
        timestamp: '',
        reason: autoApproved
          ? `Auto-approved by policy: ${govPolicies[0]?.name || 'Automatic Remediation'}`
          : `Approved for remediation. Actions: ${govAllowed.join(', ') || 'all actions'}`,
      })
    }

    // in_remediation / failed / awaiting_manual
    const proposalRunbook = ctx.proposal?.runbook_name || ctx.proposal?.action
    const executionResults: any[] = ctx.execution_results || []
    const lcState = workflow.lifecycle_state
    if (proposalRunbook && ['executing', 'in_remediation', 'resolved', 'failed', 'awaiting_manual', 'verified'].includes(lcState)) {
      events.push({
        state: 'in_remediation',
        timestamp: '',
        reason: `Executing runbook: ${proposalRunbook.replace(/_/g, ' ')}`,
      })

      if (lcState === 'awaiting_manual') {
        events.push({
          state: 'awaiting_manual',
          timestamp: '',
          reason: 'Automated remediation did not resolve the issue. Human intervention required.',
        })
      } else if (executionResults.length > 0) {
        const anyFailed = executionResults.some((r: any) => r.status === 'failed' || r.success === false)
        if (lcState === 'failed' || anyFailed) {
          const failedStep = executionResults.find((r: any) => r.status === 'failed' || r.success === false)
          events.push({
            state: 'failed',
            timestamp: '',
            reason: failedStep?.output || failedStep?.error || 'Remediation failed',
          })
        }
      } else if (lcState === 'failed') {
        events.push({
          state: 'failed',
          timestamp: '',
          reason: ctx.last_error?.message || 'Execution failed — check audit log',
        })
      }
    }

    // rejected — governance rejected
    if (lcState === 'rejected' || decisionResult === 'rejected') {
      events.push({
        state: 'rejected',
        timestamp: '',
        reason: ctx.decision_notes || ctx.governance?.decision_notes || 'Remediation rejected by policy',
      })
    }

    // resolved / closed
    if (['resolved', 'closed'].includes(lcState)) {
      if (!events.find(e => e.state === 'resolved')) {
        events.push({ state: 'resolved', timestamp: '', reason: workflow.resolution_notes || undefined })
      }
    }
    if (lcState === 'closed') {
      events.push({ state: 'closed', timestamp: '', reason: workflow.resolution_notes || undefined })
    }

    // Distribute timestamps proportionally between created_at and updated_at
    const totalMs = Math.max(updatedMs - createdMs, 1000)
    events.forEach((event, idx) => {
      if (idx === 0) return // first event keeps real created_at
      if (!event.timestamp) {
        const fraction = events.length > 1 ? idx / (events.length - 1) : 1
        event.timestamp = new Date(createdMs + totalMs * fraction).toISOString()
      }
    })

    return events
  }

  const timeline = buildTimeline()

  // ─────────────────────────────────────────────────────────────────────────
  return (
    <div style={{ maxWidth: '1280px', margin: '0 auto' }} className="page-transition-enter">
      {/* ── HEADER ── */}
      <div
        style={{
          backgroundColor: '#1a1f2e',
          border: '1px solid #3d4557',
          borderLeft: `4px solid ${sevColor}`,
          borderRadius: '12px',
          padding: '1.5rem 2rem',
          marginBottom: '1.5rem',
          position: 'relative',
          overflow: 'hidden',
        }}
      >
        {/* Background glow */}
        <div
          style={{
            position: 'absolute',
            top: -60,
            right: -60,
            width: 200,
            height: 200,
            background: `radial-gradient(circle, ${sevColor}10 0%, transparent 70%)`,
            pointerEvents: 'none',
          }}
        />

        {/* Top row: back + live indicator */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '1rem',
          }}
        >
          <button
            onClick={onBack}
            className="btn btn-secondary gap-2"
            style={{ fontSize: '0.8125rem', padding: '0.375rem 0.875rem' }}
          >
            <IconArrowLeft size={16} />
            Incidents
          </button>

          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  backgroundColor: wsConnected ? '#10b981' : '#6b7280',
                  boxShadow: wsConnected ? '0 0 8px #10b98180' : 'none',
                  animation: wsConnected ? 'pulse 2s infinite' : 'none',
                }}
              />
              <span style={{ fontSize: '0.75rem', color: wsConnected ? '#10b981' : '#6b7280', fontWeight: 500 }}>
                {wsConnected ? 'Live' : 'Offline'}
              </span>
            </div>
            <button
              onClick={loadWorkflow}
              className="btn btn-secondary"
              style={{ fontSize: '0.75rem', padding: '0.25rem 0.625rem' }}
            >
              <IconRefresh size={14} />
            </button>
          </div>
        </div>

        {/* Title + INC# (opposite sides) */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1.25rem', flexWrap: 'wrap' }}>
          <h1
            style={{
              fontSize: '1.375rem',
              fontWeight: 700,
              color: '#e8eef5',
              margin: 0,
              lineHeight: 1.3,
              flex: 1,
            }}
          >
            {title}
          </h1>
          {incNum && (
            <span
              style={{
                fontFamily: "'Monaco', 'Courier New', monospace",
                fontSize: '0.9rem',
                fontWeight: 700,
                color: '#e8eef5',
                backgroundColor: 'transparent',
                border: '1px solid #e8eef5',
                padding: '0.3rem 0.875rem',
                borderRadius: '6px',
                whiteSpace: 'nowrap',
                flexShrink: 0,
              }}
            >
              {incNum}
            </span>
          )}
        </div>

        {/* Badges row */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.75rem',
            marginTop: '0.875rem',
            flexWrap: 'wrap',
          }}
        >
          <span
            style={{
              fontSize: '0.7rem',
              fontWeight: 700,
              color: lifColor,
              border: `1px solid ${lifColor}60`,
              backgroundColor: 'transparent',
              borderRadius: '6px',
              padding: '0.25rem 0.625rem',
              textTransform: 'uppercase',
              letterSpacing: '0.5px',
            }}
          >
            {lifecycleLabel(workflow.lifecycle_state)}
          </span>
          {workflow.severity && (
            <span
              style={{
                fontSize: '0.7rem',
                fontWeight: 700,
                color: sevColor,
                border: `1px solid ${sevColor}60`,
                backgroundColor: 'transparent',
                borderRadius: '6px',
                padding: '0.25rem 0.625rem',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
              }}
            >
              {workflow.severity} severity
            </span>
          )}
          {approvalReqd && workflow.lifecycle_state === 'waiting_approval' && (
            <span
              style={{
                fontSize: '0.7rem',
                fontWeight: 700,
                color: '#a855f7',
                border: '1px solid #a855f760',
                backgroundColor: 'transparent',
                borderRadius: '6px',
                padding: '0.25rem 0.625rem',
                textTransform: 'uppercase',
                letterSpacing: '0.5px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.3rem',
              }}
            >
              <IconClock size={12} />
              Awaiting Approval
            </span>
          )}
          {isExternalSource && (
            <span
              style={{
                fontSize: '0.7rem',
                fontWeight: 700,
                color: '#38bdf8',
                border: '1px solid #0ea5e960',
                backgroundColor: 'transparent',
                borderRadius: '6px',
                padding: '0.25rem 0.625rem',
                textTransform: 'capitalize',
                letterSpacing: '0.3px',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '0.3rem',
              }}
            >
              <IconActivity size={12} />
              via {sourceConnector}
            </span>
          )}
          <span style={{ marginLeft: 'auto', fontSize: '0.75rem', color: '#7a8ba3' }}>
            {formatDate(workflow.created_at)}
          </span>
        </div>
      </div>

      {/* ── METRIC TILES ── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: '0.875rem',
          marginBottom: '1.5rem',
        }}
      >
        {isStormParent ? (
          /* Storm parent tiles — replace Sentinel metrics with storm-specific data */
          <>
            <MetricTile
              label="Child Incidents"
              value={stormDetail?.child_count ?? ctx.storm_analysis?.child_count ?? (ctx.storm_children as any[])?.length ?? '—'}
              color="#fcd34d"
              sub="in this storm"
            />
            <MetricTile
              label="Pattern"
              value={(stormDetail?.pattern || ctx.storm_analysis?.event_type_pattern || '—').replace(/_/g, ' ')}
              color="#c084fc"
              sub="storm type"
            />
            <MetricTile
              label="Confidence"
              value={
                stormDetail?.confidence != null ? `${Math.round(stormDetail.confidence * 100)}%`
                : ctx.storm_analysis?.confidence != null ? `${Math.round(ctx.storm_analysis.confidence * 100)}%`
                : '—'
              }
              color={
                (stormDetail?.confidence ?? ctx.storm_analysis?.confidence) != null
                  ? aiConfidenceColor(stormDetail?.confidence ?? ctx.storm_analysis?.confidence)
                  : '#7a8ba3'
              }
              sub="storm confidence"
            />
            <MetricTile
              label="Duration"
              value={durationStr}
              color="#7a8ba3"
              sub={`since ${parseUTC(workflow.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`}
            />
            <MetricTile
              label="Resources"
              value={stormDetail?.affected_resources?.length ?? (ctx.storm_analysis?.affected_resources as any[])?.length ?? resourceName}
              color="#f97316"
              sub="affected"
            />
          </>
        ) : (
          /* Normal incident tiles */
          <>
            <MetricTile
              label="Risk Score"
              value={workflow.risk_score != null ? `${Math.round(workflow.risk_score)}/100` : '—'}
              color={rColor}
              sub="out of 100"
            />
            <MetricTile
              label="Severity"
              value={workflow.severity?.toUpperCase() || '—'}
              color={sevColor}
              sub="reported"
            />
            <MetricTile
              label="Blast Radius"
              value={blastRadius != null ? `L${blastRadius}` : '—'}
              color={blastRadius && blastRadius >= 3 ? '#dc2626' : blastRadius === 2 ? '#f59e0b' : '#10b981'}
              sub={blastRadius === 1 ? 'pod only' : blastRadius === 2 ? 'service' : blastRadius === 3 ? 'service group' : undefined}
            />
            <MetricTile
              label="Duration"
              value={durationStr}
              color="#7a8ba3"
              sub={`since ${parseUTC(workflow.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}`}
            />
            <MetricTile
              label="Resource"
              value={resourceName === '—' ? '—' : resourceName.length > 16 ? resourceName.slice(0, 16) + '…' : resourceName}
              color="#3b82f6"
              sub={environment !== '—' ? environment : undefined}
            />
          </>
        )}
      </div>

      {/* ── STORM CHILD BANNER ── */}
      {isStormChild && (
        <div
          style={{
            backgroundColor: '#110d1f',
            border: '1px solid #7c3aed60',
            borderLeft: '4px solid #7c3aed',
            borderRadius: '10px',
            padding: '1rem 1.25rem',
            marginBottom: '1.5rem',
          }}
        >
          {/* Header row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '1rem' }}>⚡</span>
            <p style={{ fontSize: '0.875rem', fontWeight: 700, color: '#c084fc', margin: 0 }}>
              Part of an Event Storm
            </p>
            {stormDetail && (
              <span
                style={{
                  fontSize: '0.7rem',
                  fontWeight: 600,
                  color: '#a78bfa',
                  border: '1px solid #7c3aed50',
                  backgroundColor: 'transparent',
                  borderRadius: '5px',
                  padding: '0.125rem 0.5rem',
                  textTransform: 'uppercase',
                  letterSpacing: '0.3px',
                }}
              >
                {stormDetail.pattern?.replace(/_/g, ' ') || 'correlated events'}
              </span>
            )}
            {stormDetail && (
              <span style={{ marginLeft: 'auto', fontSize: '0.75rem', color: '#7a8ba3' }}>
                {stormDetail.child_count} related incident{stormDetail.child_count !== 1 ? 's' : ''}
              </span>
            )}
          </div>

          {/* Hypothesis */}
          {stormLoading && (
            <p style={{ fontSize: '0.8125rem', color: '#7a8ba3', margin: '0 0 0.75rem' }}>Loading storm analysis…</p>
          )}
          {stormDetail?.hypothesis && (
            <div
              style={{
                backgroundColor: '#1a1030',
                border: '1px solid #7c3aed30',
                borderRadius: '8px',
                padding: '0.75rem 1rem',
                marginBottom: '0.875rem',
              }}
            >
              <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#a78bfa', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.375rem' }}>
                ✦ AI Root Cause Hypothesis
              </p>
              <p style={{ fontSize: '0.85rem', color: '#d1c4f5', lineHeight: 1.6, margin: 0 }}>
                {stormDetail.hypothesis}
              </p>
            </div>
          )}

          {/* Sibling incidents */}
          {stormDetail?.children && stormDetail.children.length > 1 && (
            <div style={{ marginBottom: '0.875rem' }}>
              <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.375rem' }}>
                Related Incidents in Storm
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                {stormDetail.children
                  .filter(c => c.workflow_id !== workflowId)
                  .slice(0, 8)
                  .map(child => (
                    <button
                      key={child.workflow_id}
                      onClick={() => onViewWorkflow?.(child.workflow_id)}
                      disabled={!onViewWorkflow}
                      style={{
                        fontSize: '0.75rem',
                        fontWeight: 600,
                        padding: '0.25rem 0.625rem',
                        backgroundColor: 'transparent',
                        color: '#c084fc',
                        border: '1px solid #7c3aed40',
                        borderRadius: '5px',
                        cursor: onViewWorkflow ? 'pointer' : 'default',
                        transition: 'background 150ms',
                        fontFamily: "'Monaco', 'Courier New', monospace",
                      }}
                      title={child.title || child.workflow_id}
                    >
                      {child.workflow_id.slice(-6).toUpperCase()}
                      {child.resource_name ? ` · ${child.resource_name}` : ''}
                    </button>
                  ))}
              </div>
            </div>
          )}

          {/* Go to storm parent */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
            <p style={{ fontSize: '0.78rem', color: '#7a8ba3', margin: 0, flex: 1 }}>
              Document your investigation on the storm parent so all related incidents are tracked together.
            </p>
            {stormDetail && onViewWorkflow && (
              <button
                onClick={() => onViewWorkflow(stormDetail.storm_id)}
                className="btn gap-2"
                style={{
                  fontSize: '0.8rem',
                  backgroundColor: '#4c1d95',
                  color: '#e9d5ff',
                  border: '1px solid #7c3aed60',
                  fontWeight: 600,
                  flexShrink: 0,
                }}
              >
                ⚡ Go to Storm Parent
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── STORM PARENT BANNER ── */}
      {isStormParent && (
        <div
          style={{
            backgroundColor: '#0e1420',
            border: '1px solid #f59e0b50',
            borderLeft: '4px solid #f59e0b',
            borderRadius: '10px',
            padding: '1rem 1.25rem',
            marginBottom: '1.5rem',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '1rem' }}>⚡</span>
            <p style={{ fontSize: '0.875rem', fontWeight: 700, color: '#fcd34d', margin: 0 }}>
              Storm Parent Incident
            </p>
            {stormDetail && (
              <span style={{ fontSize: '0.75rem', color: '#fbbf24', backgroundColor: 'transparent', border: '1px solid #f59e0b50', borderRadius: '5px', padding: '0.125rem 0.5rem', fontWeight: 600 }}>
                {stormDetail.child_count} child incident{stormDetail.child_count !== 1 ? 's' : ''}
              </span>
            )}
            {stormDetail && (
              <span style={{ fontSize: '0.75rem', color: '#7a8ba3', marginLeft: 'auto' }}>
                {stormDetail.affected_resources?.length || 0} resource{(stormDetail.affected_resources?.length || 0) !== 1 ? 's' : ''} affected
              </span>
            )}
          </div>
        </div>
      )}

      {/* ── EXTERNAL-SOURCE DOCUMENTED NOTICE ── */}
      {isExternalDocumented && workflow.lifecycle_state === 'in_progress' && (
        /* External alert with auto-remediation OFF — enrichment written to work notes */
        <div
          style={{
            backgroundColor: '#0d1f2e',
            border: '1px solid #0ea5e930',
            borderLeft: '4px solid #0ea5e9',
            borderRadius: '10px',
            padding: '1rem 1.25rem',
            marginBottom: '1.5rem',
            display: 'flex',
            alignItems: 'flex-start',
            gap: '1rem',
            flexWrap: 'wrap',
          }}
        >
          <IconFileText size={18} style={{ color: '#38bdf8', flexShrink: 0, marginTop: '0.1rem' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <p style={{ fontSize: '0.8125rem', fontWeight: 600, color: '#38bdf8', margin: '0 0 0.25rem' }}>
              External alert from{' '}
              <span style={{ textTransform: 'capitalize' }}>{sourceConnector}</span>
              {' '}— enrichment documented
            </p>
            <p style={{ fontSize: '0.775rem', color: '#8090a8', margin: '0 0 0.2rem' }}>
              Sentinel analysis, CMDB assessment, and Mechanic remediation steps have been written to{' '}
              <strong style={{ color: '#a0aec0' }}>Work Notes</strong>.
              Auto-remediation is <strong style={{ color: '#f59e0b' }}>disabled</strong> for this connector — no actions have been executed.
            </p>
            <p style={{ fontSize: '0.72rem', color: '#556070', margin: 0 }}>
              To enable auto-execution for future {sourceConnector} alerts: Connector Hub → {sourceConnector} → Allow Auto-Remediation
            </p>
          </div>
          {canAct && (
            <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0, alignSelf: 'center' }}>
              <button
                onClick={() => setActiveTab('notes')}
                className="btn gap-2"
                style={{ fontSize: '0.8rem', backgroundColor: 'transparent', color: '#94a3b8', border: '1px solid #94a3b8', fontWeight: 500 }}
              >
                <IconMessage size={14} />
                Work Notes
              </button>
              <button
                onClick={() => openCloseModal('manual_fix')}
                className="btn gap-2"
                style={{ fontSize: '0.8rem', backgroundColor: 'transparent', color: '#10b981', border: '1px solid #10b981', fontWeight: 600 }}
              >
                <IconCircleCheck size={14} />
                Close Incident
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── LEGACY EXTERNAL-PENDING BANNER (old incidents before v5.8) ── */}
      {workflow.lifecycle_state === 'waiting_approval' && isExternalPending && (
        <div
          style={{
            backgroundColor: '#0d1f2e',
            border: '1px solid #0ea5e940',
            borderLeft: '4px solid #0ea5e9',
            borderRadius: '10px',
            padding: '1rem 1.25rem',
            marginBottom: '1.5rem',
            display: 'flex',
            alignItems: 'center',
            gap: '1rem',
          }}
        >
          <IconActivity size={16} style={{ color: '#38bdf8', flexShrink: 0 }} />
          <p style={{ fontSize: '0.8125rem', color: '#8090a8', margin: 0, flex: 1 }}>
            External alert from <strong style={{ color: '#e8eef5', textTransform: 'capitalize' }}>{sourceConnector}</strong> —
            recommended remediation steps available. Check Work Notes tab.
          </p>
        </div>
      )}

      {/* ── STORM RESPONSE PANEL (replaces standard approval for storm parents) ── */}
      {workflow.lifecycle_state === 'waiting_approval' && isStormParent && canAct && (
        <div
          style={{
            backgroundColor: '#0e0e1a',
            border: '1px solid #f59e0b60',
            borderLeft: '4px solid #f59e0b',
            borderRadius: '10px',
            padding: '1.25rem 1.5rem',
            marginBottom: '1.5rem',
          }}
        >
          <p style={{ fontSize: '0.875rem', fontWeight: 700, color: '#fcd34d', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            ⚡ Storm Response Required
          </p>
          <p style={{ fontSize: '0.8125rem', color: '#a0aec0', marginBottom: '1rem' }}>
            The Storm Agent has grouped {stormDetail?.child_count ?? '?'} correlated incidents. Choose how to proceed — this will affect all child incidents simultaneously.
          </p>

          {/* Notes field */}
          <textarea
            value={stormActionNotes}
            onChange={e => setStormActionNotes(e.target.value)}
            placeholder="Optional: add context or notes for the audit log…"
            rows={2}
            style={{
              width: '100%',
              padding: '0.5rem 0.75rem',
              borderRadius: '6px',
              border: '1px solid #3d4557',
              backgroundColor: '#0d1117',
              color: '#e8eef5',
              fontSize: '0.82rem',
              lineHeight: 1.5,
              resize: 'none',
              outline: 'none',
              fontFamily: 'inherit',
              boxSizing: 'border-box',
              marginBottom: '0.875rem',
            }}
          />

          {stormActionError && (
            <p style={{ fontSize: '0.8rem', color: '#f87171', marginBottom: '0.75rem' }}>{stormActionError}</p>
          )}

          <div style={{ display: 'flex', gap: '0.625rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <button
              onClick={handleStormRelease}
              disabled={stormActionLoading}
              className="btn gap-2"
              style={{ fontSize: '0.8rem', background: 'transparent', color: '#60a5fa', border: '1px solid #3b82f650', fontWeight: 500 }}
              title="Each child incident proceeds through its own remediation pipeline independently"
            >
              <IconRefresh size={14} />
              {stormActionLoading ? 'Working…' : 'Release to Pipelines'}
            </button>

            <button
              onClick={handleStormResolve}
              disabled={stormActionLoading}
              className="btn gap-2"
              style={{ fontSize: '0.8rem', background: 'transparent', color: '#34d399', border: '1px solid #10b98150', fontWeight: 500 }}
              title="Mark all child incidents as resolved — use when the root cause is already fixed"
            >
              <IconCircleCheck size={14} />
              {stormActionLoading ? 'Working…' : 'Resolve All'}
            </button>

            <button
              onClick={handleStormDismiss}
              disabled={stormActionLoading}
              className="btn gap-2"
              style={{ fontSize: '0.8rem', background: 'transparent', color: '#6b7280', border: '1px solid #37415150', fontWeight: 500 }}
              title="False positive — incidents are not actually correlated"
            >
              <IconX size={13} />
              Dismiss
            </button>
          </div>

          <p style={{ fontSize: '0.72rem', color: '#4b5563', margin: '0.625rem 0 0' }}>
            <strong style={{ color: '#6b7280' }}>Release</strong> — each child gets its own runbook proposal.{'  '}
            <strong style={{ color: '#6b7280' }}>Resolve All</strong> — marks all children resolved (root cause already fixed).{'  '}
            <strong style={{ color: '#6b7280' }}>Dismiss</strong> — false positive, releases grouping.
          </p>
        </div>
      )}

      {/* ── APPROVAL ACTIONS (normal CAB approval — not for storm parents) ── */}
      {workflow.lifecycle_state === 'waiting_approval' && !isExternalPending && !isStormParent && (
        /* Normal CAB approval */
        <div
          style={{
            backgroundColor: '#1a0d2e',
            border: '1px solid #a855f740',
            borderLeft: '4px solid #a855f7',
            borderRadius: '10px',
            padding: '1.25rem 1.5rem',
            marginBottom: '1.5rem',
            display: 'flex',
            alignItems: 'center',
            gap: '1.5rem',
            flexWrap: 'wrap',
          }}
        >
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: '0.875rem', fontWeight: 600, color: '#c084fc', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <IconClock size={16} />
              Approval Required
            </p>
            <p style={{ fontSize: '0.8125rem', color: '#a0aec0' }}>
              This incident is waiting for your review. The proposed remediation is: <strong style={{ color: '#e8eef5' }}>{runbookName}</strong>
            </p>
          </div>
          <div style={{ flexShrink: 0 }}>
            <button
              onClick={() => setShowReviewModal(true)}
              className="btn gap-2"
              style={{
                fontSize: '0.8125rem',
                backgroundColor: 'transparent',
                color: '#f59e0b',
                border: '1px solid #f59e0b',
                fontWeight: 600,
              }}
            >
              <IconShield size={16} />
              Review
            </button>
          </div>
        </div>
      )}

      {/* ── AWAITING MANUAL BANNER ── */}
      {workflow.lifecycle_state === 'awaiting_manual' && (
        <div
          style={{
            backgroundColor: '#1c1200',
            border: '1px solid #f59e0b40',
            borderLeft: '4px solid #f59e0b',
            borderRadius: '10px',
            padding: '1.25rem 1.5rem',
            marginBottom: '1.5rem',
            display: 'flex',
            alignItems: 'center',
            gap: '1.5rem',
            flexWrap: 'wrap',
          }}
        >
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: '0.875rem', fontWeight: 600, color: '#f59e0b', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <IconAlertTriangle size={16} />
              Automated Remediation Stalled — Human Action Required
            </p>
            <p style={{ fontSize: '0.8125rem', color: '#a0aec0', margin: 0 }}>
              {workflow.remediation_outcome === 'failed'
                ? 'The runbook executed but could not resolve the issue. Review the Remediation tab for details, then retry automation or close manually.'
                : 'Automated remediation could not complete. Review the findings and take manual action or retry.'}
            </p>
          </div>
          {canAct && (
            <div style={{ display: 'flex', gap: '0.625rem', flexShrink: 0, flexWrap: 'wrap' }}>
              <button
                onClick={handleRetry}
                disabled={actionLoading}
                className="btn gap-2"
                style={{ fontSize: '0.8125rem', backgroundColor: '#1e3a5f', color: '#93c5fd', border: '1px solid #3b82f640', fontWeight: 600 }}
              >
                <IconRefresh size={15} />
                {actionLoading ? 'Retrying…' : 'Retry Automation'}
              </button>
              <button
                onClick={() => { setActiveTab('notes'); }}
                className="btn gap-2"
                style={{ fontSize: '0.8125rem', backgroundColor: '#1a1f2e', color: '#a0aec0', border: '1px solid #3d4557', fontWeight: 500 }}
              >
                <IconMessage size={15} />
                Add Work Note
              </button>
              <button
                onClick={() => openCloseModal('resolved')}
                className="btn gap-2"
                style={{ fontSize: '0.8125rem', backgroundColor: '#f59e0b', color: '#0d0d0d', border: 'none', fontWeight: 600 }}
              >
                Resolve Manually
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── REJECTED BANNER ── */}
      {workflow.lifecycle_state === 'rejected' && (
        <div
          style={{
            backgroundColor: 'transparent',
            border: '1px solid #dc262640',
            borderLeft: '4px solid #dc2626',
            borderRadius: '10px',
            padding: '1.25rem 1.5rem',
            marginBottom: '1.5rem',
            display: 'flex',
            alignItems: 'center',
            gap: '1.5rem',
            flexWrap: 'wrap',
          }}
        >
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: '0.875rem', fontWeight: 600, color: '#f87171', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <IconX size={16} />
              Remediation Rejected by Governance
            </p>
            <p style={{ fontSize: '0.8125rem', color: '#a0aec0', margin: 0 }}>
              {workflow.context?.governance?.decision_notes || workflow.context?.decision_notes || 'The proposed remediation was rejected by governance policy. No automated actions were taken.'}
            </p>
          </div>
          {canAct && (
            <div style={{ flexShrink: 0 }}>
              <button
                onClick={() => openCloseModal('resolved')}
                className="btn gap-2"
                style={{ fontSize: '0.8125rem', backgroundColor: 'transparent', color: '#f87171', border: '1px solid #f87171', fontWeight: 600 }}
              >
                Close
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── CLOSE INCIDENT BANNER (diagnostics-only / manual investigation) ── */}
      {workflow.lifecycle_state === 'in_progress' && workflow.remediation_outcome === 'diagnostics_only' && (
        <div
          style={{
            backgroundColor: '#1a1a0d',
            border: '1px solid #f59e0b40',
            borderLeft: '4px solid #f59e0b',
            borderRadius: '10px',
            padding: '1.25rem 1.5rem',
            marginBottom: '1.5rem',
            display: 'flex',
            alignItems: 'center',
            gap: '1.5rem',
            flexWrap: 'wrap',
          }}
        >
          <div style={{ flex: 1 }}>
            <p style={{ fontSize: '0.875rem', fontWeight: 600, color: '#f59e0b', marginBottom: '0.25rem', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <IconTestPipe size={16} />
              Diagnostics Complete — Awaiting Human Decision
            </p>
            <p style={{ fontSize: '0.8125rem', color: '#a0aec0' }}>
              Automated diagnostics have run. Review the findings above, take any manual steps needed, then close this incident with a summary.
            </p>
          </div>
          {canAct && (
            <div style={{ flexShrink: 0 }}>
              <button
                onClick={() => openCloseModal('resolved')}
                className="btn gap-2"
                style={{ fontSize: '0.8125rem', backgroundColor: '#f59e0b', color: '#0d0d0d', border: 'none', fontWeight: 600 }}
              >
                Close Incident
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── TAB NAV ── */}
      <div
        style={{
          display: 'flex',
          gap: '0.25rem',
          marginBottom: '1.25rem',
          overflowX: 'auto',
          paddingBottom: '2px',
          borderBottom: '1px solid #3d4557',
        }}
      >
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.375rem',
              padding: '0.5rem 0.875rem',
              fontSize: '0.8125rem',
              fontWeight: activeTab === tab.id ? 600 : 400,
              color: activeTab === tab.id ? '#60a5fa' : '#7a8ba3',
              backgroundColor: 'transparent',
              border: 'none',
              borderBottom: activeTab === tab.id ? '2px solid #3b82f6' : '2px solid transparent',
              cursor: 'pointer',
              transition: 'all 150ms ease',
              whiteSpace: 'nowrap',
              marginBottom: '-1px',
              position: 'relative',
            }}
            onMouseEnter={(e) => {
              if (activeTab !== tab.id) e.currentTarget.style.color = '#c1c7d0'
            }}
            onMouseLeave={(e) => {
              if (activeTab !== tab.id) e.currentTarget.style.color = '#7a8ba3'
            }}
          >
            {tab.icon}
            {tab.label}
            {tab.badge && activeTab !== tab.id && (
              <span style={{
                width: 6, height: 6, borderRadius: '50%',
                backgroundColor: '#6366f1',
                boxShadow: '0 0 6px #6366f180',
                flexShrink: 0,
              }} />
            )}
          </button>
        ))}
      </div>

      {/* ── TAB CONTENT ── */}
      <div className="page-transition-enter">

        {/* OVERVIEW TAB */}
        {activeTab === 'overview' && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: '1fr 380px',
              gap: '1.25rem',
              alignItems: 'start',
            }}
          >
            {/* Left: Alert + Context + Reasoning */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

              {/* Alert Details */}
              <SectionCard title="Alert Details" icon={<IconAlertCircle size={16} />} accent={sevColor}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 1.5rem' }}>
                  <DataRow label="Anomaly Type" value={anomalyType} />
                  <DataRow label="Resource" value={resourceName} />
                  <DataRow label="Environment" value={environment} />
                  <DataRow label="Owner" value={resourceOwner} />
                  <DataRow label="Resource Type" value={resourceType} />
                  {alertPayload.anomaly_process && (
                    <DataRow label="Process" value={alertPayload.anomaly_process} mono />
                  )}
                  {sentCtx.confidence != null && (
                    <DataRow label="Confidence" value={`${Math.round(sentCtx.confidence * 100)}%`} />
                  )}
                  {alertPayload.description && (
                    <div style={{ gridColumn: '1 / -1' }}>
                      <DataRow label="Description" value={alertPayload.description} />
                    </div>
                  )}
                </div>
              </SectionCard>

              {/* Storm Context (parent: full analysis; child: sibling list) */}
              {(isStormParent || isStormChild) && stormDetail && (
                <SectionCard title="Storm Analysis" icon={<span style={{ fontSize: '0.95rem' }}>⚡</span>} accent="#7c3aed">
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '0.875rem' }}>
                    <div style={{ padding: '0.625rem 0.75rem', backgroundColor: '#252c3c', borderRadius: '8px' }}>
                      <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.25rem' }}>Pattern</p>
                      <p style={{ fontSize: '0.85rem', fontWeight: 600, color: '#c084fc', margin: 0, textTransform: 'capitalize' }}>
                        {stormDetail.pattern?.replace(/_/g, ' ') || '—'}
                      </p>
                    </div>
                    <div style={{ padding: '0.625rem 0.75rem', backgroundColor: '#252c3c', borderRadius: '8px' }}>
                      <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.25rem' }}>Confidence</p>
                      <p style={{ fontSize: '0.85rem', fontWeight: 600, color: '#a78bfa', margin: 0 }}>
                        {stormDetail.confidence != null ? `${Math.round(stormDetail.confidence * 100)}%` : '—'}
                      </p>
                    </div>
                    <div style={{ padding: '0.625rem 0.75rem', backgroundColor: '#252c3c', borderRadius: '8px' }}>
                      <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.25rem' }}>Child Incidents</p>
                      <p style={{ fontSize: '0.85rem', fontWeight: 600, color: '#e8eef5', margin: 0 }}>{stormDetail.child_count ?? stormDetail.children?.length ?? 0}</p>
                    </div>
                    <div style={{ padding: '0.625rem 0.75rem', backgroundColor: '#252c3c', borderRadius: '8px' }}>
                      <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.25rem' }}>Resources</p>
                      <p style={{ fontSize: '0.85rem', fontWeight: 600, color: '#e8eef5', margin: 0 }}>{stormDetail.affected_resources?.length ?? 0}</p>
                    </div>
                  </div>

                  {/* AI Insights button nudge — directs to the rich analysis tab */}
                  {stormDetail.hypothesis && (
                    <div style={{
                      display: 'flex', alignItems: 'center', gap: '0.625rem',
                      padding: '0.5rem 0.875rem', marginBottom: '0.875rem',
                      backgroundColor: '#1a1030', border: '1px solid #7c3aed30', borderRadius: '8px',
                    }}>
                      <span style={{ fontSize: '0.8rem' }}>✦</span>
                      <p style={{ fontSize: '0.8rem', color: '#a78bfa', margin: 0, flex: 1 }}>
                        AI root cause hypothesis available
                      </p>
                      <button
                        onClick={() => setActiveTab('ai-insights')}
                        style={{
                          fontSize: '0.75rem', fontWeight: 600, padding: '0.2rem 0.75rem',
                          borderRadius: '5px', border: '1px solid #7c3aed60',
                          backgroundColor: 'transparent', color: '#c084fc', cursor: 'pointer',
                        }}
                      >
                        View in AI Insights →
                      </button>
                    </div>
                  )}

                  {/* Child incidents list */}
                  {stormDetail.children && stormDetail.children.length > 0 && (
                    <div>
                      <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.375rem' }}>
                        {isStormParent ? 'All Child Incidents' : 'Related Incidents in Storm'}
                      </p>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem', maxHeight: '220px', overflowY: 'auto' }}>
                        {stormDetail.children
                          .filter(c => isStormParent || c.workflow_id !== workflowId)
                          .map(child => (
                          <div
                            key={child.workflow_id}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              gap: '0.5rem',
                              padding: '0.5rem 0.625rem',
                              backgroundColor: child.workflow_id === workflowId ? '#1e1640' : '#252c3c',
                              borderRadius: '6px',
                              borderLeft: child.workflow_id === workflowId ? '3px solid #7c3aed' : '3px solid transparent',
                            }}
                          >
                            <span style={{ fontFamily: "'Monaco','Courier New',monospace", fontSize: '0.7rem', color: '#a78bfa', fontWeight: 700, flexShrink: 0 }}>
                              {child.workflow_id.slice(-6).toUpperCase()}
                            </span>
                            <span style={{ fontSize: '0.8rem', color: '#c1c7d0', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {child.resource_name || child.event_type || '—'}
                            </span>
                            <span
                              style={{
                                fontSize: '0.65rem',
                                fontWeight: 600,
                                color: ['resolved', 'closed'].includes(child.lifecycle_state) ? '#10b981' : '#f59e0b',
                                backgroundColor: 'transparent',
                                border: `1px solid ${['resolved', 'closed'].includes(child.lifecycle_state) ? '#10b98150' : '#f59e0b50'}`,
                                borderRadius: '4px',
                                padding: '1px 5px',
                                flexShrink: 0,
                              }}
                            >
                              {child.lifecycle_state.replace(/_/g, ' ')}
                            </span>
                            {onViewWorkflow && child.workflow_id !== workflowId && (
                              <button
                                onClick={() => onViewWorkflow(child.workflow_id)}
                                style={{
                                  fontSize: '0.7rem',
                                  padding: '2px 8px',
                                  borderRadius: '4px',
                                  border: '1px solid #7c3aed40',
                                  backgroundColor: 'transparent',
                                  color: '#a78bfa',
                                  cursor: 'pointer',
                                  flexShrink: 0,
                                }}
                              >
                                View
                              </button>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </SectionCard>
              )}

              {/* Governance Decision */}
              {(governance.matching_policies?.length > 0 || governance.decision_notes) && (
                <SectionCard title="Governance Decision" icon={<IconShield size={16} />} accent="#a855f7">
                  {governance.matching_policies?.length > 0 && (
                    <div style={{ marginBottom: '0.875rem' }}>
                      <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.375rem' }}>
                        Matching Policies
                      </p>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                        {governance.matching_policies.map((p: any, i: number) => (
                          <span
                            key={i}
                            style={{
                              fontSize: '0.75rem',
                              padding: '0.25rem 0.625rem',
                              backgroundColor: 'transparent',
                              color: '#c084fc',
                              borderRadius: '6px',
                              border: '1px solid #7c3aed50',
                            }}
                          >
                            {p.name || p}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {governance.allowed_actions?.length > 0 && (
                    <div style={{ marginBottom: '0.875rem' }}>
                      <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.375rem' }}>
                        Allowed Actions
                      </p>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                        {governance.allowed_actions.map((a: string, i: number) => (
                          <span
                            key={i}
                            style={{
                              fontSize: '0.75rem',
                              padding: '0.25rem 0.625rem',
                              backgroundColor: 'transparent',
                              color: '#34d399',
                              borderRadius: '6px',
                              border: '1px solid #10b98150',
                              fontFamily: "'Monaco', 'Courier New', monospace",
                            }}
                          >
                            {a}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {governance.decision_notes && (
                    <DataRow label="Notes" value={governance.decision_notes} />
                  )}
                </SectionCard>
              )}

              {/* Reasoning Trace */}
              {workflow.reasoning_trace.length > 0 && (
                <SectionCard title="Agent Reasoning Trace" icon={<IconBook size={16} />} accent="#3b82f6">
                  <div style={{ maxHeight: '480px', overflowY: 'auto' }}>
                    {workflow.reasoning_trace.map((step, idx) => (
                      <TraceStep key={idx} idx={idx} text={step} />
                    ))}
                  </div>
                </SectionCard>
              )}

              {/* Execution Results */}
              {execResults.length > 0 && (
                <SectionCard title="Execution Results" icon={<IconActivity size={16} />} accent="#10b981">
                  {execResults.map((result: any, idx: number) => {
                    // New format (ctx.execution_results): flat object with status:'success'|'failed'
                    // Legacy format (runbook_execution_results): has a nested .result:{success:bool,message}
                    const r = result.result || result
                    const success = r.success === true
                      || r.status === 'success'
                      || r.status === 'completed'
                    const label = result.tool || r.tool || `Step ${idx + 1}`
                    const detail = r.output || r.message || r.error || ''
                    return (
                      <div
                        key={idx}
                        style={{
                          padding: '0.875rem',
                          backgroundColor: '#252c3c',
                          borderRadius: '8px',
                          borderLeft: `3px solid ${success ? '#10b981' : '#dc2626'}`,
                          marginBottom: '0.5rem',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: detail ? '0.375rem' : 0 }}>
                          {success
                            ? <IconCircleCheck size={16} color="#10b981" />
                            : <IconX size={16} color="#dc2626" />
                          }
                          <span style={{ fontSize: '0.875rem', fontWeight: 600, color: '#e8eef5' }}>
                            {label}
                          </span>
                          <span style={{ fontSize: '0.7rem', color: success ? '#10b981' : '#dc2626', marginLeft: 'auto', fontWeight: 600 }}>
                            {success ? 'SUCCESS' : 'FAILED'}
                          </span>
                        </div>
                        {detail && (
                          <p style={{ fontSize: '0.8125rem', color: '#a0aec0', margin: 0 }}>
                            {detail}
                          </p>
                        )}
                      </div>
                    )
                  })}
                </SectionCard>
              )}
            </div>

            {/* Right: Status + Runbook + Verification */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

              {/* Status Summary */}
              <SectionCard title="Status" icon={<IconClipboardList size={16} />} accent={lifColor}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '0.875rem' }}>
                  {[
                    { label: 'Lifecycle', val: lifecycleLabel(workflow.lifecycle_state), color: lifColor },
                    { label: 'Severity', val: workflow.severity?.toUpperCase() || '—', color: sevColor },
                    { label: 'Risk', val: workflow.risk_score != null ? `${Math.round(workflow.risk_score)}/100` : '—', color: rColor },
                    { label: 'Duration', val: durationStr, color: '#7a8ba3' },
                  ].map(({ label, val, color }) => (
                    <div
                      key={label}
                      style={{
                        padding: '0.75rem',
                        backgroundColor: '#252c3c',
                        borderRadius: '8px',
                        border: `1px solid ${color}30`,
                      }}
                    >
                      <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.25rem' }}>
                        {label}
                      </p>
                      <p style={{ fontSize: '0.875rem', fontWeight: 700, color, margin: 0 }}>{val}</p>
                    </div>
                  ))}
                </div>
                {/* Recurrence notice — same condition validated as repeating while
                    this incident sits open, before anyone has acted on it */}
                {!!workflow.duplicate_count && workflow.duplicate_count > 0 && (
                  <div
                    style={{
                      padding: '0.5rem 0.75rem',
                      backgroundColor: 'transparent',
                      borderRadius: '6px',
                      border: '1px solid #f9731650',
                      marginBottom: '0.75rem',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.5rem',
                    }}
                  >
                    <span style={{ fontSize: '0.8125rem', fontWeight: 700, color: '#f97316' }}>
                      ↻ Recurred {workflow.duplicate_count} additional time{workflow.duplicate_count === 1 ? '' : 's'}
                    </span>
                    <span style={{ fontSize: '0.75rem', color: '#7a8ba3' }}>
                      while this incident has been open
                    </span>
                  </div>
                )}
                {/* Remediation outcome + resolution source — separate from lifecycle */}
                {(workflow.remediation_outcome || workflow.resolution_source) && (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginBottom: '0.75rem' }}>
                    {workflow.remediation_outcome && (() => {
                      const outcomeColor: Record<string, string> = {
                        succeeded: '#10b981', failed: '#ef4444', aborted: '#f59e0b',
                        skipped: '#7a8ba3', pending: '#3b82f6',
                        diagnostics_only: '#f59e0b',
                        resolved_manual: '#10b981', self_resolved: '#10b981', monitoring_manual: '#3b82f6',
                        escalated: '#ef4444', no_action_required: '#7a8ba3',
                      }
                      const outcomeLabel: Record<string, string> = {
                        succeeded: 'Succeeded', failed: 'Failed', aborted: 'Aborted',
                        skipped: 'Skipped', pending: 'Pending',
                        diagnostics_only: 'Diagnostics Only',
                        resolved_manual: 'Resolved (Manual)', self_resolved: 'Self-Resolved', monitoring_manual: 'Monitoring',
                        escalated: 'Escalated', no_action_required: 'No Action Required',
                      }
                      const c = outcomeColor[workflow.remediation_outcome] || '#7a8ba3'
                      return (
                        <div style={{ padding: '0.5rem 0.75rem', backgroundColor: 'transparent', borderRadius: '6px', border: `1px solid ${c}50` }}>
                          <p style={{ fontSize: '0.6rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '2px' }}>Remediation</p>
                          <p style={{ fontSize: '0.8rem', fontWeight: 700, color: c, margin: 0 }}>
                            {outcomeLabel[workflow.remediation_outcome] || workflow.remediation_outcome}
                          </p>
                        </div>
                      )
                    })()}
                    {workflow.resolution_source && (() => {
                      const srcColor: Record<string, string> = {
                        automated_remediation: '#10b981',
                        watcher_all_clear: '#a855f7',
                        manual: '#3b82f6',
                      }
                      const srcLabel: Record<string, string> = {
                        automated_remediation: 'Auto-remediated',
                        watcher_all_clear: 'Watcher all-clear',
                        manual: 'Manual',
                      }
                      const c = srcColor[workflow.resolution_source] || '#7a8ba3'
                      return (
                        <div style={{ padding: '0.5rem 0.75rem', backgroundColor: 'transparent', borderRadius: '6px', border: `1px solid ${c}50` }}>
                          <p style={{ fontSize: '0.6rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '2px' }}>Resolved by</p>
                          <p style={{ fontSize: '0.8rem', fontWeight: 700, color: c, margin: 0 }}>
                            {srcLabel[workflow.resolution_source] || workflow.resolution_source}
                          </p>
                        </div>
                      )
                    })()}
                  </div>
                )}
                {workflow.all_clear_received_at && (
                  <div style={{ marginBottom: '0.75rem', padding: '0.5rem 0.75rem', backgroundColor: 'transparent', borderRadius: '6px', border: '1px solid #a855f750', fontSize: '0.78rem', color: '#c084fc', display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                    <IconBell size={14} />
                    Watcher all-clear received at {parseUTC(workflow.all_clear_received_at).toLocaleString()}
                  </div>
                )}
                <div style={{ paddingTop: '0.75rem', borderTop: '1px solid #3d4557' }}>
                  <DataRow label="Workflow ID" value={workflow.workflow_id} mono />
                  <DataRow label="Created" value={formatDate(workflow.created_at)} />
                  <DataRow label="Last Updated" value={formatDate(workflow.updated_at)} />
                </div>
              </SectionCard>

              {/* ServiceNow */}
              {snowSyncEnabled && (
                <SectionCard title="ServiceNow" icon={<IconBell size={16} />} accent="#38bdf8">
                  {snowLoading ? (
                    <p style={{ fontSize: '0.8125rem', color: '#7a8ba3', margin: 0 }}>Loading…</p>
                  ) : snowMap?.mapped ? (
                    <div>
                      <div style={{ marginBottom: '0.875rem' }}>
                        <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.375rem' }}>Ticket</p>
                        {snowBaseUrl && snowMap.snow_sys_id ? (
                          <a
                            href={`${snowBaseUrl}/nav_to.do?uri=incident.do?sys_id=${snowMap.snow_sys_id}`}
                            target="_blank"
                            rel="noreferrer"
                            style={{ fontFamily: "'Monaco','Courier New',monospace", fontSize: '0.8125rem', fontWeight: 700, color: '#38bdf8', textDecoration: 'none', backgroundColor: '#0c2a3f', padding: '0.2rem 0.6rem', borderRadius: '6px', border: '1px solid #38bdf840' }}
                          >
                            {snowMap.snow_number}
                          </a>
                        ) : (
                          <span style={{ fontFamily: "'Monaco','Courier New',monospace", fontSize: '0.8125rem', fontWeight: 700, color: '#38bdf8' }}>{snowMap.snow_number}</span>
                        )}
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', marginBottom: '0.875rem' }}>
                        <div style={{ padding: '0.5rem 0.75rem', backgroundColor: '#252c3c', borderRadius: '6px' }}>
                          <p style={{ fontSize: '0.6rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '2px' }}>Status</p>
                          <p style={{ fontSize: '0.8rem', fontWeight: 700, color: snowMap.push_status === 'error' ? '#f87171' : '#34d399', margin: 0 }}>{snowMap.push_status || 'synced'}</p>
                        </div>
                        <div style={{ padding: '0.5rem 0.75rem', backgroundColor: '#252c3c', borderRadius: '6px' }}>
                          <p style={{ fontSize: '0.6rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '2px' }}>Last Synced</p>
                          <p style={{ fontSize: '0.7rem', color: '#c1c7d0', margin: 0 }}>{snowMap.last_pushed_at ? parseUTC(snowMap.last_pushed_at).toLocaleString() : '—'}</p>
                        </div>
                      </div>
                      <button onClick={handleSnowPush} disabled={snowPushing} style={{ width: '100%', padding: '0.5rem', fontSize: '0.8125rem', fontWeight: 600, color: '#38bdf8', border: '1px solid #38bdf840', backgroundColor: '#0c2a3f', borderRadius: '6px', cursor: snowPushing ? 'wait' : 'pointer', opacity: snowPushing ? 0.6 : 1 }}>
                        {snowPushing ? 'Pushing…' : 'Push Update to SN'}
                      </button>
                    </div>
                  ) : (
                    <div>
                      <p style={{ fontSize: '0.8125rem', color: '#7a8ba3', marginBottom: '0.75rem' }}>No ticket linked yet.</p>
                      <button onClick={handleSnowPush} disabled={snowPushing} style={{ width: '100%', padding: '0.5rem', fontSize: '0.8125rem', fontWeight: 600, color: '#38bdf8', border: '1px solid #38bdf840', backgroundColor: '#0c2a3f', borderRadius: '6px', cursor: snowPushing ? 'wait' : 'pointer', opacity: snowPushing ? 0.6 : 1 }}>
                        {snowPushing ? 'Creating…' : 'Push to ServiceNow'}
                      </button>
                    </div>
                  )}
                </SectionCard>
              )}

              {/* Proposed Runbook */}
              {(proposal.runbook_name || proposal.diagnostics_steps?.length > 0 || proposal.remediation_steps?.length > 0) && (
                <SectionCard title="Proposed Runbook" icon={<IconBook size={16} />} accent="#f59e0b">
                  <DataRow label="Runbook" value={proposal.runbook_name} />
                  {proposal.confidence != null && (
                    <DataRow label="Confidence" value={`${Math.round(proposal.confidence * 100)}%`} />
                  )}
                  {/* governance.approval_required is authoritative; fall back to proposal field */}
                  {(governance.approval_required != null || proposal.approval_required != null) && (
                    <DataRow
                      label="Approval"
                      value={(governance.approval_required ?? proposal.approval_required)
                        ? 'Required'
                        : 'Auto-approve'}
                    />
                  )}
                  {proposal.diagnostics_steps?.length > 0 && (
                    <div style={{ marginTop: '0.875rem' }}>
                      <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#f59e0b', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.5rem' }}>
                        Diagnostic Steps ({proposal.diagnostics_steps.length})
                      </p>
                      {proposal.diagnostics_steps.map((s: any, i: number) => (
                        <RunbookStepRow key={i} step={s} accent="#f59e0b" />
                      ))}
                    </div>
                  )}
                  {proposal.remediation_steps?.length > 0 && (
                    <div style={{ marginTop: '0.875rem' }}>
                      <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#10b981', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.5rem' }}>
                        Remediation Steps ({proposal.remediation_steps.length})
                      </p>
                      {proposal.remediation_steps.map((s: any, i: number) => (
                        <RunbookStepRow key={i} step={s} accent="#10b981" />
                      ))}
                    </div>
                  )}
                </SectionCard>
              )}

              {/* Verification */}
              {verification.verification_results?.length > 0 && (
                <SectionCard
                  title="Verification"
                  icon={<IconCircleCheck size={16} />}
                  accent={verification.overall_success ? '#10b981' : '#dc2626'}
                >
                  <div style={{ marginBottom: '0.75rem' }}>
                    <span
                      style={{
                        fontSize: '0.75rem',
                        fontWeight: 700,
                        padding: '0.25rem 0.75rem',
                        borderRadius: '6px',
                        backgroundColor: 'transparent',
                        color: verification.overall_success ? '#34d399' : '#fca5a5',
                        border: `1px solid ${verification.overall_success ? '#10b98150' : '#dc262650'}`,
                      }}
                    >
                      {verification.overall_success ? '✓ REMEDIATION EFFECTIVE' : '✗ REMEDIATION INEFFECTIVE'}
                    </span>
                  </div>
                  {verification.verification_results.map((vr: any, i: number) => (
                    <div
                      key={i}
                      style={{
                        padding: '0.625rem',
                        backgroundColor: '#252c3c',
                        borderRadius: '6px',
                        borderLeft: `3px solid ${vr.status === 'passed' ? '#10b981' : '#dc2626'}`,
                        marginBottom: '0.375rem',
                        fontSize: '0.8125rem',
                        color: '#c1c7d0',
                      }}
                    >
                      <strong style={{ color: '#e8eef5' }}>{vr.step_name}</strong>
                      {' — '}{vr.message}
                    </div>
                  ))}
                </SectionCard>
              )}
            </div>
          </div>
        )}

        {/* EVENTS TAB — storm parent shows aggregated events across all children */}
        {activeTab === 'events-page' && isStormParent && stormDetail && stormDetail.children.length > 0 && (() => {
          const sevDot = (sev: string | null | undefined) => {
            const c = sev === 'critical' ? '#dc2626' : sev === 'high' ? '#f97316' : sev === 'medium' ? '#f59e0b' : sev === 'low' ? '#10b981' : '#a0aec0'
            return <span style={{ width: 8, height: 8, borderRadius: '50%', backgroundColor: c, display: 'inline-block', flexShrink: 0 }} />
          }
          const srcBadge = (src: string | null | undefined) => {
            const isSplunk = src && src !== 'watcher_brain'
            return (
              <span style={{
                fontSize: '0.65rem', fontWeight: 700, padding: '1px 6px', borderRadius: '4px',
                backgroundColor: 'transparent',
                color: isSplunk ? '#c084fc' : '#38bdf8',
                border: `1px solid ${isSplunk ? '#a855f750' : '#0ea5e950'}`,
                textTransform: 'uppercase', letterSpacing: '0.3px',
                whiteSpace: 'nowrap',
              }}>
                {isSplunk ? (src || 'splunk') : 'watcher'}
              </span>
            )
          }
          const age = (iso: string) => {
            const ms = Date.now() - new Date(iso).getTime()
            const m = Math.floor(ms / 60000)
            return m < 60 ? `${m}m ago` : `${Math.floor(m / 60)}h ${m % 60}m ago`
          }

          return (
            <div>
              {/* Header */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.25rem' }}>
                <span style={{ fontSize: '1rem' }}>⚡</span>
                <h3 style={{ fontSize: '0.875rem', fontWeight: 700, color: '#e8eef5', margin: 0 }}>
                  Correlated Storm Events
                </h3>
                <span style={{ fontSize: '0.75rem', color: '#7a8ba3', marginLeft: 'auto' }}>
                  {stormDetail.children.length} event{stormDetail.children.length !== 1 ? 's' : ''} across {stormDetail.affected_resources?.length || stormDetail.children.length} resource{(stormDetail.affected_resources?.length || 1) !== 1 ? 's' : ''}
                </span>
              </div>

              {/* Source breakdown */}
              <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '1.25rem', flexWrap: 'wrap' }}>
                {(['watcher_brain', 'splunk'] as const).map(src => {
                  const count = stormDetail.children.filter(c => {
                    if (src === 'watcher_brain') return !c.source_connector || c.source_connector === 'watcher_brain'
                    return c.source_connector && c.source_connector !== 'watcher_brain'
                  }).length
                  if (count === 0) return null
                  const isSplunk = src !== 'watcher_brain'
                  return (
                    <div key={src} style={{ padding: '0.5rem 0.875rem', backgroundColor: isSplunk ? '#1a1030' : '#0d1a2e', borderRadius: '8px', border: `1px solid ${isSplunk ? '#a855f730' : '#0ea5e930'}` }}>
                      <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', margin: '0 0 0.125rem' }}>
                        {isSplunk ? 'Splunk SIEM' : 'Watcher Brain'}
                      </p>
                      <p style={{ fontSize: '1.25rem', fontWeight: 700, color: isSplunk ? '#c084fc' : '#38bdf8', margin: 0 }}>
                        {count}
                      </p>
                    </div>
                  )
                })}
                {/* Event type breakdown */}
                {Array.from(new Set(stormDetail.children.map(c => c.event_type).filter(Boolean))).map(et => (
                  <div key={et} style={{ padding: '0.5rem 0.875rem', backgroundColor: '#1a1f2e', borderRadius: '8px', border: '1px solid #3d4557' }}>
                    <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', margin: '0 0 0.125rem' }}>
                      {et}
                    </p>
                    <p style={{ fontSize: '1.25rem', fontWeight: 700, color: '#f59e0b', margin: 0 }}>
                      {stormDetail.children.filter(c => c.event_type === et).length}
                    </p>
                  </div>
                ))}
              </div>

              {/* Event table */}
              <div style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557', borderRadius: '10px', overflow: 'hidden' }}>
                {/* Table header */}
                <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 140px 80px 100px 80px 60px', gap: '0 0.75rem', padding: '0.5rem 1rem', backgroundColor: '#1e2535', borderBottom: '1px solid #3d4557' }}>
                  {['INC #', 'Resource', 'Event Type', 'Severity', 'Source', 'State', ''].map((h, i) => (
                    <span key={i} style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{h}</span>
                  ))}
                </div>

                {stormDetail.children.map((child, idx) => (
                  <div
                    key={child.workflow_id}
                    style={{
                      display: 'grid',
                      gridTemplateColumns: '90px 1fr 140px 80px 100px 80px 60px',
                      gap: '0 0.75rem',
                      padding: '0.75rem 1rem',
                      borderBottom: idx < stormDetail.children.length - 1 ? '1px solid #252c3c' : 'none',
                      alignItems: 'center',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.backgroundColor = '#252c3c')}
                    onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
                  >
                    {/* INC # */}
                    <span style={{ fontFamily: "'Monaco','Courier New',monospace", fontSize: '0.75rem', fontWeight: 700, color: '#a78bfa' }}>
                      {child.incident_number_str || child.workflow_id.slice(-6).toUpperCase()}
                    </span>

                    {/* Resource */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', minWidth: 0 }}>
                      {sevDot(child.severity)}
                      <div style={{ minWidth: 0 }}>
                        <p style={{ fontSize: '0.82rem', fontWeight: 600, color: '#e8eef5', margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {child.resource_name || '—'}
                        </p>
                        {child.signal_value != null && child.signal_value > 0 && (
                          <p style={{ fontSize: '0.7rem', color: '#f59e0b', margin: 0 }}>
                            {Number(child.signal_value).toLocaleString('en', { maximumFractionDigits: 1 })}
                            {child.signal_threshold != null && child.signal_threshold > 0 && (
                              <span style={{ color: '#6b7280' }}>
                                {' / '}{Number(child.signal_threshold).toLocaleString('en', { maximumFractionDigits: 1 })} limit
                              </span>
                            )}
                          </p>
                        )}
                      </div>
                    </div>

                    {/* Event Type */}
                    <span style={{ fontSize: '0.78rem', color: '#c1c7d0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {child.event_type || '—'}
                    </span>

                    {/* Severity */}
                    <span style={{ fontSize: '0.75rem', fontWeight: 700, color: severityColor(child.severity), textTransform: 'uppercase' }}>
                      {child.severity || '—'}
                    </span>

                    {/* Source */}
                    {srcBadge(child.source_connector)}

                    {/* State */}
                    <span style={{
                      fontSize: '0.7rem', fontWeight: 600,
                      color: ['resolved', 'closed'].includes(child.lifecycle_state) ? '#10b981' : child.lifecycle_state === 'storm_hold' ? '#f59e0b' : '#7a8ba3',
                      textTransform: 'capitalize',
                    }}>
                      {child.lifecycle_state.replace(/_/g, ' ')}
                    </span>

                    {/* Age + view */}
                    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '0.125rem' }}>
                      <span style={{ fontSize: '0.65rem', color: '#6b7280' }}>{age(child.created_at)}</span>
                      {onViewWorkflow && (
                        <button
                          onClick={() => onViewWorkflow(child.workflow_id)}
                          style={{ fontSize: '0.65rem', padding: '1px 6px', borderRadius: '3px', border: '1px solid #3d4557', backgroundColor: 'transparent', color: '#a78bfa', cursor: 'pointer' }}
                        >
                          View
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {/* Pattern note */}
              {stormDetail.pattern && (
                <p style={{ fontSize: '0.78rem', color: '#6b7280', marginTop: '0.875rem', textAlign: 'center' }}>
                  Storm pattern: <strong style={{ color: '#9ca3af' }}>{stormDetail.pattern.replace(/_/g, ' ')}</strong>
                  {stormDetail.confidence != null && ` · ${Math.round(stormDetail.confidence * 100)}% confidence`}
                  {' · '}Events grouped by Storm Agent within a {stormDetail.children.length > 0 ? 'correlated' : ''} time window.
                </p>
              )}
            </div>
          )
        })()}

        {/* EVENTS TAB — regular (non-storm-parent) incidents */}
        {activeTab === 'events-page' && !isStormParent && (
          <IncidentEventsPage workflow={workflow} darkMode={darkMode} />
        )}

        {/* GOVERNANCE TAB */}
        {activeTab === 'policy' && (
          <PolicyEvaluation
            matched_policies={workflow.context?.matched_policies || governance.matching_policies || []}
            approved_actions={workflow.context?.approved_actions || governance.allowed_actions || []}
            requires_manual_approval={workflow.context?.requires_manual_approval || governance.approval_required || false}
            constraints={workflow.context?.constraints || {}}
          />
        )}

        {/* REMEDIATION TAB */}
        {activeTab === 'remediation' && (() => {
          const ctxProposal = ctx.proposal || {}
          // Support both new typed format (runbook_name, remediation_steps) and old flat format (action)
          const runbookName     = ctxProposal.runbook_name || ctxProposal.action || null
          const blastRadius     = ctxProposal.blast_radius ?? 1
          const confidence      = ctxProposal.confidence ?? 0.85
          const remediationSteps: any[] = ctxProposal.remediation_steps || []
          const diagnosticsSteps: any[] = ctxProposal.diagnostics_steps || []

          // execution_results — new format: [{step, tool, status, output}]
          // runbook_execution_results — legacy format: [{step, tool, result:{success,message}}]
          const execResults: any[] = ctx.execution_results || ctx.runbook_execution_results || []

          // Derive overall execution status
          const allDone    = execResults.length > 0 && execResults.every((r: any) => r.status === 'success' || r.success === true)
          const anyFailed  = execResults.some((r: any) => r.status === 'failed' || r.success === false)
          const exStatus: 'pending' | 'in_progress' | 'completed' | 'failed' =
            workflow.lifecycle_state === 'resolved' ? 'completed'
            : workflow.lifecycle_state === 'failed'  ? 'failed'
            : allDone ? 'completed'
            : anyFailed ? 'failed'
            : execResults.length > 0 ? 'in_progress'
            : 'pending'

          // Map backend source strings to RemediationRecommendation tier keys
          const SOURCE_MAP: Record<string, string> = {
            runbook_library:   'runbook',
            cmdb_playbook:     'playbook',
            fallback_escalation: 'fallback',
            llm_generated:     'llm',
          }
          const rawSource   = ctxProposal.source || ''
          const mappedSource = SOURCE_MAP[rawSource] || (
            ctxProposal.runbook_id === 'fallback-escalate' ? 'fallback'
            : ctxProposal.runbook_id ? 'runbook'
            : 'playbook'
          )

          // Build RemediationRecommendation from typed proposal
          const recommendation = runbookName ? {
            source: mappedSource as any,
            confidence,
            risk_level: (blastRadius >= 3 ? 'high' : blastRadius >= 2 ? 'medium' : 'low') as any,
            blast_radius: blastRadius,
            summary: runbookName.replace(/_/g, ' '),
            actions: remediationSteps.map((s: any) => ({ tool: s.tool || 'unknown', args: s.args_json || s.args || {}, timeout: 30 })),
          } : null

          // Map a raw execution result to the RemediationStep shape consumed by RemediationSteps
          const toConsoleStep = (result: any, allPlanned: any[]) => {
            const isNew    = 'status' in result
            const skipped  = isNew ? result.status === 'skipped' : false
            const success  = isNew ? result.status === 'success' : result.result?.success
            const failed   = isNew ? result.status === 'failed'  : !!result.result?.error
            const planned  = allPlanned.find((s: any) => s.tool === result.tool || s.order === result.step)
            return {
              step:        result.step || 1,
              tool:        result.tool || 'unknown',
              description: planned?.description || planned?.name || result.tool || '',
              parameters:  result.parameters || planned?.args_json || planned?.args || {},
              command:     result.command || undefined,
              raw_output:  result.raw_output ?? result.result?.raw_output ?? undefined,
              status:      (skipped ? 'skipped' : success ? 'completed' : failed ? 'failed' : 'pending') as 'completed' | 'failed' | 'pending' | 'in_progress' | 'skipped',
              run_if:      result.run_if || planned?.run_if || undefined,
              message:     result.output || result.message || result.result?.output || result.result?.message || result.result?.error,
              duration:    undefined,
            }
          }

          // Separate executed results by step_type (backend-tagged in new format).
          // Legacy results (no step_type) all go to remediation — preserving prior behavior.
          const diagExecResults  = execResults.filter((r: any) => r.step_type === 'diagnostic')
          const remExecResults   = execResults.filter((r: any) => r.step_type === 'remediation')
          const verifyExecResults = execResults.filter((r: any) => r.step_type === 'verification')
          const incidentUpdateExecResults = execResults.filter((r: any) => r.step_type === 'incident_update')
          const notifyExecResults = execResults.filter((r: any) => r.step_type === 'notify')

          const allDiagResults = diagExecResults
          const allPlanned     = [...diagnosticsSteps, ...remediationSteps]

          const diagConsoleSteps = allDiagResults.map((r: any) => toConsoleStep(r, allPlanned))
          const remConsoleSteps  = remExecResults.map((r: any)  => toConsoleStep(r, allPlanned))
          const verifyConsoleSteps = verifyExecResults.map((r: any) => toConsoleStep(r, allPlanned))
          const incidentUpdateConsoleSteps = incidentUpdateExecResults.map((r: any) => toConsoleStep(r, allPlanned))
          const notifyConsoleSteps = notifyExecResults.map((r: any) => toConsoleStep(r, allPlanned))

          // Progress overview uses all executed steps combined
          const allExecForProgress = allDiagResults.concat(remExecResults)
          const actionSteps: any[] = allExecForProgress.map((r: any) => {
            const skipped = r.status === 'skipped'
            const success = r.status === 'success' || r.success === true
            const failed  = r.status === 'failed'  || r.success === false
            return {
              step:     r.step ?? 1,
              action:   (r.tool || 'unknown').replace(/_/g, ' '),
              status:   skipped ? 'skipped' : success ? 'completed' : failed ? 'failed' : 'in_progress' as any,
              duration: skipped ? '⏭ skipped' : success ? '✓ done' : failed ? '✗ failed' : '…',
              details:  r.output || r.message || undefined,
            }
          })

          // Determine if this was a diagnostics-only run (remediation suppressed)
          const isDiagnosticsOnly = workflow.lifecycle_state === 'in_progress'
            && diagConsoleSteps.length > 0
            && remConsoleSteps.length === 0
            && remediationSteps.length > 0

          return (
            <div className="space-y-6">
              <RemediationRecommendation recommendation={recommendation} executionStatus={exStatus} />

              {/* Progress overview — only shown when steps have actually run */}
              {actionSteps.length > 0 && (
                <ActionExecution actions={actionSteps} overallStatus={exStatus} />
              )}

              {/* Diagnostics Steps — console output */}
              {(diagConsoleSteps.length > 0 || diagnosticsSteps.length > 0) && (
                <RemediationSteps
                  steps={diagConsoleSteps}
                  label="Diagnostics Steps"
                />
              )}

              {/* Remediation Steps — console output */}
              {isDiagnosticsOnly ? (
                <div style={{
                  backgroundColor: '#1a1f2e',
                  border: '1px solid #3d4557',
                  borderRadius: '10px',
                  padding: '16px',
                }}>
                  <div style={{
                    fontSize: '10px', fontWeight: 600, color: '#a0aec0',
                    letterSpacing: '0.07em', textTransform: 'uppercase',
                    paddingBottom: '8px', borderBottom: '1px solid #3d4557',
                    marginBottom: '12px',
                  }}>
                    Remediation Steps ({remediationSteps.length})
                  </div>
                  <p style={{ fontSize: '12px', color: '#f59e0b' }}>
                    Suppressed — diagnostics-only approval granted. Remediation requires a separate full approval.
                  </p>
                </div>
              ) : remConsoleSteps.length > 0 ? (
                <RemediationSteps
                  steps={remConsoleSteps}
                  label="Remediation Steps"
                />
              ) : null}

              {/* Verification Steps — metric validation */}
              {verifyConsoleSteps.length > 0 && (
                <RemediationSteps
                  steps={verifyConsoleSteps}
                  label="Verification Steps"
                />
              )}

              {/* Incident Update — resolution state declared by the runbook itself */}
              {incidentUpdateConsoleSteps.length > 0 && (
                <RemediationSteps
                  steps={incidentUpdateConsoleSteps}
                  label="Incident Update"
                />
              )}

              {/* Notifications — notify/alert_escalate/alert_update/send_alert steps.
                  Sending a message isn't a remediation action, so it gets its own
                  section instead of being lumped in with Remediation Steps. */}
              {notifyConsoleSteps.length > 0 && (
                <RemediationSteps
                  steps={notifyConsoleSteps}
                  label="Notifications"
                />
              )}

              {/* ── Save AI Runbook to Library — only for llm_generated proposals, ITOM admin+ ── */}
              {(() => {
                const ctx2 = (workflow.context || {}) as any
                const proposal2 = ctx2.proposal || {}
                const canSaveToLib = user?.role === 'admin' || user?.role === 'itom_admin'
                if (
                  exStatus === 'completed' &&
                  proposal2.source === 'llm_generated' &&
                  canSaveToLib &&
                  (remediationSteps.length > 0 || remConsoleSteps.length > 0)
                ) {
                  return (
                    <div style={{
                      backgroundColor: '#1a1f2e',
                      border: '1px solid rgba(139,92,246,0.35)',
                      borderRadius: '10px',
                      padding: '14px 16px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: '12px',
                    }}>
                      <div>
                        <p style={{ fontSize: '12px', fontWeight: 600, color: '#e8eef5', margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span style={{ fontSize: '10px', fontWeight: 700, color: '#a78bfa', backgroundColor: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)', borderRadius: '4px', padding: '1px 6px', letterSpacing: '0.05em' }}>AI</span>
                          Save AI Runbook to Library
                        </p>
                        <p style={{ fontSize: '11px', color: '#7a8ba3', margin: '2px 0 0' }}>
                          Persist these AI-generated steps as a disabled runbook — review and enable it in the Runbooks library before it runs automatically
                        </p>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
                        {addRunbookError && (
                          <span style={{ fontSize: '11px', color: '#f87171' }}>{addRunbookError}</span>
                        )}
                        {addedToRunbook ? (
                          <span style={{
                            display: 'flex', alignItems: 'center', gap: '5px',
                            fontSize: '11px', fontWeight: 600, color: '#a78bfa',
                            backgroundColor: 'rgba(139,92,246,0.10)',
                            border: '1px solid rgba(139,92,246,0.28)',
                            borderRadius: '6px', padding: '5px 12px',
                          }}>
                            <IconCheck size={13} /> Saved — pending review in Runbooks
                          </span>
                        ) : (
                          <button
                            onClick={handleAddToRunbook}
                            disabled={addingToRunbook}
                            style={{
                              display: 'flex', alignItems: 'center', gap: '6px',
                              padding: '6px 14px', borderRadius: '6px', border: 'none',
                              backgroundColor: '#7c3aed',
                              color: '#fff', fontSize: '12px', fontWeight: 600,
                              cursor: addingToRunbook ? 'not-allowed' : 'pointer',
                              opacity: addingToRunbook ? 0.7 : 1,
                            }}
                          >
                            {addingToRunbook
                              ? <><IconLoader2 size={13} /> Saving…</>
                              : <><IconPlus size={13} /> Save to Library</>
                            }
                          </button>
                        )}
                      </div>
                    </div>
                  )
                }
                return null
              })()}
            </div>
          )
        })()}

        {/* AI INSIGHTS TAB */}
        {activeTab === 'ai-insights' && (() => {
          const ins = (ctx.llm_insights || {}) as Record<string, any>
          const hasInsights = Object.keys(ins).length > 0
          const conf = typeof ins.confidence === 'number' ? ins.confidence : null
          const confColor = conf != null ? aiConfidenceColor(conf) : '#7a8ba3'
          const ragCount: number = ins.rag_similar_count || 0
          const noSimilar = !ins.similar_pattern || (ins.similar_pattern as string).toLowerCase().startsWith('no similar')

          const CARD: React.CSSProperties = {
            backgroundColor: '#1a1f2e',
            border: '1px solid #3d4557',
            borderRadius: '10px',
            overflow: 'hidden',
          }
          const CARD_HDR: React.CSSProperties = {
            padding: '0.625rem 1rem',
            borderBottom: '1px solid #3d4557',
            backgroundColor: '#1e2535',
          }
          const LABEL: React.CSSProperties = {
            fontSize: '0.7rem',
            fontWeight: 600,
            color: '#a0aec0',
            letterSpacing: '0.07em',
            textTransform: 'uppercase',
          }
          const INNER: React.CSSProperties = {
            backgroundColor: '#252c3c',
            border: '1px solid #3d4557',
            borderRadius: '8px',
            padding: '12px 14px',
          }

          // ── Storm parent: show rich storm intelligence instead of generic summary ──
          if (isStormParent) {
            if (stormLoading) {
              return (
                <div style={{ textAlign: 'center', padding: '4rem 2rem', color: '#7a8ba3' }}>
                  <div className="spinner" />
                  <p style={{ marginTop: '1rem', fontSize: '0.875rem', color: '#a0aec0' }}>Loading storm intelligence…</p>
                </div>
              )
            }
            if (!stormDetail) {
              return (
                <div style={{ textAlign: 'center', padding: '4rem 2rem', color: '#7a8ba3' }}>
                  <IconRobot size={40} style={{ opacity: 0.3 }} />
                  <p style={{ marginTop: '1rem', fontSize: '0.875rem', fontWeight: 600, color: '#a0aec0' }}>Storm analysis unavailable</p>
                </div>
              )
            }

            // Compute severity breakdown from children
            const sevBreakdown: Record<string, number> = {}
            stormDetail.children.forEach(c => {
              const s = c.severity || 'unknown'
              sevBreakdown[s] = (sevBreakdown[s] || 0) + 1
            })
            const sevOrder = ['critical', 'high', 'medium', 'low', 'unknown']

            // State breakdown
            const stateBreakdown: Record<string, number> = {}
            stormDetail.children.forEach(c => {
              const s = c.lifecycle_state || 'unknown'
              stateBreakdown[s] = (stateBreakdown[s] || 0) + 1
            })

            // Event type breakdown
            const etBreakdown: Record<string, number> = {}
            stormDetail.children.forEach(c => {
              const et = c.event_type || 'unknown'
              etBreakdown[et] = (etBreakdown[et] || 0) + 1
            })

            // Topology evidence resource count
            const topoKeys = Object.keys(stormDetail.topology_evidence || {})

            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

                {/* ── Storm Intelligence Header ── */}
                <div style={{ ...CARD, borderLeft: '4px solid #f59e0b', border: '1px solid #f59e0b40' }}>
                  <div style={{
                    ...CARD_HDR,
                    borderBottom: '1px solid #f59e0b20',
                    backgroundColor: '#0e1015',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <span style={{ fontSize: '1rem' }}>⚡</span>
                      <span style={{ ...LABEL, color: '#fcd34d', letterSpacing: '0.06em' }}>Storm Intelligence</span>
                      {stormDetail.pattern && (
                        <span style={{
                          fontSize: '0.7rem', fontWeight: 700, color: '#c084fc',
                          border: '1px solid #7c3aed50', borderRadius: '5px', padding: '2px 8px',
                          textTransform: 'capitalize',
                        }}>
                          {stormDetail.pattern.replace(/_/g, ' ')}
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
                      {stormDetail.llm_used && (
                        <span style={{
                          fontSize: '0.65rem', fontWeight: 700, color: '#a78bfa',
                          backgroundColor: 'rgba(139,92,246,0.12)', border: '1px solid rgba(139,92,246,0.3)',
                          borderRadius: '4px', padding: '2px 7px', letterSpacing: '0.06em',
                        }}>
                          LLM
                        </span>
                      )}
                      {stormDetail.confidence != null && (
                        <span style={{
                          fontSize: '0.75rem', fontWeight: 700,
                          color: aiConfidenceColor(stormDetail.confidence),
                          backgroundColor: `${aiConfidenceColor(stormDetail.confidence)}18`,
                          border: `1px solid ${aiConfidenceColor(stormDetail.confidence)}40`,
                          borderRadius: '5px', padding: '2px 9px',
                        }}>
                          {Math.round(stormDetail.confidence * 100)}% confidence
                        </span>
                      )}
                    </div>
                  </div>

                  {/* Storm summary tiles */}
                  <div style={{ padding: '1rem 1.25rem' }}>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '0.625rem', marginBottom: '1rem' }}>
                      {[
                        { label: 'Child Incidents', val: stormDetail.child_count, color: '#fcd34d' },
                        { label: 'Resources Affected', val: stormDetail.affected_resources?.length ?? 0, color: '#f97316' },
                        { label: 'Event Types', val: Object.keys(etBreakdown).length, color: '#60a5fa' },
                        { label: 'Topology Nodes', val: topoKeys.length, color: '#a78bfa' },
                      ].map(({ label, val, color }) => (
                        <div key={label} style={{ padding: '0.625rem 0.75rem', backgroundColor: '#252c3c', borderRadius: '8px', border: `1px solid ${color}20` }}>
                          <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.25rem' }}>{label}</p>
                          <p style={{ fontSize: '1.25rem', fontWeight: 700, color, margin: 0 }}>{val}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* ── AI Root Cause Hypothesis ── */}
                {stormDetail.hypothesis && (
                  <div style={{ ...CARD, borderLeft: '4px solid #a855f7', border: '1px solid #7c3aed40' }}>
                    <div style={{ ...CARD_HDR, backgroundColor: '#110d1f', borderBottom: '1px solid #7c3aed30', display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '0.8rem' }}>✦</span>
                      <span style={{ ...LABEL, color: '#c084fc' }}>AI Root Cause Hypothesis</span>
                    </div>
                    <div style={{ padding: '1.25rem 1.5rem' }}>
                      <p style={{ fontSize: '0.925rem', color: '#e2d9f3', lineHeight: 1.75, margin: 0, letterSpacing: '0.01em' }}>
                        {stormDetail.hypothesis}
                      </p>
                    </div>
                  </div>
                )}

                {/* ── Root Cause Candidates ── */}
                {stormDetail.root_cause_candidates && stormDetail.root_cause_candidates.length > 0 && (
                  <div style={CARD}>
                    <div style={{ ...CARD_HDR, display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ ...LABEL }}>Root Cause Candidates</span>
                      <span style={{ fontSize: '0.7rem', color: '#5a6a83', marginLeft: 'auto' }}>
                        ranked by confidence
                      </span>
                    </div>
                    <div style={{ padding: '0.875rem 1.25rem', display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                      {stormDetail.root_cause_candidates.map((rc, i) => {
                        const pct = rc.confidence != null ? Math.round(rc.confidence * 100) : null
                        const barColor = i === 0 ? '#a855f7' : i === 1 ? '#6366f1' : '#3b82f6'
                        return (
                          <div key={i} style={{ padding: '0.75rem 1rem', backgroundColor: '#252c3c', borderRadius: '8px', borderLeft: `3px solid ${barColor}` }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: pct != null ? '0.375rem' : 0 }}>
                              <span style={{
                                fontSize: '0.7rem', fontWeight: 800, color: barColor,
                                backgroundColor: `${barColor}18`, border: `1px solid ${barColor}40`,
                                borderRadius: '4px', padding: '1px 7px', minWidth: 28, textAlign: 'center',
                              }}>#{i + 1}</span>
                              <span style={{ fontSize: '0.875rem', fontWeight: 600, color: '#e8eef5', flex: 1 }}>
                                {rc.name || rc.ci_name || '—'}
                              </span>
                              {rc.type && (
                                <span style={{ fontSize: '0.65rem', color: '#7a8ba3', border: '1px solid #3d4557', borderRadius: '4px', padding: '1px 6px' }}>
                                  {rc.type}
                                </span>
                              )}
                              {pct != null && (
                                <span style={{ fontSize: '0.75rem', fontWeight: 700, color: barColor }}>{pct}%</span>
                              )}
                            </div>
                            {pct != null && (
                              <div style={{ height: 4, backgroundColor: '#1a1f2e', borderRadius: 2, overflow: 'hidden' }}>
                                <div style={{ height: '100%', width: `${pct}%`, backgroundColor: barColor, borderRadius: 2, transition: 'width 600ms ease' }} />
                              </div>
                            )}
                            {rc.shared_count != null && rc.shared_count > 0 && (
                              <p style={{ fontSize: '0.72rem', color: '#6b7280', marginTop: '0.375rem', marginBottom: 0 }}>
                                Shared by {rc.shared_count} incident{rc.shared_count !== 1 ? 's' : ''} in topology
                              </p>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* ── Impact Breakdown (severity + state + event types) ── */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.875rem' }}>

                  {/* Severity breakdown */}
                  <div style={CARD}>
                    <div style={{ ...CARD_HDR }}><span style={LABEL}>Severity Breakdown</span></div>
                    <div style={{ padding: '0.875rem' }}>
                      {sevOrder.filter(s => sevBreakdown[s]).map(s => {
                        const count = sevBreakdown[s]
                        const color = s === 'critical' ? '#dc2626' : s === 'high' ? '#f97316' : s === 'medium' ? '#f59e0b' : s === 'low' ? '#10b981' : '#6b7280'
                        const pct = Math.round((count / stormDetail.child_count) * 100)
                        return (
                          <div key={s} style={{ marginBottom: '0.5rem' }}>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '3px' }}>
                              <span style={{ fontSize: '0.78rem', fontWeight: 600, color, textTransform: 'capitalize' }}>{s}</span>
                              <span style={{ fontSize: '0.75rem', color: '#a0aec0', fontWeight: 700 }}>{count}</span>
                            </div>
                            <div style={{ height: 5, backgroundColor: '#252c3c', borderRadius: 3, overflow: 'hidden' }}>
                              <div style={{ height: '100%', width: `${pct}%`, backgroundColor: color, borderRadius: 3 }} />
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  {/* State breakdown */}
                  <div style={CARD}>
                    <div style={{ ...CARD_HDR }}><span style={LABEL}>Incident States</span></div>
                    <div style={{ padding: '0.875rem' }}>
                      {Object.entries(stateBreakdown).sort(([, a], [, b]) => b - a).map(([state, count]) => {
                        const color = ['resolved', 'closed'].includes(state) ? '#10b981' : state === 'storm_hold' ? '#f59e0b' : '#60a5fa'
                        return (
                          <div key={state} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.3rem 0', borderBottom: '1px solid #252c3c' }}>
                            <span style={{ fontSize: '0.78rem', color: '#c1c7d0', textTransform: 'capitalize' }}>{state.replace(/_/g, ' ')}</span>
                            <span style={{ fontSize: '0.78rem', fontWeight: 700, color }}>{count}</span>
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  {/* Event types */}
                  <div style={CARD}>
                    <div style={{ ...CARD_HDR }}><span style={LABEL}>Event Types</span></div>
                    <div style={{ padding: '0.875rem' }}>
                      {Object.entries(etBreakdown).sort(([, a], [, b]) => b - a).map(([et, count]) => (
                        <div key={et} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '0.3rem 0', borderBottom: '1px solid #252c3c' }}>
                          <span style={{ fontSize: '0.72rem', color: '#c1c7d0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '75%' }}>{et}</span>
                          <span style={{ fontSize: '0.78rem', fontWeight: 700, color: '#f59e0b', flexShrink: 0 }}>{count}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* ── Topology Evidence ── */}
                {topoKeys.length > 0 && (
                  <div style={CARD}>
                    <div style={{ ...CARD_HDR, display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={LABEL}>Topology Evidence</span>
                      <span style={{ fontSize: '0.7rem', color: '#5a6a83', marginLeft: 'auto' }}>
                        shared dependency graph
                      </span>
                    </div>
                    <div style={{ padding: '0.875rem 1.25rem', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '0.5rem' }}>
                      {topoKeys.map(resource => {
                        const rawDeps = stormDetail.topology_evidence[resource] || []
                        const deps = rawDeps.map(d => typeof d === 'string' ? d : d.name || JSON.stringify(d))
                        return (
                          <div key={resource} style={{ ...INNER }}>
                            <p style={{ fontSize: '0.72rem', fontWeight: 700, color: '#a78bfa', marginBottom: '0.25rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              {resource}
                            </p>
                            {deps.slice(0, 4).map((d, i) => (
                              <p key={i} style={{ fontSize: '0.7rem', color: '#6b7280', margin: '1px 0', display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                                <span style={{ color: '#4b5563' }}>→</span> {d}
                              </p>
                            ))}
                            {deps.length > 4 && (
                              <p style={{ fontSize: '0.65rem', color: '#4b5563', margin: '2px 0 0' }}>+{deps.length - 4} more</p>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* ── Affected Resources list ── */}
                {stormDetail.affected_resources && stormDetail.affected_resources.length > 0 && (
                  <div style={CARD}>
                    <div style={{ ...CARD_HDR }}><span style={LABEL}>Affected Resources ({stormDetail.affected_resources.length})</span></div>
                    <div style={{ padding: '0.875rem 1.25rem', display: 'flex', flexWrap: 'wrap', gap: '0.375rem' }}>
                      {stormDetail.affected_resources.map(r => (
                        <span key={r} style={{
                          fontSize: '0.78rem', fontWeight: 600, padding: '0.25rem 0.75rem',
                          backgroundColor: '#252c3c', border: '1px solid #3d4557', borderRadius: '6px',
                          color: '#c1c7d0', fontFamily: "'Monaco','Courier New',monospace",
                        }}>
                          {r}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

              </div>
            )
          }

          // ── Non-storm incidents: standard AI insights ──
          if (!workflow.summary && !workflow.technical_summary && !hasInsights) {
            return (
              <div style={{ textAlign: 'center', padding: '4rem 2rem', color: '#7a8ba3' }}>
                <IconRobot size={40} style={{ opacity: 0.3 }} />
                <p style={{ marginTop: '1rem', fontSize: '0.875rem', fontWeight: 600, color: '#a0aec0' }}>
                  Generating AI insights…
                </p>
                <p style={{ fontSize: '0.8rem', margin: '0.375rem 0 1.5rem' }}>
                  This page will update automatically when the analysis is ready.
                </p>
                <div style={{ display: 'flex', justifyContent: 'center', gap: '6px' }}>
                  {[0, 1, 2].map(i => (
                    <span key={i} style={{
                      width: '7px', height: '7px', borderRadius: '50%',
                      backgroundColor: '#6366f1',
                      animation: `pulse 1.2s ease-in-out ${i * 0.2}s infinite`,
                      display: 'inline-block',
                    }} />
                  ))}
                </div>
              </div>
            )
          }

          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

              {/* ── Executive Summary ── */}
              {workflow.summary && (
                <div style={{ ...CARD, borderLeft: '4px solid #3b82f6' }}>
                  <div style={{ ...CARD_HDR, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.08em',
                      color: '#60a5fa', backgroundColor: 'rgba(59,130,246,0.12)',
                      border: '1px solid rgba(59,130,246,0.3)', borderRadius: '4px', padding: '2px 7px' }}>
                      AI
                    </span>
                    <span style={LABEL}>Executive Summary</span>
                  </div>
                  <div style={{ padding: '1rem 1.25rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                    {workflow.summary.split(/\n\n+/).filter(s => s.trim()).map((sentence, i) => (
                      <p key={i} style={{ fontSize: '0.875rem', color: '#c1c7d0', lineHeight: 1.7, margin: 0 }}>
                        {sentence.trim()}
                      </p>
                    ))}
                  </div>
                </div>
              )}

              {/* ── Technical Digest ── collapsed by default; key forces remount on incident change */}
              {workflow.technical_summary && (
                <TechnicalDigestCard key={workflow.workflow_id} text={workflow.technical_summary} />
              )}

              {/* ── LLM Operational Insights ── */}
              {hasInsights && (
                <div style={{ ...CARD, border: '1px solid #2d3a52' }}>
                  {/* Header */}
                  <div style={{
                    ...CARD_HDR,
                    borderLeft: '3px solid #6366f1',
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <span style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.08em',
                        color: '#a78bfa', backgroundColor: 'rgba(99,102,241,0.15)',
                        border: '1px solid rgba(99,102,241,0.3)', borderRadius: '4px', padding: '2px 7px' }}>
                        AI
                      </span>
                      <span style={LABEL}>Operational Insights</span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexShrink: 0 }}>
                      <span style={{ fontSize: '0.7rem', color: '#5a6a83' }}>
                        {ragCount > 0
                          ? `${ragCount} similar incident${ragCount !== 1 ? 's' : ''} analysed`
                          : 'No historical data'}
                      </span>
                      {conf != null && (
                        <span style={{
                          fontSize: '0.75rem', fontWeight: 700,
                          color: confColor, backgroundColor: `${confColor}18`,
                          border: `1px solid ${confColor}40`,
                          borderRadius: '5px', padding: '2px 9px',
                        }}>
                          {Math.round(conf * 100)}% confidence
                        </span>
                      )}
                    </div>
                  </div>

                  <div style={{ padding: '1rem 1.25rem', display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

                    {/* Root Cause Hypothesis */}
                    {ins.root_cause_hypothesis && (
                      <div style={{ ...INNER, borderLeft: '3px solid #6366f1' }}>
                        <p style={{ ...LABEL, marginBottom: '6px' }}>Root Cause Hypothesis</p>
                        <p style={{ fontSize: '13px', color: '#e8eef5', lineHeight: 1.6, margin: 0 }}>
                          {ins.root_cause_hypothesis}
                        </p>
                        {ins.confidence_reason && (
                          <p style={{ fontSize: '11px', color: '#7a8ba3', marginTop: '6px', marginBottom: 0 }}>
                            <span style={{ color: confColor, fontWeight: 600 }}>Confidence basis:</span>{' '}
                            {ins.confidence_reason}
                          </p>
                        )}
                      </div>
                    )}

                    {/* Key Concerns + Est. Resolution */}
                    {(ins.key_concerns?.length > 0 || ins.estimated_resolution_time) && (
                      <div style={{ display: 'grid', gridTemplateColumns: ins.key_concerns?.length > 0 && ins.estimated_resolution_time ? '1fr auto' : '1fr', gap: '0.75rem' }}>
                        {ins.key_concerns?.length > 0 && (
                          <div style={INNER}>
                            <p style={{ ...LABEL, marginBottom: '8px' }}>Key Concerns</p>
                            <AIBulletList items={ins.key_concerns} accent="#f97316" />
                          </div>
                        )}
                        {ins.estimated_resolution_time && (
                          <div style={{ ...INNER, minWidth: '150px', maxWidth: '260px', textAlign: 'center' }}>
                            <p style={{ ...LABEL, marginBottom: '6px' }}>Est. Resolution</p>
                            <p style={{
                              fontSize: ins.estimated_resolution_time.length <= 20 ? '20px' : '13px',
                              fontWeight: ins.estimated_resolution_time.length <= 20 ? 700 : 500,
                              color: '#e8eef5',
                              margin: 0,
                              lineHeight: 1.4,
                              textAlign: ins.estimated_resolution_time.length <= 20 ? 'center' : 'left',
                            }}>
                              {ins.estimated_resolution_time}
                            </p>
                          </div>
                        )}
                      </div>
                    )}

                    {/* Remediation Rationale */}
                    {ins.remediation_rationale && (
                      <div style={INNER}>
                        <p style={{ ...LABEL, marginBottom: '6px' }}>Remediation Rationale</p>
                        <p style={{ fontSize: '12px', color: '#c8d6e5', lineHeight: 1.6, margin: 0 }}>
                          {ins.remediation_rationale}
                        </p>
                      </div>
                    )}

                    {/* Historical Match */}
                    {!noSimilar && ins.similar_pattern && (
                      <div style={{ ...INNER, borderLeft: '3px solid #10b981' }}>
                        <p style={{ ...LABEL, marginBottom: '6px' }}>Historical Match</p>
                        <p style={{ fontSize: '12px', color: '#c8d6e5', lineHeight: 1.6, margin: 0 }}>
                          {ins.similar_pattern}
                        </p>
                        {ins.rag_runbook_name && (
                          <p style={{ fontSize: '11px', color: '#10b981', marginTop: '6px', marginBottom: 0, fontWeight: 600 }}>
                            Suggested runbook: {ins.rag_runbook_name}
                          </p>
                        )}
                      </div>
                    )}

                    {/* Post-Remediation Checks */}
                    {ins.post_remediation_checks?.length > 0 && (
                      <div style={INNER}>
                        <p style={{ ...LABEL, marginBottom: '8px' }}>Post-Remediation Checks</p>
                        <AIBulletList items={ins.post_remediation_checks} accent="#10b981" />
                      </div>
                    )}

                  </div>
                </div>
              )}

            </div>
          )
        })()}

        {/* RISK TAB */}
        {activeTab === 'risk-summary' && (
          <RiskSummaryPage workflow={workflow} darkMode={darkMode} />
        )}

        {/* TIMELINE TAB */}
        {activeTab === 'timeline' && (
          <IncidentTimeline
            timeline={timeline}
            currentState={workflow.lifecycle_state}
          />
        )}

        {/* AUDIT TAB */}
        {activeTab === 'audit' && (
          <AuditPage workflow={workflow} darkMode={darkMode} />
        )}

        {/* WORK NOTES TAB */}
        {activeTab === 'notes' && (() => {
          const NOTE_STYLES: Record<string, { border: string; label: string; labelColor: string; bg: string }> = {
            note:       { border: '#3b82f6', label: 'Note',       labelColor: '#93c5fd', bg: '#0d1929' },
            action:     { border: '#f59e0b', label: 'Action',     labelColor: '#fcd34d', bg: '#1c1200' },
            escalation: { border: '#dc2626', label: 'Escalation', labelColor: '#f87171', bg: '#1a0505' },
            system:     { border: '#4a5568', label: 'System',     labelColor: '#7a8ba3', bg: '#141820' },
          }
          const fmt = (iso: string) => {
            const d = parseUTC(iso)
            return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' · ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
          }
          return (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

              {/* Storm guidance for child incidents */}
              {isStormChild && (
                <div
                  style={{
                    backgroundColor: '#110d1f',
                    border: '1px solid #7c3aed50',
                    borderLeft: '4px solid #7c3aed',
                    borderRadius: '10px',
                    padding: '0.875rem 1.125rem',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '1rem',
                    flexWrap: 'wrap',
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <p style={{ fontSize: '0.82rem', fontWeight: 600, color: '#c084fc', margin: '0 0 0.2rem', display: 'flex', alignItems: 'center', gap: '0.375rem' }}>
                      <span>⚡</span> Storm Incident — Work Note Guidance
                    </p>
                    <p style={{ fontSize: '0.78rem', color: '#8090a8', margin: 0, lineHeight: 1.5 }}>
                      This incident is part of a correlated storm. Document your root-cause findings and steps on the{' '}
                      <strong style={{ color: '#c084fc' }}>storm parent</strong> so all related incidents are tracked in one place.
                      Notes added here capture local observations.
                    </p>
                  </div>
                  {stormDetail && onViewWorkflow && (
                    <button
                      onClick={() => onViewWorkflow(stormDetail.storm_id)}
                      style={{
                        fontSize: '0.78rem',
                        fontWeight: 600,
                        padding: '0.375rem 0.875rem',
                        borderRadius: '6px',
                        border: '1px solid #7c3aed60',
                        backgroundColor: 'transparent',
                        color: '#e9d5ff',
                        cursor: 'pointer',
                        flexShrink: 0,
                      }}
                    >
                      ⚡ Go to Storm Parent
                    </button>
                  )}
                </div>
              )}

              {/* Thread */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.625rem' }}>
                {notesLoading ? (
                  [...Array(3)].map((_, i) => (
                    <div key={i} style={{ height: '72px', borderRadius: '8px', background: '#1a1f2e', animation: 'pulse 1.4s ease-in-out infinite' }} />
                  ))
                ) : notes.length === 0 ? (
                  <div style={{ padding: '3rem 1rem', textAlign: 'center', color: '#7a8ba3' }}>
                    <IconMessage size={36} style={{ opacity: 0.4 }} />
                    <p style={{ marginTop: '0.75rem', fontSize: '0.875rem', fontWeight: 600, color: '#a0aec0' }}>No work notes yet</p>
                    <p style={{ fontSize: '0.8rem', margin: '0.25rem 0 0' }}>Add the first note to start the incident thread.</p>
                  </div>
                ) : (
                  notes.map(n => {
                    const s = NOTE_STYLES[n.note_type] ?? NOTE_STYLES.note
                    return (
                      <div
                        key={n.id}
                        style={{
                          backgroundColor: s.bg,
                          border: `1px solid ${s.border}30`,
                          borderLeft: `3px solid ${s.border}`,
                          borderRadius: '8px',
                          padding: '0.875rem 1.125rem',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.4rem' }}>
                          <span style={{
                            fontSize: '0.7rem', fontWeight: 700, color: s.labelColor,
                            border: `1px solid ${s.border}60`, borderRadius: '4px',
                            padding: '1px 6px', textTransform: 'uppercase', letterSpacing: '0.3px',
                          }}>
                            {s.label}
                          </span>
                          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#e8eef5' }}>{n.author}</span>
                          <span style={{ fontSize: '0.75rem', color: '#7a8ba3', marginLeft: 'auto' }}>{fmt(n.created_at)}</span>
                        </div>
                        <p style={{ fontSize: '0.85rem', color: '#c1c7d0', lineHeight: 1.6, margin: 0, whiteSpace: 'pre-wrap' }}>
                          {n.body}
                        </p>
                      </div>
                    )
                  })
                )}
                <div ref={notesEndRef} />
              </div>

              {/* Composer — hidden for viewer role */}
              {canAct && <div
                style={{
                  backgroundColor: '#1a1f2e',
                  border: '1px solid #3d4557',
                  borderRadius: '10px',
                  padding: '1.25rem',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.875rem',
                }}
              >
                <p style={{ fontSize: '0.75rem', fontWeight: 600, color: '#a0aec0', textTransform: 'uppercase', letterSpacing: '0.5px', margin: 0 }}>
                  Add Work Note
                </p>

                {/* Resolved/closed notice */}
                {['resolved', 'closed', 'deployed'].includes(workflow.lifecycle_state) && (
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.5rem',
                    padding: '0.4rem 0.75rem',
                    borderRadius: '6px',
                    backgroundColor: '#10b98112',
                    border: '1px solid #10b98130',
                  }}>
                    <span style={{ width: 6, height: 6, borderRadius: '50%', backgroundColor: '#10b981', flexShrink: 0, display: 'inline-block' }} />
                    <span style={{ fontSize: '0.78rem', color: '#6ee7b7' }}>
                      This incident is resolved — notes added here are for record keeping only.
                    </span>
                  </div>
                )}

                {/* Type row */}
                <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
                  <div style={{ display: 'flex', gap: '0.375rem' }}>
                    {(['note', 'action', 'escalation'] as const).map(t => (
                      <button
                        key={t}
                        onClick={() => setNoteType(t)}
                        style={{
                          padding: '4px 12px',
                          borderRadius: '5px',
                          fontSize: '0.76rem',
                          fontWeight: 600,
                          cursor: 'pointer',
                          border: `1px solid ${noteType === t ? NOTE_STYLES[t].border : '#3d4557'}`,
                          backgroundColor: noteType === t ? `${NOTE_STYLES[t].border}18` : 'transparent',
                          color: noteType === t ? NOTE_STYLES[t].labelColor : '#7a8ba3',
                          transition: 'all 150ms',
                        }}
                      >
                        {NOTE_STYLES[t].label}
                      </button>
                    ))}
                  </div>
                </div>

                <textarea
                  value={noteBody}
                  onChange={e => setNoteBody(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleAddNote() }}
                  placeholder="Describe what you found, what you did, or any relevant context…"
                  rows={4}
                  style={{
                    width: '100%',
                    padding: '0.625rem 0.75rem',
                    borderRadius: '6px',
                    border: '1px solid #3d4557',
                    backgroundColor: '#0d1117',
                    color: '#e8eef5',
                    fontSize: '0.85rem',
                    lineHeight: 1.6,
                    resize: 'vertical',
                    outline: 'none',
                    fontFamily: 'inherit',
                    boxSizing: 'border-box',
                  }}
                />

                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.75rem' }}>
                  <span style={{ fontSize: '0.75rem', color: '#7a8ba3' }}>{/Mac|iPhone|iPad|iPod/.test(navigator.platform) ? '⌘↵' : 'Ctrl+↵'} to submit</span>
                  <button
                    onClick={handleAddNote}
                    disabled={actionLoading || !noteBody.trim()}
                    className="btn gap-2"
                    style={{
                      fontSize: '0.8125rem',
                      backgroundColor: noteBody.trim() ? '#3b82f6' : '#1a1f2e',
                      color: noteBody.trim() ? '#fff' : '#4a5568',
                      border: 'none',
                      fontWeight: 600,
                      transition: 'background 150ms',
                    }}
                  >
                    {actionLoading ? <IconLoader2 size={15} /> : <IconMessage size={15} />}
                    {actionLoading ? 'Posting…' : 'Post Note'}
                  </button>
                </div>
              </div>}

              {!canAct && (
                <div style={{ padding: '0.75rem 1rem', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid #2d3748', color: '#64748b', fontSize: '0.8rem', textAlign: 'center' }}>
                  You have view-only access — work notes can be read but not added.
                </div>
              )}

            </div>
          )
        })()}
      </div>

      {/* ── REVIEW APPROVAL MODAL ── */}
      {showReviewModal && workflow && (
        <ApprovalModal
          incident={workflow}
          loading={actionLoading}
          onApprove={handleModalApprove}
          onDiagnosticsOnly={handleModalDiagnosticsOnly}
          onReject={handleModalReject}
          onClose={() => setShowReviewModal(false)}
        />
      )}

      {/* ── CLOSE INCIDENT MODAL ── rendered via portal to escape CSS transform containment ── */}
      {showCloseModal && createPortal(
        <div
          style={{
            position: 'fixed', inset: 0,
            backgroundColor: 'rgba(0,0,0,0.75)',
            backdropFilter: 'blur(4px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            zIndex: 9999,
            padding: '1rem',
          }}
          onClick={() => setShowCloseModal(false)}
        >
          <div
            style={{
              backgroundColor: '#1a1f2e',
              border: '1px solid #f59e0b50',
              borderTop: '3px solid #f59e0b',
              borderRadius: '12px',
              padding: '2rem',
              maxWidth: '520px',
              width: '100%',
              maxHeight: '90vh',
              overflowY: 'auto',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.25rem' }}>
              <IconClipboardList size={24} color="#f59e0b" />
              <h2 style={{ fontSize: '1.25rem', fontWeight: 700, color: '#e8eef5', margin: 0 }}>
                Close Incident
              </h2>
            </div>

            <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#a0aec0', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '0.4rem' }}>
              Outcome
            </label>
            <select
              value={closeOutcome}
              onChange={(e) => setCloseOutcome(e.target.value)}
              className="form-input"
              style={{ width: '100%', marginBottom: '1rem' }}
            >
              <option value="resolved">Resolved — remediation was executed and successful</option>
              <option value="self_resolved">Self-Resolved — issue recovered without intervention</option>
              <option value="monitoring">Monitoring — keeping an eye on it</option>
              <option value="escalated">Escalated — passed to another team</option>
              <option value="no_action">No Action Required — false positive</option>
            </select>

            <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#a0aec0', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '0.4rem' }}>
              Close Summary <span style={{ color: '#dc2626' }}>*</span>
            </label>
            <textarea
              value={closeSummary}
              onChange={(e) => setCloseSummary(e.target.value)}
              placeholder="What was found? What is the root cause or explanation?"
              className="form-input"
              style={{ width: '100%', marginBottom: '1rem', resize: 'vertical', minHeight: '80px' }}
            />

            <label style={{ fontSize: '0.75rem', fontWeight: 600, color: '#a0aec0', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '0.4rem' }}>
              Steps Taken <span style={{ color: '#dc2626' }}>*</span>
            </label>
            <textarea
              value={closeStepsTaken}
              onChange={(e) => setCloseStepsTaken(e.target.value)}
              placeholder="What manual steps did you take? (e.g. killed process, updated config, notified team…)"
              className="form-input"
              style={{ width: '100%', marginBottom: '1.25rem', resize: 'vertical', minHeight: '80px' }}
            />

            {actionError && (
              <div style={{
                padding: '0.625rem 0.875rem',
                marginBottom: '1rem',
                backgroundColor: '#3d1515',
                border: '1px solid #dc2626',
                borderRadius: '6px',
                fontSize: '0.8125rem',
                color: '#fca5a5',
              }}>
                {actionError}
              </div>
            )}

            <div style={{ display: 'flex', gap: '0.75rem' }}>
              <button
                onClick={handleClose}
                disabled={actionLoading || !closeSummary.trim() || !closeStepsTaken.trim()}
                className="btn gap-2"
                style={{
                  flex: 1,
                  backgroundColor: '#f59e0b',
                  color: '#0d0d0d',
                  border: 'none',
                  fontWeight: 600,
                  opacity: (!closeSummary.trim() || !closeStepsTaken.trim()) ? 0.5 : 1,
                }}
              >
                {actionLoading ? 'Closing…' : '✓ Close Incident'}
              </button>
              <button
                onClick={() => { setShowCloseModal(false); clearActionError() }}
                className="btn btn-secondary"
                style={{ flex: 1 }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </div>
  )
}
