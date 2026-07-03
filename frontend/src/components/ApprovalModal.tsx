import { useState } from 'react'
import { createPortal } from 'react-dom'
import { Workflow } from '../types'
import { IconShield, IconCheck, IconX, IconTestPipe } from './icons'

interface ApprovalModalProps {
  incident: Workflow
  loading: boolean
  error?: string | null
  onApprove: (notes: string) => Promise<boolean>
  onDiagnosticsOnly: (notes: string) => Promise<boolean>
  onReject: (notes: string) => Promise<boolean>
  onClose: () => void
}

type Decision = 'approve' | 'diagnostics' | 'reject'

const SEV_COLOR: Record<string, string> = {
  critical: '#dc2626',
  high: '#f97316',
  medium: '#f59e0b',
  low: '#10b981',
}

export function ApprovalModal({
  incident,
  loading,
  error,
  onApprove,
  onDiagnosticsOnly,
  onReject,
  onClose,
}: ApprovalModalProps) {
  const [decision, setDecision] = useState<Decision | null>(null)
  const [notes, setNotes] = useState('')

  const ctx = incident.context || {}
  const proposal = ctx.proposal || {}
  const runbookName: string = proposal.runbook_name || proposal.action || '—'
  const blastRadius: number | null = proposal.blast_radius ?? ctx.risk?.blast_radius ?? null
  const diagnosticsCount: number = proposal.diagnostics_steps?.length ?? 0
  const remediationCount: number = proposal.remediation_steps?.length ?? 0

  const incNum =
    incident.incident_number_str ||
    (incident.incident_number
      ? `INC${String(incident.incident_number).padStart(4, '0')}`
      : null)

  const sevColor = SEV_COLOR[incident.severity?.toLowerCase() ?? ''] ?? '#a0aec0'

  const canSubmit =
    decision !== null &&
    !loading &&
    !(decision === 'reject' && !notes.trim())

  const handleConfirm = async () => {
    if (!canSubmit) return
    let success = false
    if (decision === 'approve') success = await onApprove(notes)
    else if (decision === 'diagnostics') success = await onDiagnosticsOnly(notes)
    else success = await onReject(notes)
    if (success) onClose()
  }

  const decisionStyles: Record<Decision, { border: string; color: string }> = {
    approve:     { border: '#10b981', color: '#10b981' },
    diagnostics: { border: '#3b82f6', color: '#3b82f6' },
    reject:      { border: '#ef4444', color: '#ef4444' },
  }

  const confirmLabel = !decision
    ? 'Select a decision above'
    : decision === 'approve'
    ? 'Confirm Approval'
    : decision === 'diagnostics'
    ? 'Run Diagnostics Only'
    : 'Confirm Rejection'

  const confirmBg =
    decision === 'approve'
      ? '#10b981'
      : decision === 'diagnostics'
      ? '#3b82f6'
      : decision === 'reject'
      ? '#ef4444'
      : '#3d4557'

  return createPortal(
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0,0,0,0.75)',
        backdropFilter: 'blur(4px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: '1rem',
      }}
      onClick={onClose}
    >
      <div
        style={{
          backgroundColor: '#1a1f2e',
          border: '1px solid #a855f750',
          borderTop: '3px solid #a855f7',
          borderRadius: '12px',
          padding: '2rem',
          maxWidth: '540px',
          width: '100%',
          maxHeight: '90vh',
          overflowY: 'auto',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Header ── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1.5rem' }}>
          <IconShield size={24} color="#a855f7" />
          <h2 style={{ fontSize: '1.25rem', fontWeight: 700, color: '#e8eef5', margin: 0 }}>
            Review Approval Request
          </h2>
        </div>

        {/* ── Incident summary card ── */}
        <div
          style={{
            backgroundColor: '#252c3c',
            borderRadius: '10px',
            padding: '1rem 1.25rem',
            marginBottom: '1.5rem',
            border: '1px solid #3d4557',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', marginBottom: '0.625rem' }}>
            {incNum && (
              <span
                style={{
                  fontFamily: "'Monaco', 'Courier New', monospace",
                  fontSize: '0.8rem',
                  fontWeight: 700,
                  color: sevColor,
                  backgroundColor: `${sevColor}18`,
                  border: `1px solid ${sevColor}40`,
                  padding: '0.15rem 0.5rem',
                  borderRadius: '5px',
                }}
              >
                {incNum}
              </span>
            )}
            {incident.severity && (
              <span
                style={{
                  fontSize: '0.65rem',
                  fontWeight: 700,
                  color: sevColor,
                  border: `1px solid ${sevColor}60`,
                  backgroundColor: `${sevColor}15`,
                  borderRadius: '5px',
                  padding: '0.15rem 0.5rem',
                  textTransform: 'uppercase',
                  letterSpacing: '0.5px',
                }}
              >
                {incident.severity}
              </span>
            )}
          </div>

          <p style={{ fontSize: '0.875rem', fontWeight: 600, color: '#e8eef5', margin: '0 0 0.875rem 0' }}>
            {incident.title || incNum || 'Unknown Incident'}
          </p>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.625rem' }}>
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.2rem' }}>
                <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', margin: 0 }}>
                  Proposed Runbook
                </p>
                {(() => {
                  const src: string = (proposal as any).source || ''
                  const tier: Record<string, { label: string; color: string }> = {
                    runbook_library:    { label: 'Runbook',   color: '#10b981' },
                    cmdb_playbook:      { label: 'Playbook',  color: '#3b82f6' },
                    llm_generated:      { label: 'AI-Driven', color: '#f59e0b' },
                    fallback_escalation:{ label: 'Fallback',  color: '#7a8ba3' },
                  }
                  const t = tier[src]
                  if (!t) return null
                  return (
                    <span style={{
                      fontSize: '0.6rem',
                      fontWeight: 700,
                      color: t.color,
                      border: `1px solid ${t.color}50`,
                      backgroundColor: `${t.color}15`,
                      borderRadius: '4px',
                      padding: '0.1rem 0.4rem',
                      letterSpacing: '0.4px',
                      textTransform: 'uppercase' as const,
                    }}>
                      {t.label}
                    </span>
                  )
                })()}
              </div>
              <p style={{ fontSize: '0.8125rem', color: '#c1c7d0', margin: 0 }}>
                {runbookName.replace(/_/g, ' ')}
              </p>
            </div>
            {blastRadius != null && (
              <div>
                <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.2rem' }}>
                  Blast Radius
                </p>
                <p
                  style={{
                    fontSize: '0.8125rem',
                    fontWeight: 700,
                    color: blastRadius >= 3 ? '#dc2626' : blastRadius >= 2 ? '#f59e0b' : '#10b981',
                    margin: 0,
                  }}
                >
                  L{blastRadius}
                </p>
              </div>
            )}
            {diagnosticsCount > 0 && (
              <div>
                <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.2rem' }}>
                  Diagnostic Steps
                </p>
                <p style={{ fontSize: '0.8125rem', color: '#f59e0b', margin: 0 }}>{diagnosticsCount} steps</p>
              </div>
            )}
            {remediationCount > 0 && (
              <div>
                <p style={{ fontSize: '0.65rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.2rem' }}>
                  Remediation Steps
                </p>
                <p style={{ fontSize: '0.8125rem', color: '#10b981', margin: 0 }}>{remediationCount} steps</p>
              </div>
            )}
          </div>
        </div>

        {/* ── Decision selector ── */}
        <p style={{ fontSize: '0.75rem', fontWeight: 600, color: '#7a8ba3', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '0.625rem' }}>
          Decision
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.625rem', marginBottom: '1.25rem' }}>
          {(
            [
              { id: 'approve', icon: <IconCheck size={18} />, label: 'Approve', sub: 'Full remediation' },
              { id: 'diagnostics', icon: <IconTestPipe size={18} />, label: 'Diagnostics', sub: 'Run checks only' },
              { id: 'reject', icon: <IconX size={18} />, label: 'Reject', sub: 'No action' },
            ] as { id: Decision; icon: React.ReactNode; label: string; sub: string }[]
          ).map(({ id, icon, label, sub }) => {
            const s = decisionStyles[id]
            const active = decision === id
            return (
              <button
                key={id}
                onClick={() => setDecision(id)}
                style={{
                  padding: '0.75rem 0.5rem',
                  borderRadius: '8px',
                  border: `2px solid ${active ? s.border : '#3d4557'}`,
                  backgroundColor: active ? `${s.border}18` : 'transparent',
                  color: active ? s.color : '#7a8ba3',
                  fontWeight: 600,
                  fontSize: '0.8125rem',
                  cursor: 'pointer',
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: '0.3rem',
                  transition: 'all 150ms ease',
                  outline: 'none',
                }}
                onMouseEnter={(e) => {
                  if (!active) {
                    e.currentTarget.style.border = `2px solid ${s.border}80`
                    e.currentTarget.style.color = s.color
                  }
                }}
                onMouseLeave={(e) => {
                  if (!active) {
                    e.currentTarget.style.border = '2px solid #3d4557'
                    e.currentTarget.style.color = '#7a8ba3'
                  }
                }}
              >
                {icon}
                {label}
                <span style={{ fontSize: '0.6rem', color: active ? `${s.color}aa` : '#7a8ba3', fontWeight: 400 }}>{sub}</span>
              </button>
            )
          })}
        </div>

        {/* ── Notes ── */}
        {decision && (
          <>
            <label
              style={{
                fontSize: '0.75rem',
                fontWeight: 600,
                color: '#a0aec0',
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                display: 'block',
                marginBottom: '0.4rem',
              }}
            >
              {decision === 'reject' ? (
                <>Rejection Reason <span style={{ color: '#dc2626' }}>*</span></>
              ) : (
                'Notes (optional)'
              )}
            </label>
            <textarea
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder={
                decision === 'reject'
                  ? 'Why is this being rejected?'
                  : 'Additional context…'
              }
              className="form-input"
              style={{ width: '100%', marginBottom: '1.25rem', resize: 'vertical', minHeight: '72px' }}
            />
          </>
        )}

        {/* ── Error banner ── */}
        {error && (
          <div
            style={{
              backgroundColor: '#2d1515',
              border: '1px solid #ef444450',
              borderRadius: '8px',
              padding: '0.625rem 0.875rem',
              marginBottom: '1rem',
              fontSize: '0.8125rem',
              color: '#fca5a5',
              display: 'flex',
              alignItems: 'flex-start',
              gap: '0.5rem',
            }}
          >
            <IconX size={15} color="#ef4444" style={{ flexShrink: 0, marginTop: '1px' }} />
            <span>{error}</span>
          </div>
        )}

        {/* ── Actions ── */}
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button
            onClick={handleConfirm}
            disabled={!canSubmit}
            style={{
              flex: 1,
              padding: '0.75rem',
              borderRadius: '8px',
              border: `2px solid ${decision ? confirmBg : '#3d4557'}`,
              fontWeight: 600,
              fontSize: '0.875rem',
              cursor: canSubmit ? 'pointer' : 'not-allowed',
              backgroundColor: decision ? `${confirmBg}20` : 'transparent',
              color: canSubmit ? confirmBg : '#7a8ba3',
              opacity: canSubmit ? 1 : 0.55,
              transition: 'all 150ms ease',
            }}
          >
            {loading ? 'Processing…' : confirmLabel}
          </button>
          <button
            onClick={onClose}
            className="btn btn-secondary"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

export default ApprovalModal
