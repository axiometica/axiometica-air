/**
 * CMDBEditor — live Neo4j CI browser with role-gated editing.
 *
 * Layout: left list panel (search + filter) + right detail panel (fields + relationships).
 * Edit mode is available to admin and itom_admin roles only.
 */
import { useState, useEffect, useCallback, useRef } from 'react'
import axios from 'axios'
import { useCurrentUser } from '../hooks/useCurrentUser'
import {
  IconRefresh, IconSearch, IconAlertTriangle, IconX,
} from './icons'
import './CMDBEditor.css'

// ─── Types ────────────────────────────────────────────────────────────────────

interface CIRecord {
  name: string
  type?: string
  status?: string
  environment?: string
  owner?: string
  description?: string
  business_criticality?: string
  ci_tier?: number
  is_spof?: boolean
  failover_available?: boolean
  user_count?: number
  sla_percent?: number
  platform?: string
  support_group?: string
  assignment_group?: string
  managed_by?: string
  data_center?: string
  health_status?: string
  container_status?: string
  docker_image?: string
  ip_address?: string
  exposed_ports?: string
  discovery_source?: string
  watcher_source_id?: string
  last_discovered_at?: string
  last_metrics_update?: string
  current_cpu_percent?: number
  current_memory_mb?: number
  depends_on?: RelCI[]
  depended_on_by?: RelCI[]
  incident_count?: number
}

interface RelCI {
  name: string
  tier?: number
  status?: string
  health?: string
}

// ── Custom field types ────────────────────────────────────────────────────────
type CustomFieldType = 'text' | 'number' | 'date' | 'boolean' | 'url'
interface CustomFieldDef { label: string; type: CustomFieldType }
type CustomMeta = Record<string, CustomFieldDef>

const CUSTOM_FIELD_TYPE_LABELS: Record<CustomFieldType, string> = {
  text: 'Text', number: 'Number', date: 'Date', boolean: 'Boolean (Yes / No)', url: 'URL',
}

function parseCustomMeta(raw: unknown): CustomMeta {
  try { return JSON.parse((raw as string) || '{}') } catch { return {} }
}

function toFieldKey(label: string): string {
  return label.toLowerCase().trim().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '').slice(0, 48)
}

// Fields editable by users — maps key → label + input type
const EDITABLE_FIELDS: { key: keyof CIRecord; label: string; type: 'text' | 'select' | 'number' | 'boolean' }[] = [
  { key: 'type',                 label: 'Type',                 type: 'select'  },
  { key: 'status',               label: 'Status',               type: 'select'  },
  { key: 'environment',          label: 'Environment',          type: 'select'  },
  { key: 'owner',                label: 'Owner',                type: 'text'    },
  { key: 'description',          label: 'Description',          type: 'text'    },
  { key: 'business_criticality', label: 'Business Criticality', type: 'select'  },
  { key: 'ci_tier',              label: 'CI Tier',              type: 'select'  },
  { key: 'platform',             label: 'Platform',             type: 'text'    },
  { key: 'support_group',        label: 'Support Group',        type: 'text'    },
  { key: 'assignment_group',     label: 'Assignment Group',     type: 'text'    },
  { key: 'managed_by',           label: 'Managed By',           type: 'text'    },
  { key: 'data_center',          label: 'Data Center',          type: 'text'    },
  { key: 'is_spof',              label: 'Single Point of Failure', type: 'boolean' },
  { key: 'failover_available',   label: 'Failover Available',   type: 'boolean' },
  { key: 'user_count',           label: 'User Count',           type: 'number'  },
  { key: 'sla_percent',          label: 'SLA %',                type: 'number'  },
]

const SELECT_OPTIONS: Record<string, string[]> = {
  type:                 ['Service', 'Server', 'Container', 'Database', 'Application'],
  status:               ['active', 'decommissioned', 'maintenance'],
  environment:          ['production', 'staging', 'development', 'test', 'qa'],
  business_criticality: ['tier_1', 'tier_2', 'tier_3'],
  ci_tier:              ['1', '2', '3'],
}

// ─── Design system tokens (mirrors the app palette) ───────────────────────────
const DS = {
  bg:      '#0d1117',
  surface: '#1a1f2e',
  raised:  '#252c3c',
  border:  '#3d4557',
  txtP:    '#e8eef5',
  txtM:    '#a0aec0',
  txtS:    '#7a8ba3',
  accent:  '#3b82f6',
  green:   '#10b981',
  red:     '#ef4444',
  amber:   '#f59e0b',
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function HealthDot({ health }: { health?: string }) {
  const color = health === 'degraded' ? DS.red : health === 'healthy' ? DS.green : DS.txtS
  return (
    <span style={{
      display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
      backgroundColor: color, flexShrink: 0,
    }} />
  )
}

function RelChip({ rel, onNavigate }: { rel: RelCI; onNavigate: (name: string) => void }) {
  return (
    <button
      onClick={() => onNavigate(rel.name)}
      className="cmdb-rel-chip"
    >
      <HealthDot health={rel.health} />
      {rel.name}
      {rel.tier != null && <span className="cmdb-rel-tier">T{rel.tier}</span>}
    </button>
  )
}

function FieldValue({ value }: { value: unknown }) {
  if (value == null) return <span className="cmdb-field-null">—</span>
  if (typeof value === 'boolean') return <span className={`cmdb-bool ${value ? 'yes' : 'no'}`}>{value ? 'Yes' : 'No'}</span>
  return <span className="cmdb-field-val">{String(value)}</span>
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function CMDBEditor() {
  const { isITOMAdmin, isAdmin } = useCurrentUser()
  const canEdit   = isITOMAdmin   // admin + itom_admin can edit
  const canDelete = isAdmin       // admin only can decommission

  // List state
  const [allCIs, setAllCIs]         = useState<CIRecord[]>([])
  const [listLoading, setListLoading] = useState(true)
  const [listError, setListError]   = useState<string | null>(null)
  const [search, setSearch]         = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [envFilter, setEnvFilter]   = useState('')

  // Detail state
  const [selected, setSelected]     = useState<CIRecord | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  // Edit state
  const [editing, setEditing]       = useState(false)
  const [editFields, setEditFields] = useState<Record<string, unknown>>({})
  const [saving, setSaving]         = useState(false)
  const [saveError, setSaveError]   = useState<string | null>(null)

  // Custom field define-field form state
  const [definingField, setDefiningField]     = useState(false)
  const [pendingDefLabel, setPendingDefLabel] = useState('')
  const [pendingDefType, setPendingDefType]   = useState<CustomFieldType>('text')

  // Create dialog state
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState({ name: '', type: 'Service', environment: '', owner: '', support_group: '', assignment_group: '', managed_by: '', data_center: '' })
  const [creating, setCreating]     = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const searchRef = useRef<HTMLInputElement>(null)

  // ── Load CI list ────────────────────────────────────────────────────────────
  const loadList = useCallback(async () => {
    setListLoading(true)
    setListError(null)
    try {
      const { data } = await axios.get('/api/cmdb/nodes')
      setAllCIs(data.nodes || [])
    } catch {
      setListError('Failed to load CMDB data')
    } finally {
      setListLoading(false)
    }
  }, [])

  useEffect(() => { loadList() }, [loadList])

  // ── Load CI detail ──────────────────────────────────────────────────────────
  const loadDetail = useCallback(async (name: string) => {
    setDetailLoading(true)
    setDetailError(null)
    setEditing(false)
    setSaveError(null)
    try {
      const { data } = await axios.get(`/api/cmdb/nodes/${encodeURIComponent(name)}`)
      setSelected(data)
    } catch {
      setDetailError(`Failed to load CI '${name}'`)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleSelectCI = useCallback((ci: CIRecord) => {
    loadDetail(ci.name)
  }, [loadDetail])

  // ── Edit ────────────────────────────────────────────────────────────────────
  const startEdit = () => {
    if (!selected) return
    const initial: Record<string, unknown> = {}
    EDITABLE_FIELDS.forEach(({ key }) => { initial[key] = (selected as Record<string, unknown>)[key] })
    // Seed u_ values and the custom field schema
    Object.entries(selected as Record<string, unknown>).forEach(([k, v]) => {
      if (k.startsWith('u_') || k === '_custom_meta') initial[k] = v
    })
    setEditFields(initial)
    setEditing(true)
    setSaveError(null)
    setDefiningField(false)
    setPendingDefLabel('')
    setPendingDefType('text')
  }

  const cancelEdit = () => {
    setEditing(false)
    setSaveError(null)
    setDefiningField(false)
    setPendingDefLabel('')
    setPendingDefType('text')
  }

  const saveEdit = async () => {
    if (!selected) return
    setSaving(true)
    setSaveError(null)
    try {
      await axios.patch(`/api/cmdb/nodes/${encodeURIComponent(selected.name)}`, { fields: editFields })
      await loadDetail(selected.name)
      // Refresh list to pick up any changed fields shown there
      loadList()
      setEditing(false)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Save failed'
      setSaveError(msg)
    } finally {
      setSaving(false)
    }
  }

  const setField = (key: string, value: unknown) => {
    setEditFields(prev => ({ ...prev, [key]: value }))
  }

  // ── Decommission ────────────────────────────────────────────────────────────
  const [decommissioning, setDecommissioning] = useState(false)
  const handleDecommission = async () => {
    if (!selected) return
    if (!window.confirm(`Mark '${selected.name}' as decommissioned?`)) return
    setDecommissioning(true)
    try {
      await axios.delete(`/api/cmdb/nodes/${encodeURIComponent(selected.name)}`)
      await loadDetail(selected.name)
      loadList()
    } catch {
      setSaveError('Decommission failed')
    } finally {
      setDecommissioning(false)
    }
  }

  // ── Create CI ───────────────────────────────────────────────────────────────
  const handleCreate = async () => {
    if (!createForm.name.trim()) { setCreateError('Name is required'); return }
    setCreating(true)
    setCreateError(null)
    try {
      await axios.post('/api/cmdb/nodes', createForm)
      setCreateOpen(false)
      setCreateForm({ name: '', type: 'Service', environment: '', owner: '', support_group: '', assignment_group: '', managed_by: '', data_center: '' })
      await loadList()
      loadDetail(createForm.name)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || 'Create failed'
      setCreateError(msg)
    } finally {
      setCreating(false)
    }
  }

  // ── Filtered list ───────────────────────────────────────────────────────────
  const filtered = allCIs.filter(ci => {
    const q = search.toLowerCase()
    const matchSearch = !q || ci.name.toLowerCase().includes(q) || (ci.owner || '').toLowerCase().includes(q)
    const matchType   = !typeFilter || ci.type === typeFilter
    const matchEnv    = !envFilter  || ci.environment === envFilter
    return matchSearch && matchType && matchEnv
  })

  const uniqueTypes = [...new Set(allCIs.map(c => c.type).filter(Boolean))] as string[]
  const uniqueEnvs  = [...new Set(allCIs.map(c => c.environment).filter(Boolean))] as string[]

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="cmdb-editor">

      {/* ── Left panel: CI list ── */}
      <div className="cmdb-list-panel">
        <div className="cmdb-list-toolbar">
          <div className="cmdb-search-wrap">
            <IconSearch size={13} />
            <input
              ref={searchRef}
              className="cmdb-search"
              placeholder="Search CIs…"
              value={search}
              onChange={e => setSearch(e.target.value)}
            />
            {search && (
              <button className="cmdb-search-clear" onClick={() => setSearch('')}>
                <IconX size={11} />
              </button>
            )}
          </div>

          <div className="cmdb-filters">
            <select className="cmdb-filter-sel" value={typeFilter} onChange={e => setTypeFilter(e.target.value)}>
              <option value="">All types</option>
              {uniqueTypes.map(t => <option key={t} value={t}>{t}</option>)}
            </select>
            <select className="cmdb-filter-sel" value={envFilter} onChange={e => setEnvFilter(e.target.value)}>
              <option value="">All envs</option>
              {uniqueEnvs.map(e => <option key={e} value={e}>{e}</option>)}
            </select>
            <button className="cmdb-icon-btn" onClick={loadList} title="Refresh">
              <IconRefresh size={13} />
            </button>
            {canEdit && (
              <button className="cmdb-create-btn" onClick={() => setCreateOpen(true)}>+ New CI</button>
            )}
          </div>
        </div>

        <div className="cmdb-list-meta">
          {listLoading
            ? <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}><span className="cmdb-spinner-sm" />Loading…</span>
            : `${filtered.length} of ${allCIs.length} CIs`}
        </div>

        {listError && (
          <div className="cmdb-error"><IconAlertTriangle size={13} />{listError}</div>
        )}

        <div className="cmdb-list">
          {filtered.map(ci => {
            const isActive = selected?.name === ci.name
            const health   = ci.health_status
            return (
              <button
                key={ci.name}
                className={`cmdb-list-row${isActive ? ' active' : ''}`}
                onClick={() => handleSelectCI(ci)}
              >
                <div className="cmdb-list-row-main">
                  <HealthDot health={health} />
                  <span className="cmdb-list-name">{ci.name}</span>
                  {ci.is_spof && <span className="cmdb-spof-tag">SPOF</span>}
                </div>
                <div className="cmdb-list-row-sub">
                  <span className="cmdb-type-tag">{ci.type || '—'}</span>
                  <span className="cmdb-env-tag">{ci.environment || '—'}</span>
                  {ci.ci_tier != null && <span className="cmdb-tier-tag">T{ci.ci_tier}</span>}
                </div>
              </button>
            )
          })}
          {!listLoading && filtered.length === 0 && (
            <div className="cmdb-list-empty">No CIs match the filter</div>
          )}
        </div>
      </div>

      {/* ── Right panel: detail ── */}
      <div className="cmdb-detail-panel">
        {!selected && !detailLoading && (
          <div className="cmdb-detail-empty">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.25 }}>
              <rect x="2" y="3" width="20" height="14" rx="2" />
              <line x1="8" y1="21" x2="16" y2="21" />
              <line x1="12" y1="17" x2="12" y2="21" />
            </svg>
            <p style={{ margin: 0 }}>Select a CI to view details</p>
          </div>
        )}

        {detailLoading && (
          <div className="cmdb-detail-empty">
            <div className="cmdb-spinner" />
          </div>
        )}

        {detailError && !detailLoading && (
          <div className="cmdb-error"><IconAlertTriangle size={13} />{detailError}</div>
        )}

        {selected && !detailLoading && (
          <>
            {/* Header */}
            <div className="cmdb-detail-header">
              <div>
                <h2 className="cmdb-detail-name">{selected.name}</h2>
                <div className="cmdb-detail-badges">
                  <span className="cmdb-badge-type">{selected.type || '—'}</span>
                  {selected.environment && <span className="cmdb-badge-env">{selected.environment}</span>}
                  {selected.status && (
                    <span className={`cmdb-badge-status ${selected.status}`}>{selected.status}</span>
                  )}
                  {selected.is_spof && <span className="cmdb-badge-spof">SPOF</span>}
                  {(selected.incident_count ?? 0) > 0 && (
                    <span className="cmdb-badge-incident">{selected.incident_count} active incident{selected.incident_count !== 1 ? 's' : ''}</span>
                  )}
                </div>
              </div>

              {canEdit && !editing && (
                <div className="cmdb-detail-actions">
                  <button className="cmdb-btn-edit" onClick={startEdit}>Edit</button>
                  {canDelete && selected.status !== 'decommissioned' && (
                    <button
                      className="cmdb-btn-decommission"
                      onClick={handleDecommission}
                      disabled={decommissioning}
                    >
                      {decommissioning ? 'Working…' : 'Decommission'}
                    </button>
                  )}
                </div>
              )}
              {editing && (
                <div className="cmdb-detail-actions">
                  <button className="cmdb-btn-save" onClick={saveEdit} disabled={saving}>
                    {saving ? 'Saving…' : 'Save'}
                  </button>
                  <button className="cmdb-btn-cancel" onClick={cancelEdit} disabled={saving}>Cancel</button>
                </div>
              )}
            </div>

            {saveError && (
              <div className="cmdb-error" style={{ margin: '0 0 1rem' }}>
                <IconAlertTriangle size={13} />{saveError}
              </div>
            )}

            <div className="cmdb-detail-body">

              {/* ── Managed fields ── */}
              <section className="cmdb-section">
                <h3 className="cmdb-section-title">Configuration</h3>
                <div className="cmdb-fields-grid">
                  {EDITABLE_FIELDS.map(({ key, label, type }) => {
                    const rawVal = (selected as Record<string, unknown>)[key]
                    return (
                      <div key={key} className="cmdb-field-row">
                        <span className="cmdb-field-label">{label}</span>
                        {editing ? (
                          type === 'boolean' ? (
                            <select
                              className="cmdb-field-input"
                              value={String(editFields[key] ?? false)}
                              onChange={e => setField(key, e.target.value === 'true')}
                            >
                              <option value="false">No</option>
                              <option value="true">Yes</option>
                            </select>
                          ) : type === 'select' && SELECT_OPTIONS[key] ? (
                            <select
                              className="cmdb-field-input"
                              value={String(editFields[key] ?? '')}
                              onChange={e => setField(key, key === 'ci_tier' ? parseInt(e.target.value) : e.target.value)}
                            >
                              <option value="">—</option>
                              {SELECT_OPTIONS[key].map(opt => (
                                <option key={opt} value={opt}>{opt}</option>
                              ))}
                            </select>
                          ) : (
                            <input
                              className="cmdb-field-input"
                              type={type === 'number' ? 'number' : 'text'}
                              value={String(editFields[key] ?? '')}
                              onChange={e => setField(key, type === 'number' ? parseFloat(e.target.value) || null : e.target.value)}
                            />
                          )
                        ) : (
                          <FieldValue value={rawVal} />
                        )}
                      </div>
                    )
                  })}
                </div>
              </section>

              {/* ── Custom fields (u_ prefix) ── */}
              {(() => {
                const raw = selected as Record<string, unknown>
                const meta: CustomMeta = parseCustomMeta(
                  editing ? editFields._custom_meta : raw._custom_meta
                )
                const definedKeys = Object.keys(meta)
                if (!editing && definedKeys.length === 0) return null

                const CHIP = (
                  <span style={{
                    fontSize: '0.6rem', fontWeight: 700, padding: '1px 5px', borderRadius: 3,
                    background: 'rgba(139,92,246,0.12)', color: '#a78bfa',
                    border: '1px solid rgba(139,92,246,0.28)', fontFamily: 'monospace',
                    lineHeight: 1.4, flexShrink: 0,
                  }}>custom</span>
                )

                return (
                  <section className="cmdb-section">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.6rem' }}>
                      <h3 className="cmdb-section-title" style={{ margin: 0 }}>Custom Fields</h3>
                      {editing && !definingField && (
                        <button type="button" className="cmdb-btn-edit"
                          style={{ fontSize: '0.75rem', padding: '3px 10px' }}
                          onClick={() => setDefiningField(true)}>
                          + Define field
                        </button>
                      )}
                    </div>

                    {/* Defined field rows */}
                    {definedKeys.length > 0 && (
                      <div className="cmdb-fields-grid" style={{ marginBottom: definingField ? '0.75rem' : 0 }}>
                        {definedKeys.map(k => {
                          const def = meta[k]
                          const val = editing ? editFields[k] : raw[k]
                          const isDeleted = editing && (editFields[k] === null || editFields[k] === undefined)
                          if (isDeleted) return null
                          return (
                            <div key={k} className="cmdb-field-row" style={{
                              borderLeft: '2px solid rgba(139,92,246,0.25)',
                              paddingLeft: 8, marginLeft: -8,
                            }}>
                              <span className="cmdb-field-label" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                                {def.label}
                                {CHIP}
                              </span>
                              {editing ? (
                                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                                  {def.type === 'boolean' ? (
                                    <select className="cmdb-field-input" style={{ flex: 1 }}
                                      value={String(val ?? '')}
                                      onChange={e => setField(k, e.target.value)}>
                                      <option value="">—</option>
                                      <option value="true">Yes</option>
                                      <option value="false">No</option>
                                    </select>
                                  ) : (
                                    <input className="cmdb-field-input" style={{ flex: 1 }}
                                      type={def.type === 'number' ? 'number' : def.type === 'date' ? 'date' : 'text'}
                                      value={String(val ?? '')}
                                      onChange={e => setField(k, e.target.value)}
                                    />
                                  )}
                                  <button type="button" title={`Remove ${def.label}`}
                                    onClick={() => {
                                      const newMeta = { ...meta }
                                      delete newMeta[k]
                                      setField('_custom_meta', JSON.stringify(newMeta))
                                      setField(k, null)
                                    }}
                                    style={{
                                      background: 'transparent', border: 'none', color: DS.txtS,
                                      cursor: 'pointer', fontSize: '1.1rem', lineHeight: 1, padding: '0 2px',
                                    }}>×</button>
                                </div>
                              ) : (
                                def.type === 'url' && val ? (
                                  <a href={String(val)} target="_blank" rel="noopener noreferrer"
                                    style={{ color: DS.accent, fontSize: '0.82rem', wordBreak: 'break-all' }}>
                                    {String(val)}
                                  </a>
                                ) : def.type === 'boolean' ? (
                                  <span style={{ fontSize: '0.82rem', color: DS.txtP }}>
                                    {val === 'true' || val === true ? 'Yes' : val === 'false' || val === false ? 'No' : '—'}
                                  </span>
                                ) : (
                                  <FieldValue value={val} />
                                )
                              )}
                            </div>
                          )
                        })}
                      </div>
                    )}

                    {editing && !definingField && definedKeys.length === 0 && (
                      <p style={{ color: DS.txtS, fontSize: '0.8rem', margin: 0 }}>
                        No custom fields defined — click "+ Define field" to create one.
                      </p>
                    )}

                    {/* Define new field form */}
                    {editing && definingField && (() => {
                      const derivedKey = pendingDefLabel ? `u_${toFieldKey(pendingDefLabel)}` : ''
                      const keyExists = !!derivedKey && !!meta[derivedKey]
                      return (
                        <div style={{
                          padding: '0.85rem', background: DS.raised, borderRadius: 8,
                          border: '1px solid rgba(139,92,246,0.2)',
                        }}>
                          <div style={{ display: 'grid', gridTemplateColumns: '1fr auto 1fr', gap: '0.5rem', alignItems: 'end', marginBottom: '0.6rem' }}>
                            <label className="cmdb-modal-label" style={{ margin: 0 }}>
                              Display name
                              <input className="cmdb-field-input" placeholder="e.g. Cost Center"
                                value={pendingDefLabel} autoFocus
                                onChange={e => setPendingDefLabel(e.target.value)}
                                onKeyDown={e => e.key === 'Escape' && (setDefiningField(false), setPendingDefLabel(''), setPendingDefType('text'))}
                              />
                            </label>
                            <span style={{ color: DS.txtS, fontSize: '0.82rem', paddingBottom: '0.35rem' }}>→</span>
                            <label className="cmdb-modal-label" style={{ margin: 0 }}>
                              Internal key (auto)
                              <div className="cmdb-field-input" style={{
                                fontFamily: 'monospace', fontSize: '0.8rem',
                                color: derivedKey ? DS.txtM : DS.txtS,
                                background: DS.bg, display: 'flex', alignItems: 'center', gap: 2,
                              }}>
                                {derivedKey || <span style={{ opacity: 0.4 }}>u_…</span>}
                              </div>
                            </label>
                          </div>
                          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'flex-end' }}>
                            <label className="cmdb-modal-label" style={{ margin: 0, flex: 1 }}>
                              Field type
                              <select className="cmdb-field-input" value={pendingDefType}
                                onChange={e => setPendingDefType(e.target.value as CustomFieldType)}>
                                {(Object.entries(CUSTOM_FIELD_TYPE_LABELS) as [CustomFieldType, string][]).map(([v, l]) => (
                                  <option key={v} value={v}>{l}</option>
                                ))}
                              </select>
                            </label>
                            <button type="button" className="cmdb-btn-save"
                              style={{ fontSize: '0.75rem', padding: '4px 14px', flexShrink: 0 }}
                              disabled={!pendingDefLabel.trim() || !derivedKey || keyExists}
                              onClick={() => {
                                const newMeta: CustomMeta = { ...meta, [derivedKey]: { label: pendingDefLabel.trim(), type: pendingDefType } }
                                setField('_custom_meta', JSON.stringify(newMeta))
                                setField(derivedKey, '')
                                setDefiningField(false)
                                setPendingDefLabel('')
                                setPendingDefType('text')
                              }}>
                              Add
                            </button>
                            <button type="button" className="cmdb-btn-cancel"
                              style={{ fontSize: '0.75rem', padding: '4px 14px', flexShrink: 0 }}
                              onClick={() => { setDefiningField(false); setPendingDefLabel(''); setPendingDefType('text') }}>
                              Cancel
                            </button>
                          </div>
                          {keyExists && (
                            <p style={{ color: DS.amber, fontSize: '0.75rem', margin: '0.4rem 0 0' }}>
                              A field with key <code>{derivedKey}</code> already exists on this CI.
                            </p>
                          )}
                        </div>
                      )
                    })()}
                  </section>
                )
              })()}

              {/* ── Live / discovery fields (read-only) ── */}
              <section className="cmdb-section">
                <h3 className="cmdb-section-title">
                  Live Data
                  <span className="cmdb-live-badge">Watcher-managed</span>
                </h3>
                <div className="cmdb-fields-grid">
                  {[
                    ['health_status',       'Health Status'],
                    ['container_status',    'Container Status'],
                    ['docker_image',        'Docker Image'],
                    ['ip_address',         'IP Address'],
                    ['exposed_ports',       'Exposed Ports'],
                    ['current_cpu_percent', 'CPU %'],
                    ['current_memory_mb',   'Memory (MB)'],
                    ['discovery_source',    'Discovery Source'],
                    ['watcher_source_id',   'Watcher Source ID'],
                    ['last_discovered_at',  'Last Discovered'],
                    ['last_metrics_update', 'Last Metrics'],
                  ].map(([key, label]) => (
                    <div key={key} className="cmdb-field-row readonly">
                      <span className="cmdb-field-label">{label}</span>
                      <FieldValue value={(selected as Record<string, unknown>)[key]} />
                    </div>
                  ))}
                </div>
              </section>

              {/* ── Relationships ── */}
              {((selected.depends_on?.length ?? 0) > 0 || (selected.depended_on_by?.length ?? 0) > 0) && (
                <section className="cmdb-section">
                  <h3 className="cmdb-section-title">Relationships</h3>

                  {(selected.depends_on?.length ?? 0) > 0 && (
                    <div className="cmdb-rel-group">
                      <span className="cmdb-rel-group-label">Depends on</span>
                      <div className="cmdb-rel-chips">
                        {selected.depends_on!.map(rel => (
                          <RelChip key={rel.name} rel={rel} onNavigate={loadDetail} />
                        ))}
                      </div>
                    </div>
                  )}

                  {(selected.depended_on_by?.length ?? 0) > 0 && (
                    <div className="cmdb-rel-group">
                      <span className="cmdb-rel-group-label">Used by</span>
                      <div className="cmdb-rel-chips">
                        {selected.depended_on_by!.map(rel => (
                          <RelChip key={rel.name} rel={rel} onNavigate={loadDetail} />
                        ))}
                      </div>
                    </div>
                  )}
                </section>
              )}

            </div>
          </>
        )}
      </div>

      {/* ── Create CI modal ── */}
      {createOpen && (
        <div className="cmdb-modal-backdrop" onClick={() => setCreateOpen(false)}>
          <div className="cmdb-modal" onClick={e => e.stopPropagation()}>
            <div className="cmdb-modal-header">
              <h3>New Configuration Item</h3>
              <button className="cmdb-icon-btn" onClick={() => setCreateOpen(false)}><IconX size={14} /></button>
            </div>

            <div className="cmdb-modal-body">
              {createError && (
                <div className="cmdb-error"><IconAlertTriangle size={13} />{createError}</div>
              )}
              <div className="cmdb-modal-fields">
                <label className="cmdb-modal-label">
                  Name <span style={{ color: DS.red }}>*</span>
                  <input
                    className="cmdb-field-input"
                    placeholder="e.g. payment-service"
                    value={createForm.name}
                    onChange={e => setCreateForm(f => ({ ...f, name: e.target.value }))}
                    autoFocus
                  />
                </label>
                <label className="cmdb-modal-label">
                  Type
                  <select className="cmdb-field-input" value={createForm.type}
                    onChange={e => setCreateForm(f => ({ ...f, type: e.target.value }))}>
                    {SELECT_OPTIONS.type.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
                <label className="cmdb-modal-label">
                  Environment
                  <select className="cmdb-field-input" value={createForm.environment}
                    onChange={e => setCreateForm(f => ({ ...f, environment: e.target.value }))}>
                    <option value="">—</option>
                    {SELECT_OPTIONS.environment.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
                <label className="cmdb-modal-label">
                  Owner
                  <input
                    className="cmdb-field-input"
                    placeholder="team or person name"
                    value={createForm.owner}
                    onChange={e => setCreateForm(f => ({ ...f, owner: e.target.value }))}
                  />
                </label>
                <label className="cmdb-modal-label">
                  Support Group
                  <input
                    className="cmdb-field-input"
                    placeholder="e.g. platform-ops"
                    value={createForm.support_group}
                    onChange={e => setCreateForm(f => ({ ...f, support_group: e.target.value }))}
                  />
                </label>
                <label className="cmdb-modal-label">
                  Assignment Group
                  <input
                    className="cmdb-field-input"
                    placeholder="e.g. infra-team"
                    value={createForm.assignment_group}
                    onChange={e => setCreateForm(f => ({ ...f, assignment_group: e.target.value }))}
                  />
                </label>
                <label className="cmdb-modal-label">
                  Managed By
                  <input
                    className="cmdb-field-input"
                    placeholder="manager or contact name"
                    value={createForm.managed_by}
                    onChange={e => setCreateForm(f => ({ ...f, managed_by: e.target.value }))}
                  />
                </label>
                <label className="cmdb-modal-label">
                  Data Center
                  <input
                    className="cmdb-field-input"
                    placeholder="e.g. us-east-1, dc-london-01"
                    value={createForm.data_center}
                    onChange={e => setCreateForm(f => ({ ...f, data_center: e.target.value }))}
                  />
                </label>
              </div>
            </div>

            <div className="cmdb-modal-footer">
              <button className="cmdb-btn-cancel" onClick={() => setCreateOpen(false)}>Cancel</button>
              <button className="cmdb-btn-save" onClick={handleCreate} disabled={creating}>
                {creating ? 'Creating…' : 'Create CI'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
