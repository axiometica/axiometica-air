import { useState, useEffect, CSSProperties } from 'react'
import {
  listLogMonitors,
  createLogMonitor,
  updateLogMonitor,
  deleteLogMonitor,
  validateLogPattern,
  type LogMonitor,
  type LogMonitorPayload,
} from '../services/api'
import {
  IconPlus,
  IconPencil,
  IconTrash,
  IconCheck,
  IconX,
  IconAlertCircle,
} from './icons'

// ── Design tokens ─────────────────────────────────────────────────────────────

const DS = {
  bg:      '#0d1117',
  surface: '#1a1f2e',
  raised:  '#252c3c',
  border:  '#3d4557',
  txtP:    '#e8eef5',
  txtS:    '#7a8ba3',
  txtM:    '#a0aec0',
  accent:  '#3b82f6',
  error:   '#f85149',
  success: '#3fb950',
} as const

// ── Shared style helpers ───────────────────────────────────────────────────────

const sectionCard: CSSProperties = {
  backgroundColor: DS.surface,
  border: `1px solid ${DS.border}`,
  borderRadius: 10,
  overflow: 'hidden',
  marginBottom: '0.6rem',
}

const sectionBody: CSSProperties = {
  padding: '1.1rem 1.25rem 1.25rem',
}

const primaryBtn: CSSProperties = {
  padding: '7px 18px',
  borderRadius: 7,
  border: 'none',
  backgroundColor: DS.accent,
  color: '#fff',
  fontSize: '0.82rem',
  fontWeight: 600,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
}

const secondaryBtn: CSSProperties = {
  padding: '7px 18px',
  borderRadius: 7,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.raised,
  color: DS.txtP,
  fontSize: '0.82rem',
  fontWeight: 500,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
}

const compactBtn: CSSProperties = {
  ...secondaryBtn,
  padding: '3px 10px',
  fontSize: '0.72rem',
  border: 'none',
  backgroundColor: 'transparent',
  color: DS.accent,
  cursor: 'pointer',
  display: 'inline-flex',
  gap: 3,
}

const dangerBtn: CSSProperties = {
  ...compactBtn,
  color: DS.error,
}

const table: CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: '0.85rem',
}

const th: CSSProperties = {
  textAlign: 'left',
  padding: '0.75rem 0.5rem',
  borderBottom: `1px solid ${DS.border}`,
  color: DS.txtS,
  fontWeight: 600,
  fontSize: '0.75rem',
  textTransform: 'uppercase',
  letterSpacing: '0.5px',
}

const td: CSSProperties = {
  padding: '0.75rem 0.5rem',
  borderBottom: `1px solid ${DS.border}`,
  color: DS.txtP,
}

interface LogMonitorsSetupProps {
  watcherName: string
}

export default function LogMonitorsSetup({ watcherName }: LogMonitorsSetupProps) {
  const [monitors, setMonitors] = useState<LogMonitor[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)

  // Form state
  const [formData, setFormData] = useState<LogMonitorPayload>({
    name: '',
    file: '',
    pattern: '',
    event_type: 'log_error_detected',
    interval_sec: 5,
    enabled: true,
  })
  const [patternError, setPatternError] = useState('')
  const [patternValid, setPatternValid] = useState<boolean | null>(null)

  // Load monitors on mount or when watcher changes
  useEffect(() => {
    loadMonitors()
  }, [watcherName])

  const loadMonitors = async () => {
    if (!watcherName) return
    setLoading(true)
    setError('')
    try {
      const response = await listLogMonitors(watcherName)
      setMonitors(response.data)
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to load log monitors')
    } finally {
      setLoading(false)
    }
  }

  const validatePattern = async (pattern: string) => {
    if (!pattern) {
      setPatternError('')
      setPatternValid(null)
      return
    }
    try {
      const response = await validateLogPattern(pattern)
      if (response.data.valid) {
        setPatternError('')
        setPatternValid(true)
      } else {
        setPatternError(response.data.error || 'Invalid regex pattern')
        setPatternValid(false)
      }
    } catch (err: any) {
      setPatternError('Failed to validate pattern')
      setPatternValid(false)
    }
  }

  const handlePatternChange = (value: string) => {
    setFormData(prev => ({ ...prev, pattern: value }))
    // Validate with slight delay (debounce)
    setTimeout(() => validatePattern(value), 300)
  }

  const resetForm = () => {
    setFormData({
      name: '',
      file: '',
      pattern: '',
      event_type: 'log_error_detected',
      interval_sec: 5,
      enabled: true,
    })
    setPatternError('')
    setPatternValid(null)
    setEditingId(null)
    setShowForm(false)
  }

  const handleEdit = (monitor: LogMonitor) => {
    setFormData({
      name: monitor.name,
      file: monitor.file,
      pattern: monitor.pattern,
      event_type: monitor.event_type,
      interval_sec: monitor.interval_sec,
      enabled: monitor.enabled,
    })
    setEditingId(monitor.id)
    setShowForm(true)
    validatePattern(monitor.pattern)
  }

  const handleSave = async () => {
    setError('')

    // Validation
    if (!formData.name.trim()) {
      setError('Monitor name is required')
      return
    }
    if (!formData.file.trim()) {
      setError('Log file path is required')
      return
    }
    if (!formData.pattern.trim()) {
      setError('Regex pattern is required')
      return
    }
    if (patternValid === false) {
      setError('Fix regex pattern errors before saving')
      return
    }
    if (!formData.event_type.trim()) {
      setError('Event type is required')
      return
    }

    try {
      if (editingId) {
        // Update
        const response = await updateLogMonitor(watcherName, editingId, formData)
        setMonitors(monitors.map(m => m.id === editingId ? response.data : m))
      } else {
        // Create
        const response = await createLogMonitor(watcherName, formData)
        setMonitors([...monitors, response.data])
      }
      resetForm()
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to save log monitor')
    }
  }

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this log monitor?')) return
    try {
      await deleteLogMonitor(watcherName, id)
      setMonitors(monitors.filter(m => m.id !== id))
    } catch (err: any) {
      setError(err.response?.data?.detail || 'Failed to delete log monitor')
    }
  }

  const commonEventTypes = [
    'log_error_detected',
    'log_http_error',
    'database_error',
    'high_memory',
    'custom',
  ]

  return (
    <div style={sectionCard}>
      {/* Header */}
      <div style={{ padding: '1rem 1.25rem', borderBottom: `1px solid ${DS.border}`, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h3 style={{ margin: '0 0 0.2rem 0', fontSize: '1rem', color: DS.txtP }}>Log Monitors</h3>
          <p style={{ margin: 0, fontSize: '0.8rem', color: DS.txtS }}>Watch log files and trigger runbooks on patterns</p>
        </div>
        {!showForm && (
          <button style={primaryBtn} onClick={() => setShowForm(true)}>
            <IconPlus size={14} /> Add Monitor
          </button>
        )}
      </div>

      {/* Body */}
      <div style={sectionBody}>
        {error && (
          <div style={{
            backgroundColor: `${DS.error}15`,
            border: `1px solid ${DS.error}40`,
            borderRadius: 6,
            padding: '0.75rem 1rem',
            marginBottom: '1rem',
            display: 'flex',
            gap: 8,
            alignItems: 'flex-start',
            fontSize: '0.85rem',
            color: DS.error,
          }}>
            <IconAlertCircle size={16} style={{ marginTop: '0.1rem', flexShrink: 0 }} />
            {error}
          </div>
        )}

        {/* Form */}
        {showForm && (
          <div style={{
            backgroundColor: DS.raised,
            border: `1px solid ${DS.border}`,
            borderRadius: 8,
            padding: '1rem',
            marginBottom: '1rem',
          }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
              {/* Name */}
              <div>
                <label style={{ fontSize: '0.8rem', color: DS.txtS, fontWeight: 500, display: 'block', marginBottom: '0.4rem' }}>
                  Monitor Name *
                </label>
                <input
                  type="text"
                  placeholder="e.g., app_error_detector"
                  value={formData.name}
                  onChange={e => setFormData(prev => ({ ...prev, name: e.target.value }))}
                  style={{
                    width: '100%',
                    padding: '0.6rem 0.75rem',
                    backgroundColor: DS.bg,
                    border: `1px solid ${DS.border}`,
                    borderRadius: 6,
                    color: DS.txtP,
                    fontSize: '0.85rem',
                  }}
                />
              </div>

              {/* Event Type */}
              <div>
                <label style={{ fontSize: '0.8rem', color: DS.txtS, fontWeight: 500, display: 'block', marginBottom: '0.4rem' }}>
                  Event Type *
                </label>
                <select
                  value={formData.event_type}
                  onChange={e => setFormData(prev => ({ ...prev, event_type: e.target.value }))}
                  style={{
                    width: '100%',
                    padding: '0.6rem 0.75rem',
                    backgroundColor: DS.bg,
                    border: `1px solid ${DS.border}`,
                    borderRadius: 6,
                    color: DS.txtP,
                    fontSize: '0.85rem',
                  }}
                >
                  {commonEventTypes.map(type => (
                    <option key={type} value={type}>{type}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* File Path */}
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ fontSize: '0.8rem', color: DS.txtS, fontWeight: 500, display: 'block', marginBottom: '0.4rem' }}>
                Log File Path *
              </label>
              <input
                type="text"
                placeholder="e.g., /var/log/app.log"
                value={formData.file}
                onChange={e => setFormData(prev => ({ ...prev, file: e.target.value }))}
                style={{
                  width: '100%',
                  padding: '0.6rem 0.75rem',
                  backgroundColor: DS.bg,
                  border: `1px solid ${DS.border}`,
                  borderRadius: 6,
                  color: DS.txtP,
                  fontSize: '0.85rem',
                }}
              />
            </div>

            {/* Pattern */}
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ fontSize: '0.8rem', color: DS.txtS, fontWeight: 500, display: 'flex', justifyContent: 'space-between', marginBottom: '0.4rem' }}>
                Regex Pattern *
                {patternValid === true && <span style={{ color: DS.success, display: 'flex', gap: 4, alignItems: 'center' }}><IconCheck size={12} /> Valid</span>}
                {patternValid === false && <span style={{ color: DS.error, display: 'flex', gap: 4, alignItems: 'center' }}><IconX size={12} /> Invalid</span>}
              </label>
              <input
                type="text"
                placeholder="e.g., ERROR|CRITICAL|panic"
                value={formData.pattern}
                onChange={e => handlePatternChange(e.target.value)}
                style={{
                  width: '100%',
                  padding: '0.6rem 0.75rem',
                  backgroundColor: DS.bg,
                  border: patternValid === false ? `1px solid ${DS.error}` : `1px solid ${DS.border}`,
                  borderRadius: 6,
                  color: DS.txtP,
                  fontSize: '0.85rem',
                }}
              />
              {patternError && (
                <div style={{ fontSize: '0.75rem', color: DS.error, marginTop: '0.4rem' }}>{patternError}</div>
              )}
            </div>

            {/* Interval */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
              <div>
                <label style={{ fontSize: '0.8rem', color: DS.txtS, fontWeight: 500, display: 'block', marginBottom: '0.4rem' }}>
                  Poll Interval (seconds)
                </label>
                <input
                  type="number"
                  min="1"
                  max="3600"
                  value={formData.interval_sec}
                  onChange={e => setFormData(prev => ({ ...prev, interval_sec: parseInt(e.target.value) || 5 }))}
                  style={{
                    width: '100%',
                    padding: '0.6rem 0.75rem',
                    backgroundColor: DS.bg,
                    border: `1px solid ${DS.border}`,
                    borderRadius: 6,
                    color: DS.txtP,
                    fontSize: '0.85rem',
                  }}
                />
              </div>

              {/* Enabled */}
              <div>
                <label style={{ fontSize: '0.8rem', color: DS.txtS, fontWeight: 500, display: 'block', marginBottom: '0.4rem' }}>
                  Enabled
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={formData.enabled}
                    onChange={e => setFormData(prev => ({ ...prev, enabled: e.target.checked }))}
                  />
                  <span style={{ fontSize: '0.85rem', color: DS.txtM }}>
                    {formData.enabled ? 'Active' : 'Disabled'}
                  </span>
                </label>
              </div>
            </div>

            {/* Buttons */}
            <div style={{ display: 'flex', gap: 8 }}>
              <button style={primaryBtn} onClick={handleSave}>
                <IconCheck size={14} /> {editingId ? 'Update' : 'Create'} Monitor
              </button>
              <button style={secondaryBtn} onClick={resetForm}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {/* Monitors Table */}
        {loading ? (
          <div style={{ textAlign: 'center', color: DS.txtS, padding: '2rem' }}>Loading monitors...</div>
        ) : monitors.length === 0 ? (
          <div style={{ textAlign: 'center', color: DS.txtS, padding: '2rem' }}>
            {showForm ? 'Add your first log monitor above' : 'No log monitors configured'}
          </div>
        ) : (
          <table style={table}>
            <thead>
              <tr>
                <th style={th}>Name</th>
                <th style={th}>File</th>
                <th style={th}>Pattern</th>
                <th style={th}>Event Type</th>
                <th style={th}>Interval</th>
                <th style={th}>Status</th>
                <th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {monitors.map(monitor => (
                <tr key={monitor.id}>
                  <td style={td}>
                    <code style={{ fontSize: '0.8rem', color: DS.accent }}>{monitor.name}</code>
                  </td>
                  <td style={td}>
                    <code style={{ fontSize: '0.8rem', color: DS.txtM }}>{monitor.file}</code>
                  </td>
                  <td style={td}>
                    <code style={{ fontSize: '0.75rem', color: DS.txtM, maxWidth: '200px', display: 'inline-block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {monitor.pattern}
                    </code>
                  </td>
                  <td style={td}>
                    <span style={{ fontSize: '0.8rem', color: DS.accent }}>{monitor.event_type}</span>
                  </td>
                  <td style={td}>
                    <span style={{ fontSize: '0.8rem', color: DS.txtM }}>{monitor.interval_sec}s</span>
                  </td>
                  <td style={td}>
                    <span style={{
                      fontSize: '0.75rem',
                      padding: '0.2rem 0.6rem',
                      backgroundColor: monitor.enabled ? `${DS.success}20` : `${DS.border}`,
                      color: monitor.enabled ? DS.success : DS.txtS,
                      borderRadius: 4,
                      fontWeight: 500,
                    }}>
                      {monitor.enabled ? 'Active' : 'Disabled'}
                    </span>
                  </td>
                  <td style={{ ...td, display: 'flex', gap: 4 }}>
                    <button
                      style={compactBtn}
                      onClick={() => handleEdit(monitor)}
                      title="Edit"
                    >
                      <IconPencil size={14} />
                    </button>
                    <button
                      style={dangerBtn}
                      onClick={() => handleDelete(monitor.id)}
                      title="Delete"
                    >
                      <IconTrash size={14} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
