import type { CSSProperties } from 'react'
import { Policy } from '../types'
import { IconCheck, IconAlertTriangle, IconInfoCircle } from './icons'

interface PolicyEvaluationProps {
  matched_policies: Policy[]
  approved_actions: string[]
  requires_manual_approval: boolean
  constraints: {
    max_blast_radius?: number
    max_restart_frequency?: number
    requires_post_monitoring?: boolean
  }
}

const CARD: CSSProperties = {
  backgroundColor: '#1a1f2e',
  border: '1px solid #3d4557',
  borderRadius: '12px',
  overflow: 'hidden',
}

const CARD_HEADER: CSSProperties = {
  padding: '10px 16px',
  borderBottom: '1px solid #3d4557',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
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

export default function PolicyEvaluation({
  matched_policies,
  approved_actions,
  requires_manual_approval,
  constraints,
}: PolicyEvaluationProps) {

  const approvalAccent = requires_manual_approval ? '#a855f7' : '#10b981'

  if (!matched_policies || matched_policies.length === 0) {
    return (
      <div style={CARD}>
        <div style={CARD_HEADER}>
          <span style={LABEL}>Policy Evaluation</span>
        </div>
        <div style={{ padding: '20px 16px' }}>
          <p style={{ fontSize: '13px', color: '#a0aec0' }}>No policies matched — conservative defaults applied</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">

      {/* Approval status banner */}
      <div style={{
        backgroundColor: `${approvalAccent}12`,
        border: `1px solid ${approvalAccent}40`,
        borderRadius: '10px',
        padding: '12px 16px',
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
      }}>
        {requires_manual_approval
          ? <IconAlertTriangle size={16} style={{ color: approvalAccent, flexShrink: 0 }} />
          : <IconCheck size={16} style={{ color: approvalAccent, flexShrink: 0 }} />
        }
        <div>
          <span style={{ fontSize: '11px', fontWeight: 700, color: approvalAccent, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {requires_manual_approval ? 'Manual Approval Required' : 'Auto-Approved'}
          </span>
          <p style={{ fontSize: '11px', color: '#a0aec0', marginTop: '2px' }}>
            {requires_manual_approval
              ? 'This incident requires manual approval before remediation can proceed'
              : 'This incident is approved for automatic remediation'}
          </p>
        </div>
      </div>

      {/* Matched Policies */}
      <div style={CARD}>
        <div style={CARD_HEADER}>
          <span style={LABEL}>Matched Policies</span>
          <span style={{
            fontSize: '11px', fontWeight: 600, color: '#3b82f6',
            backgroundColor: 'rgba(59,130,246,0.12)', border: '1px solid rgba(59,130,246,0.3)',
            borderRadius: '5px', padding: '2px 8px',
          }}>
            {matched_policies.length} matched
          </span>
        </div>
        <div style={{ padding: '14px 16px' }} className="space-y-3">
          {matched_policies.map((policy, idx) => {
            const isGoverning = idx === 0 && matched_policies.length > 1
            return (
            <div key={policy.policy_id || idx} style={{
              ...INNER,
              ...(isGoverning ? { border: '1px solid rgba(16,185,129,0.4)', backgroundColor: 'rgba(16,185,129,0.04)' } : {}),
            }}>
              {isGoverning && (
                <div style={{ marginBottom: '8px' }}>
                  <span style={{
                    fontSize: '10px', fontWeight: 700, color: '#10b981',
                    backgroundColor: 'rgba(16,185,129,0.12)', border: '1px solid rgba(16,185,129,0.3)',
                    borderRadius: '5px', padding: '2px 8px', letterSpacing: '0.06em', textTransform: 'uppercase',
                  }}>
                    Governing Policy
                  </span>
                  <span style={{ fontSize: '10px', color: '#7a8ba3', marginLeft: '8px' }}>
                    Decided this outcome
                  </span>
                </div>
              )}
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1">
                  <p style={{ fontSize: '13px', fontWeight: 600, color: '#e8eef5' }}>{policy.name}</p>
                  <p style={{ fontSize: '11px', color: '#7a8ba3', marginTop: '3px' }}>
                    Priority:{' '}
                    <span style={{ fontFamily: 'monospace', color: '#a0aec0', fontWeight: 700 }}>
                      {policy.approval_priority}
                    </span>
                    {isGoverning && (
                      <span style={{ color: '#7a8ba3', marginLeft: '6px' }}>(lowest = highest precedence)</span>
                    )}
                  </p>
                </div>
                {policy.requires_manual_approval && (
                  <span style={{
                    fontSize: '10px', fontWeight: 600, color: '#f59e0b',
                    backgroundColor: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)',
                    borderRadius: '5px', padding: '2px 7px', whiteSpace: 'nowrap',
                  }}>
                    Manual Required
                  </span>
                )}
              </div>

              {/* Rules */}
              {policy.rules && Object.keys(policy.rules).length > 0 && (
                <div style={{
                  marginTop: '8px',
                  backgroundColor: '#1a1f2e',
                  border: '1px solid #3d4557',
                  borderRadius: '6px',
                  padding: '8px 10px',
                }}>
                  <p style={{ fontFamily: 'monospace', fontSize: '10px', color: '#7a8ba3', lineHeight: 1.7 }}>
                    {Object.entries(policy.rules)
                      .map(([k, v]) => `${k}: ${JSON.stringify(v)}`)
                      .join(' | ')}
                  </p>
                </div>
              )}
            </div>
          )})}

        </div>
      </div>

      {/* Approved Actions */}
      {approved_actions && approved_actions.length > 0 && (
        <div style={CARD}>
          <div style={CARD_HEADER}>
            <span style={LABEL}>Approved Actions</span>
            <span style={{ fontSize: '11px', color: '#7a8ba3' }}>{approved_actions.length} action{approved_actions.length !== 1 ? 's' : ''}</span>
          </div>
          <div style={{ padding: '14px 16px' }}>
            <div className="flex flex-wrap gap-2">
              {approved_actions.map((action) => (
                <span key={action} style={{
                  fontSize: '11px', fontWeight: 600,
                  color: '#93c5fd',
                  backgroundColor: 'rgba(59,130,246,0.1)',
                  border: '1px solid rgba(59,130,246,0.25)',
                  borderRadius: '6px',
                  padding: '4px 10px',
                  fontFamily: 'monospace',
                }}>
                  {action}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Constraints */}
      {constraints && Object.keys(constraints).some(k => (constraints as any)[k] !== undefined) && (
        <div style={CARD}>
          <div style={CARD_HEADER}>
            <span style={LABEL}>Constraints</span>
          </div>
          <div style={{ padding: '14px 16px' }} className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {constraints.max_blast_radius != null && (
              <div style={{
                backgroundColor: 'rgba(245,158,11,0.08)',
                border: '1px solid rgba(245,158,11,0.25)',
                borderRadius: '8px',
                padding: '12px 14px',
              }}>
                <p style={{ fontSize: '10px', fontWeight: 600, color: '#f59e0b', letterSpacing: '0.07em', textTransform: 'uppercase' }}>Max Blast Radius</p>
                <p style={{ fontSize: '24px', fontWeight: 700, color: '#e8eef5', marginTop: '4px' }}>{constraints.max_blast_radius}</p>
              </div>
            )}
            {constraints.max_restart_frequency != null && (
              <div style={{
                backgroundColor: 'rgba(245,158,11,0.08)',
                border: '1px solid rgba(245,158,11,0.25)',
                borderRadius: '8px',
                padding: '12px 14px',
              }}>
                <p style={{ fontSize: '10px', fontWeight: 600, color: '#f59e0b', letterSpacing: '0.07em', textTransform: 'uppercase' }}>Max Restart Frequency</p>
                <p style={{ fontSize: '24px', fontWeight: 700, color: '#e8eef5', marginTop: '4px' }}>{constraints.max_restart_frequency}</p>
              </div>
            )}
            {constraints.requires_post_monitoring && (
              <div style={{
                backgroundColor: 'rgba(59,130,246,0.08)',
                border: '1px solid rgba(59,130,246,0.25)',
                borderRadius: '8px',
                padding: '10px 14px',
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
              }} className="md:col-span-2">
                <IconInfoCircle size={15} style={{ color: '#3b82f6', flexShrink: 0 }} />
                <p style={{ fontSize: '12px', color: '#a0aec0' }}>Post-remediation monitoring required</p>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  )
}
