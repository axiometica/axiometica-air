import { useState, useEffect, type CSSProperties } from 'react'
import { Workflow } from '../types'
import { getMonitoringEventForWorkflow, MonitoringEvent } from '../services/api'
import { parseUTC } from '../utils/dateFormatter'

interface IncidentEventsPageProps {
  workflow: Workflow
  darkMode?: boolean
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
}

const LABEL_STYLE: CSSProperties = {
  fontSize: '10px',
  fontWeight: 600,
  color: '#a0aec0',
  letterSpacing: '0.07em',
  textTransform: 'uppercase',
}

const ROW_STYLE: CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  gap: '12px',
  padding: '7px 0',
  borderBottom: '1px solid #252c3c',
}

function fmtDate(iso: string) {
  try {
    return parseUTC(iso).toLocaleString(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    })
  } catch {
    return iso
  }
}

function criticalityColor(c: string) {
  if (c === 'critical') return '#dc2626'
  if (c === 'warning') return '#f59e0b'
  return '#64748b'
}

// scores are stored as 0-100 floats
function scoreColor(score: number) {
  if (score >= 80) return '#ef4444'
  if (score >= 50) return '#f59e0b'
  return '#10b981'
}

function fmtScore(v: number | null | undefined): string {
  if (v == null) return '—'
  // scores stored as 0-100; cap display at 100
  return `${Math.min(Math.round(v), 100)}%`
}

export default function IncidentEventsPage({ workflow }: IncidentEventsPageProps) {
  const [event, setEvent] = useState<MonitoringEvent | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)

    getMonitoringEventForWorkflow(workflow.workflow_id)
      .then(ev => {
        if (!cancelled) {
          setEvent(ev)
          setLoading(false)
        }
      })
      .catch(err => {
        if (!cancelled) {
          setError(err?.response?.data?.detail || 'Failed to load monitoring event')
          setLoading(false)
        }
      })

    return () => { cancelled = true }
  }, [workflow.workflow_id])

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 0', color: '#64748b', fontSize: '13px' }}>
        Loading event data…
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 0', color: '#ef4444', fontSize: '13px' }}>
        {error}
      </div>
    )
  }

  // No linked monitoring event — incident was created manually
  if (!event) {
    return (
      <div style={{ textAlign: 'center', padding: '40px 0', color: '#64748b', fontSize: '13px' }}>
        No linked monitoring event — this incident was raised manually.
      </div>
    )
  }

  // Validated recurrences of this exact condition (same source + event_type)
  // while this incident has stayed open — see the dedup branches in the
  // backend's monitoring_events.py.
  const duplicateCount: number = workflow.duplicate_count ?? 0

  const qualScore = event.qualification_score ?? 0
  const confidence = event.confidence

  // Build a clean raw-data object to display (exclude fields already shown above)
  const rawDisplayPayload = Object.keys(event.raw_payload || {}).length > 0
    ? event.raw_payload
    : null

  return (
    <div className="space-y-4">

      {/* Classification header */}
      <div style={CARD}>
        <div style={CARD_HEADER}>
          <span style={LABEL_STYLE}>Event Classification</span>
        </div>
        <div style={{ padding: '14px 16px' }}>

          <div style={ROW_STYLE}>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>Alert Type</span>
            <span style={{ fontSize: '12px', color: '#e8eef5', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
              {event.event_type.replace(/_/g, ' ')}
            </span>
          </div>

          <div style={ROW_STYLE}>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>Resource</span>
            <span style={{ fontSize: '12px', color: '#e8eef5', fontWeight: 500, fontFamily: 'monospace' }}>
              {event.resource_name}
            </span>
          </div>

          <div style={ROW_STYLE}>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>Source</span>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>
              {event.source.replace(/_/g, ' ')}
            </span>
          </div>

          <div style={ROW_STYLE}>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>Criticality</span>
            <span style={{ fontSize: '12px', fontWeight: 600, color: criticalityColor(event.raw_criticality), textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              {event.raw_criticality}
            </span>
          </div>

          <div style={ROW_STYLE}>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>Detected At</span>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>
              {fmtDate(event.detected_at)}
            </span>
          </div>

          <div style={ROW_STYLE}>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>Duplicate Count</span>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>
              {duplicateCount > 0
                ? `${duplicateCount} other alert${duplicateCount !== 1 ? 's' : ''} with same signature`
                : 'None'}
            </span>
          </div>

          <div style={{ ...ROW_STYLE, borderBottom: 'none' }}>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>Status</span>
            <span style={{ fontSize: '11px', fontWeight: 600, color: '#e8eef5', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              {event.status}
            </span>
          </div>
        </div>
      </div>

      {/* Signal values */}
      {((event.signal_value != null && event.signal_value > 0) || (event.signal_threshold != null && event.signal_threshold > 0)) && (
        <div style={CARD}>
          <div style={CARD_HEADER}>
            <span style={LABEL_STYLE}>Signal Metrics</span>
          </div>
          <div style={{ padding: '14px 16px' }}>
            {event.signal_value != null && event.signal_value > 0 && (
              <div style={ROW_STYLE}>
                <span style={{ fontSize: '12px', color: '#a0aec0' }}>Observed Value</span>
                <span style={{ fontSize: '12px', color: '#e8eef5', fontWeight: 600 }}>
                  {Number(event.signal_value).toLocaleString('en', { maximumFractionDigits: 1 })}
                </span>
              </div>
            )}
            {event.signal_threshold != null && event.signal_threshold > 0 && (
              <div style={ROW_STYLE}>
                <span style={{ fontSize: '12px', color: '#a0aec0' }}>Alert Limit</span>
                <span style={{ fontSize: '12px', color: '#a0aec0' }}>
                  {Number(event.signal_threshold).toLocaleString('en', { maximumFractionDigits: 1 })}
                </span>
              </div>
            )}
            {event.anomaly_process && (
              <div style={{ ...ROW_STYLE, borderBottom: 'none' }}>
                <span style={{ fontSize: '12px', color: '#a0aec0' }}>Process</span>
                <span style={{ fontSize: '12px', color: '#e8eef5', fontFamily: 'monospace' }}>
                  {event.anomaly_process}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Qualification scoring */}
      <div style={CARD}>
        <div style={CARD_HEADER}>
          <span style={LABEL_STYLE}>Qualification Scoring</span>
        </div>
        <div style={{ padding: '14px 16px' }}>

          <div style={ROW_STYLE}>
            <span style={{ fontSize: '12px', color: '#a0aec0' }}>Qualification Score</span>
            <span style={{ fontSize: '13px', fontWeight: 700, color: scoreColor(qualScore) }}>
              {fmtScore(qualScore)}
            </span>
          </div>

          {confidence != null && (
            <div style={ROW_STYLE}>
              <span style={{ fontSize: '12px', color: '#a0aec0' }}>Confidence</span>
              <span style={{ fontSize: '12px', color: '#a0aec0' }}>
                {fmtScore(confidence)}
              </span>
            </div>
          )}

          <div style={{ ...ROW_STYLE, borderBottom: 'none', alignItems: 'flex-start' }}>
            <span style={{ fontSize: '12px', color: '#a0aec0', flexShrink: 0 }}>Qualified as Incident</span>
            <span style={{ fontSize: '12px', fontWeight: 600, color: event.qualified_as_incident ? '#10b981' : '#ef4444' }}>
              {event.qualified_as_incident ? 'Yes' : 'No'}
            </span>
          </div>

          {event.qualification_reason && (
            <div style={{ marginTop: '12px', paddingTop: '12px', borderTop: '1px solid #3d4557' }}>
              <p style={{ ...LABEL_STYLE, color: '#3b82f6', marginBottom: '6px' }}>Escalation Reason</p>
              <p style={{
                fontSize: '12px',
                color: '#a0aec0',
                lineHeight: 1.6,
                backgroundColor: 'rgba(59,130,246,0.07)',
                border: '1px solid rgba(59,130,246,0.2)',
                borderRadius: '6px',
                padding: '8px 10px',
                margin: 0,
              }}>
                {event.qualification_reason}
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Payload title / description if present */}
      {(event.payload_title || event.payload_description) && (
        <div style={CARD}>
          <div style={CARD_HEADER}>
            <span style={LABEL_STYLE}>Alert Summary</span>
          </div>
          <div style={{ padding: '14px 16px' }}>
            {event.payload_title && (
              <p style={{ fontSize: '13px', fontWeight: 600, color: '#e8eef5', marginBottom: '8px' }}>
                {event.payload_title}
              </p>
            )}
            {event.payload_description && (
              <p style={{ fontSize: '12px', color: '#a0aec0', lineHeight: 1.6, margin: 0 }}>
                {event.payload_description}
              </p>
            )}
          </div>
        </div>
      )}

      {/* Raw event payload */}
      {rawDisplayPayload && (
        <div style={CARD}>
          <div style={CARD_HEADER}>
            <span style={LABEL_STYLE}>Raw Event Data</span>
          </div>
          <div style={{ padding: '14px 16px' }}>
            <pre style={{
              fontSize: '11px',
              color: '#a0aec0',
              backgroundColor: '#0f1419',
              border: '1px solid #3d4557',
              borderRadius: '6px',
              padding: '12px',
              overflow: 'auto',
              fontFamily: 'monospace',
              lineHeight: 1.6,
              maxHeight: '300px',
              margin: 0,
            }}>
              {JSON.stringify(rawDisplayPayload, null, 2)}
            </pre>
          </div>
        </div>
      )}

    </div>
  )
}
