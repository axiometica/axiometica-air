import React from 'react'

/**
 * StatusBadge Component
 *
 * Displays critical metrics with colored text + colored border (no fill)
 * Follows strict design system: DESIGN_SYSTEM_STRICT.md
 *
 * Key metrics: Lifecycle Stage, Criticality, Priority, Risk, Confidence, Status
 */

type MetricType = 'lifecycle' | 'criticality' | 'priority' | 'risk' | 'confidence' | 'status'
type Size = 'sm' | 'md'

interface StatusBadgeProps {
  /**
   * Metric type determines color mapping
   * lifecycle: open, investigating, diagnostics, waiting_approval, approved, executing, verifying, resolved, failed
   * criticality: critical, high, medium, low
   * priority: 1, 2, 3, 4, 5
   * risk: 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
   * confidence: 0-100 (%)
   * status: pending, approved, executing, completed, failed
   */
  type: MetricType
  value: string | number
  size?: Size
  className?: string
}

const getColor = (type: MetricType, value: string | number): string => {
  // Lifecycle Stage Colors
  if (type === 'lifecycle') {
    const lifecycle = String(value).toLowerCase()
    // Phase 1 — system working, no action needed
    if (['open', 'submitted', 'in_progress', 'investigating', 'diagnostics', 'executing', 'verifying'].includes(lifecycle)) return '#94a3b8' // Slate
    // Phase 2 — decision gate
    if (['waiting_approval'].includes(lifecycle)) return '#f59e0b' // Amber — decision needed
    if (['approved'].includes(lifecycle)) return '#06b6d4' // Cyan — decided, about to execute
    // Phase 3 — human action required (escalated, not catastrophic)
    if (['awaiting_manual', 'failed', 'rejected', 'rolled_back'].includes(lifecycle)) return '#f97316' // Orange — human handoff
    // Special
    if (['storm_hold'].includes(lifecycle)) return '#8b5cf6' // Violet — held in storm cluster
    // Terminal
    if (['resolved', 'closed', 'deployed'].includes(lifecycle)) return '#10b981' // Emerald
  }

  // Criticality/Severity Colors
  if (type === 'criticality') {
    const severity = String(value).toLowerCase()
    if (severity === 'critical') return '#dc2626' // Red
    if (severity === 'high') return '#f97316' // Orange
    if (severity === 'medium') return '#f59e0b' // Amber
    if (severity === 'low') return '#10b981' // Emerald
  }

  // Priority Colors (1-5 scale)
  if (type === 'priority') {
    const priority = Number(value)
    if (priority === 1) return '#dc2626' // Red - Highest
    if (priority === 2) return '#f97316' // Orange
    if (priority === 3) return '#f59e0b' // Amber - Medium
    if (priority === 4) return '#3b82f6' // Blue
    if (priority === 5) return '#10b981' // Emerald - Lowest
  }

  // Risk Score Colors (0-100 scale)
  if (type === 'risk') {
    const risk = Number(value)
    if (risk >= 70) return '#dc2626' // Red - Critical risk
    if (risk >= 50) return '#f97316' // Orange - High risk
    if (risk >= 20) return '#f59e0b' // Amber - Medium risk
    return '#10b981' // Emerald - Low risk
  }

  // Remediation Confidence (0-100 %)
  if (type === 'confidence') {
    const conf = Number(value)
    if (conf >= 90) return '#10b981' // Emerald - High confidence
    if (conf >= 70) return '#f59e0b' // Amber - Medium confidence
    if (conf >= 50) return '#f97316' // Orange - Low confidence
    return '#dc2626' // Red - Very low confidence
  }

  // Status Colors
  if (type === 'status') {
    const status = String(value).toLowerCase()
    if (status === 'pending' || status === 'pending_approval') return '#f59e0b' // Amber
    if (status === 'approved') return '#06b6d4' // Cyan — decided, not yet executing or resolved
    if (status === 'executing' || status === 'in_progress') return '#3b82f6' // Blue
    if (status === 'completed' || status === 'resolved') return '#10b981' // Emerald
    if (status === 'failed' || status === 'rejected') return '#dc2626' // Red
  }

  // Default fallback
  return '#a0aec0' // Secondary text color
}

const LIFECYCLE_LABELS: Record<string, string> = {
  open:             'Open',
  in_progress:      'In Progress',
  waiting_approval: 'Waiting Approval',
  approved:         'Approved',
  rejected:         'Rejected',
  executing:        'Executing',
  awaiting_manual:  'Awaiting Manual',
  storm_hold:       'Storm Hold',
  resolved:         'Resolved',
  deployed:         'Deployed',
  rolled_back:      'Rolled Back',
  failed:           'Failed',
  monitoring:       'Monitoring',
  closed:           'Closed',
}

const formatLabel = (type: MetricType, value: string | number): string => {
  if (type === 'priority') return `Priority ${value}`
  if (type === 'risk') return `Risk ${Math.round(Number(value))}/100`
  if (type === 'confidence') return `${value}% Confidence`
  if (type === 'criticality') {
    const sev = String(value).charAt(0).toUpperCase() + String(value).slice(1)
    return sev
  }
  if (type === 'lifecycle') {
    return LIFECYCLE_LABELS[String(value).toLowerCase()] ?? String(value).charAt(0).toUpperCase() + String(value).slice(1)
  }
  const label = String(value).charAt(0).toUpperCase() + String(value).slice(1)
  return label
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  type,
  value,
  size = 'md',
  className = '',
}) => {
  const color = getColor(type, value)
  const label = formatLabel(type, value)

  const styles: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',

    // Colors
    color: color,
    borderColor: color,
    backgroundColor: 'transparent',

    // Border & Radius
    border: '1px solid',
    borderRadius: '6px',

    // Sizing
    ...(size === 'sm' && {
      padding: '0.25rem 0.625rem',
      fontSize: '0.75rem',
      fontWeight: 600,
      letterSpacing: '0.5px',
    }),
    ...(size === 'md' && {
      padding: '0.375rem 0.75rem',
      fontSize: '0.75rem',
      fontWeight: 600,
      letterSpacing: '0.5px',
    }),

    // Typography
    textTransform: 'none',
    whiteSpace: 'nowrap',
    userSelect: 'none',

    // Transition
    transition: 'all 150ms ease',

    // Hover effect
    cursor: 'default',
  }

  return (
    <span
      className={`status-badge status-badge-${type} status-badge-${size} ${className}`}
      style={styles}
      title={label}
    >
      {label}
    </span>
  )
}

export default StatusBadge
