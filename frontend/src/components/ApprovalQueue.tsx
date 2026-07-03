import { useState, useEffect, useCallback } from 'react'
import { getPendingApprovals, getApprovalHistory, submitApprovalDecision } from '../services/api'
import { Approval } from '../types'
import { parseUTC, formatRelativeTime } from '../utils/dateFormatter'
import { IconCheck, IconX, IconClock, IconSearch, IconChevronRight, IconRefresh } from './icons'
import StatusBadge from './StatusBadge'
import { useCurrentUser } from '../hooks/useCurrentUser'
import { useGlobalEvents } from '../hooks/useGlobalEvents'

// ── Design system constants ──────────────────────────────────────────────────
const C = {
  bgPrimary:   '#0f1419',
  bgSecondary: '#1a1f2e',
  bgElevated:  '#252c3c',
  border:      '#3d4557',
  textPrimary:   '#e8eef5',
  textSecondary: '#a0aec0',
  textTertiary:  '#6b7280',
  critical: '#dc2626',
  high:     '#f97316',
  medium:   '#3b82f6',
  low:      '#10b981',
  amber:    '#f59e0b',
}

function riskColor(score: number): string {
  if (score >= 70) return C.critical
  if (score >= 50) return C.high
  if (score >= 20) return C.amber
  return C.low
}

function blastColor(radius: number): string {
  if (radius >= 3) return C.critical
  if (radius >= 2) return C.high
  return C.low
}

function confidenceColor(c: number): string {
  if (c >= 0.85) return C.low
  if (c >= 0.65) return C.amber
  return C.high
}

function confidenceLabel(c: number): string {
  if (c >= 0.85) return 'High'
  if (c >= 0.65) return 'Medium'
  return 'Low'
}

// ── History status badge ─────────────────────────────────────────────────────

function historyStatusColor(status: string): string {
  switch (status) {
    case 'approved':         return C.low       // green
    case 'diagnostics_only': return C.medium    // blue
    case 'rejected':         return C.critical  // red
    case 'cancelled':        return C.amber     // amber
    default:                 return C.textTertiary
  }
}

function historyStatusLabel(status: string): string {
  switch (status) {
    case 'approved':         return 'Approved'
    case 'diagnostics_only': return 'Diag Only'
    case 'rejected':         return 'Rejected'
    case 'cancelled':        return 'Cancelled'
    default:                 return status.replace(/_/g, ' ')
  }
}

/** Compact absolute timestamp: "May 23 · 17:04" */
function formatCompact(iso: string): string {
  const d = parseUTC(iso)
  const month = d.toLocaleDateString('en-US', { month: 'short' })
  const day   = d.getDate()
  const hh    = d.getHours().toString().padStart(2, '0')
  const mm    = d.getMinutes().toString().padStart(2, '0')
  return `${month} ${day} · ${hh}:${mm}`
}

/** How long the approval sat pending before a decision was made */
function formatTimeToDecide(requestedAt: string, decidedAt?: string | null): string {
  if (!decidedAt) return '—'
  const diffMs = new Date(decidedAt).getTime() - new Date(requestedAt).getTime()
  if (diffMs <= 0) return '< 1s'
  const totalSeconds = Math.floor(diffMs / 1000)
  if (totalSeconds < 60) return `${totalSeconds}s`
  const minutes = Math.floor(totalSeconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const mins  = minutes % 60
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`
}

// ── HistoryDetailPanel — read-only card shown on row expand ──────────────────

function HistoryDetailPanel({ approval }: { approval: Approval }) {
  const inc        = approval.incident_summary
  const pa         = approval.proposed_action
  const color      = historyStatusColor(approval.status)
  const statusLabel = historyStatusLabel(approval.status)
  const totalSteps = (pa?.diagnostics_steps?.length ?? 0) + (pa?.remediation_steps?.length ?? 0)

  const dividerStyle: React.CSSProperties = { borderTop: `1px solid ${C.border}` }
  const labelStyle: React.CSSProperties   = {
    fontSize: '0.75rem', fontWeight: 600,
    textTransform: 'uppercase', letterSpacing: '0.5px', color: C.textTertiary,
    margin: 0,
  }

  const riskCol = (score: number) =>
    score >= 70 ? C.critical : score >= 50 ? C.high : score >= 20 ? C.amber : C.low
  const blastCol = (r: number) =>
    r >= 3 ? C.critical : r >= 2 ? C.high : C.low
  const confCol = (c: number) =>
    c >= 0.85 ? C.low : c >= 0.65 ? C.amber : C.high

  return (
    <div style={{ background: C.bgSecondary }}>

      {/* ── Incident context ──────────────────────────────────────────────── */}
      <div style={{ padding: '1.125rem 1.5rem', borderTop: `2px solid ${C.border}` }}>
        <p style={{ ...labelStyle, marginBottom: '0.625rem' }}>Incident at time of approval</p>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', flexWrap: 'wrap' }}>
          {inc?.severity && <StatusBadge type="criticality" value={inc.severity} size="sm" />}
          {inc?.anomaly_type && (
            <span style={{ padding: '0.25rem 0.625rem', borderRadius: '6px', border: `1px solid ${C.border}`, color: C.textSecondary, fontSize: '0.75rem', fontWeight: 600 }}>
              {inc.anomaly_type.replace(/_/g, ' ')}
            </span>
          )}
          {inc?.resource && (
            <span style={{ fontFamily: 'Monaco, Courier New, monospace', fontSize: '0.875rem', fontWeight: 600, color: C.medium }}>
              {inc.resource}
            </span>
          )}
          {inc?.environment && (
            <span style={{ padding: '0.125rem 0.5rem', borderRadius: '4px', border: `1px solid ${C.border}`, color: C.textTertiary, fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px' }}>
              {inc.environment}
            </span>
          )}
          {inc?.risk_score != null && (
            <span style={{ fontSize: '0.8125rem', color: C.textSecondary }}>
              Risk <span style={{ fontWeight: 700, color: riskCol(inc.risk_score) }}>{inc.risk_score.toFixed(1)}</span>
            </span>
          )}
        </div>
      </div>

      {/* ── Proposed action ───────────────────────────────────────────────── */}
      {pa && (
        <div style={{ padding: '1rem 1.5rem', ...dividerStyle }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '1.5rem' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <p style={{ ...labelStyle, marginBottom: '0.375rem' }}>Proposed Remediation</p>
              <p style={{ color: C.textPrimary, fontSize: '0.9375rem', fontWeight: 600, margin: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {pa.runbook}
              </p>
              {pa.target && pa.target !== 'unknown' && (
                <p style={{ color: C.textSecondary, fontSize: '0.8125rem', margin: '0.25rem 0 0' }}>
                  Target <span style={{ fontFamily: 'Monaco, Courier New, monospace', color: C.medium }}>{pa.target}</span>
                </p>
              )}
            </div>
            <div style={{ display: 'flex', gap: '1.5rem', flexShrink: 0, textAlign: 'right' }}>
              <div>
                <p style={labelStyle}>Blast Radius</p>
                <p style={{ fontSize: '1.5rem', fontWeight: 700, color: blastCol(pa.blast_radius ?? 0), margin: '0.25rem 0 0' }}>
                  {pa.blast_radius ?? '—'}
                </p>
              </div>
              {pa.confidence != null && (
                <div>
                  <p style={labelStyle}>Confidence</p>
                  <p style={{ fontSize: '1.5rem', fontWeight: 700, color: confCol(pa.confidence), margin: '0.25rem 0 0' }}>
                    {Math.round(pa.confidence * 100)}%
                  </p>
                </div>
              )}
              {pa.action && pa.action !== 'unknown' && (
                <div>
                  <p style={labelStyle}>Action</p>
                  <p style={{ fontFamily: 'Monaco, Courier New, monospace', fontSize: '0.8125rem', color: C.medium, margin: '0.375rem 0 0' }}>
                    {pa.action}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Steps — always expanded ───────────────────────────────────────── */}
      {pa && totalSteps > 0 && (
        <div style={{ padding: '1rem 1.5rem', ...dividerStyle }}>
          <p style={{ ...labelStyle, marginBottom: '0.875rem' }}>Runbook Steps</p>
          <div style={{ display: 'grid', gridTemplateColumns: (pa.diagnostics_steps?.length ?? 0) > 0 && (pa.remediation_steps?.length ?? 0) > 0 ? '1fr 1fr' : '1fr', gap: '1.5rem' }}>
            {(pa.diagnostics_steps?.length ?? 0) > 0 && (
              <div>
                <p style={{ ...labelStyle, color: C.medium, marginBottom: '0.75rem' }}>Diagnostics</p>
                <StepList steps={pa.diagnostics_steps ?? []} accentColor={C.medium} />
              </div>
            )}
            {(pa.remediation_steps?.length ?? 0) > 0 && (
              <div>
                <p style={{ ...labelStyle, color: C.low, marginBottom: '0.75rem' }}>Remediation</p>
                <StepList steps={pa.remediation_steps ?? []} accentColor={C.low} />
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Decision summary bar ──────────────────────────────────────────── */}
      <div style={{ padding: '0.875rem 1.5rem', ...dividerStyle, backgroundColor: C.bgElevated }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', flexWrap: 'wrap' }}>
          <span style={{
            padding: '0.2rem 0.6rem', borderRadius: '4px',
            border: `1px solid ${color}`, color, fontSize: '0.7rem',
            fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.5px',
          }}>
            {statusLabel}
          </span>
          <span style={{ fontSize: '0.8125rem', color: C.textSecondary }}>
            Decided by <span style={{ color: C.textPrimary, fontWeight: 600 }}>{approval.decided_by || '—'}</span>
          </span>
          {approval.decided_at && (
            <span style={{ fontSize: '0.8125rem', color: C.textTertiary }}>
              {formatCompact(approval.decided_at)}
            </span>
          )}
          <span style={{ fontSize: '0.8125rem', color: C.textTertiary, marginLeft: 'auto' }}>
            Requested {formatCompact(approval.requested_at)}
            {' · '}
            <span style={{ color, fontWeight: 600 }}>{formatTimeToDecide(approval.requested_at, approval.decided_at)}</span>
            {' to decide'}
          </span>
        </div>
        {approval.decision_notes && (
          <p style={{
            margin: '0.625rem 0 0',
            paddingTop: '0.625rem',
            borderTop: `1px solid ${C.border}`,
            fontSize: '0.8125rem',
            color: C.textSecondary,
            fontStyle: 'italic',
            lineHeight: 1.5,
          }}>
            "{approval.decision_notes}"
          </p>
        )}
      </div>
    </div>
  )
}

// ── StepList ─────────────────────────────────────────────────────────────────

function StepList({ steps, accentColor }: {
  steps: { order: number; name: string; description: string; tool: string }[]
  accentColor: string
}) {
  if (!steps || steps.length === 0) {
    return <p style={{ color: C.textTertiary, fontSize: '0.8125rem', fontStyle: 'italic' }}>None</p>
  }
  return (
    <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
      {steps.map((step, i) => (
        <li key={i} style={{ display: 'flex', gap: '0.75rem' }}>
          <span style={{
            flexShrink: 0,
            width: '1.375rem',
            height: '1.375rem',
            borderRadius: '50%',
            border: `1px solid ${accentColor}`,
            color: accentColor,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '0.6875rem',
            fontWeight: 700,
            marginTop: '0.125rem',
          }}>{step.order}</span>
          <div>
            <p style={{ color: C.textPrimary, fontSize: '0.8125rem', fontWeight: 500, margin: 0 }}>{step.name}</p>
            <p style={{ color: C.textSecondary, fontSize: '0.75rem', margin: '0.25rem 0' }}>{step.description}</p>
            <span style={{
              display: 'inline-block',
              padding: '0.125rem 0.5rem',
              borderRadius: '4px',
              border: `1px solid ${C.border}`,
              color: accentColor,
              fontSize: '0.75rem',
              fontFamily: 'Monaco, Courier New, monospace',
              fontWeight: 600,
            }}>{step.tool}</span>
          </div>
        </li>
      ))}
    </ol>
  )
}

// ── ApprovalCard ──────────────────────────────────────────────────────────────

function ApprovalCard({
  approval,
  onApprove,
  onDiagnosticsOnly,
  onReject,
  isDeciding,
  notes,
  onNotesChange,
}: {
  approval: Approval
  onApprove: () => void
  onDiagnosticsOnly: () => void
  onReject: () => void
  isDeciding: boolean
  notes: string
  onNotesChange: (notes: string) => void
}) {
  const [showSteps, setShowSteps] = useState(true)
  const [hoverBtn, setHoverBtn] = useState<string | null>(null)

  const daysOld = Math.floor(
    (Date.now() - new Date(approval.requested_at).getTime()) / (1000 * 60 * 60 * 24)
  )
  const isUrgent = daysOld > 2
  const inc = approval.incident_summary
  const pa  = approval.proposed_action
  const totalSteps = (pa?.diagnostics_steps?.length ?? 0) + (pa?.remediation_steps?.length ?? 0)
  // Prefer the live score (joined fresh from the incident each request) over the
  // frozen incident_summary snapshot taken when the approval was first created.
  const liveRiskScore = approval.risk_score ?? inc?.risk_score ?? null

  const cardStyle: React.CSSProperties = {
    backgroundColor: C.bgSecondary,
    border: `1px solid ${isUrgent ? C.high : C.border}`,
    borderRadius: '10px',
    overflow: 'hidden',
    transition: 'all 200ms ease',
  }

  const dividerStyle: React.CSSProperties = {
    borderTop: `1px solid ${C.border}`,
  }

  const labelStyle: React.CSSProperties = {
    fontSize: '0.75rem',
    fontWeight: 600,
    textTransform: 'uppercase' as const,
    letterSpacing: '0.5px',
    color: C.textTertiary,
  }

  function btnStyle(color: string, id: string): React.CSSProperties {
    const isHov = hoverBtn === id && !isDeciding
    return {
      flex: 1,
      padding: '0.5rem 0.75rem',
      borderRadius: '6px',
      border: `1px solid ${color}`,
      backgroundColor: isHov ? `${color}18` : 'transparent',
      color: color,
      fontSize: '0.8125rem',
      fontWeight: 600,
      cursor: isDeciding ? 'not-allowed' : 'pointer',
      opacity: isDeciding ? 0.5 : 1,
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '0.375rem',
      transition: 'all 150ms ease',
      transform: isHov ? 'translateY(-1px)' : 'translateY(0)',
    }
  }

  return (
    <div style={cardStyle}>

      {/* ── Header: severity + resource + time ───────────────────────────── */}
      <div style={{ padding: '1.25rem 1.5rem', ...dividerStyle, borderTop: 'none' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '1rem' }}>
          <div style={{ flex: 1 }}>

            {/* Badges row */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' as const, marginBottom: '0.625rem' }}>
              {inc?.severity && (
                <StatusBadge type="criticality" value={inc.severity} size="sm" />
              )}
              {inc?.anomaly_type && (
                <span style={{
                  padding: '0.25rem 0.625rem',
                  borderRadius: '6px',
                  border: `1px solid ${C.border}`,
                  color: C.textSecondary,
                  fontSize: '0.75rem',
                  fontWeight: 600,
                }}>
                  {inc.anomaly_type.replace(/_/g, ' ')}
                </span>
              )}
              {/* Source badge */}
              {pa?.source && (() => {
                const sourceTier: Record<string, { label: string; color: string }> = {
                  runbook_library:    { label: 'Runbook',   color: '#10b981' },
                  cmdb_playbook:      { label: 'Playbook',  color: '#3b82f6' },
                  llm_generated:      { label: 'AI-Driven', color: '#f59e0b' },
                  fallback_escalation:{ label: 'Fallback',  color: '#7a8ba3' },
                }
                const st = sourceTier[pa.source as string] || sourceTier.fallback_escalation
                return (
                  <span style={{
                    padding: '0.25rem 0.625rem',
                    borderRadius: '6px',
                    border: `1px solid ${st.color}60`,
                    backgroundColor: `${st.color}18`,
                    color: st.color,
                    fontSize: '0.75rem',
                    fontWeight: 700,
                    letterSpacing: '0.3px',
                    textTransform: 'uppercase' as const,
                  }}>
                    {st.label}
                  </span>
                )
              })()}
              {isUrgent && (
                <span style={{
                  padding: '0.25rem 0.625rem',
                  borderRadius: '6px',
                  border: `1px solid ${C.high}`,
                  color: C.high,
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '0.25rem',
                }}>
                  <IconClock size={12} />
                  Pending {daysOld}d
                </span>
              )}
            </div>

            {/* Resource + environment + risk */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' as const }}>
              {inc?.resource && (
                <span style={{
                  fontFamily: 'Monaco, Courier New, monospace',
                  fontSize: '0.875rem',
                  fontWeight: 600,
                  color: C.medium,
                }}>
                  {inc.resource}
                </span>
              )}
              {inc?.environment && (
                <span style={{
                  padding: '0.125rem 0.5rem',
                  borderRadius: '4px',
                  border: `1px solid ${C.border}`,
                  color: C.textTertiary,
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  textTransform: 'uppercase' as const,
                  letterSpacing: '0.5px',
                }}>
                  {inc.environment}
                </span>
              )}
              {liveRiskScore != null && (
                <span style={{ fontSize: '0.8125rem', color: C.textSecondary }}>
                  Risk Score{' '}
                  <span style={{ fontWeight: 700, color: riskColor(liveRiskScore) }}>
                    {liveRiskScore.toFixed(1)}
                  </span>
                </span>
              )}
            </div>
          </div>

          {/* Timestamp + ID */}
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <p style={{ fontSize: '0.75rem', color: C.textTertiary, margin: 0 }}>
              {formatRelativeTime(approval.requested_at)}
            </p>
            <p style={{ fontFamily: 'Monaco, Courier New, monospace', fontSize: '0.75rem', color: C.textTertiary, margin: '0.25rem 0 0' }}>
              #{approval.approval_id.substring(0, 8)}
            </p>
          </div>
        </div>
      </div>

      {/* ── Proposed action ───────────────────────────────────────────────── */}
      {pa && (
        <div style={{ padding: '1rem 1.5rem', ...dividerStyle }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '1.5rem' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.375rem' }}>
                <p style={{ ...labelStyle, margin: 0 }}>Proposed Remediation</p>
                {(() => {
                  const src = pa.source
                  const tier: Record<string, { label: string; color: string }> = {
                    runbook_library:    { label: 'Runbook',    color: '#10b981' },
                    cmdb_playbook:      { label: 'Playbook',   color: '#3b82f6' },
                    llm_generated:      { label: 'AI-Driven',  color: '#f59e0b' },
                    fallback_escalation:{ label: 'Fallback',   color: '#7a8ba3' },
                  }
                  const t = src ? (tier[src] || tier.fallback_escalation) : null
                  if (!t) return null
                  return (
                    <span style={{
                      fontSize: '0.65rem',
                      fontWeight: 700,
                      color: t.color,
                      border: `1px solid ${t.color}50`,
                      backgroundColor: `${t.color}15`,
                      borderRadius: '4px',
                      padding: '0.1rem 0.45rem',
                      letterSpacing: '0.4px',
                      textTransform: 'uppercase' as const,
                    }}>
                      {t.label}
                    </span>
                  )
                })()}
              </div>
              <p style={{ color: C.textPrimary, fontSize: '0.9375rem', fontWeight: 600, margin: '0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
                {pa.runbook}
              </p>
              {pa.target && pa.target !== 'unknown' && (
                <p style={{ color: C.textSecondary, fontSize: '0.8125rem', margin: '0.25rem 0 0' }}>
                  Target{' '}
                  <span style={{ fontFamily: 'Monaco, Courier New, monospace', color: C.medium }}>
                    {pa.target}
                  </span>
                </p>
              )}
              {pa.decision_notes && (
                <p style={{ color: C.textTertiary, fontSize: '0.75rem', fontStyle: 'italic', margin: '0.5rem 0 0' }}>
                  {pa.decision_notes}
                </p>
              )}
            </div>

            {/* Blast radius + confidence + action */}
            <div style={{ display: 'flex', gap: '1.5rem', flexShrink: 0, textAlign: 'right' }}>
              <div>
                <p style={labelStyle}>Blast Radius</p>
                <p style={{ fontSize: '1.5rem', fontWeight: 700, color: blastColor(pa.blast_radius ?? 0), margin: '0.25rem 0 0' }}>
                  {pa.blast_radius ?? '—'}
                </p>
              </div>
              {pa.confidence != null && (
                <div>
                  <p style={labelStyle}>Confidence</p>
                  <p style={{ fontSize: '1.5rem', fontWeight: 700, color: confidenceColor(pa.confidence), margin: '0.25rem 0 0' }}>
                    {Math.round(pa.confidence * 100)}%
                  </p>
                  <p style={{ fontSize: '0.6875rem', fontWeight: 600, color: confidenceColor(pa.confidence), margin: '0.125rem 0 0', letterSpacing: '0.4px', textTransform: 'uppercase' }}>
                    {confidenceLabel(pa.confidence)}
                  </p>
                </div>
              )}
              {pa.action && pa.action !== 'unknown' && (
                <div>
                  <p style={labelStyle}>Action</p>
                  <p style={{ fontFamily: 'Monaco, Courier New, monospace', fontSize: '0.8125rem', color: C.medium, margin: '0.375rem 0 0' }}>
                    {pa.action}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Steps (collapsible) ───────────────────────────────────────────── */}
      {pa && totalSteps > 0 && (
        <div style={dividerStyle}>
          <button
            onClick={() => setShowSteps(!showSteps)}
            style={{
              width: '100%',
              padding: '0.625rem 1.5rem',
              background: 'none',
              border: 'none',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              color: C.textTertiary,
              fontSize: '0.75rem',
              fontWeight: 600,
              textTransform: 'uppercase' as const,
              letterSpacing: '0.5px',
              transition: 'color 150ms ease',
            }}
            onMouseEnter={e => (e.currentTarget.style.color = C.textSecondary)}
            onMouseLeave={e => (e.currentTarget.style.color = C.textTertiary)}
          >
            <span>Steps ({totalSteps} total)</span>
            <IconChevronRight
              size={16}
              style={{ transform: showSteps ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 150ms ease' }}
            />
          </button>

          {showSteps && (
            <div style={{
              padding: '0 1.5rem 1.25rem',
              display: 'grid',
              gridTemplateColumns: (pa.diagnostics_steps?.length ?? 0) > 0 && (pa.remediation_steps?.length ?? 0) > 0
                ? '1fr 1fr' : '1fr',
              gap: '1.5rem',
            }}>
              {(pa.diagnostics_steps?.length ?? 0) > 0 && (
                <div>
                  <p style={{ ...labelStyle, color: C.medium, marginBottom: '0.75rem' }}>Diagnostics</p>
                  <StepList steps={pa.diagnostics_steps ?? []} accentColor={C.medium} />
                </div>
              )}
              {(pa.remediation_steps?.length ?? 0) > 0 && (
                <div>
                  <p style={{ ...labelStyle, color: C.low, marginBottom: '0.75rem' }}>Remediation</p>
                  <StepList steps={pa.remediation_steps ?? []} accentColor={C.low} />
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── Notes + action buttons ────────────────────────────────────────── */}
      <div style={{ padding: '1rem 1.5rem', ...dividerStyle }}>
        <textarea
          value={notes}
          onChange={(e) => onNotesChange(e.target.value)}
          placeholder="Add decision notes (optional)"
          rows={2}
          style={{
            width: '100%',
            padding: '0.625rem 0.75rem',
            borderRadius: '6px',
            border: `1px solid ${C.border}`,
            backgroundColor: C.bgElevated,
            color: C.textPrimary,
            fontSize: '0.8125rem',
            resize: 'vertical' as const,
            marginBottom: '0.75rem',
            boxSizing: 'border-box' as const,
            outline: 'none',
            transition: 'border-color 150ms ease',
            fontFamily: 'inherit',
          }}
          onFocus={e => (e.currentTarget.style.borderColor = C.medium)}
          onBlur={e => (e.currentTarget.style.borderColor = C.border)}
        />

        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            onClick={onReject}
            disabled={isDeciding}
            style={btnStyle(C.critical, 'reject')}
            onMouseEnter={() => setHoverBtn('reject')}
            onMouseLeave={() => setHoverBtn(null)}
          >
            <IconX size={16} />
            Reject
          </button>

          <button
            onClick={onDiagnosticsOnly}
            disabled={isDeciding}
            style={btnStyle(C.medium, 'diag')}
            onMouseEnter={() => setHoverBtn('diag')}
            onMouseLeave={() => setHoverBtn(null)}
            title="Run diagnostics only — review results before remediation"
          >
            <IconSearch size={16} />
            Diagnostics Only
          </button>

          <button
            onClick={onApprove}
            disabled={isDeciding}
            style={btnStyle(C.low, 'approve')}
            onMouseEnter={() => setHoverBtn('approve')}
            onMouseLeave={() => setHoverBtn(null)}
          >
            <IconCheck size={16} />
            {isDeciding ? 'Processing...' : 'Approve & Remediate'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── HistoryTable ──────────────────────────────────────────────────────────────

function HistoryTable({ history, loading }: { history: Approval[]; loading: boolean }) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const thStyle: React.CSSProperties = {
    padding: '0.5rem 0.875rem',
    textAlign: 'left',
    fontSize: '0.7rem',
    fontWeight: 700,
    color: C.textTertiary,
    textTransform: 'uppercase',
    letterSpacing: '0.5px',
    borderBottom: `1px solid ${C.border}`,
    whiteSpace: 'nowrap',
    userSelect: 'none',
  }

  const tdStyle: React.CSSProperties = {
    padding: '0.625rem 0.875rem',
    borderBottom: `1px solid ${C.border}`,
    fontSize: '0.8125rem',
    color: C.textSecondary,
    verticalAlign: 'middle',
    whiteSpace: 'nowrap',
  }

  if (loading && history.length === 0) {
    return (
      <div style={{ textAlign: 'center', padding: '3rem 0' }}>
        <div style={{
          width: '1.75rem', height: '1.75rem',
          border: `2px solid ${C.border}`, borderTopColor: C.medium,
          borderRadius: '50%', animation: 'spin 0.8s linear infinite',
          margin: '0 auto',
        }} />
        <p style={{ color: C.textTertiary, fontSize: '0.875rem', marginTop: '1rem' }}>Loading history…</p>
      </div>
    )
  }

  if (history.length === 0) {
    return (
      <div style={{ backgroundColor: C.bgSecondary, border: `1px solid ${C.border}`, borderRadius: '10px', padding: '3.5rem 2rem', textAlign: 'center' }}>
        <p style={{ color: C.textSecondary, fontSize: '1rem', fontWeight: 500, margin: 0 }}>No approval history yet</p>
        <p style={{ color: C.textTertiary, fontSize: '0.875rem', marginTop: '0.5rem' }}>Decided approvals will appear here</p>
      </div>
    )
  }

  return (
    <div style={{ backgroundColor: C.bgSecondary, border: `1px solid ${C.border}`, borderRadius: '10px', overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
        <colgroup>
          <col style={{ width: '108px' }} /> {/* Status */}
          <col style={{ width: '155px' }} /> {/* Resource */}
          <col style={{ width: '138px' }} /> {/* Requested At */}
          <col style={{ width: '108px' }} /> {/* Time to Decide */}
          <col style={{ width: '145px' }} /> {/* Decided By */}
          <col />                            {/* Notes */}
          <col style={{ width: '36px'  }} /> {/* Expand chevron */}
        </colgroup>
        <thead style={{ background: C.bgElevated }}>
          <tr>
            <th style={thStyle}>Status</th>
            <th style={thStyle}>Resource / CI</th>
            <th style={thStyle}>Requested At</th>
            <th style={thStyle}>Time to Decide</th>
            <th style={thStyle}>Decided By</th>
            <th style={thStyle}>Notes</th>
            <th style={{ ...thStyle, padding: '0.5rem 0.5rem' }} />
          </tr>
        </thead>
        <tbody>
          {history.map(a => {
            const isExpanded = expandedId === a.approval_id
            const color      = historyStatusColor(a.status)
            const resource   = a.incident_summary?.resource || '—'
            const hasNotes   = Boolean(a.decision_notes)
            const notesTrunc = hasNotes
              ? (a.decision_notes!.length > 55 ? a.decision_notes!.slice(0, 52) + '…' : a.decision_notes!)
              : ''

            const rowBg = isExpanded ? 'rgba(59,130,246,0.06)' : 'transparent'

            return (
              <>
                <tr
                  key={a.approval_id}
                  onClick={() => setExpandedId(isExpanded ? null : a.approval_id)}
                  style={{ cursor: 'pointer', background: rowBg, transition: 'background 120ms' }}
                  onMouseEnter={e => { if (!isExpanded) e.currentTarget.style.background = '#1e2538' }}
                  onMouseLeave={e => { if (!isExpanded) e.currentTarget.style.background = rowBg }}
                >
                  {/* Status badge */}
                  <td style={tdStyle}>
                    <span style={{
                      display: 'inline-block', padding: '0.175rem 0.5rem', borderRadius: '4px',
                      border: `1px solid ${color}`, color, fontSize: '0.67rem',
                      fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.45px',
                    }}>
                      {historyStatusLabel(a.status)}
                    </span>
                  </td>

                  {/* Resource / CI */}
                  <td style={{ ...tdStyle, fontFamily: 'Monaco, Courier New, monospace', fontSize: '0.78rem', color: C.medium, overflow: 'hidden', textOverflow: 'ellipsis' }}
                      title={resource}>
                    {resource}
                  </td>

                  {/* Requested At — absolute timestamp */}
                  <td style={{ ...tdStyle, color: C.textTertiary, fontSize: '0.775rem' }}>
                    {formatCompact(a.requested_at)}
                  </td>

                  {/* Time to Decide — duration, coloured to match outcome */}
                  <td style={{ ...tdStyle, color, fontWeight: 600, fontSize: '0.8rem' }}>
                    {formatTimeToDecide(a.requested_at, a.decided_at)}
                  </td>

                  {/* Decided By */}
                  <td style={{ ...tdStyle, color: C.textPrimary, overflow: 'hidden', textOverflow: 'ellipsis' }}
                      title={a.decided_by || '—'}>
                    {a.decided_by || '—'}
                  </td>

                  {/* Notes */}
                  <td style={{ ...tdStyle, color: hasNotes ? C.textSecondary : C.textTertiary, fontStyle: hasNotes ? 'normal' : 'italic', overflow: 'hidden', textOverflow: 'ellipsis' }}
                      title={a.decision_notes || ''}>
                    {hasNotes ? notesTrunc : 'no notes'}
                  </td>

                  {/* Expand chevron */}
                  <td style={{ ...tdStyle, padding: '0.625rem 0.5rem', textAlign: 'right', color: C.textTertiary }}>
                    <IconChevronRight size={13} style={{ display: 'block', transform: isExpanded ? 'rotate(90deg)' : 'rotate(0deg)', transition: 'transform 150ms' }} />
                  </td>
                </tr>

                {/* Expanded detail panel */}
                {isExpanded && (
                  <tr key={`${a.approval_id}-detail`}>
                    <td colSpan={7} style={{ padding: 0, borderBottom: `1px solid ${C.border}` }}>
                      <HistoryDetailPanel approval={a} />
                    </td>
                  </tr>
                )}
              </>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── ApprovalQueue (page) ──────────────────────────────────────────────────────

interface ApprovalQueueProps {
  onApproved: () => void
}

type Tab = 'pending' | 'history'

export default function ApprovalQueue({ onApproved: _onApproved }: ApprovalQueueProps) {
  const { user }                            = useCurrentUser()
  const [activeTab, setActiveTab]           = useState<Tab>('pending')

  // Pending state
  const [approvals, setApprovals]           = useState<Approval[]>([])
  const [pendingLoading, setPendingLoading] = useState(true)
  const [pendingError, setPendingError]     = useState<string | null>(null)
  const [decidingId, setDecidingId]         = useState<string | null>(null)
  const [decisionNotes, setDecisionNotes]   = useState<Record<string, string>>({})

  // History state
  const [history, setHistory]               = useState<Approval[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError]     = useState<string | null>(null)

  // ── Loaders ────────────────────────────────────────────────────────────────

  const loadApprovals = useCallback(async () => {
    try {
      setPendingLoading(true)
      const response = await getPendingApprovals({ limit: 20 })
      setApprovals(response.data as Approval[])
      setPendingError(null)
    } catch (err) {
      setPendingError('Failed to load approvals')
      console.error(err)
    } finally {
      setPendingLoading(false)
    }
  }, [])

  const loadHistory = useCallback(async () => {
    try {
      setHistoryLoading(true)
      const response = await getApprovalHistory({ limit: 100 })
      setHistory(response.data as Approval[])
      setHistoryError(null)
    } catch (err) {
      setHistoryError('Failed to load approval history')
      console.error(err)
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  // Initial load + 60 s safety-net poll (WS push handles sub-second updates)
  useEffect(() => {
    loadApprovals()
    const interval = setInterval(loadApprovals, 60_000)
    return () => clearInterval(interval)
  }, [loadApprovals])

  useEffect(() => {
    if (activeTab === 'history') loadHistory()
  }, [activeTab, loadHistory])

  // Live push — reload pending queue the moment the server detects a new approval
  useGlobalEvents(useCallback((event) => {
    if (event.type === 'approval_requested') loadApprovals()
    // Also reload when an incident resolves (watcher all-clear cancels the approval)
    if (event.type === 'incident_updated') loadApprovals()
  }, [loadApprovals]))

  // ── Decision handler ────────────────────────────────────────────────────────

  const handleDecision = async (
    approvalId: string,
    decision: 'approved' | 'rejected' | 'diagnostics_only'
  ) => {
    try {
      setDecidingId(approvalId)
      const notes = decisionNotes[approvalId] || ''
      const decidedBy = user?.name || user?.email || 'operator'
      await submitApprovalDecision(approvalId, decision, notes, decidedBy)
      setApprovals(prev => prev.filter((a) => a.approval_id !== approvalId))
      setDecisionNotes(prev => ({ ...prev, [approvalId]: '' }))
      loadApprovals()
      // Refresh history in background so new entry is ready if user switches tabs
      loadHistory()
    } catch (err) {
      setPendingError(err instanceof Error ? err.message : `Failed to submit ${decision} decision`)
    } finally {
      setDecidingId(null)
    }
  }

  // ── UI helpers ──────────────────────────────────────────────────────────────

  const isRefreshing = activeTab === 'pending' ? pendingLoading : historyLoading
  const handleRefresh = activeTab === 'pending' ? loadApprovals : loadHistory

  function tabStyle(tab: Tab): React.CSSProperties {
    const active = activeTab === tab
    return {
      padding: '0.5rem 1.25rem',
      borderRadius: '6px 6px 0 0',
      border: `1px solid ${active ? C.border : 'transparent'}`,
      borderBottom: active ? `1px solid ${C.bgPrimary}` : `1px solid ${C.border}`,
      backgroundColor: active ? C.bgPrimary : 'transparent',
      color: active ? C.textPrimary : C.textTertiary,
      fontSize: '0.875rem',
      fontWeight: 600,
      cursor: 'pointer',
      transition: 'all 150ms ease',
      whiteSpace: 'nowrap',
      marginBottom: '-1px',
      position: 'relative',
      zIndex: active ? 1 : 0,
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div style={{ maxWidth: '960px', margin: '0 auto' }}>

      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '2rem', fontWeight: 700, color: C.textPrimary, letterSpacing: '-0.5px', margin: 0 }}>
          Approval Queue
        </h1>
        <button
          onClick={handleRefresh}
          disabled={isRefreshing}
          style={{
            padding: '0.5rem 1rem',
            borderRadius: '6px',
            border: `1px solid ${C.border}`,
            backgroundColor: 'transparent',
            color: isRefreshing ? C.textTertiary : C.textSecondary,
            fontSize: '0.8125rem',
            fontWeight: 600,
            cursor: isRefreshing ? 'not-allowed' : 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '0.375rem',
            transition: 'all 150ms ease',
          }}
        >
          <IconRefresh size={16} style={{ animation: isRefreshing ? 'spin 1s linear infinite' : 'none' }} />
          {isRefreshing ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      {/* ── Tab bar ────────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', borderBottom: `1px solid ${C.border}`, marginBottom: '1.5rem' }}>
        <button style={tabStyle('pending')} onClick={() => setActiveTab('pending')}>
          Pending Approvals
          {approvals.length > 0 && (
            <span style={{
              marginLeft: '0.5rem',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              minWidth: '1.25rem',
              height: '1.25rem',
              padding: '0 0.35rem',
              borderRadius: '10px',
              backgroundColor: C.amber,
              color: '#0f1419',
              fontSize: '0.65rem',
              fontWeight: 800,
              letterSpacing: 0,
            }}>
              {approvals.length}
            </span>
          )}
        </button>
        <button style={tabStyle('history')} onClick={() => setActiveTab('history')}>
          Approval History
        </button>
      </div>

      {/* ── Pending tab ────────────────────────────────────────────────────── */}
      {activeTab === 'pending' && (
        <>
          {pendingError && (
            <div style={{
              marginBottom: '1rem',
              padding: '0.875rem 1rem',
              borderRadius: '6px',
              border: `1px solid ${C.critical}`,
              color: C.critical,
              fontSize: '0.875rem',
              backgroundColor: `${C.critical}10`,
            }}>
              {pendingError}
            </div>
          )}

          {pendingLoading && approvals.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '4rem 0' }}>
              <div style={{
                width: '2rem', height: '2rem',
                border: `2px solid ${C.border}`, borderTopColor: C.medium,
                borderRadius: '50%', animation: 'spin 0.8s linear infinite',
                margin: '0 auto',
              }} />
              <p style={{ color: C.textTertiary, fontSize: '0.875rem', marginTop: '1rem' }}>
                Loading approvals...
              </p>
            </div>
          ) : approvals.length === 0 ? (
            <div style={{
              backgroundColor: C.bgSecondary,
              border: `1px solid ${C.border}`,
              borderRadius: '10px',
              padding: '4rem 2rem',
              textAlign: 'center',
            }}>
              <p style={{ color: C.textSecondary, fontSize: '1rem', fontWeight: 500, margin: 0 }}>
                No pending approvals
              </p>
              <p style={{ color: C.textTertiary, fontSize: '0.875rem', marginTop: '0.5rem' }}>
                All CAB requests have been addressed
              </p>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <p style={{ color: C.textTertiary, fontSize: '0.75rem', margin: '-0.5rem 0 0' }}>
                Ranked by risk score — highest impact first, not arrival order
              </p>
              {approvals.map((approval) => (
                <ApprovalCard
                  key={approval.approval_id}
                  approval={approval}
                  onApprove={() => handleDecision(approval.approval_id, 'approved')}
                  onDiagnosticsOnly={() => handleDecision(approval.approval_id, 'diagnostics_only')}
                  onReject={() => handleDecision(approval.approval_id, 'rejected')}
                  isDeciding={decidingId === approval.approval_id}
                  notes={decisionNotes[approval.approval_id] || ''}
                  onNotesChange={(notes) =>
                    setDecisionNotes({ ...decisionNotes, [approval.approval_id]: notes })
                  }
                />
              ))}
            </div>
          )}
        </>
      )}

      {/* ── History tab ─────────────────────────────────────────────────────── */}
      {activeTab === 'history' && (
        <>
          {historyError && (
            <div style={{
              marginBottom: '1rem',
              padding: '0.875rem 1rem',
              borderRadius: '6px',
              border: `1px solid ${C.critical}`,
              color: C.critical,
              fontSize: '0.875rem',
              backgroundColor: `${C.critical}10`,
            }}>
              {historyError}
            </div>
          )}
          <HistoryTable history={history} loading={historyLoading} />
        </>
      )}
    </div>
  )
}
