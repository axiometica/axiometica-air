import { useState } from 'react'
import { createPortal } from 'react-dom'

// ── API helper ────────────────────────────────────────────────────────────────

async function apiFetch(url: string, opts?: RequestInit) {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(opts?.headers ?? {}) },
    ...opts,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`)
  return data
}

// ── Shared design tokens (mirrors the platform palette) ───────────────────────

const C = {
  bg:       '#0d1117',
  surface:  '#1a1f2e',
  raised:   '#252c3c',
  border:   '#3d4557',
  txtP:     '#e8eef5',
  txtS:     '#7a8ba3',
  txtM:     '#a0aec0',
  violet:   '#a855f7',      // AI brand accent (matches "Generate with AI" button)
  blue:     '#4070a0',      // info-500 (btn-primary)
  green:    '#3a7a5a',      // success-500
  red:      '#a04848',      // critical-500
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 11px',
  borderRadius: 7,
  border: `1px solid ${C.border}`,
  backgroundColor: C.bg,
  color: C.txtP,
  fontSize: '0.83rem',
  outline: 'none',
  boxSizing: 'border-box',
  fontFamily: 'inherit',
  lineHeight: 1.5,
}

// ── Component ─────────────────────────────────────────────────────────────────

interface ToolBuilderProps {
  onClose: () => void
  onRegistered?: () => void
}

type Step = 'describe' | 'review' | 'done'

export default function ToolBuilder({ onClose, onRegistered }: ToolBuilderProps) {
  const [step, setStep] = useState<Step>('describe')

  // Step 1
  const [description, setDescription]   = useState('')
  const [generating, setGenerating]     = useState(false)
  const [genError, setGenError]         = useState<string | null>(null)

  // Step 2 — JSON draft
  const [draftJson, setDraftJson]       = useState('')
  const [jsonError, setJsonError]       = useState<string | null>(null)

  // Step 2 — refine section
  const [sampleOutput, setSampleOutput] = useState('')
  const [refineOpen, setRefineOpen]     = useState(false)
  const [refineHint, setRefineHint]     = useState('')   // "pre-filled from AI research"
  const [parsing, setParsing]           = useState(false)
  const [parseError, setParseError]     = useState<string | null>(null)
  const [parseNotes, setParseNotes]     = useState('')

  // Step 3
  const [registering, setRegistering]   = useState(false)
  const [regError, setRegError]         = useState<string | null>(null)
  const [registeredName, setRegisteredName] = useState('')

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleGenerate = async () => {
    if (!description.trim()) return
    setGenerating(true)
    setGenError(null)
    try {
      const data = await apiFetch('/api/approved-actions/generate', {
        method: 'POST',
        body: JSON.stringify({ description }),
      })
      // Pull research sample out before storing in the JSON editor
      const rs: string = data._research_sample ?? ''
      delete data._research_sample
      setDraftJson(JSON.stringify(data, null, 2))
      if (rs) {
        setSampleOutput(rs)
        setRefineHint('Pre-filled with AI-researched sample output. Replace with real output for better accuracy.')
        setRefineOpen(true)
      } else {
        setSampleOutput('')
        setRefineHint('')
        setRefineOpen(false)
      }
      setParseNotes('')
      setParseError(null)
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

  const handleRefine = async () => {
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
          command: (draft as any).command_variants?.any
            ?? (draft as any).command_variants?.ssh
            ?? null,
        }),
      })
      // Merge: keep fields that already have patterns; fill in blanks; append new fields
      const existing: any[] = (draft as any).output_fields ?? []
      const parsed: any[] = data.output_fields ?? []
      const parsedMap = new Map(parsed.map((f: any) => [f.field, f]))
      const merged = existing.map((f: any) =>
        (!f.pattern || f.pattern === '') && parsedMap.has(f.field)
          ? parsedMap.get(f.field)
          : f
      )
      const existingNames = new Set(existing.map((f: any) => f.field))
      for (const pf of parsed) {
        if (!existingNames.has(pf.field)) merged.push(pf)
      }
      setDraftJson(JSON.stringify({ ...(draft as any), output_fields: merged }, null, 2))
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
      // Strip any internal fields before POSTing
      const { _research_sample, ...payload } = draft as any
      const data = await apiFetch('/api/approved-actions', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setRegisteredName(data.name ?? payload.name ?? 'Tool')
      setStep('done')
      onRegistered?.()
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
    setRefineHint('')
    setRefineOpen(false)
    setParseNotes('')
    setGenError(null)
    setJsonError(null)
    setParseError(null)
    setRegError(null)
    setRegisteredName('')
  }

  // ── Step pill helper ──────────────────────────────────────────────────────

  const steps: { key: Step; label: string }[] = [
    { key: 'describe', label: '1 · Describe' },
    { key: 'review',   label: '2 · Review' },
    { key: 'done',     label: '3 · Register' },
  ]
  const stepIndex = step === 'describe' ? 0 : step === 'review' ? 1 : 2

  // ── Modal ─────────────────────────────────────────────────────────────────

  return createPortal(
    <>
    <style>{`
      @keyframes tb-spin {
        to { transform: rotate(360deg); }
      }
    `}</style>
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0,0,0,0.72)',
        backdropFilter: 'blur(4px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        padding: '1.25rem',
      }}
      onClick={onClose}
    >
      <div
        style={{
          backgroundColor: C.surface,
          border: `1px solid ${C.border}`,
          borderTop: `3px solid ${C.violet}`,
          borderRadius: 12,
          width: '100%',
          maxWidth: 740,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          boxShadow: '0 25px 60px rgba(0,0,0,0.6)',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* ── Modal header ───────────────────────────────────────── */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '1rem 1.25rem 0.85rem',
          borderBottom: `1px solid ${C.border}`,
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
            <span style={{
              fontSize: '0.95rem',
              fontWeight: 700,
              color: C.txtP,
              display: 'flex',
              alignItems: 'center',
              gap: 7,
            }}>
              <span style={{ color: C.violet, fontSize: '1rem' }}>✦</span>
              AI Tool Builder
            </span>
            {/* Step pills */}
            <div style={{ display: 'flex', gap: 5 }}>
              {steps.map((s, i) => {
                const active = i === stepIndex
                const past   = i < stepIndex
                return (
                  <span key={s.key} style={{
                    padding: '2px 10px',
                    borderRadius: 20,
                    fontSize: '0.7rem',
                    fontWeight: 600,
                    backgroundColor: active ? C.violet
                      : past ? 'rgba(168,85,247,0.12)'
                      : C.raised,
                    color: active ? '#fff'
                      : past ? C.violet
                      : C.txtS,
                    border: `1px solid ${active ? C.violet : past ? 'rgba(168,85,247,0.35)' : C.border}`,
                  }}>
                    {s.label}
                  </span>
                )
              })}
            </div>
          </div>
          <button
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: C.txtS,
              fontSize: '1.1rem',
              cursor: 'pointer',
              padding: '2px 6px',
              borderRadius: 5,
              lineHeight: 1,
            }}
            title="Close"
          >×</button>
        </div>

        {/* ── Scrollable content ─────────────────────────────────── */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '1.25rem' }}>

          {/* ── Generating spinner (overlays all steps) ──────────── */}
          {generating && (
            <div style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              justifyContent: 'center', padding: '3.5rem 1rem', gap: '1.5rem',
            }}>
              {/* Ring spinner */}
              <div style={{ position: 'relative', width: 56, height: 56 }}>
                <div style={{
                  position: 'absolute', inset: 0, borderRadius: '50%',
                  border: `3px solid rgba(168,85,247,0.15)`,
                }} />
                <div style={{
                  position: 'absolute', inset: 0, borderRadius: '50%',
                  border: '3px solid transparent',
                  borderTopColor: C.violet,
                  animation: 'tb-spin 0.75s linear infinite',
                }} />
              </div>
              {/* Status */}
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '0.92rem', fontWeight: 600, color: C.txtP, marginBottom: '0.75rem' }}>
                  Building tool definition…
                </div>
                <div style={{ fontSize: '0.78rem', color: C.txtS, lineHeight: 2 }}>
                  <div>① Researching correct command &amp; sample output</div>
                  <div>② Drafting tool structure &amp; parameters</div>
                  <div>③ Generating extraction patterns</div>
                </div>
              </div>
            </div>
          )}

          {/* ── Step 1: Describe ─────────────────────────────────── */}
          {!generating && step === 'describe' && (
            <div>
              <p style={{ fontSize: '0.81rem', color: C.txtS, margin: '0 0 1.1rem' }}>
                Describe a new tool in plain English. The platform runs three LLM calls:
                research the correct command, draft the full catalog entry, then generate
                extraction patterns — all in one click.
              </p>
              <label style={{ display: 'block', fontSize: '0.77rem', fontWeight: 600, color: C.txtM, marginBottom: '0.4rem' }}>
                What should this tool do?
              </label>
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleGenerate() }}
                placeholder={
                  'e.g. "List active TCP connections and return the remote IP, port and process name — works via SSH and inside Docker containers."'
                }
                rows={5}
                style={{ ...inputStyle, resize: 'vertical' }}
              />
              <p style={{ fontSize: '0.69rem', color: C.txtS, margin: '4px 0 0' }}>
                Mention adapters (docker, ssh, kubernetes) and any output fields that matter. Cmd+Enter to generate.
              </p>
              {genError && (
                <div style={{ marginTop: '0.85rem', padding: '0.55rem 0.8rem', borderRadius: 7,
                  backgroundColor: 'rgba(160,72,72,0.12)', border: `1px solid rgba(160,72,72,0.35)`,
                  color: '#f87171', fontSize: '0.8rem' }}>
                  {genError}
                </div>
              )}
              <div style={{ marginTop: '1.1rem', display: 'flex', justifyContent: 'flex-end' }}>
                <button
                  onClick={handleGenerate}
                  disabled={generating || !description.trim()}
                  className="btn"
                  style={{
                    backgroundColor: C.violet,
                    color: '#fff',
                    border: 'none',
                    opacity: generating || !description.trim() ? 0.5 : 1,
                    cursor: generating || !description.trim() ? 'not-allowed' : 'pointer',
                    padding: '8px 20px',
                    fontSize: '0.84rem',
                    fontWeight: 600,
                  }}
                >
                  {generating ? '✦ Researching & generating…' : '✦ Generate Tool Definition'}
                </button>
              </div>
            </div>
          )}

          {/* ── Step 2: Review ──────────────────────────────────────────────── */}
          {!generating && step === 'review' && (
            <div>
              {/* JSON editor */}
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.45rem' }}>
                <label style={{ fontSize: '0.77rem', fontWeight: 600, color: C.txtM }}>
                  Generated Tool Definition
                </label>
                <span style={{ fontSize: '0.7rem', color: C.txtS }}>
                  Editable — changes are saved when you register.
                </span>
              </div>
              <textarea
                value={draftJson}
                onChange={e => { setDraftJson(e.target.value); setJsonError(null) }}
                rows={18}
                style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.77rem', resize: 'vertical' }}
              />
              {jsonError && (
                <div style={{ marginTop: '0.4rem', padding: '0.45rem 0.75rem', borderRadius: 7,
                  backgroundColor: 'rgba(160,72,72,0.12)', border: `1px solid rgba(160,72,72,0.35)`,
                  color: '#f87171', fontSize: '0.79rem' }}>
                  {jsonError}
                </div>
              )}

              {/* ── Refine section ─────────────────────────────────────────── */}
              <div style={{ marginTop: '1.1rem', border: `1px solid ${C.border}`, borderRadius: 8, overflow: 'hidden' }}>
                <button
                  onClick={() => setRefineOpen(v => !v)}
                  style={{
                    width: '100%',
                    padding: '0.65rem 1rem',
                    background: C.raised,
                    border: 'none',
                    cursor: 'pointer',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                  }}
                >
                  <span style={{ fontSize: '0.79rem', fontWeight: 600, color: C.txtM, display: 'flex', alignItems: 'center', gap: 6 }}>
                    Refine with Real Output
                    {sampleOutput && (
                      <span style={{
                        fontSize: '0.65rem', fontWeight: 600, padding: '1px 7px', borderRadius: 10,
                        backgroundColor: 'rgba(168,85,247,0.15)', color: C.violet,
                        border: '1px solid rgba(168,85,247,0.3)',
                      }}>pre-filled</span>
                    )}
                  </span>
                  <span style={{ color: C.txtS, fontSize: '0.7rem',
                    transform: refineOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>▼</span>
                </button>

                {refineOpen && (
                  <div style={{ padding: '0.85rem 1rem', backgroundColor: C.bg }}>
                    {refineHint ? (
                      <p style={{ fontSize: '0.72rem', color: C.violet, margin: '0 0 0.6rem',
                        padding: '0.4rem 0.65rem', borderRadius: 6,
                        backgroundColor: 'rgba(168,85,247,0.08)', border: '1px solid rgba(168,85,247,0.2)' }}>
                        {refineHint}
                      </p>
                    ) : (
                      <p style={{ fontSize: '0.72rem', color: C.txtS, margin: '0 0 0.6rem' }}>
                        Paste actual stdout from running one of the commands above. Blank-pattern output fields
                        will be updated; fields already extracted will be kept.
                      </p>
                    )}
                    <textarea
                      value={sampleOutput}
                      onChange={e => setSampleOutput(e.target.value)}
                      placeholder="Paste command stdout here…"
                      rows={6}
                      style={{ ...inputStyle, fontFamily: 'monospace', fontSize: '0.75rem', resize: 'vertical' }}
                    />
                    {parseNotes && (
                      <p style={{ fontSize: '0.7rem', color: C.txtS, margin: '4px 0 0' }}>
                        Strategy: {parseNotes}
                      </p>
                    )}
                    {parseError && (
                      <div style={{ marginTop: '0.45rem', padding: '0.45rem 0.75rem', borderRadius: 7,
                        backgroundColor: 'rgba(160,72,72,0.12)', border: `1px solid rgba(160,72,72,0.35)`,
                        color: '#f87171', fontSize: '0.79rem' }}>
                        {parseError}
                      </div>
                    )}
                    <div style={{ marginTop: '0.6rem' }}>
                      <button
                        onClick={handleRefine}
                        disabled={parsing || !sampleOutput.trim()}
                        className="btn"
                        style={{
                          backgroundColor: C.raised,
                          color: C.txtP,
                          border: `1px solid ${C.border}`,
                          opacity: parsing || !sampleOutput.trim() ? 0.5 : 1,
                          cursor: parsing || !sampleOutput.trim() ? 'not-allowed' : 'pointer',
                          padding: '6px 14px',
                          fontSize: '0.8rem',
                          fontWeight: 500,
                        }}
                      >
                        {parsing ? 'Refining…' : 'Apply to Blank Fields'}
                      </button>
                    </div>
                  </div>
                )}
              </div>

              {/* Register / back */}
              <div style={{ marginTop: '1.1rem', display: 'flex', gap: 10, justifyContent: 'space-between', alignItems: 'center' }}>
                <button
                  onClick={() => setStep('describe')}
                  className="btn"
                  style={{
                    backgroundColor: 'transparent',
                    color: C.txtS,
                    border: `1px solid ${C.border}`,
                    padding: '7px 14px',
                    fontSize: '0.8rem',
                    fontWeight: 500,
                    cursor: 'pointer',
                  }}
                >
                  ← Back
                </button>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  {regError && (
                    <span style={{ fontSize: '0.78rem', color: '#f87171' }}>{regError}</span>
                  )}
                  <button
                    onClick={handleRegister}
                    disabled={registering}
                    className="btn"
                    style={{
                      backgroundColor: C.green,
                      color: '#fff',
                      border: 'none',
                      opacity: registering ? 0.5 : 1,
                      cursor: registering ? 'not-allowed' : 'pointer',
                      padding: '8px 22px',
                      fontSize: '0.84rem',
                      fontWeight: 600,
                    }}
                  >
                    {registering ? 'Registering…' : 'Register Tool'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ── Step 3: Done ─────────────────────────────────────────────────── */}
          {!generating && step === 'done' && (
            <div style={{ textAlign: 'center', padding: '2.5rem 1rem' }}>
              <div style={{
                width: 52, height: 52, borderRadius: '50%',
                backgroundColor: 'rgba(58,122,90,0.15)',
                border: `2px solid ${C.green}`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                margin: '0 auto 1rem',
                fontSize: '1.4rem', color: C.green,
              }}>✓</div>
              <div style={{ fontSize: '1.05rem', fontWeight: 700, color: C.green, marginBottom: '0.4rem' }}>
                Tool Registered
              </div>
              <div style={{ fontSize: '0.84rem', color: C.txtM, marginBottom: '1.75rem' }}>
                <strong style={{ color: C.txtP }}>{registeredName}</strong> is now available
                in the runbook editor's tool picker.
              </div>
              <div style={{ display: 'flex', gap: 10, justifyContent: 'center' }}>
                <button
                  onClick={handleReset}
                  className="btn"
                  style={{
                    backgroundColor: C.raised,
                    color: C.txtP,
                    border: `1px solid ${C.border}`,
                    padding: '7px 16px',
                    fontSize: '0.82rem',
                    fontWeight: 500,
                    cursor: 'pointer',
                  }}
                >
                  Build Another Tool
                </button>
                <button
                  onClick={onClose}
                  className="btn"
                  style={{
                    backgroundColor: C.violet,
                    color: '#fff',
                    border: 'none',
                    padding: '7px 20px',
                    fontSize: '0.82rem',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  Done
                </button>
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
    </>,
    document.body
  )
}
