/**
 * ChatPanel — floating AI Ops Assistant  (Phase 3)
 *
 * Phase 3 additions:
 *   A. contextWorkflowId prop — auto-injects the currently open incident
 *   B. Pending actions — confirm/cancel buttons for approve/reject intent
 *   C. Runbook RAG results are handled transparently (backend injects context)
 */

import { useState, useRef, useEffect, useCallback } from 'react'
import { streamChat, decideApprovalByWorkflow } from '../services/api'
import type { ChatMessage, PendingAction } from '../services/api'

// ── Icons ────────────────────────────────────────────────────────────────────

const IconChat = ({ size = 20 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
)
const IconX = ({ size = 15 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
  </svg>
)
const IconSend = ({ size = 14 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <line x1="22" y1="2" x2="11" y2="13" />
    <polygon points="22 2 15 22 11 13 2 9 22 2" />
  </svg>
)
const IconTrash = ({ size = 13 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
  </svg>
)
const IconCheck = ({ size = 13 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
)

// ── Static content ────────────────────────────────────────────────────────────

const SUGGESTIONS = [
  'What critical incidents are active right now?',
  'Which incidents are waiting for approval?',
  "What's our average MTTR this week?",
  'Show me the highest risk incidents',
]

const WELCOME = "Hi! I'm your AI Ops Assistant. I have live access to current incidents, approvals, MTTR metrics, risk scores, and runbooks. What do you need?"

// ── Sub-components ───────────────────────────────────────────────────────────

const TypingDots = () => (
  <div style={{ display: 'flex', gap: '5px', alignItems: 'center', padding: '2px 0' }}>
    {[0, 1, 2].map(i => (
      <span key={i} style={{
        width: '7px', height: '7px', borderRadius: '50%',
        backgroundColor: '#6366f1', display: 'inline-block',
        animation: 'chatPulse 1.3s ease-in-out infinite',
        animationDelay: `${i * 0.22}s`,
      }} />
    ))}
  </div>
)

const Bubble = ({ msg }: { msg: { role: string; content: string } }) => {
  const isUser = msg.role === 'user'
  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      <div style={{
        maxWidth: '87%',
        padding: '8px 12px',
        borderRadius: isUser ? '12px 12px 2px 12px' : '12px 12px 12px 2px',
        backgroundColor: isUser ? 'rgba(99,102,241,0.22)' : 'rgba(255,255,255,0.04)',
        border: `1px solid ${isUser ? 'rgba(99,102,241,0.38)' : '#3d4557'}`,
        color: '#e8eef5',
        fontSize: '13px',
        lineHeight: '1.55',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}>
        {msg.content}
      </div>
    </div>
  )
}

// ── Action confirmation bar (Phase 3B) ───────────────────────────────────────

interface ActionBarProps {
  action: PendingAction
  onConfirm: () => void
  onCancel: () => void
  busy: boolean
}

const ActionBar = ({ action, onConfirm, onCancel, busy }: ActionBarProps) => {
  const isApprove = action.type === 'approve'
  const verb = isApprove ? 'Approve' : 'Reject'

  // Palette colours — no fills, no neon.
  // Approve → app indigo  (#818cf8 text / rgba(99,102,241,…) border)
  // Reject  → muted rose  (#f4a0a0 text / rgba(220,100,100,…) border)
  const textColor   = isApprove ? '#818cf8'                  : '#f4a0a0'
  const borderColor = isApprove ? 'rgba(99,102,241,0.45)'    : 'rgba(220,100,100,0.45)'
  const borderFaint = isApprove ? 'rgba(99,102,241,0.22)'    : 'rgba(220,100,100,0.22)'

  return (
    <div style={{
      margin: '4px 0 2px 0',
      padding: '10px 12px',
      borderRadius: '10px',
      background: 'transparent',
      border: `1px solid ${borderFaint}`,
      display: 'flex', flexDirection: 'column', gap: '8px',
    }}>
      {/* Label row */}
      <p style={{ margin: 0, fontSize: '12px', color: '#a0aec0' }}>
        <span style={{ color: textColor, fontWeight: 600 }}>{verb}</span>
        {' '}<span style={{ color: '#e8eef5' }}>{action.incident_number}</span>
        {action.notes ? <span style={{ color: '#7a8ba3' }}>{` — "${action.notes}"`}</span> : ''}
        <span style={{ color: '#a0aec0' }}>?</span>
      </p>

      {/* Button row */}
      <div style={{ display: 'flex', gap: '8px' }}>
        {/* Confirm — outlined, coloured text + border, no fill */}
        <button
          onClick={onConfirm}
          disabled={busy}
          style={{
            flex: 1,
            padding: '6px 10px',
            borderRadius: '6px',
            fontSize: '12px',
            fontWeight: 600,
            cursor: busy ? 'not-allowed' : 'pointer',
            background: 'transparent',
            border: `1px solid ${busy ? '#3d4557' : borderColor}`,
            color: busy ? '#4b5563' : textColor,
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '5px',
            transition: 'opacity 0.15s, border-color 0.15s',
            opacity: busy ? 0.55 : 1,
          }}>
          <IconCheck size={12} />
          {busy ? 'Processing…' : `Confirm ${verb}`}
        </button>

        {/* Cancel — neutral outlined */}
        <button
          onClick={onCancel}
          disabled={busy}
          style={{
            padding: '6px 14px',
            borderRadius: '6px',
            fontSize: '12px',
            fontWeight: 500,
            cursor: busy ? 'not-allowed' : 'pointer',
            background: 'transparent',
            border: '1px solid #3d4557',
            color: '#7a8ba3',
            transition: 'border-color 0.15s, color 0.15s',
            opacity: busy ? 0.55 : 1,
          }}>
          Cancel
        </button>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Message {
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  contextWorkflowId?: string | null   // Phase 3A: workflow currently open in details view
}

export default function ChatPanel({ contextWorkflowId }: Props) {
  const [open, setOpen]               = useState(false)
  const [messages, setMessages]       = useState<Message[]>([])
  const [input, setInput]             = useState('')
  const [loading, setLoading]         = useState(false)
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null)
  const [actionBusy, setActionBusy]   = useState(false)
  const bottomRef                     = useRef<HTMLDivElement>(null)
  const inputRef                      = useRef<HTMLTextAreaElement>(null)

  // Context badge: show when an incident is open in the UI
  const hasContext = Boolean(contextWorkflowId)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading, pendingAction])

  useEffect(() => {
    if (open) {
      if (messages.length === 0) {
        setMessages([{ role: 'assistant', content: WELCOME }])
      }
      setTimeout(() => inputRef.current?.focus(), 80)
    }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // Clear pending action when the operator navigates away from an incident
  useEffect(() => {
    if (!contextWorkflowId) setPendingAction(null)
  }, [contextWorkflowId])

  // ── Send message ──────────────────────────────────────────────────────────

  const send = useCallback(async (text: string) => {
    const msg = text.trim()
    if (!msg || loading) return

    // Clear any previous pending action when sending a new message
    setPendingAction(null)

    const userMsg: Message  = { role: 'user', content: msg }
    const withUser          = [...messages, userMsg]
    setMessages(withUser)
    setInput('')
    setLoading(true)
    // Re-focus immediately so the cursor stays in the box after pressing Enter
    inputRef.current?.focus()

    // Placeholder bubble — filled in as chunks arrive
    setMessages(prev => [...prev, { role: 'assistant', content: '' }])

    try {
      const history: ChatMessage[] = withUser.slice(0, -1).map(m => ({
        role: m.role, content: m.content,
      }))

      let receivedAny = false
      for await (const chunk of streamChat(msg, history, {
        contextWorkflowId,
        onAction: (action) => setPendingAction(action),
      })) {
        receivedAny = true
        setMessages(prev => {
          const last = prev[prev.length - 1]
          return [...prev.slice(0, -1), { ...last, content: last.content + chunk }]
        })
      }

      if (!receivedAny) {
        setMessages(prev => {
          const last = prev[prev.length - 1]
          return last.content === ''
            ? [...prev.slice(0, -1), { role: 'assistant', content: "I couldn't generate a response. Please try again." }]
            : prev
        })
      }
    } catch {
      setMessages(prev => {
        const last = prev[prev.length - 1]
        return last.content === ''
          ? [...prev.slice(0, -1), { role: 'assistant', content: "Sorry, I couldn't reach the backend. Check your connection and try again." }]
          : prev
      })
    } finally {
      setLoading(false)
      // Restore focus after response so the user can type the next message immediately
      inputRef.current?.focus()
    }
  }, [messages, loading, contextWorkflowId])

  // ── Action handlers (Phase 3B) ────────────────────────────────────────────

  const handleConfirmAction = useCallback(async () => {
    if (!pendingAction || actionBusy) return
    const { type, incident_number, workflow_id, notes } = pendingAction
    setActionBusy(true)

    try {
      await decideApprovalByWorkflow(
        workflow_id,
        type === 'approve' ? 'approved' : 'rejected',
        notes || '',
        'operator_chat',
      )
      const verb = type === 'approve' ? 'approved' : 'rejected'
      const effect = type === 'approve'
        ? 'Remediation has been queued and will begin shortly.'
        : 'Marked as rejected. No automated remediation will run.'
      setPendingAction(null)
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `✓ ${incident_number} ${verb}. ${effect}`,
      }])
    } catch {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `Failed to ${type} ${incident_number}. Please try again from the Approvals queue.`,
      }])
    } finally {
      setActionBusy(false)
    }
  }, [pendingAction, actionBusy])

  const handleCancelAction = useCallback(() => {
    setPendingAction(null)
    setMessages(prev => [...prev, {
      role: 'assistant',
      content: 'Action cancelled.',
    }])
  }, [])

  // ── Input helpers ─────────────────────────────────────────────────────────

  const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(input) }
  }

  const clearChat = () => {
    setMessages([{ role: 'assistant', content: WELCOME }])
    setInput('')
    setPendingAction(null)
  }

  const canSend = input.trim().length > 0 && !loading && !actionBusy

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <>
      <style>{`
        @keyframes chatPulse {
          0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
          40%            { transform: scale(1);   opacity: 1;   }
        }
      `}</style>

      <div style={{ position: 'fixed', bottom: '24px', right: '24px', zIndex: 9999 }}>

        {/* ── Expanded panel ──────────────────────────────────────────────── */}
        {open && (
          <div style={{
            width: '390px',
            height: '540px',
            display: 'flex',
            flexDirection: 'column',
            backgroundColor: '#1a1f2e',
            border: '1px solid #3d4557',
            borderRadius: '12px',
            boxShadow: '0 24px 64px rgba(0,0,0,0.55)',
            overflow: 'hidden',
            marginBottom: '12px',
          }}>

            {/* Header */}
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '11px 14px',
              borderBottom: '1px solid #3d4557',
              background: 'linear-gradient(to right, rgba(99,102,241,0.14), rgba(129,140,248,0.07))',
              flexShrink: 0,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '9px' }}>
                <div style={{
                  width: '30px', height: '30px', borderRadius: '50%',
                  border: '2px solid #6366f1', color: '#818cf8',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  flexShrink: 0,
                }}>
                  <IconChat size={14} />
                </div>
                <div>
                  <p style={{ color: '#e8eef5', fontSize: '13px', fontWeight: 600, margin: 0 }}>
                    AI Ops Assistant
                  </p>
                  {/* Phase 3A: context badge */}
                  {hasContext ? (
                    <p style={{ margin: 0, fontSize: '11px' }}>
                      <span style={{
                        backgroundColor: 'rgba(99,102,241,0.2)',
                        border: '1px solid rgba(99,102,241,0.4)',
                        color: '#818cf8', borderRadius: '4px',
                        padding: '1px 6px', fontSize: '10px', fontWeight: 600,
                      }}>
                        ● incident context active
                      </span>
                    </p>
                  ) : (
                    <p style={{ color: '#7a8ba3', fontSize: '11px', margin: 0 }}>
                      Live platform data
                    </p>
                  )}
                </div>
              </div>

              <div style={{ display: 'flex', gap: '4px' }}>
                {messages.length > 1 && (
                  <button onClick={clearChat} title="Clear chat"
                    style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#7a8ba3', padding: '4px 6px', borderRadius: '6px' }}>
                    <IconTrash size={13} />
                  </button>
                )}
                <button onClick={() => setOpen(false)} title="Close"
                  style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#7a8ba3', padding: '4px 6px', borderRadius: '6px' }}>
                  <IconX size={15} />
                </button>
              </div>
            </div>

            {/* Message thread */}
            <div style={{
              flex: 1, overflowY: 'auto', padding: '12px',
              display: 'flex', flexDirection: 'column', gap: '10px',
            }}>
              {messages.map((m, i) => <Bubble key={i} msg={m} />)}

              {/* Phase 3B: action confirmation bar */}
              {pendingAction && !loading && (
                <ActionBar
                  action={pendingAction}
                  onConfirm={handleConfirmAction}
                  onCancel={handleCancelAction}
                  busy={actionBusy}
                />
              )}

              {/* Typing indicator */}
              {loading && messages[messages.length - 1]?.role === 'assistant'
                       && messages[messages.length - 1]?.content === '' && (
                <div style={{ display: 'flex', justifyContent: 'flex-start', marginTop: '-4px' }}>
                  <div style={{
                    padding: '10px 14px', borderRadius: '12px 12px 12px 2px',
                    backgroundColor: 'rgba(255,255,255,0.04)', border: '1px solid #3d4557',
                  }}>
                    <TypingDots />
                  </div>
                </div>
              )}

              {/* Suggestion chips */}
              {messages.length === 1 && !loading && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '2px' }}>
                  <p style={{ color: '#7a8ba3', fontSize: '11px', margin: 0 }}>Try asking:</p>
                  {SUGGESTIONS.map(s => (
                    <button key={s} onClick={() => send(s)}
                      style={{
                        textAlign: 'left', background: 'none', cursor: 'pointer',
                        border: '1px solid #3d4557', borderRadius: '8px',
                        padding: '6px 10px', color: '#a0aec0', fontSize: '12px',
                        transition: 'border-color 0.15s, color 0.15s',
                      }}
                      onMouseEnter={e => { (e.currentTarget).style.borderColor = 'rgba(99,102,241,0.5)'; (e.currentTarget).style.color = '#818cf8' }}
                      onMouseLeave={e => { (e.currentTarget).style.borderColor = '#3d4557'; (e.currentTarget).style.color = '#a0aec0' }}>
                      {s}
                    </button>
                  ))}
                </div>
              )}

              <div ref={bottomRef} />
            </div>

            {/* Input */}
            <div style={{
              padding: '10px 12px', borderTop: '1px solid #3d4557',
              display: 'flex', gap: '8px', alignItems: 'flex-end', flexShrink: 0,
            }}>
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={handleKey}
                placeholder="Ask about incidents, approvals, runbooks…"
                rows={1}
                disabled={loading || actionBusy}
                style={{
                  flex: 1, resize: 'none',
                  background: 'rgba(255,255,255,0.05)',
                  border: '1px solid #3d4557', borderRadius: '8px',
                  color: '#e8eef5', fontSize: '13px',
                  padding: '8px 10px', outline: 'none',
                  fontFamily: 'inherit', lineHeight: '1.4',
                  maxHeight: '96px', overflowY: 'auto',
                }}
              />
              <button
                onClick={() => send(input)}
                disabled={!canSend}
                style={{
                  width: '34px', height: '34px', borderRadius: '8px', flexShrink: 0,
                  background: canSend ? '#6366f1' : '#2d3748',
                  border: 'none', cursor: canSend ? 'pointer' : 'not-allowed',
                  color: canSend ? '#fff' : '#4b5563',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  transition: 'background 0.15s',
                }}>
                <IconSend size={14} />
              </button>
            </div>

          </div>
        )}

        {/* ── Toggle button ─────────────────────────────────────────────── */}
        <button
          onClick={() => setOpen(o => !o)}
          title={open ? 'Close assistant' : 'AI Ops Assistant'}
          style={{
            width: '48px', height: '48px', borderRadius: '50%',
            background: open
              ? 'linear-gradient(135deg, #4f46e5, #6366f1)'
              : 'linear-gradient(135deg, #6366f1, #818cf8)',
            border: 'none', cursor: 'pointer', color: '#fff',
            boxShadow: '0 4px 20px rgba(99,102,241,0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            transition: 'transform 0.15s, box-shadow 0.15s',
            marginLeft: 'auto',
            position: 'relative',
          }}
          onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1.08)' }}
          onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.transform = 'scale(1)' }}>
          <IconChat size={22} />
          {/* Phase 3A: dot when context is active and panel is closed */}
          {!open && hasContext && (
            <span style={{
              position: 'absolute', top: '2px', right: '2px',
              width: '10px', height: '10px', borderRadius: '50%',
              backgroundColor: '#6366f1',
              border: '2px solid #0f1419',
            }} />
          )}
        </button>

      </div>
    </>
  )
}
