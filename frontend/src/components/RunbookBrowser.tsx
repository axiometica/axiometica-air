/**
 * RunbookBrowser — read-only library catalog for all authenticated users.
 *
 * Operators can browse runbooks, understand what automation is available,
 * and inspect step definitions — without being able to create or modify anything.
 *
 * Grouped by event_type; searchable by name/service/type.
 */

import { useState, useEffect, useMemo, useCallback } from 'react'
import { getRunbooks, getApprovedActions } from '../services/api'
import {
  IconBook,
  IconSearch,
  IconShield,
  IconCode,
  IconCheck,
  IconAlertTriangle,
  IconChevronDown,
  IconDatabase,
  IconServer,
  IconNetwork,
  IconActivityHeartbeat,
  IconBolt,
  IconClock,
} from './icons'
import { formatStepParams, interpolateCommand, buildConditionMap } from '../utils/runbookUtils'
import type { ToolDef } from '../utils/runbookUtils'
import { parseUTC } from '../utils/dateFormatter'

// ─── Types ────────────────────────────────────────────────────────────────────
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
  total_executions?: number
  successful_executions?: number
  success_rate?: number | null
  confidence_trend?: 'up' | 'down' | 'stable' | 'new' | null
  last_executed_at?: string | null
  source_steps?: { steps?: any[]; edges?: any[] } | null
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
// Tool maps are built dynamically from /api/approved-actions (see RunbookBrowser useEffect)

/**
 * True when a command template already embeds its own transport prefix
 * (ssh/kubectl/docker/WinRM). These tools are environment-specific and
 * don't need the generic per-env dispatch expansion.
 */
function isTransportExplicit(cmd: string): boolean {
  return /^(ssh |kubectl |docker (exec|logs|restart)|Invoke-Command|docker ps)/i.test(cmd.trimStart())
}

/**
 * Per-environment dispatch definitions.
 * wrap(innerCmd) returns the full command as the adapter would execute it.
 */
const ENV_DISPATCH = [
  {
    key: 'docker', icon: '🐳', label: 'Docker', color: '#38bdf8',
    mode: 'host',
    wrap: (c: string) => `docker exec {target} sh -c "${c}"`,
  },
  {
    key: 'kubernetes', icon: '☸', label: 'Kubernetes', color: '#818cf8',
    mode: 'host',
    wrap: (c: string) => `kubectl exec {target} -n {namespace} -- sh -c "${c}"`,
  },
  {
    key: 'ssh', icon: '💻', label: 'SSH', color: '#a3e635',
    mode: 'host',
    wrap: (c: string) => `ssh {target} "${c}"`,
  },
  {
    key: 'vcenter', icon: '⬡', label: 'vCenter', color: '#a78bfa',
    mode: 'target',
    wrap: (_c: string) => 'vCenter GuestProcessManager.startProgram() → {target}  (no agent on VM)',
  },
  {
    key: 'aws_ssm', icon: '☁', label: 'AWS SSM', color: '#fb923c',
    mode: 'target',
    wrap: (c: string) => `aws ssm send-command --instance-ids {target} --parameters commands=["${c}"]`,
  },
  {
    key: 'azure', icon: '☁', label: 'Azure', color: '#60a5fa',
    mode: 'target',
    wrap: (c: string) => `az vm run-command invoke --name {target} --command-id RunShellScript --scripts "${c}"`,
  },
] as const

const BLAST_LABELS: Record<number, { label: string; color: string }> = {
  1: { label: 'Low',    color: '#10b981' },
  2: { label: 'Medium', color: '#f59e0b' },
  3: { label: 'High',   color: '#ef4444' },
}

const confidenceColor = (c: number) =>
  c >= 0.80 ? '#10b981' : c >= 0.60 ? '#f59e0b' : '#ef4444'

const successRateColor = (r: number) =>
  r >= 0.80 ? '#10b981' : r >= 0.50 ? '#f59e0b' : '#ef4444'

/** Pick an icon for a given event_type string */
function EventTypeIcon({ eventType, size = 16 }: { eventType: string; size?: number }) {
  const lower = eventType.toLowerCase()
  if (lower.includes('cpu') || lower.includes('memory') || lower.includes('resource'))
    return <IconActivityHeartbeat size={size} />
  if (lower.includes('disk') || lower.includes('storage'))
    return <IconDatabase size={size} />
  if (lower.includes('network') || lower.includes('latency') || lower.includes('connection'))
    return <IconNetwork size={size} />
  if (lower.includes('service') || lower.includes('deploy') || lower.includes('restart'))
    return <IconServer size={size} />
  if (lower.includes('security') || lower.includes('auth') || lower.includes('ssl'))
    return <IconShield size={size} />
  if (lower.includes('timeout') || lower.includes('slow'))
    return <IconClock size={size} />
  return <IconBolt size={size} />
}

function TrendPip({ trend }: { trend: 'up' | 'down' | 'stable' | 'new' | null | undefined }) {
  if (!trend || trend === 'new') return <span style={{ fontSize: '0.6rem', color: '#4b5563', fontWeight: 700 }}>NEW</span>
  if (trend === 'up')   return <span title="Improving" style={{ color: '#10b981' }}>↑</span>
  if (trend === 'down') return <span title="Declining"  style={{ color: '#ef4444' }}>↓</span>
  return <span title="Stable" style={{ color: '#6b7280' }}>→</span>
}

function fmtDate(iso: string | null | undefined) {
  if (!iso) return null
  const d = parseUTC(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

// ─── Main component ───────────────────────────────────────────────────────────
export default function RunbookBrowser() {
  const [runbooks, setRunbooks]     = useState<Runbook[]>([])
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState<string | null>(null)
  const [search, setSearch]         = useState('')
  const [selectedType, setSelectedType] = useState<string>('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [toolsMap, setToolsMap]     = useState<Record<string, ToolDef>>({})

  useEffect(() => {
    getRunbooks()
      .then(res => setRunbooks((res.data as Runbook[]).filter(r => r.enabled)))
      .catch(() => setError('Failed to load runbooks'))
      .finally(() => setLoading(false))
    getApprovedActions(true).then(res => {
      const map: Record<string, ToolDef> = {}
      for (const a of (res.data ?? [])) {
        map[a.tool_name] = {
          tool: a.tool_name, label: a.name, description: a.description || '',
          commandTemplate: a.command || undefined, commandVariants: a.command_variants || undefined,
          params: [],
        }
      }
      setToolsMap(map)
    }).catch(() => {})
  }, [])

  // Unique event types for filter chips
  const eventTypes = useMemo(() => {
    const types = Array.from(new Set(runbooks.map(r => r.event_type))).sort()
    return types
  }, [runbooks])

  // Filtered runbooks
  const filtered = useMemo(() => {
    const q = search.toLowerCase()
    return runbooks.filter(r => {
      const matchSearch = !q ||
        r.name.toLowerCase().includes(q) ||
        r.event_type.toLowerCase().includes(q) ||
        (r.service ?? '').toLowerCase().includes(q) ||
        (r.description ?? '').toLowerCase().includes(q)
      const matchType = selectedType === 'all' || r.event_type === selectedType
      return matchSearch && matchType
    })
  }, [runbooks, search, selectedType])

  const toggleCard = (id: string) =>
    setExpandedId(prev => prev === id ? null : id)

  // ── Skeleton ─────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="page-transition-enter">
        <div className="mb-8">
          <div className="h-8 w-64 rounded skeleton-pulse mb-2" />
          <div className="h-4 w-80 rounded skeleton-pulse" />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="card h-36 skeleton-pulse" style={{ animationDelay: `${i * 60}ms` }} />
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="page-transition-enter">
      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h2 className="text-section-title mb-1" style={{ color: '#e8eef5' }}>Runbook Library</h2>
          <p className="text-sm" style={{ color: '#a0aec0' }}>
            {runbooks.length} automation runbook{runbooks.length !== 1 ? 's' : ''} available to the remediation engine
          </p>
        </div>
        <div className="text-right">
          <span className="text-xs font-medium px-2 py-1 rounded-md border border-info-500/40 text-info-400">
            Read-only view
          </span>
        </div>
      </div>

      {/* ── Search + type filter ────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="relative flex-1">
          <IconSearch size={15} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: '#7a8ba3' }} />
          <input
            type="text"
            placeholder="Search runbooks by name, service, or event type…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2 rounded-lg bg-slate-800 border border-slate-700 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-info-500/60 focus:ring-1 focus:ring-info-500/30"
          />
        </div>
      </div>

      {/* Event-type chips */}
      {eventTypes.length > 1 && (
        <div className="flex flex-wrap gap-2 mb-6">
          {/* "All" chip */}
          <button
            onClick={() => setSelectedType('all')}
            className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full border transition-colors ${
              selectedType === 'all'
                ? 'bg-info-500/20 border-info-500/60 text-info-300'
                : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-500'
            }`}
          >
            All types
            <span className="text-xs opacity-70">({runbooks.length})</span>
          </button>
          {eventTypes.map(type => {
            const count = runbooks.filter(r => r.event_type === type).length
            const active = selectedType === type
            return (
              <button
                key={type}
                onClick={() => setSelectedType(prev => prev === type ? 'all' : type)}
                className={`flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full border transition-colors ${
                  active
                    ? 'bg-info-500/20 border-info-500/60 text-info-300'
                    : 'bg-slate-800 border-slate-700 text-slate-400 hover:border-slate-500'
                }`}
              >
                <EventTypeIcon eventType={type} size={12} />
                {type.replace(/_/g, ' ')}
                <span className="text-xs opacity-70">({count})</span>
              </button>
            )
          })}
        </div>
      )}

      {/* ── Error ───────────────────────────────────────────────────────────── */}
      {error && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg border text-sm mb-4 bg-slate-800 border-slate-600"
          style={{ color: '#a0aec0' }}>
          <IconAlertTriangle size={15} className="text-warning-500 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* ── Empty state ──────────────────────────────────────────────────────── */}
      {!error && filtered.length === 0 && (
        <div className="empty-state py-20">
          <div className="flex justify-center mb-4" style={{ color: '#a0aec0' }}>
            <IconBook size={40} strokeWidth={1.5} />
          </div>
          <h4 className="empty-state-title">
            {search || selectedType !== 'all' ? 'No matching runbooks' : 'No enabled runbooks'}
          </h4>
          <p className="empty-state-description">
            {search || selectedType !== 'all'
              ? 'Try adjusting your search or filter'
              : 'No automation runbooks have been enabled yet'}
          </p>
        </div>
      )}

      {/* ── Runbook grid ─────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {filtered.map((runbook, idx) => {
          const blast = BLAST_LABELS[runbook.blast_radius] ?? BLAST_LABELS[1]
          const isExpanded = expandedId === runbook.runbook_id
          const totalSteps = runbook.diagnostics.length + runbook.actions.length + runbook.verification_steps.length

          return (
            <div
              key={runbook.runbook_id}
              className={`card-interactive relative overflow-hidden border-l-4 border-info-500/40 flex flex-col${isExpanded ? ' col-span-full' : ''}`}
              style={{
                animation: 'staggerFadeIn 0.35s ease-out forwards',
                animationDelay: `${idx * 50}ms`,
                opacity: 0,
              }}
            >
              {/* ── Card header ─────────────────────── */}
              <div className="flex items-start justify-between gap-3 mb-1">
                <div className="flex items-center gap-2 flex-1 min-w-0">
                  <IconBook size={16} className="flex-shrink-0 text-info-500" />
                  <h3 className="font-semibold text-sm truncate" style={{ color: '#e8eef5' }}>
                    {runbook.name}
                  </h3>
                </div>
                {/* Confidence badge */}
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <IconShield size={13} style={{ color: confidenceColor(runbook.confidence) }} />
                  <span className="text-xs font-bold" style={{ color: confidenceColor(runbook.confidence) }}>
                    {Math.round(runbook.confidence * 100)}%
                  </span>
                  <TrendPip trend={runbook.confidence_trend} />
                </div>
              </div>

              {/* Event type label */}
              <div className="flex items-center gap-1 mb-2">
                <EventTypeIcon eventType={runbook.event_type} size={11} />
                <span className="text-xs" style={{ color: '#7a8ba3' }}>
                  {runbook.event_type.replace(/_/g, ' ')}
                </span>
              </div>

              {/* Description */}
              {runbook.description && (
                <p className="text-xs mb-3 line-clamp-2" style={{ color: '#7a8ba3' }}>
                  {runbook.description}
                </p>
              )}

              {/* Badges */}
              <div className="flex flex-wrap gap-1.5 mb-3">
                {runbook.service && (
                  <span className="text-xs px-2 py-0.5 rounded border border-slate-600 text-slate-300 bg-slate-800/60">
                    {runbook.service}
                  </span>
                )}
                {runbook.environment && (
                  <span className="text-xs px-2 py-0.5 rounded border border-slate-600 text-slate-400 bg-slate-800/60">
                    {runbook.environment}
                  </span>
                )}
                <span
                  className="text-xs px-2 py-0.5 rounded border bg-transparent"
                  style={{ borderColor: blast.color + '60', color: blast.color }}
                >
                  Blast {blast.label}
                </span>
              </div>

              {/* Push step summary to bottom of card */}
              <div className="flex-1" />

              {/* Step summary row */}
              <div className="flex items-center gap-4 pt-2 border-t border-slate-700/30">
                <StepPill icon={<IconSearch size={12} />}  count={runbook.diagnostics.length}        label="Diag" />
                <StepPill icon={<IconCode size={12} />}    count={runbook.actions.length}            label="Action" />
                <StepPill icon={<IconCheck size={12} />}   count={runbook.verification_steps.length} label="Verify" />

                {totalSteps > 0 && (
                  <button
                    onClick={() => toggleCard(runbook.runbook_id)}
                    className="ml-auto flex items-center gap-1 text-xs transition-colors hover:text-info-400"
                    style={{ color: '#7a8ba3' }}
                  >
                    <IconChevronDown
                      size={13}
                      style={{
                        transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                        transition: 'transform 0.2s',
                      }}
                    />
                    {isExpanded ? 'Hide steps' : 'View steps'}
                  </button>
                )}
              </div>

              {/* Execution stats (when available) */}
              {(runbook.total_executions ?? 0) > 0 && (
                <div className="mt-2 pt-2 border-t border-slate-700/20">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-xs" style={{ color: '#7a8ba3' }}>
                      {runbook.successful_executions}/{runbook.total_executions} runs succeeded
                      {runbook.last_executed_at && (
                        <> · last run {fmtDate(runbook.last_executed_at)}</>
                      )}
                    </span>
                    <span className="text-xs font-medium" style={{ color: successRateColor(runbook.success_rate ?? 0) }}>
                      {Math.round((runbook.success_rate ?? 0) * 100)}%
                    </span>
                  </div>
                  <div className="h-1 rounded-full overflow-hidden" style={{ background: '#1e2535' }}>
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{
                        width: `${Math.round((runbook.success_rate ?? 0) * 100)}%`,
                        backgroundColor: successRateColor(runbook.success_rate ?? 0),
                      }}
                    />
                  </div>
                </div>
              )}

              {/* Expanded steps */}
              {isExpanded && (() => {
                const condMap = buildConditionMap(runbook.source_steps)
                return (
                  <div className="mt-3 pt-3 border-t border-slate-700/30 space-y-3">
                    <StepSection
                      icon={<IconSearch size={12} />}
                      label="Diagnostics"
                      steps={runbook.diagnostics}
                      toolsMap={toolsMap}
                      conditionMap={condMap}
                    />
                    <StepSection
                      icon={<IconCode size={12} />}
                      label="Actions"
                      steps={runbook.actions}
                      toolsMap={toolsMap}
                      conditionMap={condMap}
                    />
                    <StepSection
                      icon={<IconCheck size={12} />}
                      label="Verification"
                      steps={runbook.verification_steps}
                      verification
                      toolsMap={toolsMap}
                      conditionMap={condMap}
                    />
                  </div>
                )
              })()}
            </div>
          )
        })}
      </div>

      {/* Summary footer */}
      {filtered.length > 0 && (
        <div className="mt-8 pt-4 border-t border-slate-800 text-center">
          <p className="text-xs" style={{ color: '#7a8ba3' }}>
            {filtered.length} of {runbooks.length} runbook{runbooks.length !== 1 ? 's' : ''} shown
            {(search || selectedType !== 'all') && ' — clear filter to see all'}
          </p>
        </div>
      )}
    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StepPill({ icon, count, label }: { icon: React.ReactNode; count: number; label: string }) {
  return (
    <div className="flex items-center gap-1" style={{ color: count === 0 ? '#374151' : '#7a8ba3' }}>
      {icon}
      <span className="text-xs">{count} {label}{count !== 1 ? 's' : ''}</span>
    </div>
  )
}

function StepSection({
  icon, label, steps, verification = false, toolsMap = {}, conditionMap,
}: {
  icon: React.ReactNode
  label: string
  steps: any[]
  verification?: boolean
  toolsMap?: Record<string, ToolDef>
  conditionMap?: Map<string, { condition: string; branch: 'true' | 'false' }>
}) {
  if (steps.length === 0) return null
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-wide mb-2 flex items-center gap-1.5"
        style={{ color: '#7a8ba3' }}>
        {icon} {label}
      </p>
      <ol className="space-y-2 pl-3">
        {steps.map((step: any, i: number) =>
          verification
            ? <VerificationItem key={i} step={step} index={i} />
            : <StepItem key={i} step={step} index={i} toolsMap={toolsMap} conditionMap={conditionMap} />
        )}
      </ol>
    </div>
  )
}

// ── Verification step row (no command) ────────────────────────────────────────

function VerificationItem({ step, index }: { step: any; index: number }) {
  const parts = [step.metric, step.check?.replace('_', ' '), step.value].filter(Boolean).join(' ')
  return (
    <li className="text-xs flex gap-1.5" style={{ color: '#a0aec0' }}>
      <span className="flex-shrink-0 font-mono w-4 text-right" style={{ color: '#6b7280' }}>{index + 1}.</span>
      <span>
        {step.description && <span className="font-medium mr-1" style={{ color: '#c4cfd9' }}>{step.description}</span>}
        {parts && <span style={{ color: '#7a8ba3' }}>{parts}</span>}
      </span>
    </li>
  )
}

// ── Diagnostic / action step row with command + env dispatch ──────────────────

function StepItem({
  step, index, toolsMap = {}, conditionMap,
}: {
  step: any
  index: number
  toolsMap?: Record<string, ToolDef>
  conditionMap?: Map<string, { condition: string; branch: 'true' | 'false' }>
}) {
  const [envOpen, setEnvOpen] = useState(false)
  const toggleEnv = useCallback(() => setEnvOpen(o => !o), [])

  // Resolve command: prefer explicit step.command, then interpolate the tool template
  const toolDef = toolsMap[step.tool ?? '']
  const resolvedCmd: string | null =
    step.command
    || (toolDef?.commandTemplate
        ? interpolateCommand(toolDef.commandTemplate, step.args || {})
        : null)

  // Only offer the env-dispatch expansion for inner commands (not already transport-wrapped)
  const canExpand = !!(resolvedCmd && !isTransportExplicit(resolvedCmd))

  const toolLabel = toolsMap[step.tool]?.label ?? step.tool ?? '—'
  const params    = formatStepParams(step.args)
  const annotation = conditionMap?.get(step.tool || '')

  return (
    <li className="text-xs space-y-1">
      {/* Decision-condition annotation — shown when this step follows a decision node */}
      {annotation && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginLeft: 22, marginBottom: 1 }}>
          <span style={{ fontWeight: 700, color: annotation.branch === 'true' ? '#10b981' : '#f43f5e', fontSize: 10 }}>
            {annotation.branch === 'true' ? '↳ IF' : '↳ ELSE'}
          </span>
          {annotation.condition && (
            <span style={{
              fontFamily: 'monospace', fontSize: 10, color: '#a0aec0',
              background: annotation.branch === 'true' ? '#061a12' : '#1a0610',
              border: `1px solid ${annotation.branch === 'true' ? '#10b98130' : '#f43f5e30'}`,
              borderRadius: 4, padding: '1px 6px',
            }}>
              {annotation.condition}
            </span>
          )}
        </div>
      )}
      {/* Step header: number · description · tool badge · params */}
      <div className="flex gap-1.5 items-start">
        <span className="flex-shrink-0 font-mono w-4 text-right mt-0.5" style={{ color: '#6b7280' }}>
          {index + 1}.
        </span>
        <span style={{ color: '#a0aec0' }}>
          {step.description && (
            <span className="font-medium mr-1" style={{ color: '#c4cfd9' }}>{step.description}</span>
          )}
          <span className="font-mono text-xs px-1 py-0.5 rounded mr-1"
            style={{ background: '#1a2035', color: '#60a5fa' }}>
            {toolLabel}
          </span>
          {params && <span style={{ color: '#7a8ba3' }}>{params}</span>}
        </span>
      </div>

      {/* Resolved command + optional env-dispatch toggle */}
      {resolvedCmd && (
        <div className="ml-5 space-y-1">
          <div className="flex items-center gap-2">
            <div
              className="flex-1 font-mono text-xs px-2 py-1 rounded overflow-x-auto"
              style={{ background: '#0d1117', color: '#7ee787', border: '1px solid #21262d', whiteSpace: 'pre' }}
            >
              $ {resolvedCmd}
            </div>
            {canExpand && (
              <button
                onClick={toggleEnv}
                className="flex-shrink-0 text-xs px-2 py-0.5 rounded border transition-colors whitespace-nowrap"
                style={{
                  borderColor: envOpen ? '#60a5fa' : 'rgba(255,255,255,0.55)',
                  color:       envOpen ? '#60a5fa' : 'rgba(255,255,255,0.85)',
                  background:  envOpen ? 'rgba(59,130,246,0.12)' : 'transparent',
                }}
              >
                {envOpen ? '▾ envs' : '▸ envs'}
              </button>
            )}
          </div>

          {/* Per-environment dispatch rows */}
          {envOpen && canExpand && (
            <div className="rounded overflow-hidden text-xs" style={{ border: '1px solid #21262d' }}>
              {ENV_DISPATCH.map((env, ei) => (
                <div
                  key={env.key}
                  className="flex items-start gap-2 px-2 py-1.5"
                  style={{
                    borderBottom: ei < ENV_DISPATCH.length - 1 ? '1px solid #161b22' : undefined,
                    background: '#0a0e17',
                  }}
                >
                  {/* Env label */}
                  <span
                    className="flex-shrink-0 font-medium w-20"
                    style={{ color: env.color, paddingTop: 1 }}
                  >
                    {env.icon} {env.label}
                  </span>
                  {/* Mode badge */}
                  <span
                    className="flex-shrink-0 text-xs px-1 rounded self-start mt-0.5"
                    style={{
                      background: env.mode === 'target' ? 'rgba(52,211,153,0.1)' : 'rgba(96,165,250,0.1)',
                      color:      env.mode === 'target' ? '#34d399' : '#60a5fa',
                      border:     `1px solid ${env.mode === 'target' ? '#34d39930' : '#60a5fa30'}`,
                      fontSize: '0.6rem',
                    }}
                  >
                    {env.mode}
                  </span>
                  {/* Dispatched command */}
                  <code
                    className="font-mono break-all leading-relaxed"
                    style={{ color: '#6e7681', flex: 1 }}
                  >
                    {env.wrap(resolvedCmd)}
                  </code>
                </div>
              ))}
              <div className="px-2 py-1" style={{ background: '#0a0e17', borderTop: '1px solid #21262d' }}>
                <span style={{ color: '#3d4557', fontSize: '0.6rem' }}>
                  host = watcher executes transport command · target = adapter API dispatches inner command to VM
                </span>
              </div>
            </div>
          )}
        </div>
      )}
    </li>
  )
}
