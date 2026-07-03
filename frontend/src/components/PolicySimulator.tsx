import { useState, useMemo, useEffect } from 'react'
import { Policy } from '../types'
import { listEventTypeTaxonomy, EventTypeTaxonomyEntry } from '../services/api'

const SEV_ORDER = ['low', 'medium', 'high', 'critical']
const ENVS      = ['dev', 'staging', 'prod']

interface SimInputs {
  eventType:      string
  eventTypeLabel: string
  severity:       string
  environment:    string
  service:        string
  riskScore:      string
}

const BLANK: SimInputs = {
  eventType: '', eventTypeLabel: '', severity: '', environment: '', service: '', riskScore: '',
}

function severityMeets(val: string, min: string): boolean {
  const vi = SEV_ORDER.indexOf(val)
  const mi = SEV_ORDER.indexOf(min)
  return vi >= 0 && mi >= 0 && vi >= mi
}

function matchesPolicy(p: Policy, inp: SimInputs): boolean {
  if (!p.enabled) return false
  const r = p.rules || {}
  if (r.min_severity   && inp.severity    && !severityMeets(inp.severity, r.min_severity)) return false
  if (r.environment    && inp.environment && r.environment !== inp.environment)              return false
  if (r.service        && inp.service     && !inp.service.toLowerCase().includes(r.service.toLowerCase())) return false
  if (r.min_risk_score != null && inp.riskScore) {
    if (parseInt(inp.riskScore, 10) < r.min_risk_score) return false
  }
  if (r.anomaly_type && inp.eventType) {
    const types: string[] = Array.isArray(r.anomaly_type) ? r.anomaly_type : [r.anomaly_type]
    if (!types.includes(inp.eventType)) return false
  }
  return true
}

type Outcome = 'auto' | 'gated' | 'approval' | 'no_match'

function getOutcome(p: Policy): Outcome {
  if (!p.requires_manual_approval) return 'auto'
  if (p.confidence_gate_threshold != null && p.confidence_gate_min_runs != null) return 'gated'
  return 'approval'
}

const OUTCOMES: Record<Outcome, { color: string; label: string; desc: string }> = {
  auto:     { color: '#10b981', label: 'Auto-execute',      desc: 'Remediation runs automatically — no human approval needed' },
  gated:    { color: '#8b5cf6', label: 'Confidence Gated',  desc: 'Approval required until runbook reaches its confidence threshold, then auto-approved' },
  approval: { color: '#f59e0b', label: 'Approval Required', desc: 'A human must review and approve before remediation can run' },
  no_match: { color: '#6b7a93', label: 'No Policy Matched', desc: 'No enabled policy matched — incident will be held for manual operator review' },
}

function condSummary(rules: Record<string, any>): string {
  const parts: string[] = []
  if (rules.min_severity)   parts.push(`Sev ≥ ${rules.min_severity}`)
  if (rules.environment)    parts.push(`Env: ${rules.environment}`)
  if (rules.min_risk_score) parts.push(`Risk ≥ ${rules.min_risk_score}`)
  if (rules.service)        parts.push(`Service: ${rules.service}`)
  if (rules.anomaly_type) {
    const t: string[] = Array.isArray(rules.anomaly_type) ? rules.anomaly_type : [rules.anomaly_type]
    parts.push(t.length === 1 ? t[0] : `${t.length} event types`)
  }
  return parts.join(' · ') || 'Matches all incidents'
}

export default function PolicySimulator({ policies }: { policies: Policy[] }) {
  const [inp, setInp]         = useState<SimInputs>(BLANK)
  const [etOpts, setEtOpts]   = useState<EventTypeTaxonomyEntry[]>([])
  const [etSearch, setEtSearch] = useState('')
  const [showDrop, setShowDrop] = useState(false)

  useEffect(() => {
    listEventTypeTaxonomy({ enabled_only: true }).then(r => setEtOpts(r.data)).catch(() => {})
  }, [])

  const set = (k: keyof SimInputs, v: string) => setInp(prev => ({ ...prev, [k]: v }))
  const clear = () => { setInp(BLANK); setEtSearch('') }

  const hasInput = !!(inp.eventType || inp.severity || inp.environment || inp.service || inp.riskScore)

  const etFiltered = etOpts.filter(o => {
    if (inp.eventType) return false
    const q = etSearch.toLowerCase()
    return !q || o.code.toLowerCase().includes(q) || o.label.toLowerCase().includes(q) || o.category.toLowerCase().includes(q)
  }).slice(0, 30)

  const matches = useMemo(() => {
    if (!hasInput) return []
    return policies
      .filter(p => matchesPolicy(p, inp))
      .sort((a, b) => (a.approval_priority ?? 50) - (b.approval_priority ?? 50))
  }, [policies, inp, hasInput])

  const effective    = matches[0]
  const finalOutcome: Outcome = effective ? getOutcome(effective) : 'no_match'
  const oc           = OUTCOMES[finalOutcome]

  return (
    <div className="pp-simulator">

      {/* ── Input panel ── */}
      <div className="pp-sim-inputs">
        <p className="pp-sim-intro">
          Enter the properties of an incoming event to see which enabled policies would match and what
          automated response will be triggered. Leave any field blank to match any value for that dimension.
        </p>

        <div className="pp-sim-grid">

          {/* Event type — searchable picker */}
          <div className="pp-field pp-sim-span2">
            <label className="pp-label">Event Type</label>
            {inp.eventType ? (
              <div className="pp-sim-et-selected">
                <span className="pp-event-chip pp-event-chip-lg">{inp.eventTypeLabel || inp.eventType}</span>
                <button
                  type="button"
                  className="pp-sim-et-clear"
                  onClick={() => setInp(prev => ({ ...prev, eventType: '', eventTypeLabel: '' }))}
                >
                  ×
                </button>
              </div>
            ) : (
              <div style={{ position: 'relative' }}>
                <input
                  className="pp-input"
                  value={etSearch}
                  onChange={e => { setEtSearch(e.target.value); setShowDrop(true) }}
                  onFocus={() => setShowDrop(true)}
                  onBlur={() => setTimeout(() => setShowDrop(false), 150)}
                  placeholder="Search event types (e.g. cpu, disk, service_down)…"
                  autoComplete="off"
                />
                {showDrop && etFiltered.length > 0 && (
                  <div className="pp-et-dropdown">
                    {etFiltered.map(o => (
                      <button
                        type="button"
                        key={o.code}
                        className="pp-et-option"
                        onMouseDown={() => {
                          setInp(prev => ({ ...prev, eventType: o.code, eventTypeLabel: o.label }))
                          setEtSearch('')
                          setShowDrop(false)
                        }}
                      >
                        <span className="pp-et-label">{o.label}</span>
                        <span className="pp-et-code">{o.category} · {o.code}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Severity */}
          <div className="pp-field">
            <label className="pp-label">Severity</label>
            <select className="pp-input pp-select" value={inp.severity} onChange={e => set('severity', e.target.value)}>
              <option value="">Any</option>
              {SEV_ORDER.map(s => (
                <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
              ))}
            </select>
          </div>

          {/* Environment */}
          <div className="pp-field">
            <label className="pp-label">Environment</label>
            <select className="pp-input pp-select" value={inp.environment} onChange={e => set('environment', e.target.value)}>
              <option value="">Any</option>
              {ENVS.map(e => <option key={e} value={e}>{e}</option>)}
            </select>
          </div>

          {/* Risk score */}
          <div className="pp-field">
            <label className="pp-label">Risk Score (0–100)</label>
            <input
              className="pp-input"
              type="number"
              min="0"
              max="100"
              value={inp.riskScore}
              onChange={e => set('riskScore', e.target.value)}
              placeholder="Leave blank for any"
            />
          </div>

          {/* Service / CI */}
          <div className="pp-field">
            <label className="pp-label">Service / CI</label>
            <input
              className="pp-input"
              value={inp.service}
              onChange={e => set('service', e.target.value)}
              placeholder="e.g. api-server (partial match)"
            />
          </div>

        </div>

        {hasInput && (
          <button className="pp-sim-clear" onClick={clear}>Clear inputs</button>
        )}
      </div>

      {/* ── Results ── */}
      {hasInput ? (
        <div className="pp-sim-results">

          {/* Outcome banner */}
          <div className="pp-sim-outcome" style={{ borderColor: oc.color + '40' }}>
            <span
              className="pp-sim-outcome-chip"
              style={{ color: oc.color, borderColor: oc.color + '70', backgroundColor: oc.color + '18' }}
            >
              {oc.label}
            </span>
            <span className="pp-sim-outcome-desc">{oc.desc}</span>
            {effective && (
              <span className="pp-sim-outcome-policy">
                via <strong style={{ color: '#e8eef5' }}>{effective.name}</strong>
                <span className="pp-priority-chip" style={{ marginLeft: '0.5rem' }}>P{effective.approval_priority ?? 50}</span>
              </span>
            )}
          </div>

          {/* Match list */}
          {matches.length > 0 ? (
            <>
              <p className="pp-sim-match-count">
                {matches.length} {matches.length === 1 ? 'policy' : 'policies'} matched · ordered by priority
              </p>
              <div className="pp-sim-match-list">
                {matches.map((p, idx) => {
                  const out = getOutcome(p)
                  const o2  = OUTCOMES[out]
                  return (
                    <div key={p.policy_id} className={`pp-sim-match-row${idx === 0 ? ' pp-sim-match-effective' : ''}`}>
                      <div className="pp-sim-match-left">
                        <span className="pp-priority-chip">P{p.approval_priority ?? 50}</span>
                        <div className="pp-sim-match-info">
                          <span className="pp-sim-match-name">{p.name}</span>
                          <span className="pp-sim-match-cond">{condSummary(p.rules || {})}</span>
                        </div>
                      </div>
                      <div className="pp-sim-match-right">
                        {idx === 0 && <span className="pp-sim-applied-tag">Applied</span>}
                        <span
                          className="pp-sim-match-outcome"
                          style={{ color: o2.color, borderColor: o2.color + '60', backgroundColor: o2.color + '12' }}
                        >
                          {o2.label}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          ) : (
            <div className="pp-sim-empty">
              <p>No enabled policies matched this combination.</p>
              <p className="pp-sim-empty-hint">The incident will be held for manual operator review.</p>
            </div>
          )}
        </div>
      ) : (
        <div className="pp-sim-placeholder">
          <div className="pp-sim-ph-icon">⚡</div>
          <p className="pp-sim-ph-title">Ready to simulate</p>
          <p className="pp-sim-ph-sub">
            Fill in one or more fields above to see which policies would match and what
            automated response would be triggered.
          </p>
        </div>
      )}

    </div>
  )
}
