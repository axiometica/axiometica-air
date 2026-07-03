import type { CSSProperties } from 'react'
import { Workflow } from '../types'
import { parseUTC } from '../utils/dateFormatter'

interface AuditPageProps {
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

const ROW: CSSProperties = {
  display: 'flex',
  alignItems: 'baseline',
  justifyContent: 'space-between',
  gap: '12px',
  padding: '7px 0',
  borderBottom: '1px solid #252c3c',
}

export default function AuditPage({ workflow }: AuditPageProps) {
  const traces = workflow.reasoning_trace || []

  const parsedTraces = traces.map((trace, idx) => {
    const match = trace.match(/\[([\d\-T:\.]+)\]\s(.+)/)
    return {
      id: idx,
      timestamp: match ? match[1] : '',
      message:   match ? match[2] : trace,
      type: trace.includes('[AGENT]') ? 'agent'
           : trace.includes('ERROR')  ? 'error'
           : trace.includes('Workflow') ? 'workflow'
           : 'trace',
    }
  })

  const typeColor = (t: string) => {
    switch (t) {
      case 'agent':    return '#3b82f6'
      case 'error':    return '#dc2626'
      case 'workflow': return '#10b981'
      default:         return '#a0aec0'
    }
  }
  const typeBg = (t: string) => {
    switch (t) {
      case 'agent':    return 'rgba(59,130,246,0.07)'
      case 'error':    return 'rgba(220,38,38,0.07)'
      case 'workflow': return 'rgba(16,185,129,0.07)'
      default:         return 'rgba(160,174,192,0.04)'
    }
  }
  const typeBorderColor = (t: string) => {
    switch (t) {
      case 'agent':    return 'rgba(59,130,246,0.25)'
      case 'error':    return 'rgba(220,38,38,0.25)'
      case 'workflow': return 'rgba(16,185,129,0.25)'
      default:         return '#3d4557'
    }
  }
  const typeEmoji = (t: string) => {
    switch (t) {
      case 'agent':    return '🤖'
      case 'error':    return '✕'
      case 'workflow': return '✓'
      default:         return 'ℹ'
    }
  }

  const formatTime = (iso: string) => {
    try {
      return parseUTC(iso).toLocaleTimeString('en-US', {
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: true,
      })
    } catch { return iso }
  }

  const duration = (() => {
    const s = Math.floor((new Date(workflow.updated_at).getTime() - new Date(workflow.created_at).getTime()) / 1000)
    if (s >= 3600) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
    if (s >= 60)   return `${Math.floor(s / 60)}m ${s % 60}s`
    return `${s}s`
  })()

  const agentCount   = parsedTraces.filter(t => t.type === 'agent').length
  const errorCount   = parsedTraces.filter(t => t.type === 'error').length

  return (
    <div className="space-y-4">

      {/* Audit Summary */}
      <div style={CARD}>
        <div style={CARD_HEADER}>
          <span style={LABEL}>Audit Summary</span>
        </div>
        <div style={{ padding: '14px 16px' }}>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: 'Total Events', value: parsedTraces.length.toString(), color: '#e8eef5' },
              { label: 'Agent Steps',  value: agentCount.toString(),          color: '#3b82f6' },
              { label: 'Errors',       value: errorCount.toString(),           color: errorCount > 0 ? '#dc2626' : '#10b981' },
              { label: 'Duration',     value: duration,                        color: '#e8eef5' },
            ].map(({ label, value, color }) => (
              <div key={label} style={INNER}>
                <p style={LABEL}>{label}</p>
                <p style={{ fontSize: '22px', fontWeight: 700, color, marginTop: '4px' }}>{value}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Workflow Metadata */}
      <div style={CARD}>
        <div style={CARD_HEADER}>
          <span style={LABEL}>Workflow Metadata</span>
        </div>
        <div style={{ padding: '14px 16px' }}>
          {[
            { label: 'Workflow ID',     value: workflow.workflow_id,        mono: true },
            { label: 'Workflow Type',   value: workflow.workflow_type?.toUpperCase() },
            { label: 'Lifecycle State', value: workflow.lifecycle_state?.toUpperCase() },
            { label: 'Created At',      value: parseUTC(workflow.created_at).toLocaleString(), mono: true },
            { label: 'Last Updated',    value: parseUTC(workflow.updated_at).toLocaleString(), mono: true },
            { label: 'Correlation ID',  value: workflow.context?.correlation_id || 'N/A', mono: true },
          ].map(({ label, value, mono }, i, arr) => (
            <div key={label} style={{ ...ROW, borderBottom: i === arr.length - 1 ? 'none' : '1px solid #252c3c' }}>
              <span style={{ fontSize: '12px', color: '#a0aec0', flexShrink: 0 }}>{label}</span>
              <span style={{
                fontSize: '11px',
                fontWeight: 500,
                color: '#e8eef5',
                textAlign: 'right',
                wordBreak: 'break-all',
                ...(mono ? { fontFamily: 'monospace', fontSize: '10px', color: '#a0aec0' } : {}),
              }}>
                {value}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Full Audit Trail */}
      <div style={CARD}>
        <div style={CARD_HEADER}>
          <span style={LABEL}>Complete Audit Trail</span>
          <span style={{ fontSize: '11px', color: '#7a8ba3' }}>{parsedTraces.length} entries</span>
        </div>
        <div style={{ padding: '14px 16px', maxHeight: '420px', overflowY: 'auto' }} className="space-y-1.5">
          {parsedTraces.length > 0 ? (
            parsedTraces.map((trace) => (
              <div
                key={trace.id}
                style={{
                  borderLeft: `3px solid ${typeColor(trace.type)}`,
                  backgroundColor: typeBg(trace.type),
                  border: `1px solid ${typeBorderColor(trace.type)}`,
                  borderLeftColor: typeColor(trace.type),
                  borderRadius: '0 6px 6px 0',
                  padding: '8px 12px',
                }}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span style={{ fontSize: '12px' }}>{typeEmoji(trace.type)}</span>
                  <span style={{ fontFamily: 'monospace', fontSize: '10px', color: '#7a8ba3' }}>
                    {formatTime(trace.timestamp)}
                  </span>
                  <span style={{
                    fontSize: '9px', fontWeight: 700,
                    color: typeColor(trace.type),
                    textTransform: 'uppercase', letterSpacing: '0.06em',
                  }}>
                    {trace.type}
                  </span>
                </div>
                <p style={{ fontSize: '11px', color: '#a0aec0', lineHeight: 1.5, wordBreak: 'break-word' }}>
                  {trace.message}
                </p>
              </div>
            ))
          ) : (
            <p style={{ fontSize: '12px', color: '#7a8ba3' }}>No audit events recorded</p>
          )}
        </div>
      </div>

    </div>
  )
}
