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
  IconChevronDown,
  IconChevronRight,
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
  border: '1px solid rgba(64, 112, 160, 0.40)',
  backgroundColor: '#252c3c',
  color: '#a0c4e8',
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

const inputStyle: CSSProperties = {
  width: '100%',
  padding: '0.6rem 0.75rem',
  backgroundColor: DS.bg,
  border: `1px solid ${DS.border}`,
  borderRadius: 6,
  color: DS.txtP,
  fontSize: '0.85rem',
}

const labelStyle: CSSProperties = {
  fontSize: '0.8rem',
  color: DS.txtS,
  fontWeight: 500,
  display: 'block',
  marginBottom: '0.4rem',
}

const SEVERITY_STYLES: Record<string, { bg: string; color: string; border: string }> = {
  critical: { bg: 'rgba(248,81,73,0.12)',   color: '#f85149', border: 'rgba(248,81,73,0.3)'  },
  high:     { bg: 'rgba(210,153,34,0.12)',  color: '#e3a017', border: 'rgba(210,153,34,0.3)' },
  warning:  { bg: 'rgba(210,153,34,0.08)',  color: '#d49a2a', border: 'rgba(210,153,34,0.2)' },
  info:     { bg: 'rgba(59,130,246,0.10)',  color: '#60a5fa', border: 'rgba(59,130,246,0.25)' },
}

const SOURCE_STYLES: Record<string, { bg: string; color: string; border: string }> = {
  docker: { bg: 'rgba(59,130,246,0.10)', color: '#60a5fa', border: 'rgba(59,130,246,0.22)' },
  file:   { bg: 'rgba(63,185,80,0.09)',  color: '#3fb950', border: 'rgba(63,185,80,0.22)'  },
}

interface LogMonitorsSetupProps {
  watcherName: string
}

const EMPTY_FORM: LogMonitorPayload = {
  name: '',
  source: 'file',
  file: '',
  container: '',
  pattern: '',
  event_type: 'log_error_detected',
  interval_sec: 30,
  min_occurrences: 1,
  severity: 'warning',
  clear_after_polls: 3,
  enabled: true,
}

export default function LogMonitorsSetup({ watcherName }: LogMonitorsSetupProps) {
  const [monitors, setMonitors] = useState<LogMonitor[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const [editingId, setEditingId] = useState<string | null>(null)

  const [formData, setFormData] = useState<LogMonitorPayload>(EMPTY_FORM)
  const [patternError, setPatternError] = useState('')
  const [patternValid, setPatternValid] = useState<boolean | null>(null)

  useEffect(() => { loadMonitors() }, [watcherName])

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
    if (!pattern) { setPatternError(''); setPatternValid(null); return }
    try {
      const response = await validateLogPattern(pattern)
      if (response.data.valid) {
        setPatternError(''); setPatternValid(true)
      } else {
        setPatternError(response.data.error || 'Invalid regex pattern'); setPatternValid(false)
      }
    } catch {
      setPatternError('Failed to validate pattern'); setPatternValid(false)
    }
  }

  const handlePatternChange = (value: string) => {
    setFormData(prev => ({ ...prev, pattern: value }))
    setTimeout(() => validatePattern(value), 300)
  }

  const resetForm = () => {
    setFormData(EMPTY_FORM)
    setPatternError('')
    setPatternValid(null)
    setEditingId(null)
    setShowForm(false)
  }

  const handleEdit = (monitor: LogMonitor) => {
    setFormData({
      name: monitor.name,
      source: monitor.source || 'file',
      file: monitor.file,
      container: monitor.container || '',
      pattern: monitor.pattern,
      event_type: monitor.event_type,
      interval_sec: monitor.interval_sec,
      min_occurrences: monitor.min_occurrences ?? 1,
      severity: monitor.severity || 'warning',
      clear_after_polls: monitor.clear_after_polls ?? 3,
      enabled: monitor.enabled,
    })
    setEditingId(monitor.id)
    setShowForm(true)
    validatePattern(monitor.pattern)
  }

  const handleSave = async () => {
    setError('')
    if (!formData.name?.trim()) { setError('Monitor name is required'); return }
    if (formData.source === 'docker' && !formData.container?.trim()) {
      setError('Container name is required for Docker source'); return
    }
    if (formData.source !== 'docker' && !formData.file?.trim()) {
      setError('Log file path is required'); return
    }
    if (!formData.pattern.trim()) { setError('Regex pattern is required'); return }
    if (patternValid === false) { setError('Fix regex pattern errors before saving'); return }
    if (!formData.event_type.trim()) { setError('Event type is required'); return }

    try {
      if (editingId) {
        const response = await updateLogMonitor(watcherName, editingId, formData)
        setMonitors(monitors.map(m => m.id === editingId ? response.data : m))
      } else {
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

  const severityOptions: { value: string; label: string }[] = [
    { value: 'critical', label: 'Critical' },
    { value: 'high',     label: 'High'     },
    { value: 'warning',  label: 'Warning'  },
    { value: 'info',     label: 'Info'     },
  ]

  return (
    <div style={sectionCard}>
      {/* Header */}
      <div
        style={{ display: 'flex', alignItems: 'center', padding: '0.8rem 1.25rem', cursor: 'pointer', userSelect: 'none' }}
        onClick={() => setExpanded(x => !x)}
      >
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {expanded
              ? <IconChevronDown size={15} color={DS.txtS} />
              : <IconChevronRight size={15} color={DS.txtS} />
            }
            <span style={{ fontSize: '0.9rem', fontWeight: 600, color: DS.txtP, letterSpacing: '0.01em' }}>Log Monitors</span>
          </div>
          <p style={{ margin: '2px 0 0', fontSize: '0.72rem', color: DS.txtS, marginLeft: 23 }}>
            Watch log files or container stdout/stderr and trigger runbooks on patterns
          </p>
        </div>
        {!showForm && (
          <button
            style={{ ...compactBtn, backgroundColor: '#252c3c', border: '1px solid rgba(64, 112, 160, 0.40)', color: '#a0c4e8' }}
            onClick={e => { e.stopPropagation(); setShowForm(true); setExpanded(true) }}
          >
            <IconPlus size={12} /> Add Monitor
          </button>
        )}
      </div>

      {/* Body */}
      {expanded && (
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

            {/* Row 1: Name + Event Type */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
              <div>
                <label style={labelStyle}>Monitor Name *</label>
                <input
                  type="text"
                  placeholder="e.g., app_error_detector"
                  value={formData.name}
                  onChange={e => setFormData(prev => ({ ...prev, name: e.target.value }))}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Event Type *</label>
                <select
                  value={formData.event_type}
                  onChange={e => setFormData(prev => ({ ...prev, event_type: e.target.value }))}
                  style={inputStyle}
                >
                  {commonEventTypes.map(type => (
                    <option key={type} value={type}>{type}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Source toggle */}
            <div style={{ marginBottom: '1rem' }}>
              <label style={labelStyle}>Log Source *</label>
              <div style={{ display: 'flex', gap: 8 }}>
                {(['file', 'docker'] as const).map(src => (
                  <button
                    key={src}
                    type="button"
                    onClick={() => setFormData(prev => ({ ...prev, source: src }))}
                    style={{
                      padding: '0.4rem 1rem',
                      borderRadius: 6,
                      border: formData.source === src
                        ? '1px solid rgba(64,112,160,0.65)'
                        : `1px solid ${DS.border}`,
                      backgroundColor: formData.source === src ? 'rgba(64,112,160,0.15)' : DS.bg,
                      color: formData.source === src ? '#a0c4e8' : DS.txtS,
                      fontSize: '0.82rem',
                      fontWeight: 600,
                      cursor: 'pointer',
                    }}
                  >
                    {src === 'file' ? 'File' : 'Docker Container'}
                  </button>
                ))}
              </div>
              <p style={{ margin: '0.4rem 0 0', fontSize: '0.72rem', color: DS.txtS }}>
                {formData.source === 'docker'
                  ? 'Reads stdout + stderr from a named container via docker logs'
                  : 'Tails a log file mounted inside the watcher container'}
              </p>
            </div>

            {/* Target: file path or container name */}
            {formData.source === 'docker' ? (
              <div style={{ marginBottom: '1rem' }}>
                <label style={labelStyle}>Container Name *</label>
                <input
                  type="text"
                  placeholder="e.g., agentic_os_backend"
                  value={formData.container ?? ''}
                  onChange={e => setFormData(prev => ({ ...prev, container: e.target.value }))}
                  style={{ ...inputStyle, fontFamily: 'monospace' }}
                />
              </div>
            ) : (
              <div style={{ marginBottom: '1rem' }}>
                <label style={labelStyle}>Log File Path *</label>
                <input
                  type="text"
                  placeholder="e.g., /var/log/app.log"
                  value={formData.file ?? ''}
                  onChange={e => setFormData(prev => ({ ...prev, file: e.target.value }))}
                  style={{ ...inputStyle, fontFamily: 'monospace' }}
                />
              </div>
            )}

            {/* Pattern */}
            <div style={{ marginBottom: '1rem' }}>
              <label style={{ ...labelStyle, display: 'flex', justifyContent: 'space-between' }}>
                <span>Regex Pattern *</span>
                {patternValid === true && (
                  <span style={{ color: DS.success, display: 'flex', gap: 4, alignItems: 'center' }}>
                    <IconCheck size={12} /> Valid
                  </span>
                )}
                {patternValid === false && (
                  <span style={{ color: DS.error, display: 'flex', gap: 4, alignItems: 'center' }}>
                    <IconX size={12} /> Invalid
                  </span>
                )}
              </label>
              <input
                type="text"
                placeholder='e.g., "levelname":\s*"(ERROR|CRITICAL)"'
                value={formData.pattern}
                onChange={e => handlePatternChange(e.target.value)}
                style={{
                  ...inputStyle,
                  border: patternValid === false ? `1px solid ${DS.error}` : `1px solid ${DS.border}`,
                }}
              />
              {patternError && (
                <div style={{ fontSize: '0.75rem', color: DS.error, marginTop: '0.4rem' }}>{patternError}</div>
              )}
            </div>

            {/* Row: Severity + Min Occurrences + Poll Interval + Clear After Polls + Enabled */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr auto', gap: '1rem', marginBottom: '1rem', alignItems: 'start' }}>
              <div>
                <label style={labelStyle}>Severity</label>
                <select
                  value={formData.severity ?? 'warning'}
                  onChange={e => setFormData(prev => ({ ...prev, severity: e.target.value }))}
                  style={inputStyle}
                >
                  {severityOptions.map(o => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>

              <div>
                <label style={labelStyle}>Min Matches / Poll</label>
                <input
                  type="number"
                  min="1"
                  max="1000"
                  value={formData.min_occurrences ?? 1}
                  onChange={e => setFormData(prev => ({ ...prev, min_occurrences: parseInt(e.target.value) || 1 }))}
                  style={inputStyle}
                />
                <p style={{ margin: '0.3rem 0 0', fontSize: '0.7rem', color: DS.txtS }}>
                  Matching lines needed before event fires
                </p>
              </div>

              <div>
                <label style={labelStyle}>Poll Interval (s)</label>
                <input
                  type="number"
                  min="1"
                  max="3600"
                  value={formData.interval_sec}
                  onChange={e => setFormData(prev => ({ ...prev, interval_sec: parseInt(e.target.value) || 5 }))}
                  style={inputStyle}
                />
              </div>

              <div>
                <label style={labelStyle}>Clear After Polls</label>
                <input
                  type="number"
                  min="0"
                  max="100"
                  value={formData.clear_after_polls ?? 3}
                  onChange={e => setFormData(prev => ({ ...prev, clear_after_polls: parseInt(e.target.value) || 0 }))}
                  style={inputStyle}
                />
                <p style={{ margin: '0.3rem 0 0', fontSize: '0.7rem', color: DS.txtS }}>
                  Quiet polls before all-clear (0 = immediate)
                </p>
              </div>

              <div style={{ paddingTop: '1.6rem' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={formData.enabled}
                    onChange={e => setFormData(prev => ({ ...prev, enabled: e.target.checked }))}
                  />
                  <span style={{ fontSize: '0.82rem', color: DS.txtM, whiteSpace: 'nowrap' }}>
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
                <th style={th}>Source</th>
                <th style={th}>Target</th>
                <th style={th}>Pattern</th>
                <th style={th}>Event Type</th>
                <th style={th}>Severity</th>
                <th style={th}>Min / Interval</th>
                <th style={th}>Status</th>
                <th style={th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {monitors.map(monitor => {
                const sevStyle = SEVERITY_STYLES[monitor.severity] ?? SEVERITY_STYLES.warning
                const srcStyle = SOURCE_STYLES[monitor.source] ?? SOURCE_STYLES.file
                return (
                  <tr key={monitor.id}>
                    <td style={td}>
                      <code style={{ fontSize: '0.8rem', color: DS.accent }}>{monitor.name}</code>
                    </td>
                    <td style={td}>
                      <span style={{
                        fontSize: '0.72rem',
                        fontWeight: 600,
                        padding: '0.15rem 0.5rem',
                        borderRadius: 4,
                        backgroundColor: srcStyle.bg,
                        color: srcStyle.color,
                        border: `1px solid ${srcStyle.border}`,
                        textTransform: 'uppercase',
                        letterSpacing: '0.03em',
                      }}>
                        {monitor.source}
                      </span>
                    </td>
                    <td style={td}>
                      <code style={{ fontSize: '0.78rem', color: DS.txtM }}>
                        {monitor.source === 'docker' ? monitor.container : monitor.file}
                      </code>
                    </td>
                    <td style={td}>
                      <code style={{
                        fontSize: '0.75rem',
                        color: DS.txtM,
                        maxWidth: '160px',
                        display: 'inline-block',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}>
                        {monitor.pattern}
                      </code>
                    </td>
                    <td style={td}>
                      <span style={{ fontSize: '0.8rem', color: DS.accent }}>{monitor.event_type}</span>
                    </td>
                    <td style={td}>
                      <span style={{
                        fontSize: '0.72rem',
                        fontWeight: 600,
                        padding: '0.15rem 0.5rem',
                        borderRadius: 4,
                        backgroundColor: sevStyle.bg,
                        color: sevStyle.color,
                        border: `1px solid ${sevStyle.border}`,
                      }}>
                        {monitor.severity || 'warning'}
                      </span>
                    </td>
                    <td style={td}>
                      <span style={{ fontSize: '0.78rem', color: DS.txtM }}>
                        {monitor.min_occurrences ?? 1}× / {monitor.interval_sec}s
                      </span>
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
                      <button style={compactBtn} onClick={() => handleEdit(monitor)} title="Edit">
                        <IconPencil size={14} />
                      </button>
                      <button style={dangerBtn} onClick={() => handleDelete(monitor.id)} title="Delete">
                        <IconTrash size={14} />
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
      )}
    </div>
  )
}
