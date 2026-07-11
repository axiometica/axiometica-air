import { useState } from 'react'

// ── Design tokens (mirrors Settings.tsx) ──────────────────────────────────────
const DS = {
  bg:     '#0d1117',
  surface:'#1a1f2e',
  raised: '#252c3c',
  border: '#3d4557',
  txtP:   '#e8eef5',
  txtS:   '#7a8ba3',
  txtM:   '#a0aec0',
  accent: '#3b82f6',
  green:  '#10b981',
  red:    '#ef4444',
  yellow: '#f59e0b',
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '7px 10px',
  borderRadius: 6,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.bg,
  color: DS.txtP,
  fontSize: '0.83rem',
  outline: 'none',
  boxSizing: 'border-box',
  fontFamily: 'inherit',
}

const btn = (color = DS.accent): React.CSSProperties => ({
  padding: '7px 16px',
  borderRadius: 7,
  border: 'none',
  backgroundColor: color,
  color: '#fff',
  fontSize: '0.82rem',
  fontWeight: 600,
  cursor: 'pointer',
  whiteSpace: 'nowrap',
})

const ghostBtn: React.CSSProperties = {
  padding: '7px 16px',
  borderRadius: 7,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.raised,
  color: DS.txtP,
  fontSize: '0.82rem',
  fontWeight: 500,
  cursor: 'pointer',
}

// ── API helpers ───────────────────────────────────────────────────────────────

async function apiFetch(url: string, opts?: RequestInit) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(opts?.headers ?? {}) },
    ...opts,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`)
  return data
}

// ── Component ─────────────────────────────────────────────────────────────────

interface ToolBuilderProps {
  isExpanded: boolean
  onToggle: () => void
}

type Step = 'describe' | 'review' | 'parse' | 'done'

export default function ToolBuilder({ isExpanded, onToggle }: ToolBuilderProps) {
  const [step, setStep] = useState<Step>('describe')

  // Step 1 — describe
  const [description, setDescription] = useState('')
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)

  // Step 2 — review / edit draft
  const [draftJson, setDraftJson] = useState('')
  const [jsonError, setJsonError] = useState<string | null>(null)

  // Step 2b — parse output
  const [sampleOutput, setSampleOutput] = useState('')
  const [parsing, setParsing] = useState(false)
  const [parseError, setParseError] = useState<string | null>(null)
  const [parseNotes, setParseNotes] = useState('')

  // Step 3 — register
  const [registering, setRegistering] = useState(false)
  const [regError, setRegError] = useState<string | null>(null)
  const [registeredName, setRegisteredName] = useState('')

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleGenerate = async () => {
    if (!description.trim()) return
    setGenerating(true)
    setGenError(null)
    try {
      const data = await apiFetch('/api/approved-actions/generate', {
        method: 'POST',
        body: JSON.stringify({ description }),
      })
      setDraftJson(JSON.stringify(data, null, 2))
      setStep('review')
    } catch (e: any) {
      setGenError(e.message)
    } finally {
      setGenerating(false)
    }
  }

  const parseDraft = (): object | null => {
    try {
      const parsed = JSON.parse(draftJson)
      setJsonError(null)
      return parsed
    } catch {
      setJsonError('Invalid JSON — fix the syntax before continuing.')
      return null
    }
  }

  const handleParseOutput = async () => {
    const draft = parseDraft()
    if (!draft || !sampleOutput.trim()) return
    setParsing(true)
    setParseError(null)
    try {
      const data = await apiFetch('/api/approved-actions/parse-output', {
        method: 'POST',
        body: JSON.stringify({
          tool_name: (draft as any).tool_name ?? 'unknown',
          sample_output: sampleOutput,
          command: (draft as any).command_variants?.any ?? (draft as any).command_variants?.ssh ?? null,
        }),
      })
      // Merge output_fields into draft
      const updated = { ...(draft as any), output_fields: data.output_fields ?? [] }
      setDraftJson(JSON.stringify(updated, null, 2))
      setParseNotes(data.parsing_notes ?? '')
    } catch (e: any) {
      setParseError(e.message)
    } finally {
      setParsing(false)
    }
  }

  const handleRegister = async () => {
    const draft = parseDraft()
    if (!draft) return
    setRegistering(true)
    setRegError(null)
    try {
      const data = await apiFetch('/api/approved-actions', {
        method: 'POST',
        body: JSON.stringify(draft),
      })
      setRegisteredName(data.name ?? (draft as any).name ?? 'Tool')
      setStep('done')
    } catch (e: any) {
      setRegError(e.message)
    } finally {
      setRegistering(false)
    }
  }

  const handleReset = () => {
    setStep('describe')
    setDescription('')
    setDraftJson('')
    setSampleOutput('')
    setParseNotes('')
    setGenError(null)
    setJsonError(null)
    setParseError(null)
    setRegError(null)
    setRegisteredName('')
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{
      backgroundColor: DS.surface,
      border: `1px solid ${DS.border}`,
      borderRadius: 10,
      overflow: 'hidden',
    }}>
      {/* Header */}
      <button
        onClick={onToggle}
        style={{
          width: '100%',
          padding: '0.9rem 1.25rem',
          background: 'none',
          border: 'none',
          cursor: 'pointer',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        <span style={{ fontSize: '0.9rem', fontWeight: 700, color: DS.txtP }}>
          AI Tool Builder
        </span>
        <span style={{
          color: DS.txtS, fontSize: '0.75rem',
          display: 'inline-block',
          transform: isExpanded ? 'rotate(180deg)' : 'none',
          transition: 'transform 0.18s ease',
        }}>▼</span>
      </button>

      {isExpanded && (
        <div style={{ borderTop: `1px solid ${DS.border}`, padding: '1.1rem 1.25rem 1.25rem' }}>
          <p style={{ fontSize: '0.8rem', color: DS.txtS, margin: '0 0 1.25rem' }}>
            Describe a new tool in plain English. The platform LLM drafts the complete catalog entry —
            command variants, parameters, blast radius — which you can review and register in one click.
          </p>

          {/* Progress indicator */}
          <div style={{ display: 'flex', gap: 8, marginBottom: '1.5rem' }}>
            {(['describe', 'review', 'done'] as const).map((s, i) => {
              const labels = ['1. Describe', '2. Review', '3. Register']
              const active = step === s || (step === 'parse' && s === 'review')
              const past = (step === 'review' && i === 0) ||
                           (step === 'parse'  && i === 0) ||
                           (step === 'done'   && i <= 1)
              return (
                <div key={s} style={{
                  padding: '3px 12px',
                  borderRadius: 20,
                  fontSize: '0.73rem',
                  fontWeight: 600,
                  backgroundColor: active ? DS.accent : past ? 'rgba(59,130,246,0.15)' : DS.raised,
                  color: active ? '#fff' : past ? DS.accent : DS.txtS,
                  border: `1px solid ${active ? DS.accent : past ? DS.accent : DS.border}`,
                }}>
                  {labels[i]}
                </div>
              )
            })}
          </div>

          {/* ── Step 1: Describe ─────────────────────────────────────── */}
          {step === 'describe' && (
            <div>
              <label style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: DS.txtM, marginBottom: '0.4rem' }}>
                What should this tool do?
              </label>
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder={
                  'Example: "A tool that lists open TCP connections and counts them by remote IP, ' +
                  'working via SSH and inside Docker containers."'
                }
                rows={5}
                style={{ ...inputStyle, resize: 'vertical', lineHeight: 1.5 }}
              />
              <p style={{ fontSize: '0.7rem', color: DS.txtS, margin: '4px 0 0' }}>
                Mention the adapters you need (docker, ssh, kubernetes) and what output fields matter.
              </p>
              {genError && (
                <div style={{ marginTop: '0.75rem', padding: '0.5rem 0.75rem', borderRadius: 7,
                  backgroundColor: 'rgba(239,68,68,0.1)', border: `1px solid rgba(239,68,68,0.3)`,
                  color: '#f87171', fontSize: '0.8rem' }}>
                  {genError}
                </div>
              )}
              <div style={{ marginTop: '1rem' }}>
                <button
                  onClick={handleGenerate}
                  disabled={generating || !description.trim()}
                  style={{ ...btn(), opacity: generating || !description.trim() ? 0.5 : 1,
                    cursor: generating || !description.trim() ? 'not-allowed' : 'pointer' }}
                >
                  {generating ? 'Generating…' : 'Generate Tool Definition'}
                </button>
              </div>
            </div>
          )}

          {/* ── Step 2: Review & optional parse ─────────────────────── */}
          {(step === 'review' || step === 'parse') && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                <label style={{ fontSize: '0.78rem', fontWeight: 600, color: DS.txtM }}>
                  Generated Tool Definition
                </label>
                <button onClick={() => setStep('describe')} style={{ ...ghostBtn, padding: '3px 10px', fontSize: '0.73rem' }}>
                  ← Back
                </button>
              </div>
              <textarea
                value={draftJson}
                onChange={e => { setDraftJson(e.target.value); setJsonError(null) }}
                rows={18}
                style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.78rem', resize: 'vertical', lineHeight: 1.5 }}
              />
              {jsonError && (
                <div style={{ marginTop: '0.5rem', padding: '0.45rem 0.75rem', borderRadius: 7,
                  backgroundColor: 'rgba(239,68,68,0.1)', border: `1px solid rgba(239,68,68,0.3)`,
                  color: '#f87171', fontSize: '0.79rem' }}>
                  {jsonError}
                </div>
              )}

              {/* Output parser section */}
              <div style={{ marginTop: '1.25rem', padding: '0.85rem 1rem', borderRadius: 8,
                backgroundColor: DS.raised, border: `1px solid ${DS.border}` }}>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, color: DS.txtM, marginBottom: '0.5rem' }}>
                  Optional: Infer Output Fields
                </div>
                <p style={{ fontSize: '0.73rem', color: DS.txtS, margin: '0 0 0.6rem' }}>
                  Paste sample stdout from running one of the commands above. The LLM will infer
                  extractable field names and add them to <code style={{ color: DS.accent }}>output_fields</code>.
                </p>
                <textarea
                  value={sampleOutput}
                  onChange={e => setSampleOutput(e.target.value)}
                  placeholder="Paste command stdout here…"
                  rows={5}
                  style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.76rem', resize: 'vertical' }}
                />
                {parseNotes && (
                  <p style={{ fontSize: '0.72rem', color: DS.txtS, margin: '4px 0 0' }}>
                    Parsing strategy: {parseNotes}
                  </p>
                )}
                {parseError && (
                  <div style={{ marginTop: '0.5rem', padding: '0.45rem 0.75rem', borderRadius: 7,
                    backgroundColor: 'rgba(239,68,68,0.1)', border: `1px solid rgba(239,68,68,0.3)`,
                    color: '#f87171', fontSize: '0.79rem' }}>
                    {parseError}
                  </div>
                )}
                <div style={{ marginTop: '0.65rem' }}>
                  <button
                    onClick={handleParseOutput}
                    disabled={parsing || !sampleOutput.trim()}
                    style={{ ...btn(DS.yellow), opacity: parsing || !sampleOutput.trim() ? 0.5 : 1,
                      cursor: parsing || !sampleOutput.trim() ? 'not-allowed' : 'pointer' }}
                  >
                    {parsing ? 'Inferring…' : 'Infer Output Fields'}
                  </button>
                </div>
              </div>

              {/* Register */}
              <div style={{ marginTop: '1.1rem', display: 'flex', gap: 10, alignItems: 'center' }}>
                <button
                  onClick={handleRegister}
                  disabled={registering}
                  style={{ ...btn(DS.green), opacity: registering ? 0.5 : 1,
                    cursor: registering ? 'not-allowed' : 'pointer' }}
                >
                  {registering ? 'Registering…' : 'Register Tool'}
                </button>
                <span style={{ fontSize: '0.73rem', color: DS.txtS }}>
                  Tool is saved to the catalog immediately — no restart required.
                </span>
              </div>
              {regError && (
                <div style={{ marginTop: '0.65rem', padding: '0.5rem 0.75rem', borderRadius: 7,
                  backgroundColor: 'rgba(239,68,68,0.1)', border: `1px solid rgba(239,68,68,0.3)`,
                  color: '#f87171', fontSize: '0.8rem' }}>
                  {regError}
                </div>
              )}
            </div>
          )}

          {/* ── Step 3: Done ─────────────────────────────────────────── */}
          {step === 'done' && (
            <div style={{ textAlign: 'center', padding: '2rem 0' }}>
              <div style={{ fontSize: '2.5rem', marginBottom: '0.5rem' }}>✓</div>
              <div style={{ fontSize: '1rem', fontWeight: 700, color: DS.green, marginBottom: '0.4rem' }}>
                Tool Registered
              </div>
              <div style={{ fontSize: '0.83rem', color: DS.txtM, marginBottom: '1.5rem' }}>
                <strong style={{ color: DS.txtP }}>{registeredName}</strong> is now available in the
                runbook editor's tool picker.
              </div>
              <button onClick={handleReset} style={ghostBtn}>
                Build Another Tool
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
