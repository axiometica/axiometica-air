import React from 'react'
import { Workflow } from '../types'
import StatusBadge from './StatusBadge'
import { IconArrowRight, IconEye, IconShield, IconActivity } from './icons'
import {
  formatDate,
  formatDuration,
  truncateText,
  calculateDurationMinutes,
  getServiceName,
} from '../utils/workflowTransformer'

/**
 * IncidentCard Component
 *
 * Reusable card displaying incident at a glance (Level 1 + Level 2 data).
 * Shows: INC#, Type, Resource, Badges, Duration, Summary preview
 * Hover to reveal action buttons.
 *
 * All data fields are properly wired from Workflow type with transformer utilities.
 */

interface IncidentCardProps {
  incident: Workflow
  onClick?: (workflowId: string) => void
  onApprove?: (incident: Workflow) => void
  onDetails?: (workflowId: string) => void
  darkMode?: boolean
}

const IncidentCardComponent: React.FC<IncidentCardProps> = ({
  incident,
  onClick,
  onApprove,
  onDetails,
}) => {
  const [isHovering, setIsHovering] = React.useState(false)

  // Extract data from incident (using transformer utilities as fallback)
  const workflowId = incident.id || incident.workflow_id
  const incidentNumber = incident.incident_number_str || (incident.incident_number ? `INC${String(incident.incident_number).padStart(4, '0')}` : 'INC—')
  const serviceName = getServiceName(incident)
  const duration = calculateDurationMinutes(incident.created_at, incident.updated_at, incident.lifecycle_state)
  const durationFormatted = formatDuration(duration)
  const timestamp = formatDate(incident.created_at)

  // Left-border accent colour signals lifecycle state at a glance
  const accentColor = (() => {
    const s = incident.lifecycle_state?.toLowerCase()
    if (s === 'storm_hold')       return '#8b5cf6' // Violet — storm cluster
    if (s === 'resolved' || s === 'closed' || s === 'deployed') return '#10b981' // Emerald — done
    if (s === 'waiting_approval') return '#f59e0b' // Amber — decision needed
    if (s === 'approved')         return '#06b6d4' // Cyan — decided, executing soon
    if (s === 'awaiting_manual' || s === 'failed' || s === 'rejected' || s === 'rolled_back') return '#f97316' // Orange — human handoff
    return '#94a3b8' // Slate — Phase 1, system working
  })()

  // Source connector — lives in context.alert_payload.source_connector
  const alertPayload = (incident.context as Record<string, any>)?.alert_payload ?? {}
  const sourceConnector: string | undefined = alertPayload.source_connector || undefined

  // Prefer the watcher's own deterministic alert description over the evolving LLM
  // `summary` field — it's available immediately (no async enrichment delay), never
  // echoes prompt section labels, and is more specific (real numbers/process names)
  // than the pre-enrichment placeholder summary. Falls back to summary if the alert
  // payload has no description (e.g. older or manually-submitted incidents).
  const cardDescription: string = (() => {
    const d = alertPayload.description
    if (d && !d.startsWith('QUALIFIED:')) return d
    return ''
  })()

  const cardStyles: React.CSSProperties = {
    backgroundColor: '#1a1f2e',
    border: '1px solid #3d4557',
    borderLeft: `4px solid ${accentColor}`,
    borderRadius: '10px',
    padding: '1.5rem',
    marginBottom: '1rem',
    transition: 'all 200ms ease',
    cursor: onClick ? 'pointer' : 'default',
    transform: isHovering ? 'translateY(-4px)' : 'translateY(0)',
    boxShadow: isHovering
      ? '0 20px 40px rgba(0, 0, 0, 0.4), inset 0 0 0 1px #3d4557'
      : '0 4px 12px rgba(0, 0, 0, 0.15)',
  }

  return (
    <div
      style={cardStyles}
      onMouseEnter={() => setIsHovering(true)}
      onMouseLeave={() => setIsHovering(false)}
      className="incident-card"
    >
      {/* Header: row 1 — INC# + status badge on same line */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: '0.5rem',
        }}
      >
        <span
          style={{
            fontFamily: "'Monaco', 'Courier New', monospace",
            fontSize: '0.8125rem',
            fontWeight: 600,
            color: '#e8eef5',
            backgroundColor: '#252c3c',
            padding: '0.25rem 0.625rem',
            borderRadius: '6px',
            flexShrink: 0,
          }}
        >
          {incidentNumber}
        </span>
        <StatusBadge type="lifecycle" value={incident.lifecycle_state} size="sm" />
      </div>

      {/* Header: row 2 — title (full width, truncates with ellipsis) */}
      <p
        style={{
          fontSize: '0.9rem',
          fontWeight: 600,
          color: '#e8eef5',
          margin: '0 0 0.25rem',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
        title={incident.title || 'Unknown Incident'}
      >
        {incident.title || 'Unknown Incident'}
      </p>

      {/* Header: row 3 — service name */}
      {serviceName && serviceName !== 'Unknown Service' && (
        <p
          style={{
            fontSize: '0.75rem',
            fontWeight: 400,
            color: '#6b7280',
            margin: '0 0 1rem',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          on {serviceName}
        </p>
      )}
      {(!serviceName || serviceName === 'Unknown Service') && (
        <div style={{ marginBottom: '1rem' }} />
      )}

      {/* Metrics row: Severity | Risk | Duration */}
      <div
        style={{
          display: 'flex',
          gap: '1rem',
          marginBottom: '1rem',
          flexWrap: 'wrap',
        }}
      >
        {/* Severity Badge */}
        {incident.severity && (
          <StatusBadge type="criticality" value={incident.severity} size="sm" />
        )}

        {/* Risk Score Badge */}
        {incident.risk_score !== undefined && incident.risk_score !== null && (
          <StatusBadge type="risk" value={incident.risk_score} size="sm" />
        )}

        {/* Recurrence indicator — same condition validated as repeating while
            this incident sits open (e.g. awaiting_manual) without anyone
            having looked at it yet */}
        {!!incident.duplicate_count && incident.duplicate_count > 0 && (
          <span
            title={`This condition has recurred ${incident.duplicate_count} additional time(s) while this incident is open`}
            style={{
              fontSize: '0.7rem',
              fontWeight: 700,
              color: '#f97316',
              border: '1px solid #f9731660',
              backgroundColor: 'transparent',
              borderRadius: '6px',
              padding: '0.2rem 0.5rem',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.25rem',
            }}
          >
            ↻ Recurred ×{incident.duplicate_count}
          </span>
        )}

        {/* Duration */}
        <span
          style={{
            fontSize: '0.75rem',
            fontWeight: 600,
            color: '#a0aec0',
            display: 'flex',
            alignItems: 'center',
          }}
        >
          {durationFormatted}
        </span>
      </div>

      {/* Summary preview — only shown when a real description exists */}
      {cardDescription && (
        <div
          style={{
            fontSize: '0.875rem',
            fontWeight: 400,
            color: '#c1c7d0',
            lineHeight: 1.5,
            marginBottom: '1rem',
          }}
        >
          {truncateText(cardDescription, 80)}
        </div>
      )}

      {/* Footer: Timestamp + Action buttons (show on hover) */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          borderTop: '1px solid #3d4557',
          paddingTop: '1rem',
        }}
      >
        {/* Timestamp + source connector (left side of footer) */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.625rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.75rem', fontWeight: 400, color: '#6b7280' }}>
            {timestamp}
          </span>
          {sourceConnector && (
            <span style={{
              fontSize: '0.7rem',
              fontWeight: 700,
              color: '#38bdf8',
              border: '1px solid #0ea5e960',
              backgroundColor: 'transparent',
              borderRadius: '6px',
              padding: '0.2rem 0.5rem',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '0.25rem',
              letterSpacing: '0.3px',
              textTransform: 'capitalize',
            }}>
              <IconActivity size={11} />
              via {sourceConnector}
            </span>
          )}
        </div>

        {/* Action buttons (show on hover) */}
        {isHovering && (
          <div
            style={{
              display: 'flex',
              gap: '0.75rem',
            }}
          >
            {onApprove && incident.lifecycle_state === 'waiting_approval' && (
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onApprove(incident)
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.25rem',
                  padding: '0.5rem 0.75rem',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: '#a855f7',
                  border: '1px solid #a855f7',
                  backgroundColor: 'transparent',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  transition: 'all 150ms ease',
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget
                  el.style.backgroundColor = '#2d1a4a'
                  el.style.transform = 'scale(1.02)'
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget
                  el.style.backgroundColor = 'transparent'
                  el.style.transform = 'scale(1)'
                }}
              >
                <IconShield size={16} />
                Review
              </button>
            )}

            {onDetails && (
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onDetails(workflowId)
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.25rem',
                  padding: '0.5rem 0.75rem',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: '#3b82f6',
                  border: '1px solid #3b82f6',
                  backgroundColor: 'transparent',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  transition: 'all 150ms ease',
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget
                  el.style.backgroundColor = '#1e3a8a'
                  el.style.transform = 'scale(1.02)'
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget
                  el.style.backgroundColor = 'transparent'
                  el.style.transform = 'scale(1)'
                }}
              >
                <IconEye size={16} />
                Details
              </button>
            )}

            {onClick && (
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  onClick(workflowId)
                }}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  padding: '0.5rem 0.75rem',
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: '#a0aec0',
                  border: '1px solid #3d4557',
                  backgroundColor: 'transparent',
                  borderRadius: '6px',
                  cursor: 'pointer',
                  transition: 'all 150ms ease',
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget
                  el.style.borderColor = '#a0aec0'
                  el.style.color = '#e8eef5'
                  el.style.transform = 'scale(1.02)'
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget
                  el.style.borderColor = '#3d4557'
                  el.style.color = '#a0aec0'
                  el.style.transform = 'scale(1)'
                }}
              >
                <IconArrowRight size={16} />
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

/**
 * Memoized IncidentCard — prevents re-renders when parent fetches unless incident data changes.
 * Significantly improves performance on pages with many incidents being re-fetched.
 */
export const IncidentCard = React.memo(IncidentCardComponent, (prevProps, nextProps) => {
  // Return true if props are equal (skip render), false if different (re-render)
  return (
    prevProps.incident.id === nextProps.incident.id &&
    prevProps.incident.lifecycle_state === nextProps.incident.lifecycle_state &&
    prevProps.incident.severity === nextProps.incident.severity &&
    prevProps.incident.risk_score === nextProps.incident.risk_score &&
    prevProps.incident.remediation_outcome === nextProps.incident.remediation_outcome &&
    prevProps.incident.duplicate_count === nextProps.incident.duplicate_count &&
    prevProps.incident.updated_at === nextProps.incident.updated_at &&
    prevProps.onClick === nextProps.onClick &&
    prevProps.onApprove === nextProps.onApprove &&
    prevProps.onDetails === nextProps.onDetails
  )
})

IncidentCard.displayName = 'IncidentCard'

export default IncidentCard
