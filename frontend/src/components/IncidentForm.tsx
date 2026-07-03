import { useState, useEffect, useRef } from 'react'
import { submitIncident } from '../services/api'
import { IconAlertCircle } from './icons'
import './IncidentForm.css'

interface IncidentFormProps {
  onSubmitted: (workflowId: string) => void
  darkMode?: boolean
}

const SEVERITIES = [
  { value: 'critical', label: 'Critical', color: '#fca5a5' },
  { value: 'high',     label: 'High',     color: '#fb923c' },
  { value: 'medium',   label: 'Medium',   color: '#fbbf24' },
  { value: 'low',      label: 'Low',      color: '#6ee7b7' },
  { value: 'info',     label: 'Info',     color: '#a0aec0' },
]

interface EventTypeOption { code: string; label: string; category: string }

function EventTypeCombobox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [options, setOptions] = useState<EventTypeOption[]>([])
  const [query,   setQuery]   = useState(value)
  const [open,    setOpen]    = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const tok  = localStorage.getItem('ap_token') || ''
    const hdrs: Record<string, string> = tok ? { Authorization: `Bearer ${tok}` } : {}
    fetch('/api/event-types?enabled_only=true', { headers: hdrs })
      .then(r => (r.ok ? r.json() : []))
      .then((d: EventTypeOption[]) => setOptions(Array.isArray(d) ? d : []))
      .catch(() => {})
  }, [])

  useEffect(() => { setQuery(value) }, [value])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
        if (query !== value) onChange(query.trim())
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [query, value, onChange])

  const q        = query.toLowerCase().trim()
  const filtered = (q
    ? options.filter(o => o.code.includes(q) || o.label.toLowerCase().includes(q))
    : options
  ).slice(0, 12)

  return (
    <div ref={wrapRef} style={{ position: 'relative' }}>
      <input
        className="if-input"
        style={{ fontFamily: 'monospace', fontSize: 12 }}
        value={query}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={e => {
          if (e.key === 'Enter')  { onChange(query.trim()); setOpen(false) }
          if (e.key === 'Escape') setOpen(false)
        }}
        placeholder="Search event types… (e.g. cpu, disk, network)"
        spellCheck={false}
        autoComplete="off"
      />
      {open && filtered.length > 0 && (
        <div style={{
          position: 'absolute', zIndex: 9999, left: 0, right: 0,
          top: 'calc(100% + 2px)', background: '#0d1018',
          border: '1px solid #2a3548', borderRadius: 6,
          maxHeight: 240, overflowY: 'auto', boxShadow: '0 8px 24px rgba(0,0,0,.6)',
        }}>
          {filtered.map(opt => (
            <div key={opt.code}
              onMouseDown={() => { setQuery(opt.code); onChange(opt.code); setOpen(false) }}
              style={{ padding: '6px 10px', cursor: 'pointer', borderBottom: '1px solid #1a2030' }}
              onMouseEnter={e => (e.currentTarget.style.background = '#1a2030')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={{ fontSize: 11, color: '#cbd5e1', fontFamily: 'monospace' }}>{opt.code}</div>
              <div style={{ fontSize: 10, color: '#475569' }}>{opt.label}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function IncidentForm({ onSubmitted }: IncidentFormProps) {
  const [formData, setFormData] = useState({
    severity:      'medium',
    type:          '',
    resource_name: '',
    service_url:   '',
    title:         '',
    description:   '',
  })
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState<string | null>(null)

  const set = (key: string, val: string) =>
    setFormData(prev => ({ ...prev, [key]: val }))

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const response = await submitIncident({
        severity:      formData.severity,
        type:          formData.type,
        resource_name: formData.resource_name,
        description:   formData.description,
        ...(formData.service_url ? { service_url: formData.service_url } : {}),
      })
      setFormData({ severity: 'medium', type: '', resource_name: '', service_url: '', title: '', description: '' })
      onSubmitted(response.data.workflow_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit incident')
    } finally {
      setLoading(false)
    }
  }

  const activeSev = SEVERITIES.find(s => s.value === formData.severity)

  return (
    <div className="if-page">
      <div className="if-card">

        {/* Header */}
        <div className="if-header">
          <div className="if-header-icon">
            <IconAlertCircle size={20} />
          </div>
          <div>
            <h1 className="if-title">New Incident</h1>
            <p className="if-subtitle">Submit a new incident for triage and automated remediation</p>
          </div>
        </div>

        {error && (
          <div className="if-error">
            <IconAlertCircle size={15} />
            {error}
          </div>
        )}

        <form className="if-form" onSubmit={handleSubmit}>

          {/* Severity — pill selector */}
          <div className="if-field">
            <span className="if-label">Severity <span className="if-required">*</span></span>
            <div className="if-pills">
              {SEVERITIES.map(s => (
                <button
                  key={s.value}
                  type="button"
                  className={`if-pill${formData.severity === s.value ? ' active' : ''}`}
                  style={formData.severity === s.value
                    ? { color: s.color, borderColor: s.color, background: `${s.color}14` }
                    : {}}
                  onClick={() => set('severity', s.value)}
                >
                  {s.label}
                </button>
              ))}
            </div>
          </div>

          {/* Incident Type */}
          <div className="if-field">
            <label className="if-label">Incident Type <span className="if-required">*</span></label>
            <EventTypeCombobox
              value={formData.type}
              onChange={v => set('type', v)}
            />
            <span className="if-hint">Type to search across 200+ event types</span>
          </div>

          {/* Service URL */}
          <div className="if-field">
            <label className="if-label">Service URL</label>
            <input
              className="if-input"
              type="url"
              value={formData.service_url}
              onChange={e => set('service_url', e.target.value)}
              placeholder="e.g., http://api-server:8080 or https://my-service.internal"
            />
            <span className="if-hint">Used by runbook steps as <code style={{ fontSize: '0.7rem', color: '#94a3b8' }}>{'{service_url}'}</code> — leave blank if not applicable</span>
          </div>

          {/* Resource Name */}
          <div className="if-field">
            <label className="if-label">Resource Name <span className="if-required">*</span></label>
            <input
              className="if-input"
              type="text"
              value={formData.resource_name}
              onChange={e => set('resource_name', e.target.value)}
              placeholder="e.g., api-server, database-01, agentic_os_neo4j"
              required
            />
          </div>

          {/* Title */}
          <div className="if-field">
            <label className="if-label">Title</label>
            <input
              className="if-input"
              type="text"
              value={formData.title}
              onChange={e => set('title', e.target.value)}
              placeholder="e.g., Database connection timeout on production"
            />
          </div>

          {/* Description */}
          <div className="if-field">
            <label className="if-label">Description</label>
            <textarea
              className="if-textarea"
              value={formData.description}
              onChange={e => set('description', e.target.value)}
              placeholder="Additional context about the incident…"
              rows={4}
            />
          </div>

          <button
            className="if-submit"
            type="submit"
            disabled={loading || !formData.resource_name || !formData.type}
          >
            {loading ? 'Submitting…' : `Submit ${activeSev?.label ?? ''} Incident`}
          </button>

        </form>
      </div>
    </div>
  )
}
