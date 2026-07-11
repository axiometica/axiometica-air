import { useState, useEffect } from 'react'
import axios from 'axios'
import {
  IconShieldCheck,
  IconPlus,
  IconPencil,
  IconTrash,
  IconSearch,
  IconAlertTriangle,
  IconShield,
  IconBolt,
  IconActivity,
  IconLock,
  IconBell,
} from './icons'
import ToolBuilder from './ToolBuilder'

export interface ProcessRule {
  priority: number
  allow: boolean
  pattern: string
  description: string
}

export interface ActionParameter {
  name: string
  type: 'string' | 'integer' | 'float' | 'boolean'
  required: boolean
  default?: any
  description?: string
}

export interface OutputField {
  field: string
  kind: 'regex' | 'jsonpath'
  pattern: string
  type: 'boolean' | 'integer' | 'float' | 'string'
}

export interface ApprovedAction {
  action_id: string
  tool_name: string
  name: string
  description: string
  /** Default / fallback command (any environment) */
  command: string
  /** Per-environment command overrides. Keys: docker | kubernetes | ssh | aws_ssm | vcenter | any */
  command_variants: Record<string, string>
  category: 'diagnostic' | 'remediation_safe' | 'remediation_intrusive' | 'notify'
  blast_radius: number
  requires_approval: boolean
  enabled: boolean
  parameters: ActionParameter[]
  process_rules: ProcessRule[] | null
  output_fields: OutputField[]
  is_builtin: boolean
  created_at: string
  updated_at: string
}

interface Props {
  onEdit: (actionId: string) => void
  onNew: () => void
}

const CATEGORY_META: Record<string, { label: string; icon: React.ReactNode; color: string; border: string }> = {
  diagnostic: {
    label: 'Diagnostic',
    icon: <IconActivity size={14} />,
    color: 'text-info-400',
    border: 'border-l-info-500',
  },
  remediation_safe: {
    label: 'Remediation · Safe',
    icon: <IconShield size={14} />,
    color: 'text-success-400',
    border: 'border-l-success-500',
  },
  remediation_intrusive: {
    label: 'Remediation · Intrusive',
    icon: <IconBolt size={14} />,
    color: 'text-critical-400',
    border: 'border-l-critical-500',
  },
  notify: {
    label: 'Notify',
    icon: <IconBell size={14} />,
    color: 'text-purple-400',
    border: 'border-l-purple-500',
  },
}

const BLAST_LABELS: Record<number, { label: string; color: string }> = {
  1: { label: 'Low',    color: 'text-success-400 border-success-500' },
  2: { label: 'Medium', color: 'text-warning-400 border-warning-500' },
  3: { label: 'High',   color: 'text-critical-400 border-critical-500' },
}

const CATEGORIES = ['all', 'diagnostic', 'remediation_safe', 'remediation_intrusive', 'notify'] as const

export default function ApprovedActionsList({ onEdit, onNew }: Props) {
  const [actions, setActions]           = useState<ApprovedAction[]>([])
  const [loading, setLoading]           = useState(true)
  const [error, setError]               = useState<string | null>(null)
  const [search, setSearch]             = useState('')
  const [catFilter, setCatFilter]       = useState<string>('all')
  const [deletingId, setDeletingId]     = useState<string | null>(null)
  const [confirmId, setConfirmId]       = useState<string | null>(null)
  const [showAIBuilder, setShowAIBuilder] = useState(false)

  useEffect(() => { load() }, [])

  const load = async () => {
    try {
      setLoading(true)
      const { data } = await axios.get<ApprovedAction[]>('/api/approved-actions')
      setActions(data)
      setError(null)
    } catch {
      setError('Failed to load approved actions')
    } finally {
      setLoading(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (confirmId !== id) { setConfirmId(id); return }
    try {
      setDeletingId(id)
      await axios.delete(`/api/approved-actions/${id}`)
      setActions(prev => prev.filter(a => a.action_id !== id))
      setConfirmId(null)
    } catch {
      setError('Failed to delete action')
    } finally {
      setDeletingId(null)
    }
  }

  const filtered = actions.filter(a => {
    const matchCat = catFilter === 'all' || a.category === catFilter
    const q = search.toLowerCase()
    const matchQ  = !q || a.name.toLowerCase().includes(q) ||
                    a.tool_name.toLowerCase().includes(q) ||
                    (a.description || '').toLowerCase().includes(q)
    return matchCat && matchQ
  })

  // Group by category for display
  const grouped: Record<string, ApprovedAction[]> = {}
  for (const a of filtered) {
    if (!grouped[a.category]) grouped[a.category] = []
    grouped[a.category].push(a)
  }
  const categoryOrder = ['diagnostic', 'remediation_safe', 'remediation_intrusive', 'notify']

  const counts = {
    all: actions.length,
    diagnostic: actions.filter(a => a.category === 'diagnostic').length,
    remediation_safe: actions.filter(a => a.category === 'remediation_safe').length,
    remediation_intrusive: actions.filter(a => a.category === 'remediation_intrusive').length,
    notify: actions.filter(a => a.category === 'notify').length,
  }

  return (
    <div className="page-transition-enter">

      {/* Header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h2 className="text-section-title mb-1" style={{ color: '#e8eef5' }}>Approved Actions</h2>
          <p className="text-sm" style={{ color: '#a0aec0' }}>
            Catalog of diagnostic and remediation actions. Intrusive actions enforce
            per-process regex allow/deny rules before execution.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
          <button
            onClick={() => setShowAIBuilder(v => !v)}
            className="btn flex items-center gap-2"
            style={{
              background: showAIBuilder ? 'rgba(139,92,246,0.15)' : 'transparent',
              border: `1px solid ${showAIBuilder ? '#8b5cf6' : '#3d4557'}`,
              color: showAIBuilder ? '#a78bfa' : '#a0aec0',
              padding: '7px 14px',
              borderRadius: 7,
              fontSize: '0.82rem',
              fontWeight: 600,
              cursor: 'pointer',
            }}
          >
            ✦ Generate with AI
          </button>
          <button onClick={onNew} className="btn btn-primary flex items-center gap-2">
            <IconPlus size={18} />
            New Action
          </button>
        </div>
      </div>

      {/* AI Tool Builder panel */}
      {showAIBuilder && (
        <div style={{ marginBottom: '1.5rem' }}>
          <ToolBuilder
            isExpanded={true}
            onToggle={() => setShowAIBuilder(false)}
            onRegistered={() => { load(); setShowAIBuilder(false) }}
          />
        </div>
      )}

      {/* Filter tabs */}
      <div className="flex items-center gap-2 mb-5 flex-wrap">
        {CATEGORIES.map(cat => {
          const meta = cat === 'all' ? null : CATEGORY_META[cat]
          const isActive = catFilter === cat
          return (
            <button
              key={cat}
              onClick={() => setCatFilter(cat)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${
                isActive
                  ? 'bg-slate-700 border-slate-500 text-gray-100'
                  : 'bg-slate-800/50 border-slate-700 text-gray-400 hover:text-gray-200 hover:border-slate-600'
              }`}
            >
              {meta?.icon}
              <span>{meta?.label ?? 'All Actions'}</span>
              <span className={`ml-1 px-1.5 py-0.5 rounded text-xs font-semibold ${
                isActive ? 'bg-slate-600' : 'bg-slate-700'
              }`}>
                {counts[cat]}
              </span>
            </button>
          )
        })}

        {/* Search */}
        <div className="relative ml-auto">
          <IconSearch size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-500" />
          <input
            type="text"
            placeholder="Search actions…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-8 pr-3 py-1.5 text-xs rounded-lg bg-slate-800 border border-slate-700 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-info-500/60 focus:ring-1 focus:ring-info-500/30 w-48"
          />
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 border border-slate-600 text-sm mb-4" style={{ color: '#a0aec0' }}>
          <IconAlertTriangle size={15} className="text-warning-500 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Loading */}
      {loading ? (
        <div className="space-y-3">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="metric-card h-16 skeleton-pulse" style={{ animationDelay: `${i * 40}ms` }} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-state py-20">
          <div className="flex justify-center mb-4" style={{ color: '#a0aec0' }}>
            <IconShieldCheck size={40} strokeWidth={1.5} />
          </div>
          <h4 className="empty-state-title">No matching actions</h4>
          <p className="empty-state-description">
            {search ? 'Try a different search term' : 'No actions in this category'}
          </p>
        </div>
      ) : (
        <div className="space-y-8">
          {categoryOrder.filter(cat => grouped[cat]?.length).map(cat => {
            const meta = CATEGORY_META[cat]
            return (
              <div key={cat}>
                {/* Category heading */}
                <div className="flex items-center gap-2 mb-3">
                  <span className={meta.color}>{meta.icon}</span>
                  <h3 className="text-xs font-semibold uppercase tracking-widest" style={{ color: '#7a8ba3' }}>
                    {meta.label}
                  </h3>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700 text-gray-400 font-medium">
                    {grouped[cat].length}
                  </span>
                  <div className="flex-1 border-t border-slate-700/50 ml-2" />
                </div>

                {/* Action rows */}
                <div className="space-y-1.5">
                  {grouped[cat].map((action, idx) => {
                    const blast     = BLAST_LABELS[action.blast_radius] ?? BLAST_LABELS[1]
                    const isDeleting   = deletingId === action.action_id
                    const isConfirming = confirmId  === action.action_id

                    return (
                      <div
                        key={action.action_id}
                        className={`group relative flex items-center gap-4 px-4 py-3 rounded-lg border-l-2 transition-all
                          bg-slate-800/60 hover:bg-slate-800 border border-slate-700/50 hover:border-slate-600
                          ${meta.border}
                          ${!action.enabled ? 'opacity-50' : ''}
                        `}
                        style={{
                          animation: 'staggerFadeIn 0.3s ease-out forwards',
                          animationDelay: `${idx * 30}ms`,
                          opacity: 0,
                        }}
                      >
                        {/* Tool name pill */}
                        <code className="text-xs font-mono px-2 py-0.5 rounded bg-slate-700 text-gray-300 flex-shrink-0 w-48 truncate">
                          {action.tool_name}
                        </code>

                        {/* Name + description */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium truncate" style={{ color: '#e8eef5' }}>
                              {action.name}
                            </span>
                            {action.requires_approval && (
                              <span className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-warning-900/40 border border-warning-700/50 text-warning-400">
                                <IconLock size={10} /> Approval
                              </span>
                            )}
                            {action.process_rules && (
                              <span className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-slate-700 border border-slate-600 text-gray-400">
                                <IconShield size={10} />
                                {action.process_rules.filter(r => r.allow).length} allow /&nbsp;
                                {action.process_rules.filter(r => !r.allow).length} deny rules
                              </span>
                            )}
                          </div>
                          <p className="text-xs truncate mt-0.5" style={{ color: '#7a8ba3' }}>
                            {action.description}
                          </p>
                          {action.command && (
                            <p className="text-xs font-mono mt-1 truncate px-1.5 py-0.5 rounded" style={{ background: '#0d1117', color: '#7ee787', border: '1px solid #21262d' }}>
                              $ {action.command}
                            </p>
                          )}
                        </div>

                        {/* Blast radius */}
                        <span className={`text-xs border rounded px-2 py-0.5 font-semibold flex-shrink-0 ${blast.color}`}>
                          Blast {blast.label}
                        </span>

                        {/* Params count */}
                        <span className="text-xs flex-shrink-0" style={{ color: '#7a8ba3' }}>
                          {action.parameters.length} param{action.parameters.length !== 1 ? 's' : ''}
                        </span>

                        {/* Actions */}
                        <div className="flex items-center gap-1 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button
                            onClick={() => onEdit(action.action_id)}
                            className="p-1.5 rounded text-gray-400 hover:text-info-400 hover:bg-slate-700 transition-colors"
                            title="Edit"
                          >
                            <IconPencil size={14} />
                          </button>
                          {action.is_builtin ? (
                            <span
                              className="p-1.5 rounded text-slate-600 cursor-not-allowed"
                              title="Built-in actions cannot be deleted — disable instead"
                            >
                              <IconTrash size={14} />
                            </span>
                          ) : (
                            <button
                              onClick={() => handleDelete(action.action_id)}
                              disabled={isDeleting}
                              className={`p-1.5 rounded transition-colors ${
                                isConfirming
                                  ? 'text-critical-500 bg-critical-900/30'
                                  : 'text-gray-400 hover:text-critical-400 hover:bg-slate-700'
                              }`}
                              title={isConfirming ? 'Click again to confirm' : 'Delete'}
                            >
                              {isDeleting ? <span className="text-xs">…</span> : <IconTrash size={14} />}
                            </button>
                          )}
                        </div>

                        {/* Confirm delete strip */}
                        {isConfirming && !isDeleting && (
                          <div className="absolute inset-x-0 bottom-0 bg-critical-900/90 px-4 py-1.5 flex items-center justify-between text-xs text-critical-200 rounded-b-lg">
                            <span>Delete this action?</span>
                            <div className="flex gap-3">
                              <button onClick={() => setConfirmId(null)} className="underline">Cancel</button>
                              <button onClick={() => handleDelete(action.action_id)} className="font-semibold text-critical-400">Confirm</button>
                            </div>
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Dismiss confirm overlay */}
      {confirmId && (
        <div className="fixed inset-0 z-10" onClick={() => setConfirmId(null)} />
      )}
    </div>
  )
}
