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

  if (loading) {
    return (
      <div className="space-y-4">
        {[...Array(4)].map((_, i) => <div key={i} className="metric-card h-16 skeleton-pulse" />)}
      </div>
    )
  }

  const isIntrusive = category === 'remediation_intrusive'

  return (
    <div className="page-transition-enter max-w-3xl">

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
                  className={`flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer transition-all ${
                    category === opt.value
                      ? 'bg-slate-700 border-slate-500 text-gray-100'
                      : 'bg-slate-800/50 border-slate-700 text-gray-400 hover:border-slate-600'
                  }`}
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
                        : 'bg-slate-800 border-slate-700 text-gray-500 hover:border-slate-600'
                    }`}
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
          <div className="mt-1 text-xs px-3 py-2 rounded-lg" style={{ background: '#0d1117', border: '1px solid #21262d', color: '#6e7681' }}>
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
              <span>Command  <span className="normal-case font-normal" style={{ color: '#4a5568' }}>— inner shell command only; transport wrapper shown below</span></span>
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
                  transport: 'vCenter GuestProcessManager.startProgram() → {target}  (no agent on VM — controller dispatches via vCenter API)',
                  transportColor: '#a78bfa',
                },
                any: {
                  icon: '🌐',
                  label: 'Any (explicit)',
                },
              }
              const meta = envMeta[env] || { icon: '⚙', label: env }
              const resolvedTransport = meta.transport
                ? meta.transport.replace('⟨cmd⟩', (cmd ?? '').trim() || '⟨cmd⟩')
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
                      placeholder={`Inner command for ${meta.label}`}
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
                        background: '#0d1117',
                        border: '1px solid #21262d',
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
              className="grid gap-2 items-center p-2 rounded-lg bg-slate-800/50 border border-slate-700/60"
              style={{ gridTemplateColumns: '1.4fr 90px 60px 120px 1.2fr 32px' }}
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
                  className="grid gap-2 items-center p-2 rounded-lg bg-slate-800/50 border border-slate-700/60"
                  style={{ gridTemplateColumns: '1.2fr 90px 1.6fr 100px 32px' }}
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
                    <option value="jsonpath">jsonpath</option>
                  </select>
                  <input
                    value={f.pattern}
                    onChange={e => updateOutputField(idx, 'pattern', e.target.value)}
                    placeholder={f.kind === 'regex' ? 'HTTP/[\\d.]+\\s+(\\d{3})' : '$.usage_percent'}
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
              <div className="mt-5 p-4 rounded-lg bg-slate-800/60 border border-slate-700">
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

      {/* ── Save bar ────────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between pt-4 border-t border-slate-700/50 mt-8">
        <button onClick={onBack} className="btn btn-secondary">
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={saving || success}
          className={`btn flex items-center gap-2 ${
            success ? 'btn-success' : 'btn-primary'
          } disabled:opacity-60`}
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
    </div>
  )
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
    <div className="mb-6 p-5 rounded-xl bg-slate-800/40 border border-slate-700/60">
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
          checked ? 'bg-info-600' : 'bg-slate-600'
        }`}
      >
        <div className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
          checked ? 'translate-x-4' : 'translate-x-0'
        }`} />
      </div>
      <span className="text-xs" style={{ color: '#a0aec0' }}>{label}</span>
    </label>
  )
}
