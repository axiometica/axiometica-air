import { useState, useEffect, useMemo } from 'react'
import { getPolicies, deletePolicy } from '../services/api'
import { Policy } from '../types'
import PolicyEditor from './PolicyEditor'
import PolicySimulator from './PolicySimulator'
import './PolicyPage.css'

type Tab = 'policies' | 'simulator'

const getEventTypes = (p: Policy): string[] => {
  const t = p.rules?.anomaly_type
  if (!t) return []
  return Array.isArray(t) ? t : [t]
}

const getConditionSummary = (p: Policy): string => {
  const parts: string[] = []
  const r = p.rules || {}
  if (r.min_severity)   parts.push(`Sev ≥ ${r.min_severity}`)
  if (r.environment)    parts.push(`Env: ${r.environment}`)
  if (r.min_risk_score) parts.push(`Risk ≥ ${r.min_risk_score}`)
  if (r.service)        parts.push(`Service: ${r.service}`)
  return parts.length > 0 ? parts.join(' · ') : 'All incidents'
}

const getActionSummary = (p: Policy): string =>
  p.approved_actions?.includes('*') ? 'All actions' : `${p.approved_actions?.length || 0} specific`

export default function PolicyList({ darkMode: _darkMode }: { darkMode?: boolean } = {}) {
  const [tab, setTab]                     = useState<Tab>('policies')
  const [policies, setPolicies]           = useState<Policy[]>([])
  const [loading, setLoading]             = useState(true)
  const [error, setError]                 = useState('')
  const [editingPolicy, setEditingPolicy] = useState<Policy | undefined>(undefined)
  const [showEditor, setShowEditor]       = useState(false)
  const [deleting, setDeleting]           = useState<string | null>(null)

  // Search + filter
  const [search,    setSearch]    = useState('')
  const [fEnabled,  setFEnabled]  = useState<'all' | 'enabled' | 'disabled'>('all')
  const [fApproval, setFApproval] = useState<'all' | 'required' | 'auto'>('all')
  const [fEventType, setFEventType] = useState('')

  useEffect(() => { loadPolicies() }, [])

  const loadPolicies = async () => {
    try {
      setLoading(true)
      const response = await getPolicies()
      // @ts-ignore
      setPolicies(Array.isArray(response.data) ? response.data : [])
      setError('')
    } catch {
      setError('Failed to load policies')
    } finally {
      setLoading(false)
    }
  }

  const handleSave = async (_p: Policy) => {
    await loadPolicies()
    setShowEditor(false)
    setEditingPolicy(undefined)
  }

  const handleDelete = async (policyId: string) => {
    if (!window.confirm('Delete this policy?')) return
    try {
      setDeleting(policyId)
      await deletePolicy(policyId)
      await loadPolicies()
    } catch {
      setError('Failed to delete policy')
    } finally {
      setDeleting(null)
    }
  }

  const openCreate  = () => { setEditingPolicy(undefined); setShowEditor(true) }
  const openEdit    = (p: Policy) => { setEditingPolicy(p); setShowEditor(true) }
  const closeEditor = () => { setShowEditor(false); setEditingPolicy(undefined) }

  const hasFilter = search || fEnabled !== 'all' || fApproval !== 'all' || fEventType
  const clearFilters = () => { setSearch(''); setFEnabled('all'); setFApproval('all'); setFEventType('') }

  const filteredPolicies = useMemo(() => {
    return policies
      .filter(p => {
        if (search) {
          const q = search.toLowerCase()
          if (!p.name.toLowerCase().includes(q) && !(p.description || '').toLowerCase().includes(q)) return false
        }
        if (fEnabled === 'enabled'  && !p.enabled) return false
        if (fEnabled === 'disabled' && p.enabled)  return false
        if (fApproval === 'required' && !p.requires_manual_approval) return false
        if (fApproval === 'auto'     && p.requires_manual_approval)  return false
        if (fEventType) {
          const types = getEventTypes(p)
          if (types.length > 0 && !types.some(t => t.toLowerCase().includes(fEventType.toLowerCase()))) return false
        }
        return true
      })
      .sort((a, b) => (a.approval_priority ?? 50) - (b.approval_priority ?? 50))
  }, [policies, search, fEnabled, fApproval, fEventType])

  if (showEditor) {
    return (
      <PolicyEditor
        policy={editingPolicy}
        onSave={handleSave}
        onCancel={closeEditor}
      />
    )
  }

  return (
    <div className="policy-page">

      {/* ── Page header ── */}
      <div className="pp-header">
        <div>
          <h1 className="pp-title">Governance Policies</h1>
          <p className="pp-subtitle">
            Define approval requirements and permitted remediations for specific incidents
          </p>
        </div>
        {tab === 'policies' && (
          <button className="pp-btn-primary" onClick={openCreate}>+ Create Policy</button>
        )}
      </div>

      {/* ── Tab strip ── */}
      <div className="pp-tab-strip">
        <button
          className={`pp-tab${tab === 'policies' ? ' active' : ''}`}
          onClick={() => setTab('policies')}
        >
          Policies
          {policies.length > 0 && (
            <span className="pp-tab-count">{policies.length}</span>
          )}
        </button>
        <button
          className={`pp-tab${tab === 'simulator' ? ' active' : ''}`}
          onClick={() => setTab('simulator')}
        >
          Match Simulator
        </button>
      </div>

      {error && <div className="pp-banner pp-banner-error">{error}</div>}

      {/* ══ Policies tab ══ */}
      {tab === 'policies' && (
        <>
          {/* Search + filters — only when there's something to filter */}
          {!loading && policies.length > 0 && (
            <div className="pp-search-bar">
              <input
                className="pp-search-input"
                type="text"
                placeholder="Search by name or description…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
              <select
                className="pp-filter-select"
                value={fEnabled}
                onChange={e => setFEnabled(e.target.value as typeof fEnabled)}
              >
                <option value="all">All status</option>
                <option value="enabled">Enabled</option>
                <option value="disabled">Disabled</option>
              </select>
              <select
                className="pp-filter-select"
                value={fApproval}
                onChange={e => setFApproval(e.target.value as typeof fApproval)}
              >
                <option value="all">All approval</option>
                <option value="required">Approval required</option>
                <option value="auto">Auto-execute</option>
              </select>
              <input
                className="pp-filter-select pp-filter-mono"
                type="text"
                placeholder="Filter event type…"
                value={fEventType}
                onChange={e => setFEventType(e.target.value)}
              />
              {hasFilter && (
                <button className="pp-filter-clear" onClick={clearFilters}>Clear</button>
              )}
            </div>
          )}

          {/* Body */}
          {loading ? (
            <div className="pp-skeletons">
              {[...Array(3)].map((_, i) => <div key={i} className="pp-skeleton" />)}
            </div>
          ) : policies.length === 0 ? (
            <div className="pp-empty">
              <p className="pp-empty-title">No policies yet</p>
              <p className="pp-empty-sub">
                Create your first policy to define approval requirements and permitted remediations
              </p>
              <button className="pp-btn-primary" onClick={openCreate}>Create Your First Policy</button>
            </div>
          ) : filteredPolicies.length === 0 ? (
            <div className="pp-empty" style={{ padding: '2rem 1rem' }}>
              <p className="pp-empty-title">No policies match your filters</p>
              <p className="pp-empty-sub">Try adjusting the search or filter criteria</p>
              <button className="pp-btn-secondary" onClick={clearFilters}>Clear filters</button>
            </div>
          ) : (
            <div className="pp-policy-list">
              {filteredPolicies.map(policy => {
                const eventTypes = getEventTypes(policy)
                const hasGate    = policy.confidence_gate_threshold != null && policy.confidence_gate_min_runs != null
                const gateLabel  = hasGate
                  ? `≥ ${Math.round(policy.confidence_gate_threshold! * 100)}% · ${policy.confidence_gate_min_runs} runs`
                  : '—'

                return (
                  <div key={policy.policy_id} className="pp-policy-card">

                    {/* ── Card header ── */}
                    <div className="pp-card-header">
                      <div className="pp-card-title-row">
                        <span className="pp-priority-chip">P{policy.approval_priority ?? 50}</span>
                        <h3 className="pp-card-name">{policy.name}</h3>
                        <div className="pp-card-badges">
                          {policy.status === 'draft' && (
                            <span className="pp-badge pp-badge-slate" title="Not yet published — won't be used for incident governance">
                              Draft
                            </span>
                          )}
                          {policy.status === 'published' && policy.has_unpublished_changes && (
                            <span className="pp-badge pp-badge-amber" title="Has draft edits not yet published">
                              Unpublished
                            </span>
                          )}
                          <span className={`pp-badge ${policy.enabled ? 'pp-badge-green' : 'pp-badge-slate'}`}>
                            {policy.enabled ? 'Enabled' : 'Disabled'}
                          </span>
                        </div>
                      </div>
                      {policy.description && (
                        <p className="pp-card-desc">{policy.description}</p>
                      )}
                    </div>

                    {/* ── Meta row 1: Conditions | Event Types | Approval ── */}
                    <div className="pp-card-meta">
                      <div className="pp-meta-item">
                        <span className="pp-meta-label">Conditions</span>
                        <span className="pp-meta-value">{getConditionSummary(policy)}</span>
                      </div>
                      <div className="pp-meta-divider" />
                      <div className="pp-meta-item">
                        <span className="pp-meta-label">Event Types</span>
                        {eventTypes.length > 0 ? (
                          <div className="pp-meta-chips">
                            {eventTypes.slice(0, 3).map(t => (
                              <span key={t} className="pp-event-chip">{t.replace(/_/g, '.')}</span>
                            ))}
                            {eventTypes.length > 3 && (
                              <span className="pp-event-chip pp-event-chip-more">
                                +{eventTypes.length - 3}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="pp-meta-value">Any</span>
                        )}
                      </div>
                      <div className="pp-meta-divider" />
                      <div className="pp-meta-item">
                        <span className="pp-meta-label">Approval</span>
                        <span className={`pp-meta-value${policy.requires_manual_approval ? ' required' : ''}`}>
                          {policy.requires_manual_approval ? 'Required' : 'Auto-execute'}
                        </span>
                      </div>
                    </div>

                    {/* ── Meta row 2: Actions | Confidence Gate | Constraints ── */}
                    <div className="pp-card-meta pp-card-meta-secondary">
                      <div className="pp-meta-item">
                        <span className="pp-meta-label">Actions</span>
                        <span className="pp-meta-value">{getActionSummary(policy)}</span>
                      </div>
                      <div className="pp-meta-divider" />
                      <div className="pp-meta-item">
                        <span className="pp-meta-label">Confidence Gate</span>
                        <span className={`pp-meta-value${hasGate ? ' pp-meta-gate' : ''}`}>
                          {gateLabel}
                        </span>
                      </div>
                      <div className="pp-meta-divider" />
                      <div className="pp-meta-item">
                        <span className="pp-meta-label">Constraints</span>
                        <span className="pp-meta-value">
                          {policy.constraints?.max_blast_radius ? `BR ≤ ${policy.constraints.max_blast_radius}` : '—'}
                        </span>
                      </div>
                    </div>

                    {/* ── Card footer ── */}
                    <div className="pp-card-actions">
                      <button className="pp-btn-secondary" onClick={() => openEdit(policy)}>
                        Edit
                      </button>
                      <button
                        className="pp-btn-danger"
                        disabled={deleting === policy.policy_id}
                        onClick={() => handleDelete(policy.policy_id)}
                      >
                        {deleting === policy.policy_id ? 'Deleting…' : 'Delete'}
                      </button>
                    </div>

                  </div>
                )
              })}
            </div>
          )}
        </>
      )}

      {/* ══ Simulator tab ══ */}
      {tab === 'simulator' && (
        <PolicySimulator policies={policies} />
      )}

    </div>
  )
}
