import { useState, useEffect } from 'react'
import { getRunbooks, deleteRunbook } from '../services/api'
import {
  IconBook,
  IconPlus,
  IconPencil,
  IconTrash,
  IconSitemap,
  IconSearch,
  IconChevronRight,
  IconChevronDown,
  IconAlertTriangle,
  IconCheck,
  IconShield,
  IconCode,
} from './icons'
import { formatStepParams } from './RunbookEditor'
import { getApprovedActions } from '../services/api'

interface Runbook {
  runbook_id: string
  name: string
  description: string
  event_type: string
  service?: string
  environment?: string
  diagnostics: any[]
  actions: any[]
  verification_steps: any[]
  confidence: number
  blast_radius: number
  enabled: boolean
  source?: string  // operator_authored | ai_generated
  created_at: string
  // Execution feedback
  total_executions?: number
  successful_executions?: number
  failed_executions?: number
  success_rate?: number | null
  confidence_trend?: 'up' | 'down' | 'stable' | 'new' | null
  last_executed_at?: string | null
  is_seeded?: boolean
  status?: 'draft' | 'published'
  has_unpublished_changes?: boolean
}

interface RunbookListProps {
  darkMode?: boolean
  onEdit: (runbookId: string) => void
  onNew: () => void
}

const BLAST_LABELS: Record<number, { label: string; color: string }> = {
  1: { label: 'Low',    color: 'text-success-500 border-success-500' },
  2: { label: 'Medium', color: 'text-warning-500 border-warning-500' },
  3: { label: 'High',   color: 'text-critical-500 border-critical-500' },
}

const confidenceColor = (c: number) => {
  if (c >= 0.80) return '#10b981'   // green
  if (c >= 0.60) return '#f59e0b'   // amber
  return '#ef4444'                  // red
}

const successRateColor = (r: number) => {
  if (r >= 0.80) return '#10b981'
  if (r >= 0.50) return '#f59e0b'
  return '#ef4444'
}

type Trend = 'up' | 'down' | 'stable' | 'new' | null | undefined
function TrendBadge({ trend }: { trend: Trend }) {
  if (!trend || trend === 'new') return (
    <span title="Not enough data yet" style={{ fontSize: '0.6rem', color: '#4b5563', fontWeight: 600 }}>NEW</span>
  )
  if (trend === 'up')   return <span title="Improving" style={{ color: '#10b981', fontSize: '0.85rem', lineHeight: 1 }}>↑</span>
  if (trend === 'down') return <span title="Declining"  style={{ color: '#ef4444', fontSize: '0.85rem', lineHeight: 1 }}>↓</span>
  return <span title="Stable" style={{ color: '#6b7280', fontSize: '0.85rem', lineHeight: 1 }}>→</span>
}

export default function RunbookList({ onEdit, onNew }: RunbookListProps) {
  const [runbooks, setRunbooks] = useState<Runbook[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [toolLabels, setToolLabels] = useState<Record<string, string>>({})

  useEffect(() => {
    loadRunbooks()
    getApprovedActions(true).then(res => {
      const labels: Record<string, string> = {}
      for (const a of (res.data ?? [])) labels[a.tool_name] = a.name
      setToolLabels(labels)
    }).catch(() => {})
  }, [])

  const loadRunbooks = async () => {
    try {
      setLoading(true)
      const res = await getRunbooks()
      setRunbooks(res.data as Runbook[])
      setError(null)
    } catch {
      setError('Failed to load runbooks')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (confirmDelete !== id) {
      setConfirmDelete(id)
      return
    }
    try {
      setDeletingId(id)
      await deleteRunbook(id)
      setRunbooks(prev => prev.filter(r => r.runbook_id !== id))
      setConfirmDelete(null)
    } catch {
      setError('Failed to delete runbook')
    } finally {
      setDeletingId(null)
    }
  }

  const filtered = runbooks.filter(r =>
    r.name.toLowerCase().includes(search.toLowerCase()) ||
    r.event_type.toLowerCase().includes(search.toLowerCase()) ||
    (r.service || '').toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div className="page-transition-enter">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h2 className="text-section-title mb-1" style={{ color: '#e8eef5' }}>Runbooks</h2>
          <p className="text-sm" style={{ color: '#a0aec0' }}>
            Remediation procedures used by the automation engine — includes AI-generated runbooks pending review
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => window.open('/editor/', '_blank', 'noopener')}
            className="btn btn-primary flex items-center gap-2"
          >
            <IconPlus size={18} />
            New Runbook
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-6">
        <IconSearch size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-secondary" />
        <input
          type="text"
          placeholder="Search by name, event type, or service…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full pl-9 pr-4 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-info-500/60 focus:ring-1 focus:ring-info-500/30"
        />
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 border border-slate-600 text-sm mb-4" style={{ color: '#a0aec0' }}>
          <IconAlertTriangle size={15} className="text-warning-500 flex-shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Loading */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="metric-card h-36 skeleton-pulse" style={{ animationDelay: `${i * 60}ms` }} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state py-20">
          <div className="flex justify-center mb-4" style={{ color: '#a0aec0' }}>
            <IconBook size={40} strokeWidth={1.5} />
          </div>
          <h4 className="empty-state-title">
            {search ? 'No matching runbooks' : 'No runbooks yet'}
          </h4>
          <p className="empty-state-description">
            {search ? 'Try a different search term' : 'Create your first runbook to define automated remediation procedures'}
          </p>
          {!search && (
            <button onClick={onNew} className="btn btn-primary mt-4 mx-auto flex items-center gap-2">
              <IconPlus size={16} />
              Create Runbook
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 stagger-children">
          {filtered.map((runbook, index) => {
            const blast = BLAST_LABELS[runbook.blast_radius] ?? BLAST_LABELS[1]
            const isDeleting = deletingId === runbook.runbook_id
            const isConfirming = confirmDelete === runbook.runbook_id

            return (
              <div
                key={runbook.runbook_id}
                className={`card-interactive group relative overflow-hidden border-l-4 ${runbook.enabled ? 'border-info-500/50' : 'border-slate-600/50'}`}
                style={{
                  animation: 'staggerFadeIn 0.4s ease-out forwards',
                  animationDelay: `${index * 60}ms`,
                  opacity: 0,
                  ...(runbook.enabled ? {} : { backgroundColor: 'rgba(30,35,50,0.7)' }),
                }}
              >
                {/* Top row: name + actions */}
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <IconBook size={18} className={`flex-shrink-0 ${runbook.enabled ? 'text-info-500' : 'text-slate-500'}`} />
                    <h3 className="font-semibold text-sm truncate" style={{ color: runbook.enabled ? '#e8eef5' : '#7a8ba3' }}>
                      {runbook.name}
                    </h3>
                    {runbook.source === 'ai_generated' && (
                      <span className="flex-shrink-0 text-xs font-bold px-1.5 py-0.5 rounded"
                        style={{ color: '#a78bfa', backgroundColor: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.35)', letterSpacing: '0.04em' }}
                        title="Generated by AI — review and enable before use">
                        AI
                      </span>
                    )}
                    {runbook.is_seeded && (
                      <span className="flex-shrink-0 text-xs font-medium px-1.5 py-0.5 rounded border border-slate-600 text-slate-400 bg-slate-800/60"
                        title="Platform-seeded runbook — cannot be deleted">
                        Built-in
                      </span>
                    )}
                    {!runbook.enabled && (
                      <span className="flex-shrink-0 text-xs font-medium px-1.5 py-0.5 rounded border border-slate-600 text-slate-400 bg-slate-800/60">
                        {runbook.source === 'ai_generated' ? 'Pending Review' : 'Disabled'}
                      </span>
                    )}
                    {runbook.status === 'draft' && (
                      <span className="flex-shrink-0 text-xs font-medium px-1.5 py-0.5 rounded"
                        style={{ color: '#94a3b8', backgroundColor: 'rgba(148,163,184,0.1)', border: '1px solid rgba(148,163,184,0.3)' }}
                        title="Not yet published — won't be used for incident matching">
                        Draft
                      </span>
                    )}
                    {runbook.status === 'published' && runbook.has_unpublished_changes && (
                      <span className="flex-shrink-0 text-xs font-medium px-1.5 py-0.5 rounded"
                        style={{ color: '#f59e0b', backgroundColor: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)' }}
                        title="Has draft edits not yet published">
                        Unpublished changes
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      onClick={() => window.open(`/editor/?id=${runbook.runbook_id}`, '_blank', 'noopener')}
                      className="p-1.5 rounded-lg transition-colors"
                      style={{ color: '#10b981' }}
                      title="Edit in Visual Editor"
                    >
                      <IconSitemap size={15} />
                    </button>
                    <button
                      onClick={() => onEdit(runbook.runbook_id)}
                      className="p-1.5 rounded-lg text-gray-400 hover:text-info-400 hover:bg-slate-700 transition-colors"
                      title="Edit (JSON)"
                    >
                      <IconPencil size={15} />
                    </button>
                    {runbook.is_seeded ? (
                      <span
                        className="p-1.5 rounded-lg text-slate-600 cursor-not-allowed"
                        title="Built-in runbooks cannot be deleted — disable instead"
                      >
                        <IconTrash size={15} />
                      </span>
                    ) : (
                      <button
                        onClick={() => handleDelete(runbook.runbook_id)}
                        disabled={isDeleting}
                        className={`p-1.5 rounded-lg transition-colors ${
                          isConfirming
                            ? 'text-critical-500 bg-critical-900/30 hover:bg-critical-900/50'
                            : 'text-gray-400 hover:text-critical-400 hover:bg-slate-700'
                        }`}
                        title={isConfirming ? 'Click again to confirm delete' : 'Delete'}
                      >
                        {isDeleting ? (
                          <span className="text-xs">…</span>
                        ) : (
                          <IconTrash size={15} />
                        )}
                      </button>
                    )}
                  </div>
                </div>

                {/* Description */}
                {runbook.description && (
                  <p className="text-xs mb-3 line-clamp-2" style={{ color: '#a0aec0' }}>
                    {runbook.description}
                  </p>
                )}

                {/* Badges row */}
                <div className="flex flex-wrap gap-2 mb-3">
                  <span className="badge badge-low text-xs">{runbook.event_type}</span>
                  {runbook.environment && (
                    <span className="badge bg-transparent text-white border border-slate-500 text-xs">
                      {runbook.environment}
                    </span>
                  )}
                  {runbook.service && (
                    <span className="badge bg-transparent text-white border border-slate-600 text-xs">
                      {runbook.service}
                    </span>
                  )}
                  <span className={`badge bg-transparent border text-xs ${blast.color}`}>
                    Blast {blast.label}
                  </span>
                </div>

                {/* Step counts + expand toggle */}
                <div className="flex items-center gap-4 pt-2 border-t border-slate-700/40">
                  <StepCount icon={<IconSearch size={13} />} count={runbook.diagnostics.length} label="Diagnostics" />
                  <StepCount icon={<IconCode size={13} />}   count={runbook.actions.length}     label="Actions" />
                  <StepCount icon={<IconCheck size={13} />}  count={runbook.verification_steps.length} label="Verifications" />
                  {/* Confidence + trend */}
                  <div className="ml-auto flex items-center gap-2">
                    <div className="flex items-center gap-1.5">
                      <IconShield size={13} style={{ color: confidenceColor(runbook.confidence) }} />
                      <span className="text-xs font-semibold" style={{ color: confidenceColor(runbook.confidence) }}>
                        {Math.round(runbook.confidence * 100)}%
                      </span>
                      <TrendBadge trend={runbook.confidence_trend} />
                    </div>
                    {/* Expand toggle — only show if there are steps */}
                    {(runbook.diagnostics.length + runbook.actions.length + runbook.verification_steps.length) > 0 && (
                      <button
                        onClick={e => { e.stopPropagation(); setExpandedId(expandedId === runbook.runbook_id ? null : runbook.runbook_id) }}
                        className="flex items-center gap-0.5 text-xs hover:text-info-400 transition-colors"
                        style={{ color: '#7a8ba3' }}
                        title={expandedId === runbook.runbook_id ? 'Hide steps' : 'Show steps & parameters'}
                      >
                        <IconChevronDown
                          size={14}
                          style={{ transform: expandedId === runbook.runbook_id ? 'rotate(180deg)' : 'rotate(0deg)', transition: 'transform 0.2s' }}
                        />
                        steps
                      </button>
                    )}
                  </div>
                </div>

                {/* Expandable step detail */}
                {expandedId === runbook.runbook_id && (
                  <div className="mt-3 pt-3 border-t border-slate-700/30 space-y-2.5">
                    <StepGroup icon={<IconSearch size={12} />} label="Diagnostics" steps={runbook.diagnostics} toolLabels={toolLabels} />
                    <StepGroup icon={<IconCode size={12} />}   label="Actions"     steps={runbook.actions} toolLabels={toolLabels} />
                    <StepGroup icon={<IconCheck size={12} />}  label="Verification" steps={runbook.verification_steps} verification toolLabels={toolLabels} />
                  </div>
                )}

                {/* Execution stats bar — only shown once there's data */}
                {(runbook.total_executions ?? 0) > 0 && (
                  <div className="mt-2 pt-2 border-t border-slate-700/30">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs" style={{ color: '#7a8ba3' }}>
                        {runbook.successful_executions}/{runbook.total_executions} runs succeeded
                      </span>
                      <span className="text-xs font-medium" style={{ color: '#7a8ba3' }}>
                        {Math.round((runbook.success_rate ?? 0) * 100)}% success rate
                      </span>
                    </div>
                    <div style={{ height: 4, borderRadius: 2, backgroundColor: '#1e2535', overflow: 'hidden' }}>
                      <div style={{
                        height: '100%',
                        width: `${Math.round((runbook.success_rate ?? 0) * 100)}%`,
                        backgroundColor: successRateColor(runbook.success_rate ?? 0),
                        borderRadius: 2,
                        transition: 'width 0.4s ease',
                      }} />
                    </div>
                  </div>
                )}

                {/* Confirm delete banner */}
                {isConfirming && !isDeleting && (
                  <div className="absolute inset-x-0 bottom-0 bg-critical-900/90 px-4 py-2 flex items-center justify-between text-xs text-critical-200">
                    <span>Delete this runbook?</span>
                    <div className="flex gap-2">
                      <button onClick={() => setConfirmDelete(null)} className="underline">Cancel</button>
                      <button onClick={() => handleDelete(runbook.runbook_id)} className="font-semibold text-critical-400">
                        Confirm
                      </button>
                    </div>
                  </div>
                )}

                {/* Hover arrow */}
                <div className="absolute right-3 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-30 transition-opacity">
                  <IconChevronRight size={20} />
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Dismiss confirm on outside area */}
      {confirmDelete && (
        <div className="fixed inset-0 z-10" onClick={() => setConfirmDelete(null)} />
      )}
    </div>
  )
}

function StepCount({ icon, count, label }: { icon: React.ReactNode; count: number; label: string }) {
  return (
    <div className="flex items-center gap-1" style={{ color: '#7a8ba3' }}>
      {icon}
      <span className="text-xs">{count} {label}</span>
    </div>
  )
}

/** Expandable list of steps with formatted parameters for one section (diagnostics/actions/verification). */
function StepGroup({
  icon, label, steps, verification = false, toolLabels = {},
}: {
  icon: React.ReactNode
  label: string
  steps: any[]
  verification?: boolean
  toolLabels?: Record<string, string>
}) {
  if (steps.length === 0) return null
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide mb-1.5 flex items-center gap-1" style={{ color: '#7a8ba3' }}>
        {icon} {label}
      </p>
      <ol className="space-y-1 pl-3">
        {steps.map((step: any, i: number) => {
          if (verification) {
            // Verification step: "metric check value"
            const parts = [step.metric, step.check?.replace('_', ' '), step.value].filter(Boolean).join(' ')
            return (
              <li key={i} className="text-xs flex gap-1.5" style={{ color: '#a0aec0' }}>
                <span className="flex-shrink-0 font-mono" style={{ color: '#4b5563' }}>{i + 1}.</span>
                <span>
                  {step.description && <span className="font-medium" style={{ color: '#c4cfd9' }}>{step.description}</span>}
                  {parts && <span style={{ color: '#7a8ba3' }}>{step.description ? ' — ' : ''}{parts}</span>}
                </span>
              </li>
            )
          }
          const toolLabel = toolLabels[step.tool] ?? step.tool ?? '—'
          const params = formatStepParams(step.args)
          const command = step.command || ''
          return (
            <li key={i} className="text-xs space-y-0.5" style={{ color: '#a0aec0' }}>
              <div className="flex gap-1.5 items-start">
                <span className="flex-shrink-0 font-mono mt-0.5" style={{ color: '#4b5563' }}>{i + 1}.</span>
                <span>
                  {step.description && (
                    <span className="font-medium" style={{ color: '#c4cfd9' }}>{step.description} — </span>
                  )}
                  <span className="font-mono text-xs px-1 py-0.5 rounded" style={{ background: '#1a2035', color: '#60a5fa' }}>
                    {toolLabel}
                  </span>
                  {params && (
                    <span className="ml-1.5" style={{ color: '#7a8ba3' }}>{params}</span>
                  )}
                </span>
              </div>
              {command && (
                <div className="ml-5 font-mono text-xs px-2 py-1 rounded" style={{ background: '#0d1117', color: '#7ee787', border: '1px solid #21262d' }}>
                  $ {command}
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
