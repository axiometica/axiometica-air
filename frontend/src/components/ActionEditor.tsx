import { useState, useEffect } from 'react'
import axios from 'axios'
import {
  IconArrowLeft,
  IconDeviceFloppy,
  IconPlus,
  IconTrash,
  IconShield,
  IconAlertTriangle,
  IconCheck,
  IconX,
  IconBolt,
  IconActivity,
  IconTestPipe,
  IconLock,
} from './icons'
import type { ApprovedAction, ProcessRule, ActionParameter, OutputField } from './ApprovedActionsList'

interface Props {
  actionId: string | null   // null = new
  onBack: () => void
  onSaved: () => void
}

type Category = 'diagnostic' | 'remediation_safe' | 'remediation_intrusive'

const CATEGORY_OPTIONS: { value: Category; label: string; icon: React.ReactNode }[] = [
  { value: 'diagnostic',             label: 'Diagnostic',            icon: <IconActivity size={14} /> },
  { value: 'remediation_safe',       label: 'Remediation · Safe',    icon: <IconShield size={14} /> },
  { value: 'remediation_intrusive',  label: 'Remediation · Intrusive', icon: <IconBolt size={14} /> },
]

interface TestResult {
  allowed: boolean
  matched_rule: ProcessRule | null
  reason: string
}

export default function ActionEditor({ actionId, onBack, onSaved }: Props) {
  const isNew = actionId === null

  // Form state
  const [toolName,          setToolName]          = useState('')
  const [name,              setName]              = useState('')
  const [description,       setDescription]       = useState('')
  const [command,           setCommand]           = useState('')
  const [commandVariants,   setCommandVariants]   = useState<Record<string, string>>({})
  const [parameters,        setParameters]        = useState<ActionParameter[]>([])
  const [category,          setCategory]          = useState<Category>('diagnostic')
  const [blastRadius,       setBlastRadius]       = useState(1)
  const [requiresApproval,  setRequiresApproval]  = useState(false)
  const [enabled,           setEnabled]           = useState(true)
  const [processRules,      setProcessRules]      = useState<ProcessRule[]>([])
  const [hasProcessRules,   setHasProcessRules]   = useState(false)
  const [outputFields,      setOutputFields]      = useState<OutputField[]>([])
  const [isBuiltin,         setIsBuiltin]         = useState(false)

  // UI state
  const [loading,  setLoading]  = useState(!isNew)
  const [saving,   setSaving]   = useState(false)
  const [error,    setError]    = useState<string | null>(null)
  const [success,  setSuccess]  = useState(false)

  // Process rule test
  const [testProcess,  setTestProcess]  = useState('')
  const [testResult,   setTestResult]   = useState<TestResult | null>(null)
  const [testLoading,  setTestLoading]  = useState(false)

  // Shell-syntax validation (per-tool)
  const [validating,   setValidating]   = useState(false)
  const [validation,   setValidation]   = useState<Record<string, { ok: boolean; stage: string; message: string | null }> | null>(null)

  const runValidation = async () => {
    try {
      setValidating(true); setValidation(null)
      // For unsaved edits, validate each variant against the ad-hoc endpoint
      // (avoids requiring a save first). For saved actions with untouched
      // variants, /validate on the ID would also work — using the per-command
      // endpoint keeps the behaviour identical for both cases.
      const targets: Array<[string, string]> = []
      if (command) targets.push(['command', command])
      for (const [k, v] of Object.entries(commandVariants)) {
        if (v) targets.push([k, v])
      }
      const results: Record<string, { ok: boolean; stage: string; message: string | null }> = {}
      await Promise.all(targets.map(async ([key, cmd]) => {
        try {
          const { data } = await axios.post('/api/approved-actions/validate-command', { command: cmd })
          results[key] = data
        } catch (e: any) {
          results[key] = { ok: false, stage: 'error', message: e?.message || 'request failed' }
        }
      }))
      setValidation(results)
    } finally {
      setValidating(false)
    }
  }

  useEffect(() => {
    if (actionId) {
      axios.get<ApprovedAction>(`/api/approved-actions/${actionId}`)
        .then(({ data }) => {
          setToolName(data.tool_name)
          setName(data.name)
          setDescription(data.description || '')
          setCommand(data.command || '')
          setCommandVariants(data.command_variants || {})
          setParameters(data.parameters || [])
          setCategory(data.category)
          setBlastRadius(data.blast_radius)
          setRequiresApproval(data.requires_approval)
          setEnabled(data.enabled)
          if (data.process_rules && data.process_rules.length > 0) {
            setHasProcessRules(true)
            setProcessRules(data.process_rules)
          }
          setOutputFields(data.output_fields || [])
          setIsBuiltin(!!data.is_builtin)
        })
        .catch(() => setError('Failed to load action'))
        .finally(() => setLoading(false))
    }
  }, [actionId])

  // ── Parameter helpers ─────────────────────────────────────────────────────

  const addParameter = () => {
    setParameters(prev => [...prev, { name: '', type: 'string', required: false, default: '', description: '' }])
  }

  const updateParam = (idx: number, field: keyof ActionParameter, value: any) => {
    setParameters(prev => prev.map((p, i) => i === idx ? { ...p, [field]: value } : p))
  }

  const removeParam = (idx: number) => {
    setParameters(prev => prev.filter((_, i) => i !== idx))
  }

  // ── Process rule helpers ───────────────────────────────────────────────────

  const addRule = () => {
    const maxPriority = processRules.reduce((m, r) => Math.max(m, r.priority), 0)
    setProcessRules(prev => [
      ...prev,
      { priority: maxPriority + 10, allow: true, pattern: '', description: '' },
    ])
  }

  const updateRule = (idx: number, field: keyof ProcessRule, value: any) => {
    setProcessRules(prev => prev.map((r, i) => i === idx ? { ...r, [field]: value } : r))
  }

  const removeRule = (idx: number) => {
    setProcessRules(prev => prev.filter((_, i) => i !== idx))
  }

  const sortedRules = [...processRules].sort((a, b) => a.priority - b.priority)

  // ── Output field helpers ───────────────────────────────────────────────────

  const addOutputField = () => {
    setOutputFields(prev => [...prev, { field: '', kind: 'regex', pattern: '', type: 'string' }])
  }

  const updateOutputField = (idx: number, field: keyof OutputField, value: any) => {
    setOutputFields(prev => prev.map((f, i) => i === idx ? { ...f, [field]: value } : f))
  }

  const removeOutputField = (idx: number) => {
    setOutputFields(prev => prev.filter((_, i) => i !== idx))
  }

  // ── Validate regex as user types ──────────────────────────────────────────
  const isValidRegex = (pat: string) => {
    try { new RegExp(pat); return true } catch { return false }
  }

  // ── Test a process name ───────────────────────────────────────────────────
  const testProcessName = async () => {
    if (!testProcess.trim() || isNew) return
    setTestLoading(true)
    setTestResult(null)
    try {
      const { data } = await axios.post<TestResult>('/api/approved-actions/validate-process', {
        tool_name: toolName,
        process_name: testProcess.trim(),
      })
      setTestResult(data)
    } catch {
      setTestResult({ allowed: false, matched_rule: null, reason: 'Test request failed' })
    } finally {
      setTestLoading(false)
    }
  }

  // ── Save ─────────────────────────────────────────────────────────────────
  const handleSave = async () => {
    if (!toolName.trim() || !name.trim()) {
      setError('Tool name and display name are required')
      return
    }
    setSaving(true)
    setError(null)
    const payload = {
      tool_name:         toolName.trim(),
      name:              name.trim(),
      description:       description.trim(),
      category,
      blast_radius:      blastRadius,
      requires_approval: requiresApproval,
      enabled,
      command:           command.trim(),
      command_variants:  Object.fromEntries(
        Object.entries(commandVariants).filter(([k, v]) => k.trim() && v != null && v.trim())
      ),
      parameters:        parameters.filter(p => p.name.trim()),
      process_rules:     hasProcessRules ? processRules : null,
      // output_fields is locked for built-in tools — omit so the save can't be rejected
      // by the server's 403 guard when other fields on the same tool are being edited.
      ...(isBuiltin ? {} : { output_fields: outputFields.filter(f => f.field.trim()) }),
    }
    try {
      if (isNew) {
        await axios.post('/api/approved-actions', payload)
      } else {
        await axios.put(`/api/approved-actions/${actionId}`, payload)
      }
      setSuccess(true)
      setTimeout(onSaved, 600)
    } catch (err: any) {
      setError(err?.response?.data?.detail || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  // ── Delete ───────────────────────────────────────────────────────────────
  // Two-step confirm to prevent accidental clicks. First click opens the
  // confirm panel below the save bar; second click issues the DELETE.
  // Backend blocks with 409 if the tool is referenced by any enabled runbook —
  // that response includes a blockers list we surface inline.
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [deleteBlockers, setDeleteBlockers] = useState<
    { id: string; name: string; section: string }[] | null
  >(null)

  const handleDelete = async () => {
    if (!actionId) return
    setDeleting(true)
    setError(null)
    setDeleteBlockers(null)
    try {
      await axios.delete(`/api/approved-actions/${actionId}`)
      onSaved()   // returns to list; caller reloads
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      if (err?.response?.status === 409 && detail && typeof detail === 'object') {
        setDeleteBlockers(detail.blockers || [])
        setError(detail.message || 'Delete blocked')
      } else {
        setError(typeof detail === 'string' ? detail : 'Delete failed')
      }
      setConfirmDelete(false)
    } finally {
      setDeleting(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-4">
        {[...Array(4)].map((_, i) => <div key={i} className="metric-card h-16 skeleton-pulse" />)}
      </div>
    )
  }

  const isIntrusive = category === 'remediation_intrusive'

  return (
    <div className="page-transition-enter max-w-4xl mx-auto">

      {/* Back + header */}
      <div className="flex items-center gap-3 mb-8">
        <button
          onClick={onBack}
          className="p-2 rounded-lg text-gray-400 hover:text-gray-200 hover:bg-slate-700 transition-colors"
        >
          <IconArrowLeft size={18} />
        </button>
        <div>
          <h2 className="text-section-title" style={{ color: '#e8eef5' }}>
            {isNew ? 'New Action' : name || 'Edit Action'}
          </h2>
          <p className="text-xs mt-0.5" style={{ color: '#7a8ba3' }}>
            {isNew ? 'Define a new approved action for the catalog' : `Editing: ${toolName}`}
          </p>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 border border-critical-700/50 text-sm mb-4 text-critical-300">
          <IconAlertTriangle size={15} className="flex-shrink-0" />
          {error}
        </div>
      )}

      {/* ── Section 1: Basic Info ───────────────────────────────────────────── */}
      <Section title="Basic Information">
        <div className="grid grid-cols-2 gap-4">
          <Field label="Tool Name" hint="Snake_case identifier used in runbooks" required>
            <input
              value={toolName}
              onChange={e => setToolName(e.target.value.replace(/\s/g, '_').toLowerCase())}
              placeholder="e.g. process_kill"
              disabled={!isNew}
              className="form-input font-mono text-sm disabled:opacity-50"
            />
          </Field>
          <Field label="Display Name" required>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Kill Process"
              className="form-input"
            />
          </Field>
        </div>

        <Field label="Description">
          <textarea
            value={description}
            onChange={e => setDescription(e.target.value)}
            rows={2}
            placeholder="What does this action do? When is it used?"
            className="form-input resize-none"
          />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          {/* Category */}
          <Field label="Category">
            <div className="flex flex-col gap-1.5">
              {CATEGORY_OPTIONS.map(opt => (
                <label
                  key={opt.value}
                  className="flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer transition-all"
                  style={{
                    backgroundColor: category === opt.value ? '#252c3c' : '#1a1f2e',
                    borderColor: category === opt.value ? '#5b6aa0' : '#3d4557',
                    color: category === opt.value ? '#e8eef5' : '#94a3b8',
                  }}
                >
                  <input
                    type="radio"
                    className="sr-only"
                    checked={category === opt.value}
                    onChange={() => setCategory(opt.value)}
                  />
                  {opt.icon}
                  <span className="text-sm">{opt.label}</span>
                </label>
              ))}
            </div>
          </Field>

          {/* Blast radius + toggles */}
          <div className="space-y-4">
            <Field label="Blast Radius" hint="Impact if this action goes wrong">
              <div className="flex gap-2">
                {[1, 2, 3].map(n => (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setBlastRadius(n)}
                    className={`flex-1 py-2 rounded-lg text-sm font-semibold border transition-all ${
                      blastRadius === n
                        ? n === 1 ? 'bg-success-900/40 border-success-600 text-success-400'
                          : n === 2 ? 'bg-warning-900/40 border-warning-600 text-warning-400'
                          : 'bg-critical-900/40 border-critical-600 text-critical-400'
                        : 'border-slate-700 text-gray-500 hover:border-slate-600'
                    }`}
                    style={{ backgroundColor: blastRadius !== n ? '#1a1f2e' : undefined }}
                  >
                    {n === 1 ? 'Low' : n === 2 ? 'Med' : 'High'}
                  </button>
                ))}
              </div>
            </Field>

            <div className="space-y-2">
              <Toggle
                label="Requires Manual Approval"
                checked={requiresApproval}
                onChange={setRequiresApproval}
              />
              <Toggle
                label="Enabled"
                checked={enabled}
                onChange={setEnabled}
              />
            </div>
          </div>
        </div>
      </Section>

      {/* ── Section 2: Command ─────────────────────────────────────────────── */}
      <Section
        title="Command"
        subtitle='The inner shell command for this action. Use {param_name} placeholders. For controller-based adapters (SSM, Azure, vCenter, Kubernetes) the command is wrapped in the adapter API transport — no agent required on the target VM.'
      >
        {/* Default / fallback command */}
        <Field label="Default — Any Environment" hint="Runs when no environment-specific variant matches">
          <input
            value={command}
            onChange={e => setCommand(e.target.value)}
            placeholder="e.g. pkill -{signal} {process_name}"
            className="form-input w-full text-sm"
            style={{ fontFamily: '"Monaco", "Consolas", "Courier New", monospace', fontSize: '0.78rem' }}
          />
        </Field>
        {command && /\{[\w]+\}/.test(command) && (
          <div className="mt-1 text-xs px-3 py-2 rounded-lg" style={{ background: '#1a1f2e', border: '1px solid #3d4557', color: '#6e7681' }}>
            <span style={{ color: '#7a8ba3' }}>Preview: </span>
            <code style={{ color: '#7ee787' }}>{command}</code>
            <span className="ml-2 opacity-60">— placeholders resolved at execution</span>
          </div>
        )}

        {/* Environment variant rows */}
        {Object.keys(commandVariants).length > 0 && (
          <div className="mt-4 space-y-3">
            <div className="grid text-xs font-semibold uppercase tracking-wide"
              style={{ gridTemplateColumns: '140px 1fr 32px', gap: '8px', color: '#7a8ba3', padding: '0 4px' }}>
              <span>Environment</span>
              <span>Command  <span className="normal-case font-normal" style={{ color: '#4a5568' }}>— full shell command as run on the watcher host. For container/host adapters (docker, ssh, kubernetes) include the transport prefix (e.g. <code>docker exec {"{"}target{"}"} sh -c '…'</code>). For controller adapters (vcenter, aws_ssm, azure) provide only the inner command — the adapter wraps it.</span></span>
              <span />
            </div>
            {Object.entries(commandVariants).map(([env, cmd]) => {
              const envMeta: Record<string, {
                icon: string
                label: string
                transport?: string
                transportLabel?: string
                transportColor?: string
              }> = {
                docker: {
                  icon: '🐳',
                  label: 'Docker',
                  transportLabel: 'Docker exec',
                  transport: 'docker exec {target} sh -c "⟨cmd⟩"',
                  transportColor: '#38bdf8',
                },
                kubernetes: {
                  icon: '☸',
                  label: 'Kubernetes',
                  transportLabel: 'kubectl exec',
                  transport: 'kubectl exec {target} -n {namespace} -- sh -c "⟨cmd⟩"  ({namespace} resolved from WATCHER_K8S_NAMESPACE)',
                  transportColor: '#818cf8',
                },
                ssh: {
                  icon: '💻',
                  label: 'SSH / Bare-metal',
                  transportLabel: 'paramiko SSH',
                  transport: 'ssh {target} "⟨cmd⟩"',
                  transportColor: '#a3e635',
                },
                aws_ssm: {
                  icon: '☁',
                  label: 'AWS SSM (EC2)',
                  transportLabel: 'SSM send-command',
                  transport: 'aws ssm send-command --instance-ids {target} --document-name AWS-RunShellScript --parameters commands=["⟨cmd⟩"]',
                  transportColor: '#fb923c',
                },
                azure: {
                  icon: '☁',
                  label: 'Azure Run Command',
                  transportLabel: 'az vm run-command',
                  transport: 'az vm run-command invoke --resource-group {rg} --name {target} --command-id RunShellScript --scripts "⟨cmd⟩"',
                  transportColor: '#60a5fa',
                },
                vcenter: {
                  icon: '⬡',
                  label: 'VMware vCenter',
                  transportLabel: 'Guest Ops API',
                  transport: 'vCenter GuestProcessManager.startProgram({target}, "/bin/sh -c \'⟨cmd⟩\'")  (dispatched via vCenter API — no agent on VM)',
                  transportColor: '#a78bfa',
                },
                any: {
                  icon: '🌐',
                  label: 'Any (explicit)',
                },
              }
              const meta = envMeta[env] || { icon: '⚙', label: env }
              // Suppress the transport preview when the stored command already
              // begins with that adapter's transport prefix. Otherwise the UI
              // shows a misleading double-wrap (e.g. `docker exec {target} sh
              // -c "docker exec {target} sh -c '…'"`) even though execution is
              // fine — see docker_adapter.exec() with mode="host".
              const trimmedCmd = (cmd ?? '').trim()
              const transportPrefixes: Record<string, RegExp> = {
                docker:     /^docker\s+exec\b/i,
                kubernetes: /^kubectl\s+exec\b/i,
                ssh:        /^ssh\s+/i,
                aws_ssm:    /^aws\s+ssm\s+send-command\b/i,
                azure:      /^az\s+vm\s+run-command\b/i,
              }
              const alreadyWrapped = transportPrefixes[env]?.test(trimmedCmd) ?? false
              const resolvedTransport = (meta.transport && !alreadyWrapped)
                ? meta.transport.replace('⟨cmd⟩', trimmedCmd || '⟨cmd⟩')
                : null
              return (
                <div key={env} className="space-y-1">
                  <div className="grid items-center" style={{ gridTemplateColumns: '140px 1fr 32px', gap: '8px' }}>
                    <div className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs font-medium"
                      style={{ background: '#1a1f2e', border: '1px solid #3d4557', color: '#a0aec0' }}>
                      <span>{meta.icon}</span>
                      <span>{meta.label}</span>
                    </div>
                    <input
                      value={cmd ?? ''}
                      onChange={e => setCommandVariants(prev => ({ ...prev, [env]: e.target.value }))}
                      className="form-input text-sm"
                      style={{ fontFamily: '"Monaco", "Consolas", "Courier New", monospace', fontSize: '0.75rem' }}
                      placeholder={alreadyWrapped
                        ? `Full command for ${meta.label} (already includes transport)`
                        : transportPrefixes[env]
                          ? `Full command for ${meta.label} — include transport prefix`
                          : `Command for ${meta.label}`}
                    />
                    <button
                      type="button"
                      onClick={() => setCommandVariants(prev => { const n = { ...prev }; delete n[env]; return n })}
                      className="flex items-center justify-center w-8 h-8 rounded-lg text-gray-500 hover:text-red-400 hover:bg-red-900/20 transition-colors"
                    >
                      <IconX size={14} />
                    </button>
                  </div>
                  {resolvedTransport && (
                    <div
                      className="flex items-start gap-2 px-3 py-1.5 rounded-md text-xs"
                      style={{
                        marginLeft: '148px',
                        background: '#1a1f2e',
                        border: '1px solid #3d4557',
                      }}
                    >
                      <span style={{ color: '#4a5568', flexShrink: 0, marginTop: '1px' }}>↳</span>
                      <span style={{ color: '#6e7681', flexShrink: 0, marginTop: '1px' }}>
                        {meta.transportLabel}:
                      </span>
                      <code style={{ color: meta.transportColor, wordBreak: 'break-all', lineHeight: '1.5' }}>
                        {resolvedTransport}
                      </code>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {/* Add environment variant */}
        <div className="mt-3">
          <EnvVariantAdder
            existing={Object.keys(commandVariants)}
            onAdd={env => setCommandVariants(prev => ({ ...prev, [env]: '' }))}
          />
        </div>

        {/* Resolution order + transport note */}
        <div className="mt-3 space-y-1.5">
          <p className="text-xs" style={{ color: '#7a8ba3' }}>
            <strong style={{ color: '#a0aec0' }}>Resolution order:</strong>{' '}
            environment variant → any-explicit → default. The watcher's{' '}
            <code style={{ color: '#7ee787' }}>adapter_mode</code> determines which variant runs.
          </p>
          <p className="text-xs" style={{ color: '#7a8ba3' }}>
            <strong style={{ color: '#a0aec0' }}>Target aliases:</strong>{' '}
            <code style={{ color: '#7ee787' }}>{'{target}'}</code>,{' '}
            <code style={{ color: '#7ee787' }}>{'{container}'}</code>,{' '}
            <code style={{ color: '#7ee787' }}>{'{pod}'}</code>, and{' '}
            <code style={{ color: '#7ee787' }}>{'{host}'}</code>{' '}
            all resolve to the same runtime value — the detected resource. Use{' '}
            <code style={{ color: '#7ee787' }}>{'{target}'}</code> as the canonical name in parameters.
          </p>
          <p className="text-xs" style={{ color: '#7a8ba3' }}>
            <strong style={{ color: '#a0aec0' }}>Kubernetes namespace:</strong>{' '}
            <code style={{ color: '#7ee787' }}>{'{namespace}'}</code>{' '}
            is automatically injected from the watcher's{' '}
            <code style={{ color: '#7ee787' }}>WATCHER_K8S_NAMESPACE</code>{' '}
            config — no parameter definition needed. Default:{' '}
            <code style={{ color: '#7ee787' }}>"default"</code>.
          </p>
          <p className="text-xs" style={{ color: '#7a8ba3' }}>
            <strong style={{ color: '#a0aec0' }}>Controller adapters</strong>{' '}
            (SSM, Azure, vCenter) dispatch the inner command via their API —{' '}
            <span style={{ color: '#4a5568' }}>no agent on the target VM required.</span>
          </p>
        </div>

        {/* ── Shell-syntax validator ─────────────────────────────────────── */}
        <div style={{
          marginTop: 20,
          paddingTop: 16,
          borderTop: '1px solid #3d4557',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <div>
              <div style={{ color: '#e8eef5', fontWeight: 600, fontSize: '0.85rem' }}>Shell Syntax Validation</div>
              <div style={{ color: '#7a8ba3', fontSize: '0.75rem', marginTop: 2 }}>
                Run every command variant through bash's parser to catch broken quoting before saving.
              </div>
            </div>
            <button
              onClick={runValidation}
              disabled={validating || (!command && Object.keys(commandVariants).length === 0)}
              className="btn flex items-center gap-2"
              style={{
                background: 'transparent',
                border: '1px solid #3d4557',
                color: '#a0aec0',
                padding: '7px 14px',
                borderRadius: 7,
                fontSize: '0.82rem',
                fontWeight: 600,
                cursor: validating ? 'wait' : 'pointer',
                opacity: validating ? 0.6 : 1,
                flexShrink: 0,
              }}
            >
              {validating ? 'Validating…' : 'Validate Shell Syntax'}
            </button>
          </div>

          {validation && (
            <div style={{
              padding: '12px 16px',
              borderRadius: 8,
              background: Object.values(validation).every(v => v.ok) ? '#0f2a1e' : '#2a1f0f',
              border: `1px solid ${Object.values(validation).every(v => v.ok) ? '#10b981' : '#f59e0b'}`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <div style={{ color: '#e8eef5', fontWeight: 600, fontSize: '0.85rem' }}>
                  {Object.values(validation).every(v => v.ok)
                    ? `All ${Object.keys(validation).length} variant(s) OK`
                    : `${Object.values(validation).filter(v => !v.ok).length} of ${Object.keys(validation).length} variant(s) broken`}
                </div>
                <button
                  onClick={() => setValidation(null)}
                  style={{ background: 'transparent', border: 'none', color: '#7a8ba3', fontSize: '0.8rem', cursor: 'pointer' }}
                >
                  Dismiss
                </button>
              </div>
              <div style={{ fontSize: '0.78rem' }}>
                {Object.entries(validation).map(([adapter, r]) => (
                  <div key={adapter} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', color: r.ok ? '#94a3b8' : '#fbbf24', marginTop: 3 }}>
                    <span style={{ minWidth: 90, fontFamily: 'monospace' }}>[{adapter}]</span>
                    <span style={{ fontFamily: r.ok ? 'inherit' : 'monospace' }}>
                      {r.ok
                        ? (r.stage === 'skipped-powershell' ? 'skipped (PowerShell)' : 'OK')
                        : (r.message || 'invalid').split('\n')[0]}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </Section>

      {/* ── Section 3: Parameters ───────────────────────────────────────────── */}
      <Section
        title="Parameters"
        subtitle="Runtime parameters passed to this action. Placeholders in Command are matched by name."
        badge={
          parameters.length > 0 ? (
            <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700 border border-slate-600 text-gray-400">
              {parameters.length} param{parameters.length !== 1 ? 's' : ''}
            </span>
          ) : null
        }
      >
        {/* Column headers */}
        {parameters.length > 0 && (
          <div
            className="grid gap-2 mb-1.5 text-xs font-medium uppercase tracking-wider px-1"
            style={{ color: '#7a8ba3', gridTemplateColumns: '1.4fr 90px 60px 120px 1.2fr 32px' }}
          >
            <span>Name</span>
            <span>Type</span>
            <span>Req</span>
            <span>Default</span>
            <span>Description</span>
            <span />
          </div>
        )}

        <div className="space-y-2">
          {parameters.map((param, idx) => (
            <div
              key={idx}
              className="grid gap-2 items-center p-2 rounded-lg"
              style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557', gridTemplateColumns: '1.4fr 90px 60px 120px 1.2fr 32px' }}
            >
              {/* Name */}
              <input
                value={param.name}
                onChange={e => updateParam(idx, 'name', e.target.value.replace(/\s/g, '_').toLowerCase())}
                placeholder="param_name"
                className="form-input text-xs py-1.5 font-mono"
              />

              {/* Type */}
              <select
                value={param.type}
                onChange={e => updateParam(idx, 'type', e.target.value)}
                className="form-input text-xs py-1.5"
              >
                <option value="string">string</option>
                <option value="integer">integer</option>
                <option value="float">float</option>
                <option value="boolean">boolean</option>
              </select>

              {/* Required toggle */}
              <div className="flex justify-center">
                <button
                  type="button"
                  onClick={() => updateParam(idx, 'required', !param.required)}
                  className={`w-8 h-5 rounded-full transition-colors ${param.required ? 'bg-info-600' : 'bg-slate-600'}`}
                >
                  <div className={`w-4 h-4 rounded-full bg-white shadow mx-0.5 transition-transform ${param.required ? 'translate-x-3' : 'translate-x-0'}`} />
                </button>
              </div>

              {/* Default */}
              <input
                value={param.default ?? ''}
                onChange={e => updateParam(idx, 'default', e.target.value)}
                placeholder="optional"
                className="form-input text-xs py-1.5"
              />

              {/* Description */}
              <input
                value={param.description ?? ''}
                onChange={e => updateParam(idx, 'description', e.target.value)}
                placeholder="What is this param?"
                className="form-input text-xs py-1.5"
              />

              {/* Remove */}
              <button
                onClick={() => removeParam(idx)}
                className="p-1.5 rounded text-gray-500 hover:text-critical-400 hover:bg-slate-700 transition-colors"
              >
                <IconTrash size={14} />
              </button>
            </div>
          ))}
        </div>

        {parameters.length === 0 && (
          <div className="text-center py-5 text-xs" style={{ color: '#7a8ba3' }}>
            No parameters defined. Click "Add Parameter" to define runtime inputs.
          </div>
        )}

        <button
          onClick={addParameter}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-gray-300 transition-colors border border-slate-600 mt-2"
        >
          <IconPlus size={13} /> Add Parameter
        </button>
      </Section>

      {/* ── Section: Output Fields ───────────────────────────────────────── */}
      <Section
        title="Output Fields"
        subtitle={
          isBuiltin
            ? 'Built-in extraction rules for this tool. Locked — out-of-box tools ship with pre-defined parsing.'
            : 'Extract structured values from this tool\'s output for use in runbook decisions and conditions.'
        }
        badge={
          isBuiltin ? (
            <span className="flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-slate-700 border border-slate-600 text-gray-400">
              <IconLock size={11} /> built-in
            </span>
          ) : outputFields.length > 0 ? (
            <span className="text-xs px-2 py-0.5 rounded-full bg-slate-700 border border-slate-600 text-gray-400">
              {outputFields.length} field{outputFields.length !== 1 ? 's' : ''}
            </span>
          ) : null
        }
      >
        {isBuiltin ? (
          outputFields.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {outputFields.map((f, idx) => (
                <div
                  key={idx}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs"
                  style={{ background: '#1a1f2e', border: '1px solid #3d4557', color: '#a0aec0' }}
                >
                  <IconLock size={11} style={{ color: '#5a6478' }} />
                  <span className="font-mono" style={{ color: '#7ee787' }}>{f.field}</span>
                  <span style={{ color: '#5a6478' }}>:{f.type}</span>
                  <span style={{ color: '#5a6478' }}>· {f.kind}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center py-4 text-xs" style={{ color: '#7a8ba3' }}>
              No structured output fields defined for this tool.
            </div>
          )
        ) : (
          <>
            {outputFields.length > 0 && (
              <div
                className="grid gap-2 mb-1.5 text-xs font-medium uppercase tracking-wider px-1"
                style={{ color: '#7a8ba3', gridTemplateColumns: '1.2fr 90px 1.6fr 100px 32px' }}
              >
                <span>Field</span>
                <span>Via</span>
                <span>Pattern</span>
                <span>Type</span>
                <span />
              </div>
            )}

            <div className="space-y-2">
              {outputFields.map((f, idx) => (
                <div
                  key={idx}
                  className="grid gap-2 items-center p-2 rounded-lg"
                  style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557', gridTemplateColumns: '1.2fr 90px 1.6fr 100px 32px' }}
                >
                  <input
                    value={f.field}
                    onChange={e => updateOutputField(idx, 'field', e.target.value.replace(/\s/g, '_'))}
                    placeholder="variable_name"
                    className="form-input text-xs py-1.5 font-mono"
                  />
                  <select
                    value={f.kind}
                    onChange={e => updateOutputField(idx, 'kind', e.target.value)}
                    className="form-input text-xs py-1.5"
                  >
                    <option value="regex">regex</option>
                    <option value="count">count</option>
                    <option value="jsonpath">jsonpath</option>
                  </select>
                  <input
                    value={f.pattern}
                    onChange={e => updateOutputField(idx, 'pattern', e.target.value)}
                    placeholder={
                      f.kind === 'jsonpath' ? '$.usage_percent'
                      : f.kind === 'count'  ? '^tcp\\s+LISTEN  (lines matching pattern)'
                      : 'HTTP/[\\d.]+\\s+(\\d{3})'
                    }
                    className="form-input text-xs py-1.5 font-mono"
                  />
                  <select
                    value={f.type}
                    onChange={e => updateOutputField(idx, 'type', e.target.value)}
                    className="form-input text-xs py-1.5"
                  >
                    <option value="string">string</option>
                    <option value="integer">integer</option>
                    <option value="float">float</option>
                    <option value="boolean">boolean</option>
                  </select>
                  <button
                    onClick={() => removeOutputField(idx)}
                    className="p-1.5 rounded text-gray-500 hover:text-critical-400 hover:bg-slate-700 transition-colors"
                  >
                    <IconTrash size={14} />
                  </button>
                </div>
              ))}
            </div>

            {outputFields.length === 0 && (
              <div className="text-center py-5 text-xs" style={{ color: '#7a8ba3' }}>
                No output fields defined. Click "Add Field" to extract structured values from this tool's output.
              </div>
            )}

            <button
              onClick={addOutputField}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-gray-300 transition-colors border border-slate-600 mt-2"
            >
              <IconPlus size={13} /> Add Field
            </button>
          </>
        )}
      </Section>

      {/* ── Section 4: Process Rules (intrusive only) ──────────────────────── */}
      <Section
        title="Process Allow / Deny Rules"
        subtitle={
          isIntrusive
            ? 'Rules evaluated top-to-bottom by priority. First match wins. Unmatched processes are DENIED.'
            : 'Process rules are only applicable to intrusive actions that target processes.'
        }
        badge={
          hasProcessRules ? (
            <span className="text-xs px-2 py-0.5 rounded-full bg-warning-900/40 border border-warning-700/50 text-warning-400">
              {processRules.filter(r => r.allow).length} allow · {processRules.filter(r => !r.allow).length} deny
            </span>
          ) : null
        }
      >
        {/* Enable toggle */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Toggle
              label="Enable process rules for this action"
              checked={hasProcessRules}
              onChange={v => {
                setHasProcessRules(v)
                if (!v) setProcessRules([])
              }}
            />
          </div>
          {hasProcessRules && (
            <button
              onClick={addRule}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-lg bg-slate-700 hover:bg-slate-600 text-gray-300 transition-colors border border-slate-600"
            >
              <IconPlus size={13} /> Add Rule
            </button>
          )}
        </div>

        {hasProcessRules && (
          <>
            {/* Column headers */}
            {processRules.length > 0 && (
              <div className="grid gap-2 mb-1.5 text-xs font-medium uppercase tracking-wider px-1" style={{ color: '#7a8ba3', gridTemplateColumns: '56px 72px 1fr 1fr 36px' }}>
                <span>Priority</span>
                <span>Decision</span>
                <span>Regex Pattern</span>
                <span>Description</span>
                <span />
              </div>
            )}

            <div className="space-y-2">
              {sortedRules.map((rule, idx) => {
                const origIdx = processRules.indexOf(rule)
                const valid = !rule.pattern || isValidRegex(rule.pattern)
                return (
                  <div
                    key={idx}
                    className={`grid gap-2 items-center p-2 rounded-lg border ${
                      rule.allow
                        ? 'bg-success-900/10 border-success-800/40'
                        : 'bg-critical-900/10 border-critical-800/40'
                    }`}
                    style={{ gridTemplateColumns: '56px 72px 1fr 1fr 36px' }}
                  >
                    {/* Priority */}
                    <input
                      type="number"
                      min={1}
                      value={rule.priority}
                      onChange={e => updateRule(origIdx, 'priority', parseInt(e.target.value) || 1)}
                      className="form-input text-xs text-center px-1 py-1.5"
                    />

                    {/* Allow/Deny toggle */}
                    <button
                      type="button"
                      onClick={() => updateRule(origIdx, 'allow', !rule.allow)}
                      className={`flex items-center justify-center gap-1 px-2 py-1.5 rounded-lg text-xs font-semibold border transition-all ${
                        rule.allow
                          ? 'bg-success-900/40 border-success-700 text-success-400'
                          : 'bg-critical-900/40 border-critical-700 text-critical-400'
                      }`}
                    >
                      {rule.allow ? <><IconCheck size={11} /> Allow</> : <><IconX size={11} /> Deny</>}
                    </button>

                    {/* Pattern */}
                    <div className="relative">
                      <input
                        value={rule.pattern}
                        onChange={e => updateRule(origIdx, 'pattern', e.target.value)}
                        placeholder="^yes$"
                        className={`form-input font-mono text-xs py-1.5 w-full ${
                          rule.pattern && !valid ? 'border-critical-600 focus:border-critical-500' : ''
                        }`}
                      />
                      {rule.pattern && !valid && (
                        <span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-critical-400">Invalid regex</span>
                      )}
                    </div>

                    {/* Description */}
                    <input
                      value={rule.description}
                      onChange={e => updateRule(origIdx, 'description', e.target.value)}
                      placeholder="Human-readable note"
                      className="form-input text-xs py-1.5"
                    />

                    {/* Remove */}
                    <button
                      onClick={() => removeRule(origIdx)}
                      className="p-1.5 rounded text-gray-500 hover:text-critical-400 hover:bg-slate-700 transition-colors"
                    >
                      <IconTrash size={14} />
                    </button>
                  </div>
                )
              })}
            </div>

            {processRules.length === 0 && (
              <div className="text-center py-6 text-sm" style={{ color: '#7a8ba3' }}>
                No rules yet. All processes will be <span className="text-critical-400 font-semibold">DENIED</span> (whitelist-by-default).
                <br />Click "Add Rule" to define allow/deny patterns.
              </div>
            )}

            {/* ── Test panel ──────────────────────────────────────────────── */}
            {!isNew && (
              <div className="mt-5 p-4 rounded-lg" style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557' }}>
                <div className="flex items-center gap-2 mb-3">
                  <IconTestPipe size={14} className="text-info-400" />
                  <span className="text-xs font-semibold text-gray-300">Test Process Name</span>
                </div>
                <div className="flex gap-2">
                  <input
                    value={testProcess}
                    onChange={e => setTestProcess(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && testProcessName()}
                    placeholder="e.g. yes, java, stress-ng"
                    className="form-input text-sm flex-1"
                  />
                  <button
                    onClick={testProcessName}
                    disabled={!testProcess.trim() || testLoading}
                    className="btn btn-secondary text-sm px-4 disabled:opacity-50"
                  >
                    {testLoading ? '…' : 'Test'}
                  </button>
                </div>
                {testResult && (
                  <div className={`mt-3 flex items-start gap-2 p-3 rounded-lg border text-sm ${
                    testResult.allowed
                      ? 'bg-success-900/20 border-success-700/50 text-success-300'
                      : 'bg-critical-900/20 border-critical-700/50 text-critical-300'
                  }`}>
                    {testResult.allowed
                      ? <IconCheck size={16} className="flex-shrink-0 mt-0.5" />
                      : <IconX size={16} className="flex-shrink-0 mt-0.5" />}
                    <div>
                      <div className="font-semibold mb-0.5">
                        {testResult.allowed ? '✓ ALLOWED' : '✗ DENIED'}
                      </div>
                      <div className="text-xs opacity-80">{testResult.reason}</div>
                      {testResult.matched_rule && (
                        <code className="text-xs mt-1 block opacity-70">
                          Pattern: {testResult.matched_rule.pattern}
                        </code>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </Section>

      {/* ── Save bar ─────────────────────────────────────────────────────────
          All three action buttons (Cancel / Delete / Save) share the same
          outlined shape — transparent fill, colored border and text as the
          only differentiator. Keeps the row visually calm and consistent
          regardless of destructive vs. primary intent. */}
      <div className="flex items-center justify-between pt-4 border-t border-slate-700/50 mt-8">
        <div className="flex items-center gap-2">
          <button onClick={onBack} style={outlineBtn('#a0aec0', '#3d4557')}>
            Cancel
          </button>
          {/* Delete: hidden for new tools (nothing to delete) and seeded tools
              (backend rejects — surface that up front by hiding the affordance). */}
          {!isNew && !isBuiltin && (
            <button
              onClick={() => setConfirmDelete(true)}
              disabled={deleting}
              style={{ ...outlineBtn('#fca5a5', '#7f1d1d'), gap: 6,
                       cursor: deleting ? 'not-allowed' : 'pointer' }}
            >
              <IconTrash size={14} /> Delete Tool
            </button>
          )}
        </div>
        <button
          onClick={handleSave}
          disabled={saving || success}
          style={{ ...outlineBtn(
                     success ? '#86efac' : '#93c5fd',
                     success ? '#166534' : '#1e40af',
                   ),
                   gap: 6,
                   cursor: (saving || success) ? 'default' : 'pointer',
                   opacity: (saving || success) ? 0.85 : 1 }}
        >
          {success ? (
            <><IconCheck size={16} /> Saved!</>
          ) : saving ? (
            <><span className="animate-spin">⟳</span> Saving…</>
          ) : (
            <><IconDeviceFloppy size={16} /> Save Action</>
          )}
        </button>
      </div>

      {/* ── Delete confirm / blockers panel ─────────────────────────────────
          Panel fill matches the standard surface color (same as Section
          cards); only the border + heading text carry the alert semantics. */}
      {confirmDelete && (
        <div style={{ ...standardPanel, marginTop: 16 }}>
          <div style={{ color: '#fca5a5', fontWeight: 600, marginBottom: 8 }}>
            Delete this tool permanently?
          </div>
          <div style={{ color: '#a0aec0', fontSize: '0.85rem', marginBottom: 12 }}>
            This cannot be undone. Any runbook that references <code style={{ color: '#e8eef5' }}>{toolName}</code> will
            need to be updated. Seeded tools cannot be deleted.
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              onClick={handleDelete}
              disabled={deleting}
              style={{ ...outlineBtn('#fca5a5', '#7f1d1d'),
                       cursor: deleting ? 'wait' : 'pointer' }}
            >
              {deleting ? 'Deleting…' : 'Yes, delete permanently'}
            </button>
            <button
              onClick={() => { setConfirmDelete(false); setDeleteBlockers(null) }}
              disabled={deleting}
              style={outlineBtn('#a0aec0', '#3d4557')}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
      {deleteBlockers && deleteBlockers.length > 0 && (
        <div style={{ ...standardPanel, marginTop: 12 }}>
          <div style={{ color: '#fbbf24', fontWeight: 600, marginBottom: 8, fontSize: '0.9rem' }}>
            Blocked by {deleteBlockers.length} enabled runbook{deleteBlockers.length === 1 ? '' : 's'}:
          </div>
          <ul style={{ color: '#e8eef5', fontSize: '0.85rem', margin: 0, paddingLeft: 20 }}>
            {deleteBlockers.map(b => (
              <li key={b.id} style={{ marginBottom: 4 }}>
                <span style={{ fontWeight: 500 }}>{b.name}</span>
                <span style={{ color: '#7a8ba3' }}> — used in {b.section}</span>
              </li>
            ))}
          </ul>
          <div style={{ color: '#a0aec0', fontSize: '0.8rem', marginTop: 10 }}>
            Remove the tool from those runbooks (or disable them) and try again.
          </div>
        </div>
      )}
    </div>
  )
}

// ── Shared style helpers ─────────────────────────────────────────────────────
// Outlined button: transparent fill, colored border + text. All action buttons
// in the save/delete bar use this so intent (destructive / primary / cancel)
// is encoded ONLY in the border + text hue, never in the fill.
const outlineBtn = (textColor: string, borderColor: string): React.CSSProperties => ({
  padding: '0.5rem 0.9rem',
  borderRadius: 6,
  border: `1px solid ${borderColor}`,
  background: 'transparent',
  color: textColor,
  fontSize: '0.85rem',
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  fontFamily: 'inherit',
})

// Standard message panel: same fill as Section cards so the shape reads as a
// neutral information block. Semantic color goes on the heading, not the box.
const standardPanel: React.CSSProperties = {
  padding: 16,
  borderRadius: 8,
  background: '#1a1f2e',
  border: '1px solid #3d4557',
}

// ── Sub-components ────────────────────────────────────────────────────────────

function Section({
  title, subtitle, badge, children,
}: {
  title: string
  subtitle?: string
  badge?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <div className="mb-6 p-5 rounded-xl" style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557' }}>
      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1">
          <h3 className="text-sm font-semibold" style={{ color: '#e8eef5' }}>{title}</h3>
          {subtitle && <p className="text-xs mt-0.5" style={{ color: '#7a8ba3' }}>{subtitle}</p>}
        </div>
        {badge}
      </div>
      <div className="space-y-3">
        {children}
      </div>
    </div>
  )
}

function Field({
  label, hint, required, children,
}: {
  label: string
  hint?: string
  required?: boolean
  children: React.ReactNode
}) {
  return (
    <div>
      <label className="block text-xs font-medium mb-1.5" style={{ color: '#a0aec0' }}>
        {label}
        {required && <span className="text-critical-400 ml-1">*</span>}
        {hint && <span className="ml-2 font-normal" style={{ color: '#7a8ba3' }}>— {hint}</span>}
      </label>
      {children}
    </div>
  )
}

// ── Environment variant adder ─────────────────────────────────────────────────

const ENV_OPTIONS = [
  { value: 'docker',     icon: '🐳', label: 'Docker' },
  { value: 'kubernetes', icon: '☸',  label: 'Kubernetes' },
  { value: 'ssh',        icon: '💻', label: 'SSH / Bare-metal' },
  { value: 'aws_ssm',    icon: '☁',  label: 'AWS SSM (EC2)' },
  { value: 'azure',      icon: '☁',  label: 'Azure Run Command' },
  { value: 'vcenter',    icon: '⬡',  label: 'VMware vCenter' },
  { value: 'any',        icon: '🌐', label: 'Any (explicit fallback)' },
] as const

function EnvVariantAdder({
  existing,
  onAdd,
}: {
  existing: string[]
  onAdd: (env: string) => void
}) {
  const [open, setOpen] = useState(false)
  const available = ENV_OPTIONS.filter(e => !existing.includes(e.value))

  if (available.length === 0) return null

  return (
    <div className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-dashed transition-colors"
        style={{ borderColor: '#3d4557', color: '#7a8ba3' }}
        onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = '#60a5fa'; (e.currentTarget as HTMLButtonElement).style.color = '#60a5fa' }}
        onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = '#3d4557'; (e.currentTarget as HTMLButtonElement).style.color = '#7a8ba3' }}
      >
        <span>+</span> Add Environment Variant
      </button>
      {open && (
        <div
          className="absolute left-0 mt-1 z-50 rounded-lg overflow-hidden shadow-xl"
          style={{ background: '#1a1f2e', border: '1px solid #3d4557', minWidth: 200 }}
        >
          {available.map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => { onAdd(opt.value); setOpen(false) }}
              className="flex items-center gap-2 w-full px-3 py-2 text-xs text-left transition-colors"
              style={{ color: '#a0aec0' }}
              onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.04)')}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <span>{opt.icon}</span>
              <span>{opt.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Toggle({
  label, checked, onChange,
}: {
  label: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="flex items-center gap-2 cursor-pointer select-none">
      <div
        onClick={() => onChange(!checked)}
        className={`relative w-9 h-5 rounded-full transition-colors ${
          checked ? 'bg-info-500' : 'bg-slate-600'
        }`}
      >
        <div className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-0'
        }`} />
      </div>
      <span className="text-xs" style={{ color: checked ? '#e8eef5' : '#4b5563' }}>{label}</span>
    </label>
  )
}
