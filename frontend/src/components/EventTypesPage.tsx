import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import {
  IconPlus,
  IconPencil,
  IconTrash,
  IconSearch,
  IconCheck,
  IconX,
  IconLoader2,
  IconAlertTriangle,
  IconTags,
} from './icons'

// ─── Types ────────────────────────────────────────────────────────────────────

interface EventType {
  code: string
  label: string
  category: string
  enabled: boolean
  is_system: boolean
  description?: string
  default_severity?: string | null
}

const SEVERITY_COLORS: Record<string, string> = {
  info:     '#4070a0',
  warning:  '#9a7030',
  critical: '#a04848',
}

// ─── Domain badge colours ─────────────────────────────────────────────────────

const DOMAIN_COLORS: Record<string, string> = {
  infrastructure: '#f59e0b',
  container:      '#3b82f6',
  application:    '#10b981',
  database:       '#8b5cf6',
  cloud:          '#0ea5e9',
  network:        '#06b6d4',
  security:       '#ef4444',
  log:            '#f97316',
  synthetic:      '#ec4899',
  custom:         '#94a3b8',
}

function domainColor(cat: string) {
  return DOMAIN_COLORS[cat?.toLowerCase()] ?? '#94a3b8'
}

// ─── Auth helper ──────────────────────────────────────────────────────────────

function authHeaders(): Record<string, string> {
  const tok = localStorage.getItem('ap_token') || ''
  return tok
    ? { Authorization: `Bearer ${tok}`, 'Content-Type': 'application/json' }
    : { 'Content-Type': 'application/json' }
}

// ─── Add / Edit modal ─────────────────────────────────────────────────────────

interface ModalProps {
  initial?: EventType | null
  domains: string[]
  onSave: (et: EventType) => Promise<void>
  onClose: () => void
}

function EventTypeModal({ initial, domains, onSave, onClose }: ModalProps) {
  const isEdit = !!initial
  const [code,     setCode]     = useState(initial?.code     ?? '')
  const [label,    setLabel]    = useState(initial?.label    ?? '')
  const [category, setCategory] = useState(initial?.category ?? '')
  const [enabled,  setEnabled]  = useState(initial?.enabled  ?? true)
  const [desc,     setDesc]     = useState(initial?.description ?? '')
  const [severity, setSeverity] = useState(initial?.default_severity ?? '')
  const [saving,   setSaving]   = useState(false)
  const [error,    setError]    = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!code.trim())  { setError('Code is required'); return }
    if (!label.trim()) { setError('Label is required'); return }
    setSaving(true)
    setError(null)
    try {
      await onSave({
        code: code.trim(), label: label.trim(), category: category.trim(), enabled,
        is_system: initial?.is_system ?? false, description: desc.trim() || undefined,
        default_severity: severity,   // '' clears it; the API treats '' the same as unset
      })
    } catch (err: any) {
      setError(err?.message || 'Save failed')
      setSaving(false)
    }
  }

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.7)' }}>
      <div className="w-full max-w-lg rounded-xl" style={{ background: '#111827', border: '1px solid #1e2a3a' }}>
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4" style={{ borderBottom: '1px solid #1e2a3a' }}>
          <div className="flex items-center gap-2">
            <IconTags size={18} style={{ color: '#60a5fa' }} />
            <span className="font-semibold text-sm" style={{ color: '#e2e8f0' }}>
              {isEdit ? 'Edit Event Type' : 'New Event Type'}
            </span>
          </div>
          <button onClick={onClose} style={{ color: '#475569' }} className="hover:text-gray-300 transition-colors">
            <IconX size={18} />
          </button>
        </div>

        {/* Body */}
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          {error && (
            <div className="flex items-center gap-2 text-sm px-3 py-2 rounded-lg"
              style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#fca5a5' }}>
              <IconAlertTriangle size={14} />
              {error}
            </div>
          )}

          <div>
            <label className="field-label">Code *
              {isEdit && <span className="ml-2 font-normal text-xs" style={{ color: '#475569' }}>(read-only after creation)</span>}
            </label>
            <input
              className="field-input w-full"
              style={{ fontFamily: 'monospace', fontSize: 12 }}
              value={code}
              onChange={e => setCode(e.target.value.toLowerCase().replace(/\s+/g, '_'))}
              placeholder="e.g. application.availability.service_down"
              disabled={isEdit}
              required
            />
            <p className="mt-1 text-xs" style={{ color: '#475569' }}>Dot-separated taxonomy path, all lowercase</p>
          </div>

          <div>
            <label className="field-label">Label *</label>
            <input
              className="field-input w-full"
              value={label}
              onChange={e => setLabel(e.target.value)}
              placeholder="e.g. Service Down"
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="field-label">Category / Domain</label>
              <input
                className="field-input w-full"
                list="domain-options"
                value={category}
                onChange={e => setCategory(e.target.value)}
                placeholder="e.g. application"
              />
              <datalist id="domain-options">
                {domains.map(d => <option key={d} value={d} />)}
              </datalist>
            </div>

            <div>
              <label className="field-label">Description</label>
              <input
                className="field-input w-full"
                value={desc}
                onChange={e => setDesc(e.target.value)}
                placeholder="Optional short description"
              />
            </div>
          </div>

          <div>
            <label className="field-label">Default Severity</label>
            <select
              className="field-input w-full"
              value={severity}
              onChange={e => setSeverity(e.target.value)}
            >
              <option value="">— not set —</option>
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="critical">Critical</option>
            </select>
            <p className="mt-1 text-xs" style={{ color: '#475569' }}>
              Base severity for watcher-raised events of this type. Only applies to
              types the watcher itself emits — has no effect on connector-sourced events.
            </p>
          </div>

          <div className="flex items-center gap-3 pt-1">
            <button
              type="button"
              onClick={() => setEnabled(v => !v)}
              className={`relative w-10 h-6 rounded-full transition-colors flex-shrink-0 ${enabled ? 'bg-info-500' : 'bg-slate-600'}`}
            >
              <span className={`absolute top-1 left-1 w-4 h-4 rounded-full bg-white shadow transition-transform ${enabled ? 'translate-x-4' : ''}`} />
            </button>
            <span className="text-sm" style={{ color: '#a0aec0' }}>
              {enabled ? 'Enabled — appears in event type selectors' : 'Disabled — hidden from selectors'}
            </span>
          </div>
        </form>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-6 py-4" style={{ borderTop: '1px solid #1e2a3a' }}>
          <button onClick={onClose} className="btn btn-secondary">Cancel</button>
          <button onClick={handleSubmit as any} disabled={saving} className="btn btn-primary flex items-center gap-2">
            {saving ? <IconLoader2 size={14} className="animate-spin" /> : <IconCheck size={14} />}
            {saving ? 'Saving…' : isEdit ? 'Update' : 'Create'}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Delete confirm ───────────────────────────────────────────────────────────

function DeleteConfirm({ code, onConfirm, onCancel }: { code: string; onConfirm: () => void; onCancel: () => void }) {
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: 'rgba(0,0,0,0.7)' }}>
      <div className="w-full max-w-sm rounded-xl p-6 space-y-4" style={{ background: '#111827', border: '1px solid #3d1010' }}>
        <div className="flex items-center gap-3">
          <IconAlertTriangle size={20} style={{ color: '#f43f5e' }} />
          <span className="font-semibold" style={{ color: '#e2e8f0' }}>Delete event type?</span>
        </div>
        <p className="text-sm" style={{ color: '#94a3b8' }}>
          <span className="font-mono" style={{ color: '#fca5a5' }}>{code}</span> will be permanently removed.
          Runbooks using this event type will retain the value but it will no longer appear in selectors.
        </p>
        <div className="flex justify-end gap-2">
          <button onClick={onCancel} className="btn btn-secondary">Cancel</button>
          <button onClick={onConfirm} className="btn flex items-center gap-1.5"
            style={{ background: 'rgba(239,68,68,0.15)', border: '1px solid rgba(239,68,68,0.4)', color: '#f87171' }}>
            <IconTrash size={14} /> Delete
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function EventTypesPage() {
  const [items,    setItems]    = useState<EventType[]>([])
  const [domains,  setDomains]  = useState<string[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [query,    setQuery]    = useState('')
  const [modal,    setModal]    = useState<'add' | EventType | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [toggling, setToggling] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [etRes, domRes] = await Promise.all([
        fetch('/api/event-types', { headers: authHeaders() }),
        fetch('/api/event-types/domains', { headers: authHeaders() }),
      ])
      const etData  = etRes.ok  ? await etRes.json()  : []
      const domData = domRes.ok ? await domRes.json() : []
      setItems(Array.isArray(etData)  ? etData  : [])
      setDomains(Array.isArray(domData) ? domData : [])
    } catch {
      setError('Failed to load event types')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  // Filter
  const q       = query.toLowerCase().trim()
  const visible = q
    ? items.filter(i => i.code.includes(q) || i.label.toLowerCase().includes(q) || i.category?.toLowerCase().includes(q))
    : items

  // Group by category, then sort alphabetically within each group
  const grouped = visible.reduce<Record<string, EventType[]>>((acc, et) => {
    const cat = et.category || 'uncategorized'
    ;(acc[cat] = acc[cat] || []).push(et)
    return acc
  }, {})
  const sortedGroups = Object.entries(grouped).sort(([a], [b]) => a.localeCompare(b))

  const handleSave = async (et: EventType) => {
    const isEdit = items.some(i => i.code === et.code)
    const method = isEdit ? 'PATCH' : 'POST'
    const url    = isEdit ? `/api/event-types/${et.code}` : '/api/event-types'
    const res = await fetch(url, { method, headers: authHeaders(), body: JSON.stringify(et) })
    if (!res.ok) {
      const detail = await res.json().then(d => d.detail || 'Save failed').catch(() => 'Save failed')
      throw new Error(detail)
    }
    setModal(null)
    await load()
  }

  const handleToggle = async (et: EventType) => {
    setToggling(et.code)
    try {
      await fetch(`/api/event-types/${et.code}`, {
        method:  'PATCH',
        headers: authHeaders(),
        body:    JSON.stringify({ ...et, enabled: !et.enabled }),
      })
      await load()
    } finally {
      setToggling(null)
    }
  }

  const handleDelete = async (code: string) => {
    await fetch(`/api/event-types/${code}`, { method: 'DELETE', headers: authHeaders() })
    setDeleting(null)
    await load()
  }

  return (
    <div className="page-transition-enter max-w-5xl mx-auto">

      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h2 className="text-section-title mb-1 flex items-center gap-2" style={{ color: '#e8eef5' }}>
            <IconTags size={22} style={{ color: '#60a5fa' }} />
            Event Types
          </h2>
          <p className="text-sm" style={{ color: '#a0aec0' }}>
            Taxonomy of incident event types used by the remediation engine and runbook selectors
          </p>
        </div>
        <button
          onClick={() => setModal('add')}
          className="btn btn-primary flex items-center gap-2"
        >
          <IconPlus size={16} />
          Add Event Type
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-sm px-4 py-2 rounded-lg mb-6"
          style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#fca5a5' }}>
          <IconAlertTriangle size={14} />
          {error}
        </div>
      )}

      {/* Search */}
      <div className="relative mb-6">
        <IconSearch size={15} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: '#475569' }} />
        <input
          className="field-input w-full pl-9"
          placeholder="Search by code, label, or category…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
      </div>

      {/* Stats row */}
      <div className="flex items-center gap-6 mb-6 text-sm" style={{ color: '#64748b' }}>
        <span>{items.length} total</span>
        <span>{items.filter(i => i.enabled).length} enabled</span>
        <span>{Object.keys(grouped).length} categories</span>
      </div>

      {/* Content */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <IconLoader2 size={28} className="animate-spin" style={{ color: '#3b82f6' }} />
        </div>
      ) : visible.length === 0 ? (
        <div className="text-center py-16" style={{ color: '#475569' }}>
          {query ? 'No event types match your search.' : 'No event types configured yet.'}
        </div>
      ) : (
        <div className="space-y-6">
          {sortedGroups.map(([cat, types]) => {
            const accent = domainColor(cat)
            return (
              <div key={cat} className="rounded-xl overflow-hidden" style={{ border: '1px solid #1e2a3a' }}>
                {/* Category header */}
                <div className="flex items-center gap-3 px-4 py-2.5"
                  style={{ background: '#0d1018', borderBottom: '1px solid #1e2a3a' }}>
                  <span style={{
                    fontSize: 10, fontWeight: 700, letterSpacing: '.08em', textTransform: 'uppercase',
                    padding: '2px 8px', borderRadius: 4, background: accent + '22', color: accent,
                  }}>
                    {cat}
                  </span>
                  <span className="text-xs" style={{ color: '#334155' }}>{types.length} type{types.length !== 1 ? 's' : ''}</span>
                </div>

                {/* Rows */}
                <div style={{ background: '#111827' }}>
                  {types.map((et, idx) => (
                    <div key={et.code}
                      className="flex items-center gap-4 px-4 py-3 hover:bg-slate-800/50 transition-colors"
                      style={{ borderTop: idx > 0 ? '1px solid #1a2030' : undefined }}
                    >
                      {/* Enabled toggle */}
                      <button
                        onClick={() => handleToggle(et)}
                        disabled={toggling === et.code}
                        className={`relative w-9 h-5 rounded-full transition-colors flex-shrink-0 ${et.enabled ? 'bg-info-500' : 'bg-slate-600'}`}
                        title={et.enabled ? 'Disable' : 'Enable'}
                      >
                        {toggling === et.code
                          ? <IconLoader2 size={10} className="animate-spin absolute inset-0 m-auto text-white" />
                          : <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${et.enabled ? 'translate-x-4' : ''}`} />
                        }
                      </button>

                      {/* Code */}
                      <span className="flex-1 font-mono text-xs" style={{ color: et.enabled ? '#cbd5e1' : '#475569' }}>
                        {et.code}
                      </span>

                      {/* Default severity badge */}
                      {et.default_severity && (
                        <span
                          className="text-xs px-2 py-0.5 rounded uppercase tracking-wide flex-shrink-0"
                          style={{
                            background: SEVERITY_COLORS[et.default_severity] + '22',
                            color: SEVERITY_COLORS[et.default_severity],
                            fontWeight: 700, fontSize: 10,
                          }}
                          title="Default severity for watcher-raised events of this type"
                        >
                          {et.default_severity}
                        </span>
                      )}

                      {/* Label */}
                      <span className="w-48 text-sm truncate" style={{ color: et.enabled ? '#94a3b8' : '#334155' }}>
                        {et.label}
                      </span>

                      {/* Status badge */}
                      {!et.enabled && (
                        <span className="text-xs px-2 py-0.5 rounded" style={{ background: '#1e2a3a', color: '#475569' }}>
                          disabled
                        </span>
                      )}

                      {/* Actions */}
                      <div className="flex items-center gap-1 ml-auto flex-shrink-0">
                        <button
                          onClick={() => setModal(et)}
                          className="p-1.5 rounded-lg transition-colors text-gray-500 hover:text-info-400 hover:bg-slate-700"
                          title="Edit"
                        >
                          <IconPencil size={14} />
                        </button>
                        {et.is_system ? (
                          <span
                            className="p-1.5 rounded-lg text-slate-600 cursor-not-allowed"
                            title="System event types cannot be deleted — disable instead"
                          >
                            <IconTrash size={14} />
                          </span>
                        ) : (
                          <button
                            onClick={() => setDeleting(et.code)}
                            className="p-1.5 rounded-lg transition-colors text-gray-500 hover:text-critical-400 hover:bg-slate-700"
                            title="Delete"
                          >
                            <IconTrash size={14} />
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Modals */}
      {modal !== null && (
        <EventTypeModal
          initial={modal === 'add' ? null : modal}
          domains={domains}
          onSave={handleSave}
          onClose={() => setModal(null)}
        />
      )}
      {deleting && (
        <DeleteConfirm
          code={deleting}
          onConfirm={() => handleDelete(deleting)}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  )
}
