import { useState, useEffect } from 'react'
import {
  createPolicy, updatePolicy, listEventTypeTaxonomy, EventTypeTaxonomyEntry, getApprovedActions, ApprovedAction, getRunbooks, RunbookResponse,
  publishPolicy, getPolicyVersions, restorePolicyVersion, type VersionEntry,
} from '../services/api'
import { Policy } from '../types'
import './PolicyPage.css'

interface PolicyEditorProps {
  policy?: Policy
  onSave: (policy: Policy) => Promise<void>
  onCancel: () => void
}

const SEVERITY_LEVELS = ['low', 'medium', 'high', 'critical']
const ENVIRONMENTS    = ['dev', 'staging', 'prod']

// Diagnostic tools always run regardless of policy — they're read-only and
// never gated. Only actions that can actually change something on a resource
// are selectable here.
const ACTION_CATEGORY_LABELS: Record<string, string> = {
  remediation_safe:      'Remediation — Safe',
  remediation_intrusive: 'Remediation — Intrusive',
  notify:                'Notify',
}
const ACTION_CATEGORY_ORDER = ['remediation_safe', 'remediation_intrusive', 'notify']

const BLAST_RADIUS_COLOR: Record<number, string> = { 1: '#10b981', 2: '#f59e0b', 3: '#dc2626' }

export default function PolicyEditor({ policy, onSave, onCancel }: PolicyEditorProps) {
  const [name,           setName]           = useState(policy?.name || '')
  const [description,    setDescription]    = useState(policy?.description || '')
  const [minSeverity,    setMinSeverity]    = useState(policy?.rules?.min_severity || '')
  const [environment,    setEnvironment]    = useState(policy?.rules?.environment || '')
  const [minRiskScore,   setMinRiskScore]   = useState(policy?.rules?.min_risk_score?.toString() || '')
  const [service,        setService]        = useState(policy?.rules?.service || '')
  const [eventTypes,     setEventTypes]     = useState<string[]>(
    policy?.rules?.anomaly_type
      ? (Array.isArray(policy.rules.anomaly_type) ? policy.rules.anomaly_type : [policy.rules.anomaly_type])
      : []
  )
  const [eventTypeOptions, setEventTypeOptions] = useState<EventTypeTaxonomyEntry[]>([])
  const [eventTypeSearch, setEventTypeSearch]   = useState('')

  const [actionOptions, setActionOptions] = useState<ApprovedAction[]>([])

  useEffect(() => {
    listEventTypeTaxonomy({ enabled_only: true })
      .then(res => setEventTypeOptions(res.data))
      .catch(() => { /* taxonomy fetch failure shouldn't block editing the rest of the policy */ })
    getApprovedActions(true)
      .then(res => setActionOptions(res.data.filter(a => a.category !== 'diagnostic')))
      .catch(() => { /* action catalog fetch failure shouldn't block editing the rest of the policy */ })
    getRunbooks()
      .then(res => setRunbookOptions(res.data))
      .catch(() => { /* runbook catalog fetch failure shouldn't block editing the rest of the policy */ })
  }, [])
  const [allActions,     setAllActions]     = useState(policy?.approved_actions?.includes('*') ?? false)
  const [selectedActions, setSelectedActions] = useState<string[]>(
    policy?.approved_actions?.filter(a => a !== '*') || []
  )
  const [requiresApproval,        setRequiresApproval]        = useState(policy?.requires_manual_approval ?? false)
  const [approvalPriority,        setApprovalPriority]        = useState(policy?.approval_priority?.toString() || '50')
  const [confidenceGateEnabled,   setConfidenceGateEnabled]   = useState(
    policy?.confidence_gate_threshold != null
  )
  const [confidenceGateThreshold, setConfidenceGateThreshold] = useState(
    policy?.confidence_gate_threshold != null
      ? Math.round(policy.confidence_gate_threshold * 100).toString()
      : '90'
  )
  const [confidenceGateMinRuns,   setConfidenceGateMinRuns]   = useState(
    policy?.confidence_gate_min_runs?.toString() || '10'
  )
  const [confidenceGateRunbookId, setConfidenceGateRunbookId] = useState(
    policy?.confidence_gate_runbook_id || ''
  )
  const [runbookOptions, setRunbookOptions] = useState<RunbookResponse[]>([])
  const [maxBlastRadius,          setMaxBlastRadius]          = useState(policy?.constraints?.max_blast_radius?.toString() || '')
  const [loading, setLoading] = useState(false)
  const [error,   setError]   = useState('')

  // Draft/publish status — separate from the form fields above.
  const [status, setStatus] = useState<'draft' | 'published'>(
    policy?.status === 'published' ? 'published' : 'draft'
  )
  const [hasUnpublishedChanges, setHasUnpublishedChanges] = useState(!!policy?.has_unpublished_changes)
  const [publishing, setPublishing] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const [versions, setVersions] = useState<VersionEntry[]>([])
  const [versionsLoading, setVersionsLoading] = useState(false)

  const validate = () => {
    if (!name.trim()) {
      setError('Policy name is required')
      return false
    }
    if (!minSeverity && !environment && !minRiskScore && !service && eventTypes.length === 0) {
      setError('At least one matching condition must be specified')
      return false
    }
    if (!allActions && selectedActions.length === 0) {
      setError('Select at least one approved action (or allow all)')
      return false
    }
    return true
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!validate()) return
    setLoading(true)
    try {
      const rules: Record<string, unknown> = {}
      if (minSeverity)  rules.min_severity   = minSeverity
      if (environment)  rules.environment    = environment
      if (minRiskScore) rules.min_risk_score = parseInt(minRiskScore, 10)
      if (service)      rules.service        = service.trim()
      if (eventTypes.length > 0) rules.anomaly_type = eventTypes

      const constraints: Record<string, unknown> = {}
      if (maxBlastRadius) constraints.max_blast_radius = parseInt(maxBlastRadius, 10)

      const data: Partial<Policy> = {
        name:                    name.trim(),
        description:             description.trim() || undefined,
        rules,
        approved_actions:        allActions ? ['*'] : selectedActions,
        requires_manual_approval: requiresApproval,
        approval_priority:       parseInt(approvalPriority, 10),
        constraints,
        enabled:                 true,
        confidence_gate_threshold: (requiresApproval && confidenceGateEnabled)
          ? parseFloat(confidenceGateThreshold) / 100
          : undefined,
        confidence_gate_min_runs: (requiresApproval && confidenceGateEnabled)
          ? parseInt(confidenceGateMinRuns, 10)
          : undefined,
        confidence_gate_runbook_id: (requiresApproval && confidenceGateEnabled && confidenceGateRunbookId)
          ? confidenceGateRunbookId
          : undefined,
      }

      if (policy?.policy_id) {
        // Editing an existing policy: write to draft_snapshot only — keep the
        // editor open so the new Publish button is reachable afterward.
        const res = await updatePolicy(policy.policy_id, data)
        setStatus(res.data.status === 'published' ? 'published' : 'draft')
        setHasUnpublishedChanges(!!res.data.has_unpublished_changes)
      } else {
        // Brand-new policy: nothing to publish yet — create as a draft and
        // return to the list, matching the previous save-and-close behavior.
        await createPolicy(data)
        await onSave(data as Policy)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save policy')
    } finally {
      setLoading(false)
    }
  }

  const handlePublish = async () => {
    if (!policy?.policy_id) return
    setPublishing(true)
    setError('')
    try {
      const res = await publishPolicy(policy.policy_id)
      await onSave(res.data as unknown as Policy)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to publish policy')
    } finally {
      setPublishing(false)
    }
  }

  const loadVersions = async () => {
    if (!policy?.policy_id) return
    setVersionsLoading(true)
    try {
      const res = await getPolicyVersions(policy.policy_id)
      setVersions(res.data)
    } catch {
      setError('Failed to load version history')
    } finally {
      setVersionsLoading(false)
    }
  }

  const handleRestoreVersion = async (version: number) => {
    if (!policy?.policy_id) return
    try {
      const res = await restorePolicyVersion(policy.policy_id, version)
      const d = res.data
      setName(d.name)
      setSelectedActions((d.approved_actions || []).filter((a: string) => a !== '*'))
      setAllActions((d.approved_actions || []).includes('*'))
      setRequiresApproval(d.requires_manual_approval)
      setApprovalPriority(d.approval_priority?.toString() || '50')
      setMinSeverity(d.rules?.min_severity || '')
      setEnvironment(d.rules?.environment || '')
      setMinRiskScore(d.rules?.min_risk_score?.toString() || '')
      setService(d.rules?.service || '')
      setEventTypes(d.rules?.anomaly_type ? (Array.isArray(d.rules.anomaly_type) ? d.rules.anomaly_type : [d.rules.anomaly_type]) : [])
      setMaxBlastRadius(d.constraints?.max_blast_radius?.toString() || '')
      setHasUnpublishedChanges(true)
      setShowVersions(false)
    } catch {
      setError('Failed to restore version')
    }
  }

  const toggleAction = (action: string, checked: boolean) => {
    setSelectedActions(checked
      ? [...selectedActions, action]
      : selectedActions.filter(a => a !== action)
    )
  }

  return (
    <div className="policy-page">

      {/* Header */}
      <div className="pp-header">
        <div>
          <h1 className="pp-title">
            {policy?.policy_id ? 'Edit Policy' : 'Create Policy'}
          </h1>
          <p className="pp-subtitle">
            {policy?.policy_id
              ? `Editing: ${policy.name}`
              : 'Define conditions, approved actions, and approval requirements'}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {policy?.policy_id && (
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
        </div>
      </div>

      {/* Unpublished-changes notice — separate from the status badge above so
          "currently live" and "has pending edits" don't get conflated. The engine
          keeps using the last-published content the whole time this is showing;
          Save Draft never changes that. */}
      {policy?.policy_id && hasUnpublishedChanges && (
        <div className="flex items-center gap-2" style={{ padding: '0.5rem 1rem', borderRadius: '0.5rem', marginBottom: '1rem', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', color: '#f59e0b', fontSize: '0.875rem' }}>
          <span>You have draft changes that aren't live yet — the engine is still running the last published version. Click Publish to make these changes live.</span>
        </div>
      )}

      {/* Error Banner */}
      {error && <div className="pp-banner pp-banner-error">{error}</div>}

      <form className="pp-form" onSubmit={handleSubmit}>

        {/* ── Section: Policy Details ── */}
        <div className="pp-section">
          <h2 className="pp-section-title">Policy Details</h2>

          <div className="pp-field">
            <label className="pp-label">
              Policy Name <span className="pp-required">*</span>
            </label>
            <input
              className="pp-input"
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Production Critical Incidents"
            />
          </div>

          <div className="pp-field" style={{ marginTop: '0.75rem' }}>
            <label className="pp-label">Description</label>
            <textarea
              className="pp-input pp-textarea"
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Optional — describe when this policy applies"
              rows={3}
            />
          </div>

          <div className="pp-field" style={{ marginTop: '0.75rem', maxWidth: 280 }}>
            <label className="pp-label">Policy Priority</label>
            <input
              className="pp-input"
              type="number"
              min="1"
              max="100"
              value={approvalPriority}
              onChange={e => setApprovalPriority(e.target.value)}
            />
            <span className="pp-hint">
              1–100, lower number wins. When an incident matches more than one policy, only
              the lowest-priority-number match is applied — its conditions, approval
              requirement, and approved actions. Matching on more fields (environment +
              service + event type) does <strong>not</strong> automatically take precedence
              over a broader policy — priority number is the only thing that decides it.
            </span>
          </div>
        </div>

        {/* ── Section: Matching Rules ── */}
        <div className="pp-section">
          <h2 className="pp-section-title">
            Matching Rules{' '}
            <span className="pp-section-hint">(AND logic — all specified rules must match)</span>
          </h2>

          <div className="pp-grid-2">
            <div className="pp-field">
              <label className="pp-label">Minimum Severity</label>
              <select
                className="pp-input pp-select"
                value={minSeverity}
                onChange={e => setMinSeverity(e.target.value)}
              >
                <option value="">Any Severity</option>
                {SEVERITY_LEVELS.map(l => (
                  <option key={l} value={l}>
                    {l.charAt(0).toUpperCase() + l.slice(1)}
                  </option>
                ))}
              </select>
              <span className="pp-hint">Incidents at or above this severity will match</span>
            </div>

            <div className="pp-field">
              <label className="pp-label">Environment</label>
              <select
                className="pp-input pp-select"
                value={environment}
                onChange={e => setEnvironment(e.target.value)}
              >
                <option value="">Any Environment</option>
                {ENVIRONMENTS.map(env => (
                  <option key={env} value={env}>
                    {env.charAt(0).toUpperCase() + env.slice(1)}
                  </option>
                ))}
              </select>
            </div>

            <div className="pp-field">
              <label className="pp-label">Minimum Risk Score</label>
              <input
                className="pp-input"
                type="number"
                min="0"
                max="100"
                value={minRiskScore}
                onChange={e => setMinRiskScore(e.target.value)}
                placeholder="0–100 (leave empty for any)"
              />
              <span className="pp-hint">Leave empty to match any risk score</span>
            </div>

            <div className="pp-field">
              <label className="pp-label">Specific Service</label>
              <input
                className="pp-input"
                type="text"
                value={service}
                onChange={e => setService(e.target.value)}
                placeholder="e.g. api-server (leave empty for any)"
              />
            </div>
          </div>

          <div className="pp-field" style={{ marginTop: '0.75rem' }}>
            <label className="pp-label">Event Type</label>
            <span className="pp-hint" style={{ display: 'block', marginBottom: '0.4rem' }}>
              Match only specific event types from the taxonomy (leave empty to match any)
            </span>

            {eventTypes.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem', marginBottom: '0.5rem' }}>
                {eventTypes.map(code => {
                  const opt = eventTypeOptions.find(o => o.code === code)
                  return (
                    <span
                      key={code}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: '0.35rem',
                        fontSize: '12px', padding: '3px 8px', borderRadius: '999px',
                        backgroundColor: 'rgba(167,139,250,0.15)', color: '#a78bfa',
                        border: '1px solid rgba(167,139,250,0.3)',
                      }}
                    >
                      {opt?.label || code}
                      <button
                        type="button"
                        onClick={() => setEventTypes(eventTypes.filter(c => c !== code))}
                        style={{ background: 'none', border: 'none', color: '#a78bfa', cursor: 'pointer', fontSize: '13px', lineHeight: 1, padding: 0 }}
                        aria-label={`Remove ${opt?.label || code}`}
                      >
                        ×
                      </button>
                    </span>
                  )
                })}
              </div>
            )}

            <input
              className="pp-input"
              type="text"
              value={eventTypeSearch}
              onChange={e => setEventTypeSearch(e.target.value)}
              placeholder="Search event types (e.g. cpu, disk, database)…"
            />

            {eventTypeSearch.trim() && (
              <div
                style={{
                  marginTop: '0.4rem', maxHeight: '220px', overflowY: 'auto',
                  border: '1px solid var(--border, rgba(255,255,255,0.1))', borderRadius: '0.4rem',
                }}
              >
                {eventTypeOptions
                  .filter(o => {
                    const q = eventTypeSearch.trim().toLowerCase()
                    return !eventTypes.includes(o.code) && (
                      o.code.toLowerCase().includes(q)
                      || o.label.toLowerCase().includes(q)
                      || o.category.toLowerCase().includes(q)
                      || o.aliases.some(a => a.toLowerCase().includes(q))
                    )
                  })
                  .slice(0, 30)
                  .map(o => (
                    <button
                      type="button"
                      key={o.code}
                      onClick={() => { setEventTypes([...eventTypes, o.code]); setEventTypeSearch('') }}
                      style={{
                        display: 'block', width: '100%', textAlign: 'left',
                        padding: '0.4rem 0.6rem', background: 'none', border: 'none',
                        color: 'var(--txtP, #e8eef5)', cursor: 'pointer', fontSize: '13px',
                      }}
                    >
                      <span>{o.label}</span>
                      <span style={{ color: 'var(--txtS, #7a8ba3)', marginLeft: '0.5rem', fontSize: '11px' }}>
                        {o.category} · {o.code}
                      </span>
                    </button>
                  ))}
              </div>
            )}
          </div>
        </div>

        {/* ── Section: Approved Actions ── */}
        <div className="pp-section">
          <h2 className="pp-section-title">Approved Actions</h2>
          <p className="pp-section-desc">
            Which remediation actions are permitted when this policy matches
          </p>

          <label className="pp-checkbox-row pp-all-actions-row">
            <input
              type="checkbox"
              className="pp-checkbox"
              checked={allActions}
              onChange={e => {
                setAllActions(e.target.checked)
                if (e.target.checked) setSelectedActions([])
              }}
            />
            <span className="pp-checkbox-label">Allow all remediation actions (*)</span>
          </label>

          {!allActions && (
            actionOptions.length === 0 ? (
              <p className="pp-hint">Loading action catalog…</p>
            ) : (
              ACTION_CATEGORY_ORDER
                .filter(cat => actionOptions.some(a => a.category === cat))
                .map(cat => (
                  <div key={cat} style={{ marginBottom: '1rem' }}>
                    <p className="pp-hint" style={{ textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '0.4rem' }}>
                      {ACTION_CATEGORY_LABELS[cat] || cat}
                    </p>
                    <div className="pp-grid-2 pp-actions-grid">
                      {actionOptions
                        .filter(a => a.category === cat)
                        .map(a => (
                          <label key={a.tool_name} className="pp-checkbox-row" title={a.description}>
                            <input
                              type="checkbox"
                              className="pp-checkbox"
                              checked={selectedActions.includes(a.tool_name)}
                              onChange={e => toggleAction(a.tool_name, e.target.checked)}
                            />
                            <span className="pp-checkbox-label">{a.name}</span>
                            <span
                              style={{
                                fontSize: '10px', fontWeight: 700, padding: '1px 6px',
                                borderRadius: '4px', marginLeft: '6px',
                                color: BLAST_RADIUS_COLOR[a.blast_radius] || '#7a8ba3',
                                border: `1px solid ${BLAST_RADIUS_COLOR[a.blast_radius] || '#7a8ba3'}40`,
                              }}
                            >
                              BR{a.blast_radius}
                            </span>
                          </label>
                        ))}
                    </div>
                  </div>
                ))
            )
          )}
        </div>

        {/* ── Section: Approval Settings ── */}
        <div className="pp-section">
          <h2 className="pp-section-title">Approval Settings</h2>

          <div className="pp-field">
            <label className="pp-checkbox-row" style={{ marginBottom: '0.3rem' }}>
              <input
                type="checkbox"
                className="pp-checkbox"
                checked={requiresApproval}
                onChange={e => setRequiresApproval(e.target.checked)}
              />
              <span className="pp-checkbox-label pp-label">Requires Manual Approval</span>
            </label>
            <span className="pp-hint">
              When enabled, matching incidents must be approved before remediation runs
            </span>
          </div>

          {/* Confidence gate — only shown when manual approval is required */}
          {requiresApproval && (
            <div style={{ marginTop: '1.25rem', padding: '1rem', background: 'var(--surface-2, rgba(255,255,255,0.04))', borderRadius: '0.5rem', border: '1px solid rgba(167,139,250,0.2)' }}>
              <label className="pp-checkbox-row" style={{ marginBottom: '0.5rem' }}>
                <input
                  type="checkbox"
                  className="pp-checkbox"
                  checked={confidenceGateEnabled}
                  onChange={e => setConfidenceGateEnabled(e.target.checked)}
                />
                <span className="pp-checkbox-label pp-label" style={{ color: '#a78bfa' }}>
                  Enable Confidence Gate
                </span>
              </label>
              <span className="pp-hint" style={{ marginBottom: confidenceGateEnabled ? '0.75rem' : 0, display: 'block' }}>
                Automatically bypass manual approval once the runbook earns enough confidence through successful runs
              </span>

              {confidenceGateEnabled && (
                <div className="pp-grid-2" style={{ marginTop: '0.75rem' }}>
                  <div className="pp-field">
                    <label className="pp-label">Confidence Threshold (%)</label>
                    <input
                      className="pp-input"
                      type="number"
                      min="50"
                      max="100"
                      value={confidenceGateThreshold}
                      onChange={e => setConfidenceGateThreshold(e.target.value)}
                      placeholder="90"
                    />
                    <span className="pp-hint">Runbook must reach this confidence % to auto-approve</span>
                  </div>

                  <div className="pp-field">
                    <label className="pp-label">Minimum Successful Runs</label>
                    <input
                      className="pp-input"
                      type="number"
                      min="1"
                      value={confidenceGateMinRuns}
                      onChange={e => setConfidenceGateMinRuns(e.target.value)}
                      placeholder="10"
                    />
                    <span className="pp-hint">Runbook must have at least this many successful executions</span>
                  </div>
                </div>
              )}

              {confidenceGateEnabled && (
                <div className="pp-field" style={{ marginTop: '0.75rem' }}>
                  <label className="pp-label">Specific Runbook (optional)</label>
                  <select
                    className="pp-input"
                    value={confidenceGateRunbookId}
                    onChange={e => setConfidenceGateRunbookId(e.target.value)}
                  >
                    <option value="">Auto-select best match (default)</option>
                    {runbookOptions.map(rb => (
                      <option key={rb.runbook_id} value={rb.runbook_id}>
                        {rb.name} — confidence {Math.round((rb.confidence || 0) * 100)}%
                        {rb.success_rate != null ? `, success rate ${Math.round(rb.success_rate * 100)}%` : ''}
                      </option>
                    ))}
                  </select>
                  <span className="pp-hint">
                    Pin the gate to one named runbook instead of letting the event-type/service lookup pick whichever runbook matches at execution time
                  </span>
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Section: Constraints ── */}
        <div className="pp-section">
          <h2 className="pp-section-title">
            Constraints <span className="pp-section-hint">(Optional)</span>
          </h2>

          <div style={{ maxWidth: 360 }}>
            <div className="pp-field">
              <label className="pp-label">Maximum Blast Radius</label>
              <input
                className="pp-input"
                type="number"
                min="1"
                value={maxBlastRadius}
                onChange={e => setMaxBlastRadius(e.target.value)}
                placeholder="Max number of affected resources"
              />
              <span className="pp-hint">Leave empty for no blast radius limit</span>
            </div>
          </div>
        </div>

        {/* ── Footer Buttons ── */}
        <div className="pp-form-footer">
          <button
            type="button"
            className="pp-btn-secondary"
            onClick={onCancel}
            disabled={loading}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="pp-btn-secondary"
            disabled={loading}
          >
            {loading ? 'Saving…' : 'Save Draft'}
          </button>
          {policy?.policy_id && (
            <button
              type="button"
              className="pp-btn-primary"
              onClick={handlePublish}
              disabled={publishing || (status === 'published' && !hasUnpublishedChanges)}
              title={status === 'published' && !hasUnpublishedChanges ? 'No pending changes to publish' : 'Make the current draft live'}
            >
              {publishing ? 'Publishing…' : 'Publish'}
            </button>
          )}
        </div>

      </form>

      {/* Version history toggle + panel — co-located so the button is adjacent
          to the content it reveals. Button sits in the section header row. */}
      {policy?.policy_id && (
        <div className="pp-section" style={{ marginTop: '1.5rem' }}>
          <div className="flex items-center justify-between" style={{ marginBottom: showVersions ? '0.75rem' : 0 }}>
            <h2 className="pp-section-title" style={{ margin: 0 }}>Version History</h2>
            <button
              type="button"
              className="pp-btn-secondary"
              onClick={() => { setShowVersions(v => !v); if (!showVersions) loadVersions() }}
            >
              {showVersions ? 'Hide' : 'Show history'}
            </button>
          </div>

          {showVersions && (
            <>
              {versionsLoading && <p className="pp-hint" style={{ marginTop: '0.75rem' }}>Loading…</p>}
              {!versionsLoading && versions.length === 0 && (
                <p className="pp-hint" style={{ marginTop: '0.75rem' }}>
                  No published versions yet — publish a draft to create the first one.
                </p>
              )}
              <div className="space-y-2" style={{ marginTop: '0.5rem' }}>
                {versions.map(v => (
                  <div key={v.version} className="flex items-center justify-between" style={{ padding: '0.5rem 0.75rem', borderRadius: '0.5rem', background: 'rgba(148,163,184,0.05)', border: '1px solid rgba(148,163,184,0.15)' }}>
                    <span className="pp-hint">
                      <strong>v{v.version}</strong> — {new Date(v.created_at).toLocaleString()}
                      {v.change_note && <em> "{v.change_note}"</em>}
                    </span>
                    <button type="button" className="pp-btn-secondary" onClick={() => handleRestoreVersion(v.version)}>
                      Restore to draft
                    </button>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
