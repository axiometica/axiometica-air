import type { CSSProperties } from 'react'
import { Workflow } from '../types'

interface RiskSummaryPageProps {
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

function riskAccent(score: number): string {
  if (score >= 80) return '#dc2626'
  if (score >= 60) return '#f97316'
  if (score >= 40) return '#f59e0b'
  return '#10b981'
}

function riskLevel(score: number): string {
  if (score >= 80) return 'CRITICAL'
  if (score >= 60) return 'HIGH'
  if (score >= 40) return 'MEDIUM'
  return 'LOW'
}

/** Colour for a factor bar based on what fraction of its max it consumed. */
function factorAccent(value: number, maxPts: number): string {
  if (maxPts <= 0) return '#4b5563'
  const ratio = Math.min(1, Math.abs(value) / maxPts)
  if (ratio >= 0.8) return '#dc2626'
  if (ratio >= 0.6) return '#f97316'
  if (ratio >= 0.35) return '#f59e0b'
  return '#10b981'
}

/** Badge appearance per data_source value. */
const DATA_SOURCE_BADGE: Record<string, { label: string; color: string; bg: string }> = {
  cmdb:        { label: 'CMDB',      color: '#10b981', bg: 'rgba(16,185,129,0.12)' },
  computed:    { label: 'Computed',  color: '#3b82f6', bg: 'rgba(59,130,246,0.12)' },
  default:     { label: 'Default',   color: '#6b7280', bg: 'rgba(107,114,128,0.15)' },
  pessimistic: { label: 'Worst-case',color: '#f97316', bg: 'rgba(249,115,22,0.12)' },
  excluded:    { label: 'Excluded',  color: '#6b7280', bg: 'rgba(107,114,128,0.1)' },
  disabled:    { label: 'Disabled',  color: '#4b5563', bg: 'rgba(75,85,99,0.1)' },
}

function DataSourceBadge({ src }: { src?: string }) {
  if (!src) return null
  const spec = DATA_SOURCE_BADGE[src] ?? { label: src, color: '#6b7280', bg: 'rgba(107,114,128,0.1)' }
  return (
    <span style={{
      fontSize: '9px', fontWeight: 700, letterSpacing: '0.06em',
      color: spec.color, backgroundColor: spec.bg,
      border: `1px solid ${spec.color}30`,
      borderRadius: '4px', padding: '1px 5px',
      textTransform: 'uppercase',
    }}>
      {spec.label}
    </span>
  )
}

export default function RiskSummaryPage({ workflow }: RiskSummaryPageProps) {
  const ctx           = workflow.context || {}
  const riskCtx       = ctx.risk || {}
  const riskBreakdown = ctx.risk_breakdown || riskCtx.risk_breakdown || {}
  const factors       = riskBreakdown.factors || riskCtx || {}
  const riskScore     = workflow.risk_score || riskCtx.risk_score || 0
  const norm          = riskBreakdown.normalisation || {}

  const accent = riskAccent(riskScore)
  const level  = riskLevel(riskScore)

  // Confidence in CMDB data coverage
  const confidenceScore: number = riskBreakdown.confidence_score ?? (
    riskCtx.confidence != null ? riskCtx.confidence * 100 : null
  ) ?? null

  return (
    <div className="space-y-4">

      {/* ── Overall Risk Score ──────────────────────────────────────── */}
      <div style={CARD}>
        <div style={{ ...CARD_HEADER, borderLeft: `3px solid ${accent}` }}>
          <span style={LABEL}>Overall Risk Score</span>
        </div>
        <div style={{ padding: '16px' }}>
          <div className="flex items-center justify-between gap-6">
            <div className="flex-1">
              <div className="flex items-end gap-3 mb-3">
                <span style={{ fontSize: '48px', fontWeight: 800, color: accent, lineHeight: 1 }}>
                  {Math.round(riskScore)}
                </span>
                <span style={{ fontSize: '16px', color: '#7a8ba3', marginBottom: '8px' }}>/100</span>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
                <span style={{ fontSize: '12px', color: '#a0aec0' }}>Risk Level</span>
                <span style={{
                  fontSize: '11px', fontWeight: 700, letterSpacing: '0.05em',
                  color: accent, backgroundColor: `${accent}15`,
                  border: `1px solid ${accent}40`,
                  borderRadius: '5px', padding: '2px 10px',
                }}>
                  {level}
                </span>
              </div>

              {/* Score progress bar */}
              <div style={{ height: '6px', backgroundColor: '#252c3c', border: '1px solid #3d4557', borderRadius: '999px', overflow: 'hidden' }}>
                <div style={{
                  height: '100%', width: `${Math.min(riskScore, 100)}%`,
                  backgroundColor: accent, borderRadius: '999px', transition: 'width 0.5s ease',
                }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '4px' }}>
                <span style={{ fontSize: '10px', color: '#3d4557' }}>0</span>
                <span style={{ fontSize: '10px', color: '#3d4557' }}>100</span>
              </div>
            </div>

            {/* Right panel — factor count + CMDB coverage */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <div style={{ ...INNER, textAlign: 'center', padding: '14px 20px', borderLeft: `3px solid ${accent}` }}>
                <p style={LABEL}>Active factors</p>
                <p style={{ fontSize: '18px', fontWeight: 700, color: '#e8eef5', marginTop: '4px' }}>
                  {Object.values(factors).filter((f: any) => f.data_source !== 'disabled' && f.data_source !== 'excluded').length || Object.keys(factors).length || 10}
                </p>
              </div>
              {confidenceScore !== null && (
                <div style={{ ...INNER, textAlign: 'center', padding: '14px 20px', borderLeft: `3px solid ${riskAccent(100 - confidenceScore)}` }}>
                  <p style={LABEL}>CMDB coverage</p>
                  <p style={{ fontSize: '18px', fontWeight: 700, color: '#e8eef5', marginTop: '4px' }}>
                    {Math.round(confidenceScore)}%
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Normalisation note */}
          {norm.active_weight_sum > 0 && (
            <p style={{ fontSize: '10px', color: '#4b5563', marginTop: '12px', fontStyle: 'italic' }}>
              Score normalised across {norm.active_weight_sum} active weight points
              {norm.scale && norm.scale !== 1 ? ` (×${norm.scale.toFixed(3)})` : ''}.
              Disabled / excluded factors redistributed proportionally.
            </p>
          )}
        </div>
      </div>

      {/* ── Risk Assessment Factors ─────────────────────────────────── */}
      {Object.keys(factors).length > 0 && (
        <div style={CARD}>
          <div style={CARD_HEADER}>
            <span style={LABEL}>Risk Assessment Factors</span>
          </div>
          <div style={{ padding: '14px 16px' }} className="space-y-2">
            {Object.entries(factors).map(([key, factor]: [string, any]) => {
              const value   = factor.value ?? factor.score ?? 0
              const maxPts  = factor.max_pts ?? factor.weight ?? 0
              const src     = factor.data_source as string | undefined
              const isSkipped = src === 'excluded' || src === 'disabled'
              const barPct  = maxPts > 0 ? Math.min(100, (Math.abs(value) / maxPts) * 100) : 0
              const barColor = factorAccent(value, maxPts)
              const label   = factor.label || key.replace(/_/g, ' ')

              return (
                <div key={key} style={{
                  backgroundColor: isSkipped ? 'rgba(30,35,50,0.5)' : 'rgba(59,130,246,0.05)',
                  border: `1px solid ${isSkipped ? '#2a3145' : 'rgba(59,130,246,0.15)'}`,
                  borderLeftColor: isSkipped ? '#2a3145' : barColor,
                  borderLeftWidth: '3px',
                  borderRadius: '0 6px 6px 0',
                  padding: '10px 12px',
                  opacity: isSkipped ? 0.55 : 1,
                }}>
                  {/* Row 1: label + data-source badge + score */}
                  <div className="flex justify-between items-start mb-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span style={{ fontSize: '12px', fontWeight: 600, color: isSkipped ? '#4b5563' : '#e8eef5', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                        {label}
                      </span>
                      <DataSourceBadge src={src} />
                    </div>
                    <span style={{ fontSize: '15px', fontWeight: 700, color: isSkipped ? '#4b5563' : barColor, flexShrink: 0, marginLeft: '8px' }}>
                      {Math.abs(value).toFixed(1)}
                      {maxPts > 0 && <span style={{ fontSize: '11px', color: '#4b5563', fontWeight: 400 }}> / {maxPts}</span>}
                    </span>
                  </div>

                  {/* Row 2: per-factor progress bar */}
                  {!isSkipped && maxPts > 0 && (
                    <div style={{ height: '4px', backgroundColor: '#252c3c', border: '1px solid #2a3145', borderRadius: '999px', overflow: 'hidden', marginBottom: '6px' }}>
                      <div style={{
                        height: '100%', width: `${barPct}%`,
                        backgroundColor: barColor, borderRadius: '999px', transition: 'width 0.4s ease',
                      }} />
                    </div>
                  )}

                  {/* Row 3: detail text */}
                  <div style={{ fontSize: '11px', color: '#7a8ba3' }}>
                    {isSkipped
                      ? (src === 'disabled' ? 'Factor disabled in configuration' : 'Excluded — no CMDB data (exclude policy)')
                      : (() => {
                          const parts: string[] = []
                          if (factor.detail)      parts.push(factor.detail)
                          if (factor.multiplier != null) parts.push(`×${factor.multiplier.toFixed(2)} multiplier`)
                          if (factor.users != null)      parts.push(`${Number(factor.users).toLocaleString()} users`)
                          if (factor.dependents != null) parts.push(`${factor.dependents} dependent${factor.dependents !== 1 ? 's' : ''}`)
                          if (factor.incidents != null)  parts.push(`${factor.incidents} previous incident${factor.incidents !== 1 ? 's' : ''}`)
                          if (factor.sla_percent != null) parts.push(`${factor.sla_percent.toFixed(1)}% SLA`)
                          return parts.join(' · ') || `Weight: ${factor.weight || 0} pts`
                        })()
                    }
                  </div>
                </div>
              )
            })}
          </div>

          {/* Legend */}
          <div style={{ padding: '10px 16px', borderTop: '1px solid #2a3145', display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            {Object.entries(DATA_SOURCE_BADGE).map(([key, spec]) => (
              <span key={key} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10px', color: spec.color }}>
                <span style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: spec.color, display: 'inline-block' }} />
                {spec.label}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* ── Assessment Summary ──────────────────────────────────────── */}
      <div style={CARD}>
        <div style={CARD_HEADER}>
          <span style={LABEL}>Assessment Summary</span>
        </div>
        <div style={{ padding: '14px 16px' }}>
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: 'Initial Severity',  value: riskBreakdown.initial_severity  || ctx.severity || 'Unknown' },
              { label: 'Assessed Severity', value: riskBreakdown.assessed_severity || workflow.severity || 'Unknown' },
              { label: 'Priority',          value: riskBreakdown.priority          || ctx.incident_priority || 'P3' },
              { label: 'CMDB Coverage',     value: confidenceScore !== null ? `${Math.round(confidenceScore)}%` : '—' },
            ].map(({ label, value }) => (
              <div key={label} style={INNER}>
                <p style={LABEL}>{label}</p>
                <p style={{ fontSize: '18px', fontWeight: 700, color: '#e8eef5', marginTop: '4px', textTransform: 'capitalize' }}>
                  {String(value)}
                </p>
              </div>
            ))}
          </div>
        </div>
      </div>

    </div>
  )
}
