import { useState, useEffect, useRef } from 'react'
import {
  getRunbook, createRunbook, updateRunbook, getApprovedActions,
  publishRunbook, setRunbookEnabled, getRunbookVersions, restoreRunbookVersion,
  listNotificationTeams,
  type VersionEntry,
} from '../services/api'
import {
  IconArrowLeft,
  IconCheck,
  IconAlertTriangle,
  IconBook,
  IconLoader2,
  IconSitemap,
  IconHistory,
  IconRefresh,
} from './icons'

// ─── Types ────────────────────────────────────────────────────────────────────

interface Step {
  description: string
  tool?: string
  args?: Record<string, any>
  command?: string           // actual command/invocation that runs (editable, auto-populated from template)
  metric?: string
  check?: string
  value?: string | number
}

interface RunbookForm {
  name: string
  description: string
  event_type: string
  service: string
  environment: string
  platform: string
  diagnostics: Step[]
  actions: Step[]
  verification_steps: Step[]
  confidence: number
  blast_radius: number
  enabled: boolean
}

interface RunbookEditorProps {
  runbookId?: string
  darkMode?: boolean
  onSave: () => void
  onCancel: () => void
}

// ─── Constants ────────────────────────────────────────────────────────────────

const PLATFORMS: { value: string; label: string; hint: string }[] = [
  { value: 'any',        label: 'Any platform',    hint: 'Matches all resource types (default)' },
  { value: 'docker',     label: 'Docker',           hint: 'Containerised workloads — docker exec / docker restart' },
  { value: 'linux',      label: 'Linux / SSH',      hint: 'Bare-metal or VM hosts via SSH + systemctl' },
  { value: 'windows',    label: 'Windows / WinRM',  hint: 'Windows hosts via WinRM Invoke-Command' },
  { value: 'kubernetes', label: 'Kubernetes',        hint: 'K8s clusters — kubectl commands' },
]

// ─── Tool Catalog ─────────────────────────────────────────────────────────────

import { interpolateCommand, formatStepParams } from '../utils/runbookUtils'
export type { ToolParam, ToolDef } from '../utils/runbookUtils'
export { interpolateCommand, formatStepParams }

// ─── Taxonomy combobox (mirrors sidebar EventTypeCombobox) ────────────────────

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
        className="field-input w-full"
        style={{ fontFamily: 'monospace', fontSize: 12 }}
        value={query}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={e => {
          if (e.key === 'Enter')  { onChange(query.trim()); setOpen(false) }
          if (e.key === 'Escape') setOpen(false)
        }}
        placeholder="Search event types…"
        spellCheck={false}
        autoComplete="off"
      />
      {open && filtered.length > 0 && (
        <div style={{
          position: 'absolute', zIndex: 9999, left: 0, right: 0,
          top: 'calc(100% + 2px)', background: '#252c3c',
          border: '1px solid #3d4557', borderRadius: 6,
          maxHeight: 240, overflowY: 'auto', boxShadow: '0 8px 24px rgba(0,0,0,.6)',
        }}>
          {filtered.map(opt => (
            <div key={opt.code}
              onMouseDown={() => { setQuery(opt.code); onChange(opt.code); setOpen(false) }}
              style={{ padding: '6px 10px', cursor: 'pointer', borderBottom: '1px solid #3d4557' }}
              className="hover:bg-slate-700/40"
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

// ─── Notification team combobox (for the `team` arg on notify/alert_* steps) ──
// Free-text autocomplete, not a strict select: the value is only resolved
// against the live registry at execution time (case-insensitive), and
// falls back to the default channels if it doesn't match — so an
// unrecognised or not-yet-created name is still a valid, if no-op, entry.

function NotificationTeamCombobox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [options, setOptions] = useState<{ name: string; enabled: boolean }[]>([])
  const [query,   setQuery]   = useState(value)
  const [open,    setOpen]    = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    listNotificationTeams()
      .then(res => setOptions((res.data || []).map(t => ({ name: t.name, enabled: t.enabled }))))
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
  const filtered = (q ? options.filter(o => o.name.toLowerCase().includes(q)) : options).slice(0, 12)

  return (
    <div ref={wrapRef} style={{ position: 'relative', flex: 1 }}>
      <input
        className="field-input w-full text-sm py-1"
        value={query}
        onChange={e => { setQuery(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={e => {
          if (e.key === 'Enter')  { onChange(query.trim()); setOpen(false) }
          if (e.key === 'Escape') setOpen(false)
        }}
        placeholder="Team name — leave blank for default channels"
        spellCheck={false}
        autoComplete="off"
      />
      {open && (filtered.length > 0 || options.length === 0) && (
        <div style={{
          position: 'absolute', zIndex: 9999, left: 0, right: 0,
          top: 'calc(100% + 2px)', background: '#252c3c',
          border: '1px solid #3d4557', borderRadius: 6,
          maxHeight: 200, overflowY: 'auto', boxShadow: '0 8px 24px rgba(0,0,0,.6)',
        }}>
          {options.length === 0 ? (
            <div style={{ padding: '8px 10px', fontSize: 11, color: '#6b7a93' }}>
              No notification teams configured yet (Settings → Notification Teams)
            </div>
          ) : filtered.map(opt => (
            <div key={opt.name}
              onMouseDown={() => { setQuery(opt.name); onChange(opt.name); setOpen(false) }}
              style={{ padding: '6px 10px', cursor: 'pointer', borderBottom: '1px solid #1a2030', display: 'flex', alignItems: 'center', gap: 6 }}
              className="hover:bg-slate-700/40"
            >
              <span style={{ fontSize: 11, color: '#cbd5e1' }}>{opt.name}</span>
              {!opt.enabled && (
                <span style={{ fontSize: 9, color: '#f59e0b' }}>disabled</span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const BLANK_FORM: RunbookForm = {
  name: '',
  description: '',
  event_type: 'service_down',
  service: '',
  environment: '',
  platform: 'any',
  diagnostics: [],
  actions: [],
  verification_steps: [],
  confidence: 0.85,
  blast_radius: 1,
  enabled: true,
}

// ─── Main Component ───────────────────────────────────────────────────────────

/** Convert a backend approved_action record into a ToolDef for the editor. */
function approvedActionToToolDef(a: any): ToolDef {
  const variantKeys = a.command_variants ? Object.keys(a.command_variants).filter(k => k !== 'any') : []
  const VALID_TYPES = ['text', 'number', 'boolean', 'select', 'tags']
  return {
    tool:             a.tool_name,
    label:            a.name,
    description:      a.description || '',
    commandTemplate:  a.command || undefined,
    commandVariants:  a.command_variants || undefined,
    platforms:        variantKeys.length > 0 ? variantKeys : undefined,
    // DB params use { name, type, required, default, description }
    // ToolParam expects { key, label, type, required, default, hint }
    params: Array.isArray(a.parameters)
      ? a.parameters.map((p: any): ToolParam => ({
          key:      p.name || p.key || '',
          label:    p.name || p.label || p.key || '',
          type:     (VALID_TYPES.includes(p.type) ? p.type : 'text') as ToolParam['type'],
          hint:     p.description || undefined,
          default:  p.default !== undefined ? p.default : undefined,
          required: p.required || false,
        }))
      : [],
  }
}

export default function RunbookEditor({ runbookId, onSave, onCancel }: RunbookEditorProps) {
  const [form, setForm] = useState<RunbookForm>(BLANK_FORM)
  const [loading, setLoading] = useState(!!runbookId)
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const isEdit = !!runbookId

  // Draft/publish status — separate from `form` since these aren't editable
  // fields, just the runbook's current lifecycle state.
  const [status, setStatus] = useState<'draft' | 'published'>('draft')
  const [hasUnpublishedChanges, setHasUnpublishedChanges] = useState(false)
  const [togglingEnabled, setTogglingEnabled] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [versions, setVersions] = useState<VersionEntry[]>([])
  const [versionsLoading, setVersionsLoading] = useState(false)
  // Non-blocking lint from the publish endpoint — e.g. a path with no validation
  // step. Shown as a dismissible notice; never prevents publishing.
  const [publishWarnings, setPublishWarnings] = useState<string[]>([])

  // DB-backed tools fetched from /api/approved-actions
  const [dbDiagnosticTools, setDbDiagnosticTools] = useState<ToolDef[]>([])
  const [dbActionTools, setDbActionTools]         = useState<ToolDef[]>([])

  useEffect(() => {
    getApprovedActions(true).then(res => {
      const actions: any[] = res.data ?? []
      setDbDiagnosticTools(
        actions
          .filter(a => a.category === 'diagnostic')
          .map(approvedActionToToolDef)
      )
      setDbActionTools(
        actions
          .filter(a => a.category !== 'diagnostic')
          .map(approvedActionToToolDef)
      )
    }).catch(() => { /* silently ignore — static tools still work */ })
  }, [])

  useEffect(() => {
    if (!runbookId) return
    getRunbook(runbookId)
      .then(res => {
        const d = res.data as any
        setForm({
          name: d.name || '',
          description: d.description || '',
          event_type: d.event_type || 'service_down',
          service: d.service || '',
          environment: d.environment || '',
          platform: d.platform || 'any',
          diagnostics: d.diagnostics || [],
          actions: d.actions || [],
          verification_steps: d.verification_steps || [],
          confidence: d.confidence ?? 0.85,
          blast_radius: d.blast_radius ?? 1,
          enabled: d.enabled ?? true,
        })
        setStatus(d.status === 'published' ? 'published' : 'draft')
        setHasUnpublishedChanges(!!d.has_unpublished_changes)
      })
      .catch(() => setError('Failed to load runbook'))
      .finally(() => setLoading(false))
  }, [runbookId])

  // `enabled` is an instant kill-switch — applied the moment it's toggled,
  // independent of Save Draft / Publish. Only meaningful once the runbook
  // exists; a brand-new unsaved runbook just updates local form state.
  const handleToggleEnabled = async () => {
    const next = !form.enabled
    if (!isEdit) { set('enabled', next); return }
    setTogglingEnabled(true)
    try {
      await setRunbookEnabled(runbookId!, next)
      set('enabled', next)
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to update enabled state')
    } finally {
      setTogglingEnabled(false)
    }
  }

  const handleSaveDraft = async () => {
    if (!form.name.trim()) { setError('Name is required'); return }
    if (!form.event_type) { setError('Event type is required'); return }
    setSaving(true)
    setError(null)
    try {
      // `enabled` is handled instantly via handleToggleEnabled — excluded here
      // so Save Draft never has a live side effect.
      const { enabled: _enabled, ...rest } = form
      const payload: Record<string, any> = {
        ...rest,
        service: form.service || null,
        environment: form.environment || null,
      }
      if (isEdit) {
        const res = await updateRunbook(runbookId!, payload)
        setHasUnpublishedChanges(!!res.data.has_unpublished_changes)
      } else {
        await createRunbook(payload)
        onSave()
        return
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to save runbook')
    } finally {
      setSaving(false)
    }
  }

  const handlePublish = async () => {
    setPublishing(true)
    setError(null)
    setPublishWarnings([])
    try {
      const res = await publishRunbook(runbookId!)
      setStatus('published')
      setHasUnpublishedChanges(false)
      setForm(prev => ({ ...prev, ...res.data }))
      const warnings = res.data.warnings
      if (warnings && warnings.length > 0) {
        // Keep the editor open so the warning is actually seen — publishing
        // still succeeded, this is advisory only.
        setPublishWarnings(warnings)
      } else {
        onSave()
      }
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Failed to publish runbook')
    } finally {
      setPublishing(false)
    }
  }

  const loadVersions = async () => {
    if (!isEdit) return
    setVersionsLoading(true)
    try {
      const res = await getRunbookVersions(runbookId!)
      setVersions(res.data)
    } catch {
      setError('Failed to load version history')
    } finally {
      setVersionsLoading(false)
    }
  }

  const handleRestoreVersion = async (version: number) => {
    if (!isEdit) return
    try {
      const res = await restoreRunbookVersion(runbookId!, version)
      const d = res.data as any
      setForm(prev => ({
        ...prev,
        name: d.name, description: d.description || '', event_type: d.event_type,
        service: d.service || '', environment: d.environment || '', platform: d.platform || 'any',
        diagnostics: d.diagnostics || [], actions: d.actions || [], verification_steps: d.verification_steps || [],
        confidence: d.confidence ?? prev.confidence, blast_radius: d.blast_radius ?? prev.blast_radius,
      }))
      setHasUnpublishedChanges(true)
      setShowVersions(false)
    } catch {
      setError('Failed to restore version')
    }
  }

  const set = <K extends keyof RunbookForm>(key: K, value: RunbookForm[K]) =>
    setForm(prev => ({ ...prev, [key]: value }))

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <IconLoader2 size={28} className="animate-spin text-info-500" />
      </div>
    )
  }

  return (
    <div className="page-transition-enter max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <button onClick={onCancel} className="btn btn-secondary flex items-center gap-2">
          <IconArrowLeft size={16} />
          Back
        </button>
        <div className="flex-1">
          <h2 className="text-section-title" style={{ color: '#e8eef5' }}>
            {isEdit ? 'Edit Runbook' : 'New Runbook'}
          </h2>
          <p className="text-sm mt-0.5" style={{ color: '#a0aec0' }}>
            Define the automated remediation procedure for this incident type
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isEdit && (
            <span
              className="text-xs font-medium px-2 py-0.5 rounded-full"
              style={
                status === 'published'
                  ? { background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)', color: '#10b981' }
                  : { background: 'rgba(148,163,184,0.1)', border: '1px solid rgba(148,163,184,0.3)', color: '#94a3b8' }
              }
            >
              {status === 'published' ? 'Published' : 'Draft'}
            </span>
          )}
          {isEdit && (
            <button
              onClick={() => { setShowVersions(v => !v); if (!showVersions) loadVersions() }}
              className="btn btn-secondary flex items-center gap-2"
              title="View version history"
            >
              <IconHistory size={16} />
              History
            </button>
          )}
          <button onClick={onCancel} className="btn btn-secondary">Cancel</button>
          <button onClick={handleSaveDraft} disabled={saving} className="btn btn-secondary flex items-center gap-2">
            {saving ? <IconLoader2 size={16} className="animate-spin" /> : <IconCheck size={16} />}
            {saving ? 'Saving…' : 'Save Draft'}
          </button>
          {isEdit && (
            <button
              onClick={handlePublish}
              disabled={publishing || (status === 'published' && !hasUnpublishedChanges)}
              className="btn btn-primary flex items-center gap-2"
              title={status === 'published' && !hasUnpublishedChanges ? 'No pending changes to publish' : 'Make the current draft live'}
            >
              {publishing ? <IconLoader2 size={16} className="animate-spin" /> : <IconRefresh size={16} />}
              {publishing ? 'Publishing…' : 'Publish'}
            </button>
          )}
        </div>
      </div>

      {/* Unpublished-changes notice — separate from the status badge above so
          "currently live" and "has pending edits" don't get conflated into one
          confusing label. The engine keeps using the last-published content
          the whole time this is showing; Save Draft never changes that. */}
      {isEdit && hasUnpublishedChanges && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm mb-6"
          style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', color: '#f59e0b' }}>
          <IconAlertTriangle size={15} className="flex-shrink-0" />
          <span>You have draft changes that aren't live yet — the engine is still running the last published version. Click Publish to make these changes live.</span>
        </div>
      )}

      {/* Publish lint warnings — advisory only, publish already succeeded */}
      {publishWarnings.length > 0 && (
        <div className="flex items-start gap-2 px-4 py-2 rounded-lg text-sm mb-6"
          style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', color: '#f59e0b' }}>
          <IconAlertTriangle size={15} className="flex-shrink-0 mt-0.5" />
          <div className="flex-1">
            <div className="font-medium mb-1">Published — but with a path that can't auto-resolve:</div>
            {publishWarnings.map((w, i) => <div key={i} className="text-xs opacity-90">{w}</div>)}
          </div>
          <button onClick={() => setPublishWarnings([])} className="text-xs underline flex-shrink-0">Dismiss</button>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 border border-slate-600 text-sm mb-6" style={{ color: '#a0aec0' }}>
          <IconAlertTriangle size={15} className="text-warning-500 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="space-y-6">

        {/* ── Section 1: Basic Info ── */}
        <Section icon={<IconBook size={18} />} title="Basic Information">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="md:col-span-2">
              <label className="field-label">Name *</label>
              <input
                className="field-input w-full"
                placeholder="e.g. Backend High CPU Recovery"
                value={form.name}
                onChange={e => set('name', e.target.value)}
              />
            </div>
            <div className="md:col-span-2">
              <label className="field-label">Description</label>
              <textarea
                className="field-input w-full resize-none"
                rows={2}
                placeholder="When to use this runbook…"
                value={form.description}
                onChange={e => set('description', e.target.value)}
              />
            </div>
            <div>
              <label className="field-label">Event Type *</label>
              <EventTypeCombobox value={form.event_type} onChange={v => set('event_type', v)} />
            </div>
            <div>
              <label className="field-label">
                Platform
                <span className="ml-1.5 text-xs font-normal" style={{ color: '#64748b' }}>
                  — selects correct runbook per OS
                </span>
              </label>
              <select
                className="field-input w-full"
                value={form.platform}
                onChange={e => set('platform', e.target.value)}
              >
                {PLATFORMS.map(p => (
                  <option key={p.value} value={p.value}>{p.label}</option>
                ))}
              </select>
              {form.platform !== 'any' && (
                <p className="mt-1 text-xs" style={{ color: '#64748b' }}>
                  {PLATFORMS.find(p => p.value === form.platform)?.hint}
                </p>
              )}
            </div>
            <div>
              <label className="field-label">Service <span className="text-gray-500">(optional)</span></label>
              <input
                className="field-input w-full"
                placeholder="blank = catch-all"
                value={form.service}
                onChange={e => set('service', e.target.value)}
              />
            </div>
            <div>
              <label className="field-label">Risk Level</label>
              <select
                className="field-input w-full"
                value={form.blast_radius}
                onChange={e => set('blast_radius', parseInt(e.target.value))}
              >
                <option value={1}>1 — Safe (read-only)</option>
                <option value={2}>2 — Low (graceful ops)</option>
                <option value={3}>3 — Moderate (restart/scale)</option>
                <option value={4}>4 — High (data impact)</option>
                <option value={5}>5 — Critical (destructive)</option>
              </select>
              <p className="mt-1 text-xs" style={{ color: '#64748b' }}>Governs approval requirements in policy rules</p>
            </div>
          </div>

          {/* Enabled toggle (instant kill-switch — bypasses draft/publish) + open visual editor */}
          <div className="flex items-center gap-3 mt-4 pt-4 border-t border-slate-700/50">
            <button
              onClick={handleToggleEnabled}
              disabled={togglingEnabled}
              className={`relative w-10 h-6 rounded-full transition-colors flex-shrink-0 ${form.enabled ? 'bg-info-500' : 'bg-slate-600'}`}
            >
              <span className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white shadow transition-transform ${form.enabled ? 'translate-x-4' : ''}`} />
            </button>
            <span className="text-sm flex-1" style={{ color: '#a0aec0' }}>
              {form.enabled ? 'Runbook is active — engine will use it' : 'Runbook is disabled — engine will skip it'}
              {isEdit && <span className="ml-1.5 text-xs" style={{ color: '#64748b' }}>(applies immediately)</span>}
            </span>
            {isEdit && (
              <button
                onClick={() => window.open(`/editor/?id=${runbookId}`, '_blank', 'noopener')}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors"
                style={{ background: 'rgba(16,185,129,0.1)', border: '1px solid rgba(16,185,129,0.3)', color: '#10b981' }}
                title="Open in Visual Workflow Editor"
              >
                <IconSitemap size={14} />
                Open in Visual Editor
              </button>
            )}
          </div>
        </Section>

        {/* Version history — kept at the bottom, out of the way of the main
            editing flow; only relevant once you're checking past published state. */}
        {isEdit && showVersions && (
          <div className="metric-card">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: '#e8eef5' }}>Version History</h3>
              {versionsLoading && <IconLoader2 size={14} className="animate-spin text-info-500" />}
            </div>
            {versions.length === 0 && !versionsLoading && (
              <p className="text-sm" style={{ color: '#64748b' }}>No published versions yet — publish a draft to create the first one.</p>
            )}
            <div className="space-y-2">
              {versions.map(v => (
                <div key={v.version} className="flex items-center justify-between px-3 py-2 rounded-lg" style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557' }}>
                  <div className="text-sm" style={{ color: '#a0aec0' }}>
                    <span className="font-medium" style={{ color: '#e8eef5' }}>v{v.version}</span>
                    {' — '}{new Date(v.created_at).toLocaleString()}
                    {v.change_note && <span className="ml-2 italic">"{v.change_note}"</span>}
                  </div>
                  <button
                    onClick={() => handleRestoreVersion(v.version)}
                    className="text-xs px-2 py-1 rounded-lg border border-slate-600 hover:border-info-500/50 hover:text-info-400 transition-colors"
                    style={{ color: '#a0aec0' }}
                  >
                    Restore to draft
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  )
}

// ─── Section wrapper ──────────────────────────────────────────────────────────

function Section({
  icon, title, description, badge, children,
}: {
  icon: React.ReactNode
  title: string
  description?: string
  badge?: number
  children: React.ReactNode
}) {
  return (
    <div className="metric-card">
      <div className="flex items-center gap-3 mb-4 pb-3 border-b border-slate-700/50">
        <span className="text-info-500">{icon}</span>
        <div className="flex-1">
          <h3 className="text-sm font-semibold" style={{ color: '#e8eef5' }}>{title}</h3>
          {description && <p className="text-xs mt-0.5" style={{ color: '#7a8ba3' }}>{description}</p>}
        </div>
        {badge !== undefined && badge > 0 && (
          <span className="badge badge-low text-xs">{badge} step{badge !== 1 ? 's' : ''}</span>
        )}
      </div>
      {children}
    </div>
  )
}

// ─── Step List Builder ────────────────────────────────────────────────────────

function StepList({
  steps, onChange, type, platform, extraTools = [],
}: {
  steps: Step[]
  onChange: (steps: Step[]) => void
  type: 'diagnostic' | 'action' | 'verification'
  platform: string
  extraTools?: ToolDef[]
}) {
  const addStep = () => {
    const blank: Step =
      type === 'verification'
        ? { description: '', metric: '', check: 'less_than', value: '' }
        : { description: '', tool: '', args: {} }
    onChange([...steps, blank])
  }

  const updateStep = (i: number, updated: Step) => {
    const next = [...steps]
    next[i] = updated
    onChange(next)
  }

  const removeStep = (i: number) => onChange(steps.filter((_, idx) => idx !== i))

  return (
    <div className="space-y-3">
      {steps.map((step, i) => (
        <StepRow
          key={i}
          index={i}
          step={step}
          type={type}
          platform={platform}
          extraTools={extraTools}
          onChange={updated => updateStep(i, updated)}
          onRemove={() => removeStep(i)}
        />
      ))}

      <button
        onClick={addStep}
        className="w-full py-2 border border-dashed border-slate-600 rounded-lg text-sm text-gray-500 hover:border-info-500/50 hover:text-info-400 transition-colors flex items-center justify-center gap-2"
      >
        <IconPlus size={14} />
        Add {type === 'diagnostic' ? 'diagnostic step' : type === 'action' ? 'action' : 'verification check'}
      </button>
    </div>
  )
}

// ─── Individual Step Row ──────────────────────────────────────────────────────

function StepRow({
  index, step, type, platform, extraTools = [], onChange, onRemove,
}: {
  index: number
  step: Step
  type: 'diagnostic' | 'action' | 'verification'
  platform: string
  extraTools?: ToolDef[]
  onChange: (s: Step) => void
  onRemove: () => void
}) {
  const set = <K extends keyof Step>(key: K, value: Step[K]) =>
    onChange({ ...step, [key]: value })

  const [toolSearchQuery, setToolSearchQuery] = useState('')
  const toolInputRef = useRef<HTMLInputElement>(null)

  // Debug: log platform for this step
  useEffect(() => {
    if (step.tool && platform) {
      console.log(`[StepRow ${index}] tool="${step.tool}", platform="${platform}", type="${type}"`)
    }
  }, [step.tool, platform, index, type])

  // All tools come from the DB via extraTools (approved-actions API)
  const allTools = extraTools

  // Build a lookup map for this step row
  const allToolsMap: Record<string, ToolDef> = {}
  allTools.forEach(t => { allToolsMap[t.tool] = t })

  // Filter tools to only those that support the selected platform (or have no platform restriction)
  const toolList = allTools.filter(tool => {
    if (!tool.platforms || tool.platforms.length === 0) return true
    return tool.platforms.includes(platform) || tool.platforms.includes('any')
  })

  // Sort tools alphabetically by label and filter by search query
  const sortedToolList = [...toolList]
    .sort((a, b) => a.label.localeCompare(b.label))
    .filter(tool =>
      tool.label.toLowerCase().includes(toolSearchQuery.toLowerCase()) ||
      tool.tool.toLowerCase().includes(toolSearchQuery.toLowerCase())
    )

  const selectedToolDef = allToolsMap[step.tool || '']
  // isCustom: user chose the "Custom Tool" placeholder to enter raw JSON
  const isCustom = step.tool === 'custom'
  // isUnknown: tool came from DB but isn't in our catalog — show JSON editor without tool-key input
  const isUnknown = !!(step.tool && step.tool !== 'custom' && !allToolsMap[step.tool])

  // When the tool changes, reset args to defaults and populate command template
  const handleToolChange = (newTool: string) => {
    const def = allToolsMap[newTool]
    if (!def || newTool === 'custom') {
      onChange({ ...step, tool: newTool })
      return
    }
    const defaults: Record<string, any> = {}
    def.params.forEach(p => {
      if (p.default !== undefined) defaults[p.key] = p.default
      else if (p.autoResolved) defaults[p.key] = p.placeholder ?? ''
    })
    // Use platform-specific variant if available, fall back to base template
    const platformCommand = def.commandVariants?.[platform?.toLowerCase() ?? '']
    onChange({
      ...step,
      tool: newTool,
      args: defaults,
      command: platformCommand ?? def.commandTemplate ?? step.command ?? '',
    })
  }

  // Update a single arg key
  const setArg = (key: string, value: any) =>
    onChange({ ...step, args: { ...(step.args || {}), [key]: value } })

  if (type === 'verification') {
    return (
      <div className="flex gap-2 items-start p-3 rounded-lg group" style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557' }}>
        <div className="mt-2 text-gray-600 group-hover:text-gray-400 cursor-grab flex-shrink-0">
          <IconGripVertical size={16} />
        </div>
        <div className="flex-shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-xs font-semibold mt-1.5" style={{ backgroundColor: '#252c3c', color: '#a0aec0' }}>
          {index + 1}
        </div>
        <div className="flex-1 space-y-2">
          <input
            className="field-input w-full"
            placeholder="Description of this verification check…"
            value={step.description || ''}
            onChange={e => set('description', e.target.value)}
          />
          <div className="space-y-2">
            <label className="text-xs font-semibold uppercase tracking-wide" style={{ color: '#7a8ba3' }}>
              Verification Condition (optional)
              <span className="ml-1 font-normal text-gray-500">— checks metric after remediation</span>
            </label>
            <div className="grid grid-cols-3 gap-2">
              <div className="relative">
                <input
                  className="field-input w-full"
                  list="metric-suggestions"
                  placeholder="cpu_percent, memory_percent, latency_p95…"
                  value={step.metric || ''}
                  onChange={e => set('metric', e.target.value)}
                />
                <datalist id="metric-suggestions">
                  <option value="cpu_percent" />
                  <option value="memory_percent" />
                  <option value="disk_percent" />
                  <option value="error_rate" />
                  <option value="latency_p95" />
                  <option value="syscall_rate" />
                  <option value="connection_count" />
                  <option value="request_count" />
                  <option value="response_time" />
                </datalist>
              </div>
              <select
                className="field-input"
                value={step.check || 'less_than'}
                onChange={e => set('check', e.target.value)}
              >
                <optgroup label="Numeric">
                  <option value="less_than">&lt; Less than</option>
                  <option value="less_than_or_equal">≤ Less than or equal</option>
                  <option value="greater_than">&gt; Greater than</option>
                  <option value="greater_than_or_equal">≥ Greater than or equal</option>
                  <option value="equals">= Equals</option>
                  <option value="not_equals">≠ Not equals</option>
                </optgroup>
                <optgroup label="String">
                  <option value="contains">Contains</option>
                  <option value="not_contains">Does not contain</option>
                  <option value="starts_with">Starts with</option>
                  <option value="ends_with">Ends with</option>
                </optgroup>
              </select>
              <input
                className="field-input"
                type="text"
                placeholder={
                  ['contains','not_contains','starts_with','ends_with'].includes(step.check || '')
                    ? 'e.g. running, OK'
                    : 'e.g. 75'
                }
                value={String(step.value ?? '')}
                onChange={e => set('value', e.target.value)}
              />
            </div>
            <p className="text-xs" style={{ color: '#64748b' }}>
              Example: metric="disk_percent" check="&lt; Less than" value="75" → disk usage dropped below 75%. Use string checks (Contains / Starts with) for status fields. Leave blank to skip.
            </p>
          </div>
        </div>
        <button onClick={onRemove} className="mt-1.5 p-1 rounded text-gray-600 hover:text-critical-400 hover:bg-critical-900/30 transition-colors flex-shrink-0" title="Remove">
          <IconTrash size={14} />
        </button>
      </div>
    )
  }

  return (
    <div className="flex gap-2 items-start p-3 rounded-lg bg-slate-900/60 border border-slate-700/50 group">
      {/* Drag handle */}
      <div className="mt-2 text-gray-600 group-hover:text-gray-400 cursor-grab flex-shrink-0">
        <IconGripVertical size={16} />
      </div>

      {/* Step number */}
      <div className="flex-shrink-0 w-6 h-6 rounded-full bg-slate-700 flex items-center justify-center text-xs font-semibold mt-1.5" style={{ color: '#a0aec0' }}>
        {index + 1}
      </div>

      {/* Fields */}
      <div className="flex-1 space-y-2">

        {/* Description */}
        <input
          className="field-input w-full"
          placeholder="Description of this step…"
          value={step.description || ''}
          onChange={e => set('description', e.target.value)}
        />

        {/* Tool selector — HTML5 datalist for native dropdown positioning */}
        <div className="relative">
          <input
            ref={toolInputRef}
            type="text"
            list={`tool-options-${type}-${index}`}
            className="field-input w-full pr-8"
            placeholder="Search tools…"
            value={toolSearchQuery !== '' ? toolSearchQuery : (selectedToolDef?.label || '')}
            onChange={e => {
              setToolSearchQuery(e.target.value)
            }}
            onBlur={() => {
              // If selection matches a tool, trigger the change
              const selected = sortedToolList.find(t => t.label === toolSearchQuery || t.tool === toolSearchQuery)
              if (selected && selected.tool !== step.tool) {
                handleToolChange(selected.tool)
              } else if (toolSearchQuery && !selected) {
                setToolSearchQuery('')
              } else if (!toolSearchQuery) {
                // Reset to selected tool's label when input is cleared
                setToolSearchQuery('')
              }
            }}
          />
          <datalist id={`tool-options-${type}-${index}`}>
            {sortedToolList.map(tool => (
              <option key={tool.tool} value={tool.label} data-tool={tool.tool}>
                {tool.description}
              </option>
            ))}
          </datalist>
          <IconChevronDown size={14} className="absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: '#7a8ba3' }} />
        </div>

        {/* Tool description */}
        {selectedToolDef && !isCustom && !isUnknown && (
          <p className="text-xs px-1" style={{ color: '#7a8ba3' }}>
            {selectedToolDef.description}
          </p>
        )}

        {/* Command field — shown for all tool/action steps */}
        {!isCustom && (
          <div>
            <label className="flex items-center gap-1.5 mb-1">
              <span className="text-xs font-semibold uppercase tracking-wide" style={{ color: '#7a8ba3' }}>Command</span>
              {selectedToolDef?.commandTemplate && (
                <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: '#0f2d1a', color: '#34d399', fontSize: '0.65rem' }}>
                  auto-filled · editable
                </span>
              )}
            </label>
            <input
              className="field-input w-full font-mono text-sm"
              placeholder={
                isUnknown
                  ? 'Command or invocation that runs this step…'
                  : (selectedToolDef
                    ? (() => {
                        // Use platform-specific variant if available, otherwise fall back to base template
                        const platformKey = platform?.toLowerCase() || ''
                        const variant = selectedToolDef.commandVariants?.[platformKey]
                        const template = variant || selectedToolDef.commandTemplate
                        if (variant) {
                          console.log(`Using variant for platform "${platform}" (${platformKey}): ${variant.substring(0, 50)}...`)
                        }
                        return template ? interpolateCommand(template, step.args || {}) : 'e.g. docker exec {container} kill -15 {process_name}'
                      })()
                    : 'e.g. docker exec {container} kill -15 {process_name}')
              }
              value={step.command ?? ''}
              onChange={e => set('command', e.target.value)}
              style={{ fontFamily: '"Monaco", "Consolas", "Courier New", monospace', fontSize: '0.78rem' }}
            />
          </div>
        )}

        {/* Structured parameter fields */}
        {selectedToolDef && !isCustom && !isUnknown && selectedToolDef.params.length > 0 && (
          <div className="rounded-lg p-3 space-y-2" style={{ backgroundColor: '#252c3c', border: '1px solid #3d4557' }}>
            <p className="text-xs font-medium uppercase tracking-wide mb-2" style={{ color: '#7a8ba3' }}>
              Parameters
            </p>
            {selectedToolDef.params.map(param => (
              <div key={param.key} className="grid grid-cols-5 gap-2 items-start">
                {/* Label */}
                <div className="col-span-2 flex items-center gap-1 pt-1.5">
                  <span className="text-xs font-medium" style={{ color: '#a0aec0' }}>
                    {param.label}
                    {param.required && <span className="text-critical-400 ml-0.5">*</span>}
                  </span>
                  {param.hint && (
                    <span title={param.hint}>
                      <IconInfoCircle size={11} style={{ color: '#7a8ba3' }} />
                    </span>
                  )}
                </div>

                {/* Input */}
                <div className="col-span-3 flex items-center gap-1.5">
                  {param.autoResolved && (
                    <span className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded font-mono" style={{ background: '#1e3a5f', color: '#60a5fa', border: '1px solid #2563eb44' }}>
                      auto
                    </span>
                  )}

                  {param.key === 'team' ? (
                    <NotificationTeamCombobox
                      value={step.args?.[param.key] ?? param.default ?? ''}
                      onChange={v => setArg(param.key, v)}
                    />
                  ) : param.type === 'select' ? (
                    <select
                      className="field-input flex-1 text-sm py-1"
                      value={step.args?.[param.key] ?? param.default ?? ''}
                      onChange={e => setArg(param.key, e.target.value)}
                    >
                      {param.options!.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  ) : param.type === 'boolean' ? (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => setArg(param.key, !(step.args?.[param.key] ?? param.default ?? false))}
                        className={`relative w-9 h-5 rounded-full transition-colors ${(step.args?.[param.key] ?? param.default ?? false) ? 'bg-info-500' : 'bg-slate-600'}`}
                      >
                        <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${(step.args?.[param.key] ?? param.default ?? false) ? 'translate-x-4' : ''}`} />
                      </button>
                      <span className="text-xs" style={{ color: '#7a8ba3' }}>
                        {(step.args?.[param.key] ?? param.default ?? false) ? 'Yes' : 'No'}
                      </span>
                    </div>
                  ) : param.type === 'tags' ? (
                    <input
                      className="field-input flex-1 text-sm py-1"
                      placeholder={param.placeholder ?? 'Comma-separated values'}
                      value={
                        Array.isArray(step.args?.[param.key])
                          ? step.args![param.key].join(', ')
                          : (step.args?.[param.key] ?? param.default ?? '')
                      }
                      onChange={e => {
                        const raw = e.target.value
                        // Store as array if comma-separated, else as string
                        const parts = raw.split(',').map(s => s.trim()).filter(Boolean)
                        setArg(param.key, parts.length > 1 ? parts : raw)
                      }}
                    />
                  ) : param.type === 'number' ? (
                    <input
                      type="number"
                      className="field-input flex-1 text-sm py-1"
                      placeholder={param.placeholder ?? String(param.default ?? '')}
                      value={step.args?.[param.key] ?? param.default ?? ''}
                      onChange={e => setArg(param.key, parseFloat(e.target.value) || 0)}
                    />
                  ) : (
                    <input
                      className="field-input flex-1 text-sm py-1"
                      placeholder={param.placeholder ?? ''}
                      value={step.args?.[param.key] ?? param.default ?? ''}
                      onChange={e => setArg(param.key, e.target.value)}
                    />
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Custom tool: user selects "Custom Tool" to enter tool key + raw JSON */}
        {isCustom && (
          <div className="space-y-2">
            <input
              className="field-input w-full font-mono text-sm"
              placeholder="Tool key (e.g. my_custom_tool)"
              value={''}
              onChange={e => set('tool', e.target.value || 'custom')}
            />
            <textarea
              className="field-input w-full font-mono text-sm resize-none"
              rows={3}
              placeholder='{"key": "value"}'
              value={step.args ? JSON.stringify(step.args, null, 2) : '{}'}
              onChange={e => {
                try { set('args', JSON.parse(e.target.value)) } catch { /* ignore invalid JSON while typing */ }
              }}
            />
          </div>
        )}

        {/* Unknown tool (loaded from DB, not in catalog): show tool key + editable JSON args */}
        {isUnknown && (
          <div className="rounded-lg p-3 space-y-2" style={{ backgroundColor: '#252c3c', border: '1px solid #3d4557' }}>
            <p className="text-xs font-medium uppercase tracking-wide" style={{ color: '#7a8ba3' }}>
              Tool: <span className="font-mono text-info-400">{step.tool}</span> · not in catalog — editing raw args
            </p>
            <textarea
              className="field-input w-full font-mono text-sm resize-none"
              rows={3}
              value={step.args ? JSON.stringify(step.args, null, 2) : '{}'}
              onChange={e => {
                try { set('args', JSON.parse(e.target.value)) } catch { /* ignore */ }
              }}
            />
          </div>
        )}

      </div>

      {/* Remove */}
      <button
        onClick={onRemove}
        className="mt-1.5 p-1 rounded text-gray-600 hover:text-critical-400 hover:bg-critical-900/30 transition-colors flex-shrink-0"
        title="Remove step"
      >
        <IconTrash size={14} />
      </button>
    </div>
  )
}
