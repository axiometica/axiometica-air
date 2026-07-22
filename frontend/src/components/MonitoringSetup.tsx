import { useState, useEffect, useCallback, useRef, CSSProperties } from 'react'
import {
  listWatchers,
  listWatcherChecks,
  createWatcherCheck,
  updateWatcherCheck,
  deleteWatcherCheck,
  seedWatcherChecks,
  testWatcherCheck,
  approveWatcher,
  rejectWatcher,
  deleteWatcher,
  disableWatcher,
  enableWatcher,
  resetWatcher,
  invalidateWatcher,
  getWatcherSettings,
  updateWatcherSettings,
  resetWatcherSettings,
  listSyntheticMonitors,
  deleteSyntheticMonitor,
  updateSyntheticMonitor,
  type WatcherInfo,
  type ExternalCheck,
  type ExternalCheckPayload,
  type ExternalCheckTestResult,
  type WatcherSetting,
  type WatcherSettingsUpdate,
} from '../services/api'
import type { SyntheticMonitor } from '../types'
import LogMonitorsSetup from './LogMonitorsSetup'
import { MonitorModal } from './SyntheticsPage'
import {
  IconPlus,
  IconPencil,
  IconTrash,
  IconCheck,
  IconX,
  IconRadar,
  IconServer,
  IconLock,
  IconNetwork,
  IconSearch,
  IconShieldCheck,
  IconChevronDown,
  IconChevronRight,
  IconRefresh,
  IconChartLine,
  IconBolt,
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
  mutedGreen: '#3a7a5a',
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
}

const inputStyle: CSSProperties = {
  width: '100%',
  padding: '6px 10px',
  borderRadius: 6,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.bg,
  color: DS.txtP,
  fontSize: '0.85rem',
  outline: 'none',
  boxSizing: 'border-box',
}

const selectStyle: CSSProperties = {
  padding: '6px 28px 6px 10px',
  borderRadius: 6,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.bg,
  color: DS.txtP,
  fontSize: '0.82rem',
  outline: 'none',
  cursor: 'pointer',
  appearance: 'none' as const,
}

// ── Check type registry — icons only, no emoji ─────────────────────────────────

type CheckType = 'ping' | 'http' | 'https' | 'tcp' | 'dns' | 'tls'

const CHECK_TYPES: Record<CheckType, { Icon: React.ComponentType<any>; label: string; color: string; borderColor: string }> = {
  ping:  { Icon: IconRadar,       label: 'PING',  color: '#10b981', borderColor: 'rgba(16,185,129,0.35)' },
  http:  { Icon: IconServer,      label: 'HTTP',  color: DS.accent, borderColor: 'rgba(59,130,246,0.35)' },
  https: { Icon: IconLock,        label: 'HTTPS', color: DS.accent, borderColor: 'rgba(59,130,246,0.35)' },
  tcp:   { Icon: IconNetwork,     label: 'TCP',   color: '#f59e0b', borderColor: 'rgba(245,158,11,0.35)' },
  dns:   { Icon: IconSearch,      label: 'DNS',   color: '#a855f7', borderColor: 'rgba(168,85,247,0.35)' },
  tls:   { Icon: IconShieldCheck, label: 'TLS',   color: '#ef4444', borderColor: 'rgba(239,68,68,0.35)' },
}

// ── Threshold configuration ────────────────────────────────────────────────────

interface ThresholdDef {
  key: keyof WatcherSettingsUpdate
  label: string
  unit: string
  min: number
  max: number
  step: number
}

const ALERT_THRESHOLDS: ThresholdDef[] = [
  { key: 'cpu_threshold',    label: 'CPU Alert',      unit: '%',        min: 10,   max: 99,    step: 1    },
  { key: 'memory_threshold', label: 'Memory Alert',   unit: '%',        min: 10,   max: 99,    step: 1    },
  { key: 'disk_threshold',   label: 'Disk Alert',     unit: '%',        min: 10,   max: 99,    step: 1    },
  { key: 'syscall_threshold',label: 'Syscall Rate',   unit: '/5s',      min: 1000, max: 100000,step: 1000 },
]

const OPERATIONAL_SETTINGS: ThresholdDef[] = [
  { key: 'poll_interval',           label: 'Poll Interval',       unit: 's',  min: 5,  max: 120, step: 1 },
  { key: 'cooldown_seconds',        label: 'Alert Cooldown',      unit: 's',  min: 10, max: 600, step: 10 },
  { key: 'min_consecutive_polls',   label: 'Min Consecutive',     unit: 'polls', min: 1, max: 10, step: 1 },
  { key: 'connection_threshold',    label: 'Connection Limit',    unit: '',   min: 100, max: 5000, step: 100 },
  { key: 'discovery_interval_polls',label: 'Discovery Every',     unit: 'polls', min: 1, max: 60, step: 1 },
]

// ── Shared divider ─────────────────────────────────────────────────────────────

function Divider() {
  return <div style={{ borderTop: `1px solid ${DS.border}`, margin: '1rem 0' }} />
}

// ── Error banner ──────────────────────────────────────────────────────────────

function ErrorBanner({ message, onClose }: { message: string; onClose: () => void }) {
  return (
    <div style={{
      backgroundColor: 'rgba(239,68,68,0.10)',
      border: '1px solid rgba(239,68,68,0.28)',
      color: '#f87171',
      borderRadius: 7,
      padding: '0.55rem 0.85rem',
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      fontSize: '0.82rem',
      marginBottom: '1rem',
    }}>
      <span style={{ flex: 1 }}>{message}</span>
      <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#f87171', cursor: 'pointer', padding: 0, display: 'flex' }}>
        <IconX size={14} />
      </button>
    </div>
  )
}

// ── Success banner ────────────────────────────────────────────────────────────

function SuccessBanner({ message }: { message: string }) {
  return (
    <div style={{
      backgroundColor: 'rgba(16,185,129,0.10)',
      border: '1px solid rgba(16,185,129,0.28)',
      color: '#34d399',
      borderRadius: 7,
      padding: '0.55rem 0.85rem',
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      fontSize: '0.82rem',
    }}>
      <IconCheck size={14} />
      {message}
    </div>
  )
}

// ── Skeleton row ──────────────────────────────────────────────────────────────

function SkeletonRow({ height = 36 }: { height?: number }) {
  return (
    <div style={{
      height,
      borderRadius: 6,
      backgroundColor: DS.raised,
      animation: 'pulse 2s cubic-bezier(0.4,0,0.6,1) infinite',
    }} />
  )
}

// ── Section accordion header ──────────────────────────────────────────────────

function SectionHeader({
  title,
  subtitle,
  expanded,
  onToggle,
  right,
}: {
  title: string
  subtitle?: string
  expanded: boolean
  onToggle: () => void
  right?: React.ReactNode
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', padding: '0.8rem 1.25rem', cursor: 'pointer', userSelect: 'none' as const }}
         onClick={onToggle}>
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {expanded
            ? <IconChevronDown size={15} color={DS.txtS} />
            : <IconChevronRight size={15} color={DS.txtS} />
          }
          <span style={{ fontSize: '0.9rem', fontWeight: 600, color: DS.txtP, letterSpacing: '0.01em' }}>
            {title}
          </span>
        </div>
        {subtitle && (
          <p style={{ fontSize: '0.72rem', color: DS.txtS, marginLeft: 23, marginTop: 2 }}>
            {subtitle}
          </p>
        )}
      </div>
      {right && (
        <div onClick={e => e.stopPropagation()}>
          {right}
        </div>
      )}
    </div>
  )
}

// ── Threshold slider row ──────────────────────────────────────────────────────

function ThresholdRow({
  def,
  value,
  onChange,
}: {
  def: ThresholdDef
  value: number
  onChange: (v: number) => void
}) {
  const pct = Math.min(100, Math.max(0, ((value - def.min) / (def.max - def.min)) * 100))

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ fontSize: '0.82rem', color: DS.txtM }}>{def.label}</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="number"
            min={def.min}
            max={def.max}
            step={def.step}
            value={value}
            onChange={e => {
              const v = def.step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value)
              if (!isNaN(v)) onChange(Math.min(def.max, Math.max(def.min, v)))
            }}
            style={{
              width: 70,
              padding: '2px 6px',
              borderRadius: 5,
              border: `1px solid ${DS.border}`,
              backgroundColor: DS.bg,
              color: DS.txtP,
              fontSize: '0.82rem',
              textAlign: 'right',
              outline: 'none',
            }}
          />
          {def.unit && (
            <span style={{ fontSize: '0.72rem', color: DS.txtS, width: 30 }}>{def.unit}</span>
          )}
        </div>
      </div>
      {/* Slider track + transparent native range overlaid in its own containing block */}
      <div style={{ position: 'relative', height: 20, marginTop: 4 }}>
        {/* Visual track — centred vertically */}
        <div style={{ position: 'absolute', top: '50%', left: 0, right: 0, transform: 'translateY(-50%)', height: 4, borderRadius: 4, backgroundColor: DS.raised }}>
          <div style={{ position: 'absolute', top: 0, left: 0, height: '100%', width: `${pct}%`, backgroundColor: DS.accent, borderRadius: 4, transition: 'width 0.1s' }} />
        </div>
        {/* Native range — invisible, covers full hit area */}
        <input
          type="range"
          min={def.min}
          max={def.max}
          step={def.step}
          value={value}
          onChange={e => onChange(def.step < 1 ? parseFloat(e.target.value) : parseInt(e.target.value))}
          style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', opacity: 0, cursor: 'pointer', margin: 0 }}
        />
      </div>
    </div>
  )
}

// ── Container Monitoring section ──────────────────────────────────────────────

function ContainerMonitoringSection({ watcherName }: { watcherName: string }) {
  const [settings, setSettings] = useState<WatcherSetting[]>([])
  const [draft, setDraft] = useState<WatcherSettingsUpdate>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(true)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const res = await getWatcherSettings()
      setSettings(res.data.settings)
      const d: WatcherSettingsUpdate = {}
      for (const s of res.data.settings) {
        const short = s.key.replace('watcher.', '') as keyof WatcherSettingsUpdate
        ;(d as any)[short] = s.value
      }
      setDraft(d)
    } catch {
      setError('Failed to load thresholds')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleSave = async () => {
    setSaving(true)
    try {
      await updateWatcherSettings(draft)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      setError('Failed to save thresholds')
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    setSaving(true)
    try {
      await resetWatcherSettings()
      await load()
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      setError('Failed to reset thresholds')
    } finally {
      setSaving(false)
    }
  }

  const val = (key: keyof WatcherSettingsUpdate): number =>
    Number((draft as any)[key] ?? 0)

  const set = (key: keyof WatcherSettingsUpdate, v: number | boolean) =>
    setDraft(d => ({ ...d, [key]: v }))

  return (
    <div style={sectionCard}>
      <SectionHeader
        title="Container Monitoring"
        subtitle={`Thresholds applied to ${watcherName} — changes pushed live within 30 s`}
        expanded={expanded}
        onToggle={() => setExpanded(x => !x)}
      />
      {expanded && (
        <div style={sectionBody}>
          {error && <ErrorBanner message={error} onClose={() => setError(null)} />}

          {loading ? (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem 1.5rem' }}>
              {[...Array(8)].map((_, i) => <SkeletonRow key={i} height={52} />)}
            </div>
          ) : (
            <>
              {/* Alert thresholds */}
              <p style={{ fontSize: '0.72rem', fontWeight: 600, color: DS.txtS, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: '0.75rem' }}>
                Alert Thresholds
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem 1.5rem', marginBottom: '1.25rem' }}>
                {ALERT_THRESHOLDS.map(def => (
                  <ThresholdRow
                    key={def.key}
                    def={def}
                    value={val(def.key)}
                    onChange={v => set(def.key, v)}
                  />
                ))}
              </div>

              <Divider />

              {/* Operational settings */}
              <p style={{ fontSize: '0.72rem', fontWeight: 600, color: DS.txtS, letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: '0.75rem' }}>
                Operational Settings
              </p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem 1.5rem', marginBottom: '1.25rem' }}>
                {OPERATIONAL_SETTINGS.map(def => (
                  <ThresholdRow
                    key={def.key}
                    def={def}
                    value={val(def.key)}
                    onChange={v => set(def.key, v)}
                  />
                ))}
              </div>

              {/* Toggle: discovery enabled */}
              {settings.find(s => s.key === 'watcher.discovery_enabled') && (
                <>
                  <Divider />
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: '1rem' }}>
                    <input
                      type="checkbox"
                      id="discovery_enabled"
                      checked={Boolean((draft as any).discovery_enabled)}
                      onChange={e => set('discovery_enabled' as any, e.target.checked)}
                      style={{ width: 15, height: 15, accentColor: DS.accent, cursor: 'pointer' }}
                    />
                    <label htmlFor="discovery_enabled" style={{ fontSize: '0.85rem', color: DS.txtM, cursor: 'pointer' }}>
                      CMDB Discovery enabled
                    </label>
                  </div>
                </>
              )}

              <Divider />

              {/* Actions row */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <button
                  onClick={handleReset}
                  disabled={saving}
                  style={{ ...secondaryBtn, opacity: saving ? 0.5 : 1, cursor: saving ? 'not-allowed' : 'pointer' }}
                >
                  <IconRefresh size={14} />
                  Reset Defaults
                </button>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  {saved && <SuccessBanner message="Applied to watcher" />}
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    style={{ ...primaryBtn, opacity: saving ? 0.7 : 1, cursor: saving ? 'not-allowed' : 'pointer' }}
                  >
                    {saving ? 'Saving…' : (
                      <><IconCheck size={14} />Save & Apply</>
                    )}
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Check type badge ───────────────────────────────────────────────────────────

function CheckTypeBadge({ type }: { type: string }) {
  const meta = CHECK_TYPES[type as CheckType] ?? CHECK_TYPES.http
  const { Icon, label, color, borderColor } = meta
  return (
    <div style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: 4,
      padding: '2px 8px',
      borderRadius: 5,
      border: `1px solid ${borderColor}`,
      backgroundColor: `${color}14`,
    }}>
      <Icon size={12} color={color} />
      <span style={{ fontSize: '0.72rem', fontWeight: 600, color, letterSpacing: '0.04em' }}>{label}</span>
    </div>
  )
}

// ── Check modal ───────────────────────────────────────────────────────────────

const EMPTY_FORM: ExternalCheckPayload = {
  check_type: 'ping', target: '', name: '',
  port: null, expected_status: 200, timeout_ms: 5000,
  latency_threshold_ms: 0, tls_expiry_warning_days: 30, enabled: true,
}

function CheckModal({
  initial,
  watcherName,
  onSave,
  onClose,
  saving,
}: {
  initial: ExternalCheck | null
  watcherName: string
  onSave: (p: ExternalCheckPayload) => Promise<void>
  onClose: () => void
  saving: boolean
}) {
  const [testResult, setTestResult] = useState<ExternalCheckTestResult | null>(null)
  const [testLoading, setTestLoading] = useState(false)

  const runTest = async () => {
    setTestResult(null)
    setTestLoading(true)
    try {
      const res = await testWatcherCheck(watcherName, form)
      setTestResult(res.data)
    } catch (err: any) {
      setTestResult({ success: false, error: err?.response?.data?.detail || String(err) })
    } finally {
      setTestLoading(false)
    }
  }

  const [form, setForm] = useState<ExternalCheckPayload>(
    initial
      ? {
          check_type: initial.check_type,
          target: initial.target,
          name: initial.name,
          port: initial.port,
          expected_status: initial.expected_status,
          timeout_ms: initial.timeout_ms,
          latency_threshold_ms: initial.latency_threshold_ms,
          tls_expiry_warning_days: initial.tls_expiry_warning_days,
          enabled: initial.enabled,
        }
      : { ...EMPTY_FORM }
  )

  const set = (field: keyof ExternalCheckPayload, value: any) =>
    setForm(f => ({ ...f, [field]: value }))

  const submit = (e: React.FormEvent) => { e.preventDefault(); onSave(form) }

  const fieldGroup: CSSProperties = { marginBottom: '0.9rem' }
  const fieldLabel: CSSProperties = { display: 'block', fontSize: '0.78rem', color: DS.txtM, marginBottom: 4 }

  const targetPlaceholder: Record<string, string> = {
    ping: '8.8.8.8',
    http: 'http://backend:8000/api/health',
    https: 'https://example.com',
    tcp: 'redis',
    dns: 'google.com',
    tls: 'example.com',
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 50,
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '1rem',
    }}>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: 'absolute', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
      />

      <form
        onSubmit={submit}
        onClick={e => e.stopPropagation()}
        style={{
          position: 'relative',
          width: '100%',
          maxWidth: 480,
          backgroundColor: DS.surface,
          border: `1px solid ${DS.border}`,
          borderRadius: 10,
          padding: '1.4rem',
          boxShadow: '0 24px 60px rgba(0,0,0,0.5)',
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '1.2rem' }}>
          <span style={{ fontSize: '0.9rem', fontWeight: 600, color: DS.txtP }}>
            {initial ? 'Edit Check' : 'Add External Check'}
          </span>
          <button type="button" onClick={onClose}
            style={{ background: 'none', border: 'none', color: DS.txtS, cursor: 'pointer', display: 'flex', padding: 4 }}>
            <IconX size={16} />
          </button>
        </div>

        {/* Check type */}
        <div style={fieldGroup}>
          <label style={fieldLabel}>Check Type</label>
          <div style={{ position: 'relative' }}>
            <select value={form.check_type} onChange={e => set('check_type', e.target.value)} style={selectStyle}>
              {Object.entries(CHECK_TYPES).map(([k, v]) => (
                <option key={k} value={k}>{v.label}</option>
              ))}
            </select>
            <IconChevronDown size={13} color={DS.txtS} style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }} />
          </div>
        </div>

        {/* Target */}
        <div style={fieldGroup}>
          <label style={fieldLabel}>
            Target
            <span style={{ color: DS.txtS, marginLeft: 4 }}>
              {form.check_type === 'http' || form.check_type === 'https' ? '— full URL' : '— hostname or IP'}
            </span>
          </label>
          <input
            required
            type="text"
            placeholder={targetPlaceholder[form.check_type] ?? ''}
            value={form.target}
            onChange={e => set('target', e.target.value)}
            style={inputStyle}
          />
        </div>

        {/* Display name */}
        <div style={fieldGroup}>
          <label style={fieldLabel}>Display Name <span style={{ color: DS.txtS }}>— optional</span></label>
          <input
            type="text"
            placeholder="e.g. Internet reachability"
            value={form.name || ''}
            onChange={e => set('name', e.target.value)}
            style={inputStyle}
          />
        </div>

        {/* TCP port */}
        {form.check_type === 'tcp' && (
          <div style={fieldGroup}>
            <label style={fieldLabel}>Port</label>
            <input
              required type="number" min={1} max={65535}
              placeholder="6379"
              value={form.port ?? ''}
              onChange={e => set('port', e.target.value ? Number(e.target.value) : null)}
              style={inputStyle}
            />
          </div>
        )}

        {/* Timeout */}
        <div style={fieldGroup}>
          <label style={fieldLabel}>Timeout (ms)</label>
          <input
            type="number" min={100} max={30000} step={100}
            value={form.timeout_ms}
            onChange={e => set('timeout_ms', Number(e.target.value))}
            style={inputStyle}
          />
        </div>

        {/* HTTP/HTTPS extras */}
        {(form.check_type === 'http' || form.check_type === 'https') && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem', marginBottom: '0.9rem' }}>
            <div>
              <label style={fieldLabel}>Expected Status</label>
              <input
                type="number" min={100} max={599}
                value={form.expected_status}
                onChange={e => set('expected_status', Number(e.target.value))}
                style={inputStyle}
              />
            </div>
            <div>
              <label style={fieldLabel}>Latency Threshold (ms) <span style={{ color: DS.txtS }}>0=off</span></label>
              <input
                type="number" min={0} max={30000} step={100}
                value={form.latency_threshold_ms}
                onChange={e => set('latency_threshold_ms', Number(e.target.value))}
                style={inputStyle}
              />
            </div>
          </div>
        )}

        {/* TLS extra */}
        {form.check_type === 'tls' && (
          <div style={fieldGroup}>
            <label style={fieldLabel}>Expiry Warning (days before expiry)</label>
            <input
              type="number" min={1} max={365}
              value={form.tls_expiry_warning_days}
              onChange={e => set('tls_expiry_warning_days', Number(e.target.value))}
              style={inputStyle}
            />
          </div>
        )}

        {/* Enabled toggle */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '1.2rem' }}>
          <input
            type="checkbox"
            id="check_enabled"
            checked={form.enabled}
            onChange={e => set('enabled', e.target.checked)}
            style={{ width: 15, height: 15, accentColor: DS.accent, cursor: 'pointer' }}
          />
          <label htmlFor="check_enabled" style={{ fontSize: '0.85rem', color: DS.txtM, cursor: 'pointer' }}>
            Enabled
          </label>
        </div>

        {/* Test result panel */}
        {testResult && (
          <div style={{
            marginBottom: '1rem',
            padding: '0.75rem',
            borderRadius: 7,
            border: `1px solid ${
              testResult.status === 'healthy' ? '#22c55e' :
              testResult.status === 'degraded' ? '#f59e0b' : '#ef4444'
            }`,
            backgroundColor: `${
              testResult.status === 'healthy' ? 'rgba(34,197,94,0.08)' :
              testResult.status === 'degraded' ? 'rgba(245,158,11,0.08)' : 'rgba(239,68,68,0.08)'
            }`,
            fontSize: '0.78rem',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: testResult.error || testResult.response_body ? 6 : 0 }}>
              <span style={{
                fontWeight: 700,
                color: testResult.status === 'healthy' ? '#22c55e' :
                       testResult.status === 'degraded' ? '#f59e0b' : '#ef4444',
                textTransform: 'uppercase',
                fontSize: '0.72rem',
                letterSpacing: '0.05em',
              }}>
                {testResult.success ? testResult.status : 'Error'}
              </span>
              {testResult.status_code != null && (
                <span style={{ color: DS.txtM }}>HTTP {testResult.status_code}</span>
              )}
              {testResult.response_time_ms != null && (
                <span style={{ color: DS.txtS }}>{testResult.response_time_ms}ms</span>
              )}
              {testResult.tls_days_remaining != null && (
                <span style={{ color: DS.txtM }}>Cert expires in {testResult.tls_days_remaining}d</span>
              )}
            </div>
            {testResult.error && (
              <div style={{ color: '#ef4444', wordBreak: 'break-all' }}>{testResult.error}</div>
            )}
            {testResult.response_body && (
              <pre style={{
                margin: '6px 0 0',
                padding: '6px 8px',
                backgroundColor: 'rgba(0,0,0,0.25)',
                borderRadius: 4,
                fontSize: '0.72rem',
                color: DS.txtM,
                overflowX: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                maxHeight: 120,
                overflowY: 'auto',
              }}>
                {testResult.response_body}
              </pre>
            )}
          </div>
        )}

        {/* Actions */}
        <div style={{ display: 'flex', gap: 10 }}>
          <button type="button" onClick={onClose} style={{ ...secondaryBtn, flex: 1, justifyContent: 'center' }}>
            Cancel
          </button>
          <button
            type="button"
            onClick={runTest}
            disabled={testLoading || !form.target}
            style={{ ...secondaryBtn, justifyContent: 'center', opacity: (testLoading || !form.target) ? 0.5 : 1, cursor: (testLoading || !form.target) ? 'not-allowed' : 'pointer', minWidth: 72 }}
          >
            {testLoading ? '…' : 'Test'}
          </button>
          <button
            type="submit"
            disabled={saving}
            style={{ ...primaryBtn, flex: 1, justifyContent: 'center', opacity: saving ? 0.7 : 1, cursor: saving ? 'not-allowed' : 'pointer' }}
          >
            {saving ? 'Saving…' : <><IconCheck size={14} />{initial ? 'Update' : 'Add Check'}</>}
          </button>
        </div>
      </form>
    </div>
  )
}

// ── External Checks section ───────────────────────────────────────────────────

function ExternalChecksSection({ watcherName }: { watcherName: string }) {
  const [checks, setChecks] = useState<ExternalCheck[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showModal, setShowModal] = useState(false)
  const [editingCheck, setEditingCheck] = useState<ExternalCheck | null>(null)
  const [savingCheck, setSavingCheck] = useState(false)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [expanded, setExpanded] = useState(true)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const res = await listWatcherChecks(watcherName)
      setChecks(res.data as ExternalCheck[])
    } catch {
      setError('Failed to load external checks')
    } finally {
      setLoading(false)
    }
  }, [watcherName])

  useEffect(() => { load() }, [load])

  const handleSave = async (payload: ExternalCheckPayload) => {
    setSavingCheck(true)
    try {
      if (editingCheck) {
        await updateWatcherCheck(watcherName, editingCheck.id, payload)
      } else {
        await createWatcherCheck(watcherName, payload)
      }
      setShowModal(false)
      setEditingCheck(null)
      await load()
    } catch {
      setError('Failed to save check')
    } finally {
      setSavingCheck(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (confirmDeleteId !== id) { setConfirmDeleteId(id); return }
    setDeletingId(id)
    try {
      await deleteWatcherCheck(watcherName, id)
      setChecks(prev => prev.filter(c => c.id !== id))
      setConfirmDeleteId(null)
    } catch {
      setError('Failed to delete check')
    } finally {
      setDeletingId(null)
    }
  }

  const handleSeed = async () => {
    try {
      await seedWatcherChecks(watcherName)
      await load()
    } catch {
      setError('Failed to seed defaults')
    }
  }

  const headerRight = (
    <div style={{ display: 'flex', gap: 8 }}>
      {checks.length === 0 && !loading && (
        <button onClick={handleSeed} style={compactBtn}>
          <IconRefresh size={12} />
          Seed Defaults
        </button>
      )}
      <button
        onClick={() => { setEditingCheck(null); setShowModal(true) }}
        style={{ ...compactBtn, backgroundColor: '#252c3c', border: '1px solid rgba(64, 112, 160, 0.40)', color: '#a0c4e8' }}
      >
        <IconPlus size={12} />
        Add Check
      </button>
    </div>
  )

  return (
    <>
      <div style={sectionCard}>
        <SectionHeader
          title="External Connectivity Checks"
          subtitle="Probes run from inside the watcher container — ping, HTTP, TCP, DNS, TLS"
          expanded={expanded}
          onToggle={() => setExpanded(x => !x)}
          right={headerRight}
        />
        {expanded && (
          <div style={sectionBody}>
            {error && <ErrorBanner message={error} onClose={() => setError(null)} />}

            {loading ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[...Array(3)].map((_, i) => <SkeletonRow key={i} height={44} />)}
              </div>
            ) : checks.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '2rem 0' }}>
                <p style={{ fontSize: '0.85rem', color: DS.txtS }}>No checks configured for this watcher</p>
                <p style={{ fontSize: '0.72rem', color: DS.txtS, marginTop: 4, opacity: 0.7 }}>
                  Add checks individually or seed the factory defaults
                </p>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {checks.map(check => {
                  const isConfirming = confirmDeleteId === check.id
                  const isDeleting = deletingId === check.id

                  return (
                    <div
                      key={check.id}
                      style={{
                        position: 'relative',
                        display: 'flex',
                        alignItems: 'center',
                        gap: 12,
                        padding: '9px 12px',
                        borderRadius: 7,
                        backgroundColor: check.enabled ? DS.raised : 'transparent',
                        border: `1px solid ${check.enabled ? DS.border : 'transparent'}`,
                        opacity: check.enabled ? 1 : 0.5,
                      }}
                    >
                      {/* Status dot */}
                      <div style={{
                        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
                        backgroundColor: check.enabled ? '#10b981' : DS.txtS,
                      }} />

                      {/* Type badge */}
                      <CheckTypeBadge type={check.check_type} />

                      {/* Name + target */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ fontSize: '0.85rem', color: DS.txtP }}>
                          {check.name || check.target}
                        </span>
                        {check.name && (
                          <span style={{ marginLeft: 10, fontSize: '0.72rem', color: DS.txtS }}>
                            {check.target}
                          </span>
                        )}
                      </div>

                      {/* Timeout */}
                      <span style={{
                        flexShrink: 0, fontSize: '0.72rem', color: DS.txtS,
                        padding: '1px 7px', borderRadius: 4, backgroundColor: DS.bg,
                        border: `1px solid ${DS.border}`,
                      }}>
                        {check.timeout_ms} ms
                      </span>

                      {/* Latency threshold (HTTP) */}
                      {check.latency_threshold_ms > 0 && (
                        <span style={{
                          flexShrink: 0, fontSize: '0.72rem', color: '#f59e0b',
                          padding: '1px 7px', borderRadius: 4, backgroundColor: 'rgba(245,158,11,0.10)',
                          border: '1px solid rgba(245,158,11,0.30)',
                        }}>
                          &lt;{check.latency_threshold_ms} ms
                        </span>
                      )}

                      {/* Actions */}
                      <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                        <button
                          onClick={() => { setEditingCheck(check); setShowModal(true) }}
                          title="Edit"
                          style={{ background: 'none', border: 'none', color: DS.txtS, cursor: 'pointer', padding: 4, borderRadius: 4, display: 'flex' }}
                        >
                          <IconPencil size={14} />
                        </button>
                        <button
                          onClick={() => handleDelete(check.id)}
                          disabled={isDeleting}
                          title={isConfirming ? 'Click again to confirm delete' : 'Delete'}
                          style={{
                            background: isConfirming ? 'rgba(239,68,68,0.12)' : 'none',
                            border: isConfirming ? '1px solid rgba(239,68,68,0.4)' : 'none',
                            color: isConfirming ? '#ef4444' : DS.txtS,
                            cursor: isDeleting ? 'wait' : 'pointer',
                            padding: isConfirming ? '2px 8px' : 4,
                            borderRadius: 4,
                            display: 'flex',
                            alignItems: 'center',
                            gap: 4,
                            fontSize: '0.72rem',
                            fontWeight: isConfirming ? 600 : 400,
                            whiteSpace: 'nowrap' as const,
                          }}
                        >
                          {isDeleting
                            ? <span style={{ fontSize: '0.72rem', color: DS.txtS }}>…</span>
                            : isConfirming
                              ? <><IconTrash size={13} />Delete?</>
                              : <IconTrash size={14} />
                          }
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}

            {checks.length > 0 && (
              <p style={{ fontSize: '0.72rem', color: DS.txtS, marginTop: '0.75rem' }}>
                Changes are applied within 30 s on the watcher's next config refresh cycle
              </p>
            )}
          </div>
        )}
      </div>

      {showModal && (
        <CheckModal
          initial={editingCheck}
          watcherName={watcherName}
          onSave={handleSave}
          onClose={() => { setShowModal(false); setEditingCheck(null) }}
          saving={savingCheck}
        />
      )}

      {confirmDeleteId && !deletingId && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 10 }} onClick={() => setConfirmDeleteId(null)} />
      )}
    </>
  )
}

// ── Watcher status helpers ────────────────────────────────────────────────────

const STATUS_CFG: Record<string, { label: string; dot: string; text: string; bg: string; border: string }> = {
  active:   { label: 'Active',    dot: '#10b981', text: '#34d399', bg: 'rgba(16,185,129,0.10)', border: 'rgba(16,185,129,0.30)' },
  inactive: { label: 'Inactive',  dot: '#6b7280', text: '#9ca3af', bg: 'rgba(107,114,128,0.10)', border: 'rgba(107,114,128,0.25)' },
  pending:  { label: 'Pending',   dot: '#f59e0b', text: '#fbbf24', bg: 'rgba(245,158,11,0.10)', border: 'rgba(245,158,11,0.35)' },
  disabled: { label: 'Disabled',  dot: '#6366f1', text: '#a5b4fc', bg: 'rgba(99,102,241,0.10)', border: 'rgba(99,102,241,0.30)' },
  rejected: { label: 'Rejected',  dot: '#ef4444', text: '#fca5a5', bg: 'rgba(239,68,68,0.10)',  border: 'rgba(239,68,68,0.30)' },
}

function statusKey(w: WatcherInfo): string {
  if (w.registration_status === 'approved') return w.status  // 'active' | 'inactive'
  return w.registration_status
}

function StatusBadge({ w }: { w: WatcherInfo }) {
  const key = statusKey(w)
  const cfg = STATUS_CFG[key] ?? STATUS_CFG.inactive
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '2px 9px', borderRadius: 20, fontSize: '0.72rem', fontWeight: 600,
      backgroundColor: cfg.bg, border: `1px solid ${cfg.border}`, color: cfg.text,
    }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', backgroundColor: cfg.dot, flexShrink: 0 }} />
      {cfg.label}
    </span>
  )
}

function EnvBadge({ env, adapter }: { env: string; adapter: string }) {
  if (!env || env === 'unknown') return null
  return (
    <span title={`Adapter: ${adapter}`} style={{
      display: 'inline-block', padding: '1px 7px', borderRadius: 3,
      fontSize: '0.67rem', fontWeight: 500,
      backgroundColor: 'rgba(59,130,246,0.10)', border: '1px solid rgba(59,130,246,0.22)',
      color: '#60a5fa', cursor: 'default',
    }}>
      {env.replace(/_/g, ' ')}
    </span>
  )
}

// ── Register watcher modal ────────────────────────────────────────────────────

function RegisterModal({ onClose, platformUrl }: { onClose: () => void; platformUrl: string }) {
  const [name, setName] = useState('watcher_prod')
  const [apiKey, setApiKey] = useState('')
  const [copied, setCopied] = useState(false)

  const cmd = `curl -fsSL ${platformUrl}/scripts/install-watcher.sh | \\
  WATCHER_API_URL=${platformUrl} \\
  WATCHER_API_KEY=${apiKey || '<WATCHER_API_KEY>'} \\
  WATCHER_NAME=${name || 'my_watcher'} \\
  bash`

  const copy = () => {
    navigator.clipboard.writeText(cmd).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100,
      backgroundColor: 'rgba(0,0,0,0.65)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }} onClick={onClose}>
      <div style={{
        backgroundColor: DS.surface, border: `1px solid ${DS.border}`,
        borderRadius: 12, padding: '1.75rem', width: 580, maxWidth: '95vw',
        boxShadow: '0 8px 40px rgba(0,0,0,0.5)',
      }} onClick={e => e.stopPropagation()}>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.25rem' }}>
          <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, color: DS.txtP }}>Register a Watcher</h2>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: DS.txtS, padding: 4 }}>
            <IconX size={18} />
          </button>
        </div>

        <p style={{ fontSize: '0.82rem', color: DS.txtM, marginBottom: '1.25rem', lineHeight: 1.5 }}>
          Run the install script on any Linux VM (Ubuntu, RHEL, Amazon Linux) to deploy a watcher
          as a systemd service. The watcher auto-registers as <strong style={{ color: DS.txtP }}>pending</strong> and
          appears in this table for approval.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: '1.25rem' }}>
          <div>
            <label style={{ fontSize: '0.75rem', color: DS.txtS, display: 'block', marginBottom: 4 }}>Watcher Name</label>
            <input
              value={name} onChange={e => setName(e.target.value.replace(/\s/g, '_'))}
              placeholder="watcher_prod"
              style={{ width: '100%', padding: '6px 10px', borderRadius: 6, border: `1px solid ${DS.border}`, backgroundColor: DS.raised, color: DS.txtP, fontSize: '0.82rem', boxSizing: 'border-box' as const }}
            />
          </div>
          <div>
            <label style={{ fontSize: '0.75rem', color: DS.txtS, display: 'block', marginBottom: 4 }}>API Key <span style={{ color: DS.txtS }}>(Watcher Bot key)</span></label>
            <input
              value={apiKey} onChange={e => setApiKey(e.target.value)}
              placeholder="Paste API key from Admin → Users"
              type="password"
              style={{ width: '100%', padding: '6px 10px', borderRadius: 6, border: `1px solid ${DS.border}`, backgroundColor: DS.raised, color: DS.txtP, fontSize: '0.82rem', boxSizing: 'border-box' as const }}
            />
          </div>
        </div>

        <div style={{ position: 'relative', marginBottom: '1rem' }}>
          <pre style={{
            margin: 0, padding: '0.85rem 1rem', paddingRight: '3rem',
            backgroundColor: '#0d1117', border: `1px solid ${DS.border}`,
            borderRadius: 8, fontSize: '0.72rem', color: '#7ee787',
            fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            lineHeight: 1.6,
          }}>{cmd}</pre>
          <button onClick={copy} title="Copy" style={{
            position: 'absolute', top: 8, right: 8,
            background: copied ? 'rgba(16,185,129,0.15)' : DS.raised,
            border: `1px solid ${DS.border}`, borderRadius: 5,
            color: copied ? '#34d399' : DS.txtM, cursor: 'pointer', padding: '3px 8px',
            fontSize: '0.7rem', fontWeight: 600,
          }}>
            {copied ? '✓ Copied' : 'Copy'}
          </button>
        </div>

        <p style={{ fontSize: '0.72rem', color: DS.txtS, margin: 0, lineHeight: 1.5 }}>
          Supports: Docker, Kubernetes, AWS EC2 (SSM), Azure VM, GCP, VMware, bare metal.
          After install the watcher is <strong style={{ color: '#fbbf24' }}>pending</strong> — approve it in the table below.
        </p>
      </div>
    </div>
  )
}

// ── Watcher registry table ────────────────────────────────────────────────────

function WatcherTable({
  watchers,
  selectedWatcher,
  onSelect,
  onRefresh,
}: {
  watchers: WatcherInfo[]
  selectedWatcher: string
  onSelect: (name: string) => void
  onRefresh: () => void
}) {
  const [busy, setBusy] = useState<Record<string, string>>({})   // name → action
  const [openMenu, setOpenMenu] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement | null>(null)

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpenMenu(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const act = async (watcher: WatcherInfo, action: string, fn: () => Promise<unknown>, msg?: string) => {
    setBusy(b => ({ ...b, [watcher.watcher_name]: action }))
    setOpenMenu(null)
    try {
      await fn()
      if (msg) { setToast(msg); setTimeout(() => setToast(null), 3000) }
    } catch { /* ignore */ }
    finally {
      setBusy(b => { const n = { ...b }; delete n[watcher.watcher_name]; return n })
      onRefresh()
    }
  }

  const fmtLastSeen = (s: number | null) => {
    if (s === null) return '—'
    if (s < 60) return `${s}s ago`
    if (s < 3600) return `${Math.floor(s / 60)}m ago`
    return `${Math.floor(s / 3600)}h ago`
  }

  if (watchers.length === 0) return null

  return (
    <>
      {/* Toast notification */}
      {toast && (
        <div style={{
          position: 'fixed', bottom: 24, right: 24, zIndex: 200,
          backgroundColor: '#1e3a2e', border: '1px solid rgba(16,185,129,0.4)',
          color: '#34d399', padding: '10px 18px', borderRadius: 8, fontSize: '0.82rem', fontWeight: 500,
          boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
        }}>
          {toast}
        </div>
      )}

      <div style={{
        backgroundColor: DS.surface, border: `1px solid ${DS.border}`,
        borderRadius: 10, marginBottom: '1.75rem',
        /* overflow: visible so action dropdown can escape the card */
      }}>
        {/* Table header */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 110px 130px 90px 90px 80px 44px',
          padding: '0.55rem 1rem', borderBottom: `1px solid ${DS.border}`,
          backgroundColor: DS.raised,
          borderRadius: '10px 10px 0 0',  /* clip header corners since parent no longer clips */
        }}>
          {['Watcher', 'Status', 'Environment', 'Version', 'Last Seen', 'Poll', ''].map((h, i) => (
            <span key={i} style={{ fontSize: '0.68rem', fontWeight: 700, color: DS.txtS, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</span>
          ))}
        </div>

        {/* Table rows */}
        {watchers.map((w, idx) => {
          const isSelected = w.watcher_name === selectedWatcher
          const isApproved = w.registration_status === 'approved'
          const isBusy = !!busy[w.watcher_name]

          return (
            <div
              key={w.watcher_name}
              onClick={() => isApproved && onSelect(w.watcher_name)}
              style={{
                display: 'grid',
                gridTemplateColumns: '1fr 110px 130px 90px 90px 80px 44px',
                padding: '0.7rem 1rem', alignItems: 'center',
                borderBottom: idx < watchers.length - 1 ? `1px solid ${DS.border}` : 'none',
                cursor: isApproved ? 'pointer' : 'default',
                backgroundColor: isSelected ? 'rgba(59,130,246,0.06)' : 'transparent',
                transition: 'background 0.15s',
              }}
              onMouseEnter={e => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.backgroundColor = 'rgba(255,255,255,0.02)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.backgroundColor = isSelected ? 'rgba(59,130,246,0.06)' : 'transparent' }}
            >
              {/* Name + id chip */}
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  {isSelected && <div style={{ width: 3, height: 16, borderRadius: 2, backgroundColor: DS.accent, flexShrink: 0 }} />}
                  <span style={{ fontSize: '0.85rem', fontWeight: 600, color: DS.txtP, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {w.display_name || w.watcher_name}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 5, marginTop: 2, alignItems: 'center' }}>
                  <span style={{ fontSize: '0.68rem', color: DS.txtS }}>{w.host || '—'}</span>
                  {w.watcher_id && (
                    <span title={`ID: ${w.watcher_id}`} style={{
                      fontSize: '0.62rem', color: DS.txtS, fontFamily: 'monospace',
                      padding: '0 5px', borderRadius: 3, backgroundColor: DS.raised,
                      border: `1px solid ${DS.border}`, cursor: 'default', userSelect: 'all' as const,
                    }}>
                      {w.watcher_id.slice(0, 8)}…
                    </span>
                  )}
                </div>
              </div>

              {/* Status */}
              <div><StatusBadge w={w} /></div>

              {/* Environment */}
              <div><EnvBadge env={w.environment} adapter={w.adapter_mode} /></div>

              {/* Version */}
              <span style={{ fontSize: '0.75rem', color: DS.txtM, fontFamily: 'monospace' }}>{w.watcher_version || '—'}</span>

              {/* Last seen */}
              <span style={{ fontSize: '0.75rem', color: w.status === 'active' ? '#34d399' : DS.txtS }}>
                {fmtLastSeen(w.last_seen_seconds_ago)}
              </span>

              {/* Poll interval */}
              <span style={{ fontSize: '0.75rem', color: DS.txtM }}>{w.poll_interval}s</span>

              {/* Actions menu */}
              <div style={{ position: 'relative' }} ref={openMenu === w.watcher_name ? menuRef : undefined}>
                <button
                  disabled={isBusy}
                  onClick={e => { e.stopPropagation(); setOpenMenu(openMenu === w.watcher_name ? null : w.watcher_name) }}
                  title="Actions"
                  style={{
                    background: 'none', border: `1px solid ${DS.border}`, borderRadius: 5,
                    color: DS.txtM, cursor: 'pointer', padding: '3px 7px',
                    fontSize: '0.85rem', opacity: isBusy ? 0.5 : 1,
                    backgroundColor: openMenu === w.watcher_name ? DS.raised : 'transparent',
                  }}
                >
                  {isBusy ? '…' : '⋮'}
                </button>

                {openMenu === w.watcher_name && (
                  <div style={{
                    position: 'absolute', right: 0, top: '110%',
                    zIndex: 200,
                    backgroundColor: DS.surface, border: `1px solid ${DS.border}`,
                    borderRadius: 8, boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
                    minWidth: 170, overflow: 'hidden',
                  }}>
                    {/* Approve — pending only */}
                    {w.registration_status === 'pending' && (
                      <MenuItem icon="✓" label="Approve" color="#34d399"
                        onClick={() => act(w, 'approve', () => approveWatcher(w.watcher_name), `✓ ${w.watcher_name} approved`)} />
                    )}

                    {/* Enable — disabled or rejected */}
                    {(w.registration_status === 'disabled' || w.registration_status === 'rejected') && (
                      <MenuItem icon="▶" label="Enable" color="#34d399"
                        onClick={() => act(w, 'enable', () => enableWatcher(w.watcher_name), `✓ ${w.watcher_name} enabled`)} />
                    )}

                    {/* Disable — approved only */}
                    {w.registration_status === 'approved' && (
                      <MenuItem icon="⏸" label="Disable" color="#a5b4fc"
                        onClick={() => act(w, 'disable', () => disableWatcher(w.watcher_name), `${w.watcher_name} disabled`)} />
                    )}

                    {/* Reject — pending only */}
                    {w.registration_status === 'pending' && (
                      <MenuItem icon="✕" label="Reject" color="#fca5a5"
                        onClick={() => act(w, 'reject', () => rejectWatcher(w.watcher_name), `${w.watcher_name} rejected`)} />
                    )}

                    {/* Reset — approved only */}
                    {w.registration_status === 'approved' && (
                      <MenuItem icon="↺" label="Reset" color={DS.txtM}
                        onClick={() => act(w, 'reset', () => resetWatcher(w.watcher_name), `${w.watcher_name} reset`)} />
                    )}

                    {/* Invalidate — approved only (force re-registration) */}
                    {w.registration_status === 'approved' && (
                      <MenuItem icon="↻" label="Invalidate" color={DS.txtM}
                        onClick={() => act(w, 'invalidate', () => invalidateWatcher(w.watcher_name), `${w.watcher_name} re-registering as pending`)} />
                    )}

                    {/* Configure — approved only */}
                    {w.registration_status === 'approved' && (
                      <MenuItem icon="⚙" label="Configure" color={DS.txtM}
                        onClick={() => { setOpenMenu(null); onSelect(w.watcher_name); window.scrollTo({ top: 9999, behavior: 'smooth' }) }} />
                    )}

                    <div style={{ borderTop: `1px solid ${DS.border}`, margin: '2px 0' }} />

                    {/* Delete */}
                    <MenuItem icon="🗑" label="Delete" color="#fca5a5"
                      onClick={() => {
                        if (window.confirm(`Delete watcher "${w.watcher_name}"? It will re-register as pending on restart.`)) {
                          act(w, 'delete', () => deleteWatcher(w.watcher_name), `${w.watcher_name} deleted`)
                        } else { setOpenMenu(null) }
                      }} />
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </>
  )
}

function MenuItem({ icon, label, color, onClick }: { icon: string; label: string; color: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 9,
        width: '100%', textAlign: 'left',
        padding: '8px 14px', background: 'none', border: 'none',
        cursor: 'pointer', color, fontSize: '0.82rem', fontWeight: 500,
      }}
      onMouseEnter={e => (e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.04)')}
      onMouseLeave={e => (e.currentTarget.style.backgroundColor = 'transparent')}
    >
      <span style={{ width: 16, textAlign: 'center', fontSize: '0.88rem' }}>{icon}</span>
      {label}
    </button>
  )
}

// ── Sparkline ─────────────────────────────────────────────────────────────────

function Sparkline({
  values, color, width = 120, height = 36, fill = true,
}: {
  values: number[]; color: string; width?: number; height?: number; fill?: boolean
}) {
  if (values.length < 2) {
    return (
      <svg width={width} height={height}>
        <text x={width / 2} y={height / 2 + 4} textAnchor="middle"
          fill={DS.txtS} fontSize="9" fontFamily="sans-serif">no data</text>
      </svg>
    )
  }
  const pad = 3
  const w = width - pad * 2
  const h = height - pad * 2
  const min = Math.min(...values)
  const max = Math.max(...values)
  const range = max - min || 1
  const pts = values.map((v, i) => {
    const x = pad + (i / (values.length - 1)) * w
    const y = pad + h - ((v - min) / range) * h
    return `${x},${y}`
  })
  const linePath = `M ${pts.join(' L ')}`
  const fillPath = `${linePath} L ${pad + w},${pad + h} L ${pad},${pad + h} Z`
  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      {fill && (
        <path d={fillPath} fill={color} fillOpacity={0.12} stroke="none" />
      )}
      <path d={linePath} fill="none" stroke={color} strokeWidth={1.5}
        strokeLinecap="round" strokeLinejoin="round" />
      {/* current value dot */}
      <circle
        cx={pad + w}
        cy={pad + h - ((values[values.length - 1] - min) / range) * h}
        r={2.5} fill={color}
      />
    </svg>
  )
}

function MetricCard({
  label, values, color, unit = '%', width = 130,
}: {
  label: string; values: number[]; color: string; unit?: string; width?: number
}) {
  const current = values.length > 0 ? values[values.length - 1] : null
  const peak    = values.length > 0 ? Math.max(...values) : null
  return (
    <div style={{
      backgroundColor: DS.raised, border: `1px solid ${DS.border}`,
      borderRadius: 8, padding: '10px 14px', minWidth: width,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <span style={{ fontSize: '0.68rem', fontWeight: 700, color: DS.txtS, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{label}</span>
        <span style={{ fontSize: '0.85rem', fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>
          {current !== null ? `${current.toFixed(1)}${unit}` : '—'}
        </span>
      </div>
      <Sparkline values={values} color={color} width={width - 28} height={38} />
      {peak !== null && (
        <div style={{ marginTop: 4, fontSize: '0.62rem', color: DS.txtS }}>
          peak&nbsp;<span style={{ color: DS.txtM }}>{peak.toFixed(1)}{unit}</span>
        </div>
      )}
    </div>
  )
}

function WatcherMetricsDashboard({ watcher }: { watcher: WatcherInfo }) {
  const history = watcher.metrics_history ?? []
  if (history.length === 0) {
    return (
      <div style={{ ...sectionCard, marginBottom: '1.5rem' }}>
        <div style={{ padding: '0.75rem 1rem', borderBottom: `1px solid ${DS.border}`, backgroundColor: DS.raised, display: 'flex', alignItems: 'center', gap: 8 }}>
          <IconChartLine size={16} strokeWidth={2} color={DS.txtP} />
          <span style={{ fontSize: '0.78rem', fontWeight: 700, color: DS.txtP }}>Metrics</span>
        </div>
        <div style={{ padding: '1.25rem 1rem', color: DS.txtS, fontSize: '0.8rem' }}>
          No metrics yet — data accumulates after the first few poll cycles.
        </div>
      </div>
    )
  }

  const cpuVals   = history.map(p => p.cpu)
  const memVals   = history.map(p => p.mem)
  const diskVals  = history.map(p => p.disk)
  const alertVals = history.map(p => p.alerts)
  const totalAlerts = alertVals.reduce((a, b) => a + b, 0)
  const span = history.length >= 2
    ? (() => {
        const ms = new Date(history[history.length - 1].ts).getTime() - new Date(history[0].ts).getTime()
        const mins = Math.round(ms / 60000)
        return mins < 60 ? `${mins}m` : `${(mins / 60).toFixed(1)}h`
      })()
    : null

  return (
    <div style={{ ...sectionCard, marginBottom: '1.5rem' }}>
      <div style={{
        padding: '0.75rem 1rem', borderBottom: `1px solid ${DS.border}`,
        backgroundColor: DS.raised, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <IconChartLine size={16} strokeWidth={2} color={DS.txtP} />
        <span style={{ fontSize: '0.78rem', fontWeight: 700, color: DS.txtP }}>Metrics</span>
        <span style={{ fontSize: '0.68rem', color: DS.txtS, marginLeft: 'auto' }}>
          {history.length} samples{span ? ` · last ${span}` : ''}
        </span>
      </div>
      <div style={{ padding: '1rem', display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12 }}>
        <MetricCard label="CPU avg"  values={cpuVals}  color="#60a5fa" width={140} />
        <MetricCard label="Memory"   values={memVals}  color="#a78bfa" width={140} />
        <MetricCard label="Disk"     values={diskVals} color="#34d399" width={140} />
        <div style={{
          backgroundColor: DS.raised, border: `1px solid ${DS.border}`,
          borderRadius: 8, padding: '10px 14px',
          display: 'flex', flexDirection: 'column' as const, justifyContent: 'center', alignItems: 'center', gap: 4,
        }}>
          <span style={{ fontSize: '0.68rem', fontWeight: 700, color: DS.txtS, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Alerts</span>
          <span style={{
            fontSize: '1.6rem', fontWeight: 800, lineHeight: 1,
            color: totalAlerts > 0 ? '#fb923c' : '#34d399',
          }}>{totalAlerts}</span>
          <span style={{ fontSize: '0.62rem', color: DS.txtS }}>in window</span>
        </div>
      </div>
    </div>
  )
}

// ── Synthetic Monitors section ────────────────────────────────────────────────

function SyntheticMonitorsSection() {
  const [monitors, setMonitors]           = useState<SyntheticMonitor[]>([])
  const [loading, setLoading]             = useState(true)
  const [error, setError]                 = useState<string | null>(null)
  const [expanded, setExpanded]           = useState(true)
  const [showModal, setShowModal]         = useState(false)
  const [editingMonitor, setEditingMonitor] = useState<SyntheticMonitor | null>(null)
  const [outputModal, setOutputModal]     = useState<{ name: string; output: string } | null>(null)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const [deletingId, setDeletingId]       = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const res = await listSyntheticMonitors()
      setMonitors(res.data)
    } catch {
      setError('Failed to load synthetic monitors')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleDelete = async (id: string) => {
    if (confirmDeleteId !== id) { setConfirmDeleteId(id); return }
    setDeletingId(id)
    try {
      await deleteSyntheticMonitor(id)
      setMonitors(prev => prev.filter(m => m.id !== id))
      setConfirmDeleteId(null)
    } catch {
      setError('Failed to delete monitor')
    } finally {
      setDeletingId(null)
    }
  }

  const handleToggle = async (mon: SyntheticMonitor) => {
    try {
      await updateSyntheticMonitor(mon.id, { enabled: !mon.enabled })
      await load()
    } catch {
      setError('Failed to update monitor')
    }
  }

  const dotColor = (status: string | null, enabled: boolean) => {
    if (!enabled) return DS.txtS
    if (status === 'pass') return '#10b981'
    if (status === 'fail') return '#ef4444'
    if (status === 'error') return '#f59e0b'
    return DS.txtS
  }

  const headerRight = (
    <button
      onClick={() => { setEditingMonitor(null); setShowModal(true) }}
      style={{ ...compactBtn, backgroundColor: '#252c3c', border: '1px solid rgba(64, 112, 160, 0.40)', color: '#a0c4e8' }}
    >
      <IconPlus size={12} />
      New Monitor
    </button>
  )

  return (
    <>
      <div style={sectionCard}>
        <SectionHeader
          title="Synthetic Transaction Monitoring"
          subtitle="Synthetic transactions for web applications — upload a HAR file to auto-generate a Python replay script"
          expanded={expanded}
          onToggle={() => setExpanded(x => !x)}
          right={headerRight}
        />
        {expanded && (
          <div style={sectionBody}>
            {error && <ErrorBanner message={error} onClose={() => setError(null)} />}

            {loading ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[...Array(2)].map((_, i) => <SkeletonRow key={i} height={44} />)}
              </div>
            ) : monitors.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '2rem 0' }}>
                <p style={{ fontSize: '0.85rem', color: DS.txtS }}>No synthetic monitors configured</p>
                <p style={{ fontSize: '0.72rem', color: DS.txtS, marginTop: 4, opacity: 0.7 }}>
                  Upload a HAR file to generate a replay script in seconds
                </p>
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                {monitors.map(mon => {
                  const isConfirming = confirmDeleteId === mon.id
                  const isDeleting   = deletingId === mon.id

                  return (
                    <div
                      key={mon.id}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 12,
                        padding: '9px 12px', borderRadius: 7,
                        backgroundColor: mon.enabled ? DS.raised : 'transparent',
                        border: `1px solid ${mon.enabled ? DS.border : 'transparent'}`,
                        opacity: mon.enabled ? 1 : 0.55,
                      }}
                    >
                      {/* Status dot */}
                      <div style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, backgroundColor: dotColor(mon.last_status, mon.enabled) }} />

                      {/* SYNTH badge */}
                      <div style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        padding: '2px 8px', borderRadius: 4, flexShrink: 0,
                        fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.04em',
                        color: '#a855f7', backgroundColor: 'rgba(168,85,247,0.10)',
                        border: '1px solid rgba(168,85,247,0.35)',
                      }}>
                        <IconChartLine size={11} color="#a855f7" />
                        SYNTH
                      </div>

                      {/* Name + HAR filename */}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ fontSize: '0.85rem', color: DS.txtP }}>{mon.name}</span>
                        {mon.har_filename && (
                          <span style={{ marginLeft: 10, fontSize: '0.72rem', color: DS.txtS }}>
                            {mon.har_filename}
                          </span>
                        )}
                      </div>

                      {/* Schedule chip */}
                      <span style={{
                        flexShrink: 0, fontSize: '0.72rem', color: DS.txtS,
                        padding: '1px 7px', borderRadius: 4,
                        backgroundColor: DS.bg, border: `1px solid ${DS.border}`,
                      }}>
                        every {mon.schedule_mins}m
                      </span>

                      {/* Last output */}
                      {mon.last_output && (
                        <button
                          style={compactBtn}
                          onClick={() => setOutputModal({ name: mon.name, output: mon.last_output! })}
                        >
                          Log
                        </button>
                      )}

                      {/* Enabled toggle */}
                      <button
                        title={mon.enabled ? 'Disable' : 'Enable'}
                        onClick={() => handleToggle(mon)}
                        style={{
                          width: 32, height: 18, borderRadius: 9, border: 'none', cursor: 'pointer', flexShrink: 0,
                          backgroundColor: mon.enabled ? DS.mutedGreen : DS.border, transition: 'background 0.2s',
                          position: 'relative',
                        }}
                      >
                        <span style={{
                          position: 'absolute', top: 2, left: mon.enabled ? 14 : 2,
                          width: 12, height: 12, borderRadius: '50%',
                          backgroundColor: '#fff', transition: 'left 0.2s',
                        }} />
                      </button>

                      {/* Actions */}
                      <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                        <button
                          onClick={() => { setEditingMonitor(mon); setShowModal(true) }}
                          title="Edit"
                          style={{ background: 'none', border: 'none', color: DS.txtS, cursor: 'pointer', padding: 4, borderRadius: 4, display: 'flex' }}
                        >
                          <IconPencil size={14} />
                        </button>
                        <button
                          onClick={() => handleDelete(mon.id)}
                          disabled={isDeleting}
                          title={isConfirming ? 'Click again to confirm' : 'Delete'}
                          style={{
                            background: isConfirming ? 'rgba(239,68,68,0.12)' : 'none',
                            border: isConfirming ? '1px solid rgba(239,68,68,0.4)' : 'none',
                            color: isConfirming ? '#ef4444' : DS.txtS,
                            cursor: isDeleting ? 'wait' : 'pointer',
                            padding: isConfirming ? '2px 8px' : 4, borderRadius: 4,
                            display: 'flex', alignItems: 'center', gap: 4,
                            fontSize: '0.72rem', fontWeight: isConfirming ? 600 : 400,
                            whiteSpace: 'nowrap' as const,
                          }}
                        >
                          {isDeleting ? '…' : isConfirming ? 'Delete?' : <IconTrash size={14} />}
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}
      </div>

      {showModal && (
        <MonitorModal
          monitor={editingMonitor}
          onClose={() => { setShowModal(false); load() }}
        />
      )}

      {outputModal && (
        <div
          style={{ position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
          onClick={() => setOutputModal(null)}
        >
          <div
            style={{ backgroundColor: DS.surface, border: `1px solid ${DS.border}`, borderRadius: 12, width: 700, maxWidth: '95vw', maxHeight: '85vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
            onClick={e => e.stopPropagation()}
          >
            <div style={{ padding: '1rem 1.25rem', borderBottom: `1px solid ${DS.border}`, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontWeight: 700, color: DS.txtP }}>{outputModal.name} — Last Output</span>
              <button style={{ background: 'none', border: 'none', color: DS.txtS, cursor: 'pointer', display: 'flex' }} onClick={() => setOutputModal(null)}>
                <IconX size={16} />
              </button>
            </div>
            <pre style={{ padding: '1.25rem', margin: 0, overflowY: 'auto', fontSize: '0.78rem', lineHeight: 1.6, color: DS.txtM, backgroundColor: DS.bg, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {outputModal.output}
            </pre>
          </div>
        </div>
      )}
    </>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MonitoringSetup() {
  const [watchers, setWatchers] = useState<WatcherInfo[]>([])
  const [selectedWatcher, setSelectedWatcher] = useState<string>('')

  const [loadingWatchers, setLoadingWatchers] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showRegister, setShowRegister] = useState(false)

  const platformUrl = window.location.origin

  const loadWatchers = useCallback(async () => {
    try {
      const res = await listWatchers()
      const list = res.data as WatcherInfo[]
      setWatchers(list)
      if (!selectedWatcher || !list.find(w => w.watcher_name === selectedWatcher)) {
        const first = list.find(w => w.registration_status === 'approved') ?? list[0]
        if (first) setSelectedWatcher(first.watcher_name)
      }
    } catch {
      setError('Failed to load watchers')
    } finally {
      setLoadingWatchers(false)
    }
  }, [selectedWatcher])

  useEffect(() => { loadWatchers() }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // Poll while any watcher is pending (re-check every 5 s)
  useEffect(() => {
    const hasPending = watchers.some(w => w.registration_status === 'pending')
    if (!hasPending) return
    const t = setTimeout(loadWatchers, 5000)
    return () => clearTimeout(t)
  }, [watchers, loadWatchers])

  const selected = watchers.find(w => w.watcher_name === selectedWatcher)

  return (
    <div style={{ maxWidth: 1140, margin: '0 auto', padding: '0 1rem' }}>

      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ fontSize: '1.6rem', fontWeight: 700, color: DS.txtP, margin: 0, marginBottom: 4 }}>
            Monitoring Setup
          </h1>
          <p style={{ fontSize: '0.85rem', color: DS.txtM, margin: 0 }}>
            Manage watcher agents and configure thresholds pushed to each instance
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button onClick={loadWatchers} title="Refresh" style={{ ...secondaryBtn, padding: '6px 12px' }}>
            <IconRefresh size={14} />
          </button>
          <button onClick={() => setShowRegister(true)} style={{ ...primaryBtn, padding: '6px 14px', gap: 6 }}>
            <IconPlus size={14} /> Register Watcher
          </button>
        </div>
      </div>

      {error && <ErrorBanner message={error} onClose={() => setError(null)} />}

      {/* Watcher registry table */}
      {loadingWatchers ? (
        <div style={{ height: 120, backgroundColor: DS.surface, borderRadius: 10, border: `1px solid ${DS.border}`, marginBottom: '1.75rem' }} />
      ) : watchers.length === 0 ? (
        <div style={{
          textAlign: 'center', padding: '3.5rem 0',
          backgroundColor: DS.surface, border: `1px solid ${DS.border}`,
          borderRadius: 10, marginBottom: '1.75rem',
        }}>
          <IconRadar size={38} color={DS.txtS} />
          <p style={{ fontSize: '0.9rem', color: DS.txtM, marginTop: '1rem', marginBottom: 4 }}>No Watchers Registered</p>
          <p style={{ fontSize: '0.78rem', color: DS.txtS, marginBottom: '1rem' }}>
            Click <strong style={{ color: DS.txtP }}>Register Watcher</strong> to deploy one — it auto-appears here as pending.
          </p>
          <button onClick={() => setShowRegister(true)} style={{ ...primaryBtn, padding: '7px 18px', gap: 6 }}>
            <IconPlus size={14} /> Register Watcher
          </button>
        </div>
      ) : (
        <WatcherTable
          watchers={watchers}
          selectedWatcher={selectedWatcher}
          onSelect={setSelectedWatcher}

          onRefresh={loadWatchers}
        />
      )}

      {/* Config sections — click a row to select; only approved */}
      {selected && selected.registration_status === 'approved' && (
        <>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10, marginBottom: '1rem',
            paddingBottom: '0.75rem', borderBottom: `1px solid ${DS.border}`,
          }}>
            <IconServer size={16} color={DS.accent} />
            <span style={{ fontSize: '0.9rem', fontWeight: 600, color: DS.txtP }}>
              Configuring: {selected.display_name || selected.watcher_name}
            </span>
            <EnvBadge env={selected.environment} adapter={selected.adapter_mode} />
            {selected.watcher_id && (
              <span title={`ID: ${selected.watcher_id}`} style={{
                fontSize: '0.65rem', color: DS.txtS, fontFamily: 'monospace',
                padding: '1px 6px', borderRadius: 3, backgroundColor: DS.raised,
                border: `1px solid ${DS.border}`, cursor: 'default', userSelect: 'all' as const,
              }}>
                {selected.watcher_id.slice(0, 8)}…
              </span>
            )}
          </div>
          <WatcherMetricsDashboard watcher={selected} />

          <ContainerMonitoringSection watcherName={selected.watcher_name} />
          <ExternalChecksSection watcherName={selected.watcher_name} />
          <SyntheticMonitorsSection />
          <LogMonitorsSetup watcherName={selected.watcher_name} />
        </>
      )}

      {showRegister && (
        <RegisterModal platformUrl={platformUrl} onClose={() => { setShowRegister(false); loadWatchers() }} />
      )}
    </div>
  )
}
