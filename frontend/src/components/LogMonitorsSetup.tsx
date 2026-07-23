import { useState, useEffect, useRef, CSSProperties } from 'react'
import {
  listLogMonitors,
  createLogMonitor,
  updateLogMonitor,
  deleteLogMonitor,
  validateLogPattern,
  testLogMonitor,
  type LogMonitor,
  type LogMonitorPayload,
  type LogMonitorTestResult,
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
  IconTestPipe,
  IconRefresh,
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
  docker:   { bg: 'rgba(59,130,246,0.10)',  color: '#60a5fa', border: 'rgba(59,130,246,0.22)' },
  file:     { bg: 'rgba(63,185,80,0.09)',   color: '#3fb950', border: 'rgba(63,185,80,0.22)'  },
  vcenter:  { bg: 'rgba(210,153,34,0.09)',  color: '#e3a017', border: 'rgba(210,153,34,0.22)' },
}

// ── Test Modal ────────────────────────────────────────────────────────────────

function LogMonitorTestModal({
  monitor,
  watcherName,
  onClose,
}: {
  monitor: LogMonitor
  watcherName: string
  onClose: () => void
}) {
  const [pattern, setPattern] = useState(monitor.pattern)
  const [lines, setLines] = useState(50)
  const [result, setResult] = useState<LogMonitorTestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const resultsRef = useRef<HTMLDivElement>(null)

  const runTest = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await testLogMonitor(watcherName, monitor.id, pattern, lines)
      setResult(res.data)
      setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50)
    } catch (err: any) {
      setError(err.response?.data?.detail || String(err))
    } finally {
      setLoading(false)
    }
  }

  // Run immediately on open
  useEffect(() => { runTest() }, [])

  const highlightLine = (line: string, pat: string): React.ReactNode => {
    if (!pat) return line
    try {
      const re = new RegExp(`(${pat})`, 'gi')
      const parts = line.split(re)
      return parts.map((p, i) =>
        re.test(p)
          ? <mark key={i} style={{ backgroundColor: 'rgba(250,204,21,0.35)', color: '#fde047', borderRadius: 2, padding: '0 1px' }}>{p}</mark>
          : p
      )
    } catch {
      return line
    }
  }

  const sourceLabel = monitor.source === 'docker'
    ? monitor.container
    : monitor.source === 'vcenter'
    ? `${monitor.vm_name}:${monitor.file}`
    : monitor.file

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 60, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1rem' }}>
      {/* Backdrop */}
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }} />

      <div
        onClick={e => e.stopPropagation()}
        style={{
          position: 'relative',
          width: '100%',
          maxWidth: 820,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: DS.surface,
          border: `1px solid ${DS.border}`,
          borderRadius: 10,
          boxShadow: '0 24px 60px rgba(0,0,0,0.5)',
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1rem 1.25rem', borderBottom: `1px solid ${DS.border}`, flexShrink: 0 }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <IconTestPipe size={15} color={DS.txtS} />
              <span style={{ fontSize: '0.9rem', fontWeight: 600, color: DS.txtP }}>Test Monitor — {monitor.name}</span>
            </div>
            <div style={{ marginTop: 3, fontSize: '0.72rem', color: DS.txtS }}>
              <span style={{
                display: 'inline-block',
                padding: '0.1rem 0.4rem',
                borderRadius: 3,
                backgroundColor: (SOURCE_STYLES[monitor.source] ?? SOURCE_STYLES.file).bg,
                color: (SOURCE_STYLES[monitor.source] ?? SOURCE_STYLES.file).color,
                border: `1px solid ${(SOURCE_STYLES[monitor.source] ?? SOURCE_STYLES.file).border}`,
                fontSize: '0.68rem',
                fontWeight: 600,
                textTransform: 'uppercase' as const,
                marginRight: 6,
              }}>{monitor.source}</span>
              <code style={{ color: DS.txtM }}>{sourceLabel}</code>
            </div>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: DS.txtS, cursor: 'pointer', display: 'flex', padding: 4 }}>
            <IconX size={16} />
          </button>
        </div>

        {/* Controls */}
        <div style={{ padding: '0.9rem 1.25rem', borderBottom: `1px solid ${DS.border}`, flexShrink: 0, display: 'flex', gap: '0.75rem', alignItems: 'flex-end' }}>
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: '0.75rem', color: DS.txtS, fontWeight: 500, display: 'block', marginBottom: '0.3rem' }}>Regex Pattern</label>
            <input
              type="text"
              value={pattern}
              onChange={e => setPattern(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && runTest()}
              placeholder="e.g. ERROR|CRITICAL"
              style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.82rem' }}
            />
          </div>
          <div style={{ width: 90 }}>
            <label style={{ fontSize: '0.75rem', color: DS.txtS, fontWeight: 500, display: 'block', marginBottom: '0.3rem' }}>Lines</label>
            <input
              type="number"
              min={1} max={200}
              value={lines}
              onChange={e => setLines(parseInt(e.target.value) || 50)}
              style={{ ...inputStyle, fontSize: '0.82rem' }}
            />
          </div>
          <button
            onClick={runTest}
            disabled={loading}
            style={{
              padding: '0.55rem 1rem',
              borderRadius: 6,
              border: '1px solid rgba(64,112,160,0.45)',
              backgroundColor: loading ? DS.raised : 'rgba(64,112,160,0.15)',
              color: loading ? DS.txtS : '#a0c4e8',
              fontSize: '0.82rem',
              fontWeight: 600,
              cursor: loading ? 'not-allowed' : 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              whiteSpace: 'nowrap' as const,
              flexShrink: 0,
            }}
          >
            <IconRefresh size={13} style={{ animation: loading ? 'spin 0.8s linear infinite' : 'none' }} />
            {loading ? 'Running…' : 'Run Test'}
          </button>
        </div>

        {/* Results */}
        <div ref={resultsRef} style={{ flex: 1, overflowY: 'auto', padding: '0.9rem 1.25rem' }}>
          {error && (
            <div style={{ backgroundColor: `${DS.error}15`, border: `1px solid ${DS.error}40`, borderRadius: 6, padding: '0.65rem 0.9rem', color: DS.error, fontSize: '0.82rem', marginBottom: '0.75rem', display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <IconAlertCircle size={15} style={{ flexShrink: 0, marginTop: 1 }} />{error}
            </div>
          )}

          {result && (
            <>
              {/* Summary bar */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '0.75rem', fontSize: '0.78rem', color: DS.txtS }}>
                <span>{result.total_fetched} line{result.total_fetched !== 1 ? 's' : ''} fetched</span>
                <span style={{ color: DS.border }}>·</span>
                {result.match_count > 0 ? (
                  <span style={{ color: '#fde047', fontWeight: 600 }}>{result.match_count} match{result.match_count !== 1 ? 'es' : ''}</span>
                ) : (
                  <span style={{ color: DS.txtS }}>0 matches</span>
                )}
                {result.error && (
                  <>
                    <span style={{ color: DS.border }}>·</span>
                    <span style={{ color: DS.error }}>{result.error}</span>
                  </>
                )}
              </div>

              {result.lines.length === 0 ? (
                <div style={{ textAlign: 'center', color: DS.txtS, padding: '2rem', fontSize: '0.82rem' }}>
                  {result.error ? 'Could not read log source.' : 'No log lines returned. The log may be empty or the target unreachable.'}
                </div>
              ) : (
                <div style={{
                  backgroundColor: '#0a0d14',
                  border: `1px solid ${DS.border}`,
                  borderRadius: 7,
                  overflow: 'auto',
                  fontFamily: 'monospace',
                  fontSize: '0.75rem',
                  lineHeight: 1.6,
                }}>
                  {result.lines.map((line, i) => {
                    const isMatch = result.matched_indices.includes(i)
                    return (
                      <div
                        key={i}
                        style={{
                          display: 'flex',
                          padding: '0.15rem 0',
                          backgroundColor: isMatch ? 'rgba(250,204,21,0.07)' : 'transparent',
                          borderLeft: isMatch ? '2px solid rgba(250,204,21,0.6)' : '2px solid transparent',
                        }}
                      >
                        <span style={{ color: '#3d4557', userSelect: 'none', minWidth: 44, paddingLeft: 8, paddingRight: 8, textAlign: 'right', flexShrink: 0, borderRight: `1px solid ${DS.border}`, marginRight: 12 }}>
                          {i + 1}
                        </span>
                        <span style={{ color: isMatch ? DS.txtP : DS.txtM, paddingRight: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                          {isMatch ? highlightLine(line, pattern) : line}
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}
            </>
          )}

          {!result && !error && loading && (
            <div style={{ textAlign: 'center', color: DS.txtS, padding: '2.5rem', fontSize: '0.82rem' }}>
              Reading log source…
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: '0.75rem 1.25rem', borderTop: `1px solid ${DS.border}`, flexShrink: 0, display: 'flex', justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{ ...secondaryBtn }}>Close</button>
        </div>
      </div>

      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

interface LogMonitorsSetupProps {
  watcherName: string
}

const EMPTY_FORM: LogMonitorPayload = {
  name: '',
  source: 'file',
  file: '',
  container: '',
  vm_name: '',
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
  const [testingMonitor, setTestingMonitor] = useState<LogMonitor | null>(null)

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
      vm_name: monitor.vm_name || '',
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
    if (formData.source === 'vcenter' && !formData.vm_name?.trim()) {
      setError('VM name is required for vCenter source'); return
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
            Watch log files, container stdout/stderr, or VM logs (vCenter) and trigger runbooks on patterns
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
                {(['file', 'docker', 'vcenter'] as const).map(src => (
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
                    {src === 'file' ? 'File' : src === 'docker' ? 'Docker Container' : 'vCenter VM'}
                  </button>
                ))}
              </div>
              <p style={{ margin: '0.4rem 0 0', fontSize: '0.72rem', color: DS.txtS }}>
                {formData.source === 'docker'
                  ? 'Reads stdout + stderr from a named container via docker logs'
                  : formData.source === 'vcenter'
                  ? 'Reads a log file inside a VM via VMware Tools guest exec — no SSH required'
                  : 'Tails a log file mounted inside the watcher container'}
              </p>
            </div>

            {/* Target: container name (docker) */}
            {formData.source === 'docker' && (
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
            )}

            {/* Target: VM name + log file (vcenter) */}
            {formData.source === 'vcenter' && (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem', marginBottom: '1rem' }}>
                <div>
                  <label style={labelStyle}>VM Name *</label>
                  <input
                    type="text"
                    placeholder="e.g., prod-app-01"
                    value={formData.vm_name ?? ''}
                    onChange={e => setFormData(prev => ({ ...prev, vm_name: e.target.value }))}
                    style={{ ...inputStyle, fontFamily: 'monospace' }}
                  />
                  <p style={{ margin: '0.3rem 0 0', fontSize: '0.7rem', color: DS.txtS }}>
                    VM name as shown in vCenter inventory
                  </p>
                </div>
                <div>
                  <label style={labelStyle}>Log File Path *</label>
                  <input
                    type="text"
                    placeholder="e.g., /var/log/app/app.log"
                    value={formData.file ?? ''}
                    onChange={e => setFormData(prev => ({ ...prev, file: e.target.value }))}
                    style={{ ...inputStyle, fontFamily: 'monospace' }}
                  />
                  <p style={{ margin: '0.3rem 0 0', fontSize: '0.7rem', color: DS.txtS }}>
                    Absolute path inside the guest OS
                  </p>
                </div>
              </div>
            )}

            {/* Target: log file path (file) */}
            {formData.source === 'file' && (
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
                      <span style={{ fontSize: '0.85rem', color: DS.txtP }}>{monitor.name}</span>
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
                        {monitor.source === 'docker'
                          ? monitor.container
                          : monitor.source === 'vcenter'
                          ? `${monitor.vm_name}:${monitor.file}`
                          : monitor.file}
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
                      <span style={{ fontSize: '0.85rem', color: DS.txtP }}>{monitor.event_type}</span>
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
                      <button
                        title={monitor.enabled ? 'Disable' : 'Enable'}
                        onClick={async () => {
                          try {
                            const res = await updateLogMonitor(watcherName, monitor.id, { enabled: !monitor.enabled })
                            setMonitors(monitors.map(m => m.id === monitor.id ? res.data : m))
                          } catch { /* ignore */ }
                        }}
                        style={{
                          width: 32, height: 18, borderRadius: 9, border: 'none', cursor: 'pointer', flexShrink: 0,
                          backgroundColor: monitor.enabled ? '#3a7a5a' : DS.border, transition: 'background 0.2s',
                          position: 'relative',
                        }}
                      >
                        <span style={{
                          position: 'absolute', top: 3, left: monitor.enabled ? 14 : 3,
                          width: 12, height: 12, borderRadius: '50%',
                          backgroundColor: '#fff', transition: 'left 0.2s',
                        }} />
                      </button>
                    </td>
                    <td style={{ ...td, display: 'flex', gap: 4, alignItems: 'center' }}>
                      <button
                        style={{ background: 'none', border: 'none', color: DS.txtS, cursor: 'pointer', padding: 4, borderRadius: 4, display: 'flex' }}
                        onClick={() => setTestingMonitor(monitor)}
                        title="Test monitor"
                      >
                        <IconTestPipe size={14} />
                      </button>
                      <button
                        style={{ background: 'none', border: 'none', color: DS.txtS, cursor: 'pointer', padding: 4, borderRadius: 4, display: 'flex' }}
                        onClick={() => handleEdit(monitor)}
                        title="Edit"
                      >
                        <IconPencil size={14} />
                      </button>
                      <button
                        style={{ background: 'none', border: 'none', color: DS.txtS, cursor: 'pointer', padding: 4, borderRadius: 4, display: 'flex' }}
                        onClick={() => handleDelete(monitor.id)}
                        title="Delete"
                      >
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

      {testingMonitor && (
        <LogMonitorTestModal
          monitor={testingMonitor}
          watcherName={watcherName}
          onClose={() => setTestingMonitor(null)}
        />
      )}
    </div>
  )
}
