import type { CSSProperties } from 'react'
import { RemediationPlan } from '../types'
import {
  IconBook,
  IconBookmark,
  IconHistory,
  IconRobot,
  IconShield,
} from './icons'

interface RemediationRecommendationProps {
  recommendation: RemediationPlan | null
  executionStatus: 'pending' | 'in_progress' | 'completed' | 'failed'
}

const CARD: CSSProperties = {
  backgroundColor: '#1a1f2e',
  border: '1px solid #3d4557',
  borderRadius: '10px',
  overflow: 'hidden',
}
const HEADER: CSSProperties = {
  padding: '10px 14px',
  borderBottom: '1px solid #3d4557',
}
const INNER: CSSProperties = {
  backgroundColor: '#252c3c',
  border: '1px solid #3d4557',
  borderRadius: '6px',
  padding: '10px 12px',
}
const LABEL: CSSProperties = {
  fontSize: '10px',
  fontWeight: 600,
  color: '#a0aec0',
  letterSpacing: '0.07em',
  textTransform: 'uppercase',
}

const statusColor = {
  pending:     '#7a8ba3',
  in_progress: '#3b82f6',
  completed:   '#10b981',
  failed:      '#dc2626',
}

const riskColor = (level: string) => ({
  critical: '#dc2626',
  high:     '#f97316',
  medium:   '#f59e0b',
  low:      '#10b981',
  info:     '#3b82f6',
}[level] || '#a0aec0')

const tierInfo = {
  runbook:  { label: 'Runbook',       tier: '1', icon: <IconBook     size={15} />, color: '#10b981' },
  playbook: { label: 'Playbook',      tier: '2', icon: <IconBookmark size={15} />, color: '#3b82f6' },
  history:  { label: 'Historical',    tier: '3', icon: <IconHistory  size={15} />, color: '#a855f7' },
  llm:      { label: 'AI-Driven',     tier: '4', icon: <IconRobot    size={15} />, color: '#f59e0b' },
  fallback: { label: 'Safe Fallback', tier: '5', icon: <IconShield   size={15} />, color: '#7a8ba3' },
}

export default function RemediationRecommendation({
  recommendation,
  executionStatus,
}: RemediationRecommendationProps) {
  if (!recommendation) {
    return (
      <div style={CARD}>
        <div style={HEADER}><span style={LABEL}>Remediation Recommendation</span></div>
        <div style={{ padding: '14px' }}>
          <p style={{ fontSize: '12px', color: '#7a8ba3' }}>No remediation recommendation available</p>
        </div>
      </div>
    )
  }

  const tier            = tierInfo[recommendation.source] || tierInfo.fallback
  const confidencePct   = Math.round(recommendation.confidence * 100)
  const execColor       = statusColor[executionStatus] || '#a0aec0'
  const rLevel          = riskColor(recommendation.risk_level)

  return (
    <div className="space-y-2">
      {/* Source & Confidence */}
      <div style={CARD}>
        <div style={HEADER}><span style={LABEL}>Recommendation</span></div>
        <div style={{ padding: '12px 14px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <div style={{
              width: '32px', height: '32px', borderRadius: '8px',
              backgroundColor: `${tier.color}18`,
              border: `1px solid ${tier.color}40`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: tier.color, flexShrink: 0,
            }}>
              {tier.icon}
            </div>
            <div>
              <p style={{ fontSize: '10px', color: '#7a8ba3' }}>Tier {tier.tier} · {tier.label}</p>
              <p style={{ fontSize: '12px', fontWeight: 600, color: '#e8eef5', marginTop: '1px' }}>
                {recommendation.summary}
              </p>
            </div>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <p style={LABEL}>Confidence</p>
            <p style={{ fontSize: '20px', fontWeight: 700, color: '#10b981' }}>{confidencePct}%</p>
          </div>
        </div>
      </div>

      {/* Risk & Actions */}
      <div style={CARD}>
        <div style={HEADER}><span style={LABEL}>Risk & Actions</span></div>
        <div style={{ padding: '12px 14px' }}>
          <div style={{ display: 'flex', gap: '12px', marginBottom: '10px' }}>
            <div style={{ flex: 1, ...INNER }}>
              <p style={LABEL}>Risk Level</p>
              <p style={{ fontSize: '14px', fontWeight: 700, color: rLevel, textTransform: 'capitalize', marginTop: '3px' }}>
                {recommendation.risk_level}
              </p>
            </div>
            <div style={{ flex: 1, ...INNER }}>
              <p style={LABEL}>Blast Radius</p>
              <p style={{ fontSize: '14px', fontWeight: 700, color: '#e8eef5', marginTop: '3px' }}>
                {recommendation.blast_radius} service{recommendation.blast_radius !== 1 ? 's' : ''}
              </p>
            </div>
          </div>

          {recommendation.actions && recommendation.actions.length > 0 && (
            <>
              <p style={{ ...LABEL, marginBottom: '6px' }}>Actions ({recommendation.actions.length})</p>
              <div className="flex flex-wrap gap-1.5">
                {recommendation.actions.map((action, idx) => (
                  <span key={idx} style={{
                    fontSize: '10px', fontFamily: 'monospace', fontWeight: 600,
                    color: '#93c5fd',
                    backgroundColor: 'rgba(59,130,246,0.1)',
                    border: '1px solid rgba(59,130,246,0.25)',
                    borderRadius: '4px',
                    padding: '2px 7px',
                  }}>
                    {action.tool}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Execution Status */}
      <div style={{
        ...CARD,
        borderLeft: `3px solid ${execColor}`,
      }}>
        <div style={{ padding: '10px 14px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={LABEL}>Execution Status</span>
          <span style={{
            fontSize: '11px', fontWeight: 700,
            color: execColor,
            backgroundColor: `${execColor}15`,
            border: `1px solid ${execColor}40`,
            borderRadius: '5px',
            padding: '2px 10px',
            textTransform: 'capitalize',
          }}>
            {executionStatus.replace('_', ' ')}
          </span>
        </div>
      </div>
    </div>
  )
}
