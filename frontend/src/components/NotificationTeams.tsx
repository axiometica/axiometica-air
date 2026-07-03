import { useState, useEffect } from 'react'
import {
  listNotificationTeams, createNotificationTeam, updateNotificationTeam, deleteNotificationTeam,
  NotificationTeam, NotificationTeamPayload,
} from '../services/api'
import { IconAlertTriangle } from './icons'

interface Props {
  isExpanded?: boolean
  onToggle?: () => void
}

const INPUT_STYLE: React.CSSProperties = {
  backgroundColor: '#2d3748',
  color: '#e8eef5',
  border: '1px solid #3d4557',
  borderRadius: '8px',
  padding: '0.5rem 0.75rem',
  fontSize: '0.85rem',
  width: '100%',
  outline: 'none',
  boxSizing: 'border-box',
}

const LABEL_STYLE: React.CSSProperties = {
  color: '#a0aec0',
  fontSize: '0.72rem',
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  display: 'block',
  marginBottom: '0.4rem',
}

const EMPTY_FORM: NotificationTeamPayload = {
  name: '', pagerduty_routing_key: '', slack_channel: '', email_recipients: '',
  webhook_url: '', webhook_secret: '', enabled: true,
}

function channelBadges(t: NotificationTeam): string[] {
  const badges: string[] = []
  if (t.pagerduty_routing_key_set) badges.push('PagerDuty')
  if (t.slack_channel) badges.push('Slack')
  if (t.email_recipients) badges.push('Email')
  if (t.webhook_url) badges.push('Webhook')
  return badges
}

export default function NotificationTeams({ isExpanded = true, onToggle = () => {} }: Props) {
  const [internalExpanded, setInternalExpanded] = useState(isExpanded)
  useEffect(() => { if (isExpanded !== undefined) setInternalExpanded(isExpanded) }, [isExpanded])

  const [teams, setTeams]     = useState<NotificationTeam[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)

  const [editingId, setEditingId]   = useState<string | null>(null)   // null = not editing, 'new' = creating
  const [form, setForm]             = useState<NotificationTeamPayload>(EMPTY_FORM)
  const [clearPd, setClearPd]       = useState(false)
  const [clearWebhook, setClearWebhook] = useState(false)
  const [saving, setSaving]         = useState(false)
  const [saveMsg, setSaveMsg]       = useState('')
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)

  const handleToggle = () => { setInternalExpanded(!internalExpanded); onToggle() }

  useEffect(() => { if (internalExpanded) load() }, [internalExpanded])

  const load = async () => {
    setLoading(true)
    try {
      const { data } = await listNotificationTeams()
      setTeams(data)
      setError(null)
    } catch {
      setError('Failed to load notification teams')
    } finally {
      setLoading(false)
    }
  }

  const startCreate = () => {
    setForm(EMPTY_FORM)
    setClearPd(false)
    setClearWebhook(false)
    setSaveMsg('')
    setEditingId('new')
  }

  const startEdit = (t: NotificationTeam) => {
    setForm({
      name: t.name,
      pagerduty_routing_key: '',
      slack_channel: t.slack_channel || '',
      email_recipients: t.email_recipients || '',
      webhook_url: t.webhook_url || '',
      webhook_secret: '',
      enabled: t.enabled,
    })
    setClearPd(false)
    setClearWebhook(false)
    setSaveMsg('')
    setEditingId(t.team_id)
  }

  const cancelEdit = () => { setEditingId(null); setSaveMsg('') }

  const handleSave = async () => {
    if (!form.name?.trim()) { setSaveMsg('✗ Team name is required'); return }
    setSaving(true)
    setSaveMsg('')
    try {
      const payload: NotificationTeamPayload = {
        ...form,
        pagerduty_routing_key: clearPd ? '-' : (form.pagerduty_routing_key || ''),
        webhook_secret: clearWebhook ? '-' : (form.webhook_secret || ''),
      }
      if (editingId === 'new') {
        await createNotificationTeam(payload)
      } else if (editingId) {
        await updateNotificationTeam(editingId, payload)
      }
      setEditingId(null)
      await load()
    } catch (e: any) {
      setSaveMsg(`✗ ${e?.response?.data?.detail || 'Save failed'}`)
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    if (confirmDeleteId !== id) { setConfirmDeleteId(id); return }
    try {
      await deleteNotificationTeam(id)
      setTeams(prev => prev.filter(t => t.team_id !== id))
    } catch {
      setError('Failed to delete team')
    } finally {
      setConfirmDeleteId(null)
    }
  }

  return (
    <>
      {/* Header */}
      <button
        onClick={handleToggle}
        style={{ width: '100%', padding: '0.8rem 1.25rem', display: 'flex', alignItems: 'center',
          justifyContent: 'space-between', background: 'none', border: 'none', cursor: 'pointer', color: '#e8eef5' }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 600, fontSize: '0.9rem', letterSpacing: '0.01em' }}>
          Notification Teams
          {teams.length > 0 && (
            <span style={{ fontSize: '0.7rem', fontWeight: 600, padding: '1px 7px', borderRadius: 4, color: '#93c5fd', backgroundColor: '#1e3a5f' }}>
              {teams.length}
            </span>
          )}
        </span>
        <span style={{ color: '#7a8ba3', fontSize: '0.7rem', display: 'inline-block',
          transform: internalExpanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.18s ease' }}>▼</span>
      </button>

      {/* Content */}
      {internalExpanded && (
        <div style={{ padding: '0 1.25rem 1.25rem', borderTop: '1px solid #3d4557' }}>
          <p style={{ fontSize: '0.8rem', color: '#a0aec0', margin: '1rem 0' }}>
            Named teams the <code style={{ color: '#93c5fd' }}>notify</code> / <code style={{ color: '#93c5fd' }}>alert_escalate</code> /{' '}
            <code style={{ color: '#93c5fd' }}>alert_update</code> / <code style={{ color: '#93c5fd' }}>send_alert</code> runbook actions can
            route to by name (via a <code style={{ color: '#93c5fd' }}>team</code> argument). Each team can configure its own PagerDuty
            routing key, Slack channel, email recipients, and/or webhook — any combination. When no team is given, or the named team
            isn't found or is disabled, the action falls back to the default PagerDuty / Slack / SMTP connectors configured elsewhere.
          </p>

          {error && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#fca5a5', fontSize: '0.82rem', marginBottom: '0.75rem' }}>
              <IconAlertTriangle size={14} />{error}
            </div>
          )}

          {loading ? (
            <div style={{ color: '#a0aec0', fontSize: '0.85rem' }}>Loading…</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {teams.map(t => (
                <div key={t.team_id} style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  padding: '0.6rem 0.85rem', borderRadius: 8, background: '#1a1f2e', border: '1px solid #2d3748',
                }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                      <span style={{ fontWeight: 600, fontSize: '0.85rem', color: t.enabled ? '#e8eef5' : '#6b7a93' }}>
                        {t.name}
                      </span>
                      {!t.enabled && (
                        <span style={{ fontSize: '0.65rem', color: '#f59e0b', border: '1px solid #f59e0b40', borderRadius: 4, padding: '0 5px' }}>
                          disabled
                        </span>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.3rem' }}>
                      {channelBadges(t).length === 0 ? (
                        <span style={{ fontSize: '0.72rem', color: '#6b7a93' }}>No channels configured</span>
                      ) : channelBadges(t).map(b => (
                        <span key={b} style={{ fontSize: '0.7rem', color: '#93c5fd', border: '1px solid #93c5fd40', borderRadius: 4, padding: '0 6px' }}>
                          {b}
                        </span>
                      ))}
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
                    <button onClick={() => startEdit(t)} style={{ fontSize: '0.78rem', color: '#93c5fd', background: 'none', border: 'none', cursor: 'pointer' }}>
                      Edit
                    </button>
                    <button
                      onClick={() => handleDelete(t.team_id)}
                      style={{ fontSize: '0.78rem', color: confirmDeleteId === t.team_id ? '#ef4444' : '#6b7a93', background: 'none', border: 'none', cursor: 'pointer' }}
                    >
                      {confirmDeleteId === t.team_id ? 'Confirm delete?' : 'Delete'}
                    </button>
                  </div>
                </div>
              ))}

              {teams.length === 0 && !error && (
                <p style={{ fontSize: '0.8rem', color: '#6b7a93', margin: '0.5rem 0' }}>
                  No notification teams yet — actions use the default channels.
                </p>
              )}
            </div>
          )}

          {editingId === null && (
            <button
              onClick={startCreate}
              style={{
                marginTop: '0.85rem', fontSize: '0.8rem', color: '#93c5fd', background: 'none',
                border: '1px dashed #3d4557', borderRadius: 8, padding: '0.4rem 1rem', cursor: 'pointer',
              }}
            >
              + Add Team
            </button>
          )}

          {editingId !== null && (
            <div style={{ marginTop: '1rem', padding: '1rem', borderRadius: 8, background: '#1a1f2e', border: '1px solid #2d3748' }}>
              <h4 style={{ fontSize: '0.82rem', fontWeight: 600, color: '#e8eef5', margin: '0 0 0.85rem' }}>
                {editingId === 'new' ? 'New Notification Team' : `Edit "${form.name}"`}
              </h4>

              <div style={{ marginBottom: '0.85rem' }}>
                <label style={LABEL_STYLE}>Team Name</label>
                <input
                  style={INPUT_STYLE}
                  value={form.name}
                  onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                  placeholder="e.g. Network On-Call"
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.85rem', marginBottom: '0.85rem' }}>
                <div>
                  <label style={LABEL_STYLE}>PagerDuty Routing Key</label>
                  {clearPd ? (
                    <p style={{ fontSize: '0.75rem', color: '#f59e0b', margin: 0 }}>
                      Will be removed on save.{' '}
                      <button onClick={() => setClearPd(false)} style={{ color: '#93c5fd', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.75rem' }}>Cancel</button>
                    </p>
                  ) : (
                    <input
                      style={INPUT_STYLE}
                      type="password"
                      autoComplete="off"
                      value={form.pagerduty_routing_key}
                      onChange={e => setForm(f => ({ ...f, pagerduty_routing_key: e.target.value }))}
                      placeholder={editingId !== 'new' ? 'Enter a new key to replace it' : 'PagerDuty Events API v2 key'}
                    />
                  )}
                  {editingId !== 'new' && !clearPd && (
                    <button onClick={() => setClearPd(true)} style={{ fontSize: '0.7rem', color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', marginTop: '0.25rem', padding: 0 }}>
                      Clear
                    </button>
                  )}
                </div>

                <div>
                  <label style={LABEL_STYLE}>Slack Channel</label>
                  <input
                    style={INPUT_STYLE}
                    value={form.slack_channel}
                    onChange={e => setForm(f => ({ ...f, slack_channel: e.target.value }))}
                    placeholder="#network-oncall"
                  />
                </div>
              </div>

              <div style={{ marginBottom: '0.85rem' }}>
                <label style={LABEL_STYLE}>Email Recipients</label>
                <input
                  style={INPUT_STYLE}
                  value={form.email_recipients}
                  onChange={e => setForm(f => ({ ...f, email_recipients: e.target.value }))}
                  placeholder="oncall@example.com, lead@example.com"
                />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.85rem', marginBottom: '0.85rem' }}>
                <div>
                  <label style={LABEL_STYLE}>Webhook URL</label>
                  <input
                    style={INPUT_STYLE}
                    value={form.webhook_url}
                    onChange={e => setForm(f => ({ ...f, webhook_url: e.target.value }))}
                    placeholder="https://example.com/hooks/notify"
                  />
                </div>
                <div>
                  <label style={LABEL_STYLE}>Webhook Secret</label>
                  {clearWebhook ? (
                    <p style={{ fontSize: '0.75rem', color: '#f59e0b', margin: 0 }}>
                      Will be removed on save.{' '}
                      <button onClick={() => setClearWebhook(false)} style={{ color: '#93c5fd', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.75rem' }}>Cancel</button>
                    </p>
                  ) : (
                    <input
                      style={INPUT_STYLE}
                      type="password"
                      autoComplete="off"
                      value={form.webhook_secret}
                      onChange={e => setForm(f => ({ ...f, webhook_secret: e.target.value }))}
                      placeholder={editingId !== 'new' ? 'Enter a new secret to replace it' : 'Optional shared secret'}
                    />
                  )}
                  {editingId !== 'new' && !clearWebhook && (
                    <button onClick={() => setClearWebhook(true)} style={{ fontSize: '0.7rem', color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', marginTop: '0.25rem', padding: 0 }}>
                      Clear
                    </button>
                  )}
                </div>
              </div>

              <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.82rem', color: '#a0aec0', marginBottom: '0.85rem', cursor: 'pointer' }}>
                <input type="checkbox" checked={!!form.enabled} onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))} />
                Enabled
              </label>

              {saveMsg && (
                <p style={{ fontSize: '0.8rem', color: saveMsg.startsWith('✗') ? '#fca5a5' : '#6ee7b7', margin: '0 0 0.6rem' }}>
                  {saveMsg}
                </p>
              )}

              <div style={{ display: 'flex', gap: '0.6rem' }}>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  style={{
                    fontSize: '0.82rem', fontWeight: 600, color: '#0f1419', background: '#93c5fd',
                    border: 'none', borderRadius: 8, padding: '0.45rem 1.1rem', cursor: saving ? 'default' : 'pointer',
                    opacity: saving ? 0.6 : 1,
                  }}
                >
                  {saving ? 'Saving…' : 'Save Team'}
                </button>
                <button
                  onClick={cancelEdit}
                  style={{ fontSize: '0.82rem', color: '#a0aec0', background: 'none', border: '1px solid #3d4557', borderRadius: 8, padding: '0.45rem 1.1rem', cursor: 'pointer' }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </>
  )
}
