import { useState, useEffect, useCallback } from 'react'
import { getToken } from '../hooks/useCurrentUser'
import { parseUTC } from '../utils/dateFormatter'

type Role = 'admin' | 'itom_admin' | 'operator' | 'viewer' | 'automation'

interface Principal {
  id: string
  name: string
  email: string | null
  role: Role
  enabled: boolean
  created_at: string
  last_seen_at: string | null
  api_key_prefix: string | null
}

interface AuditEntry {
  id: number
  ts: string
  actor_name: string | null
  action: string
  target_name: string | null
  detail: string | null
}


const ACTION_COLORS: Record<string, string> = {
  login:            '#34d399',
  password_changed: '#60a5fa',
  password_reset:   '#f59e0b',
  role_changed:     '#a78bfa',
  updated:          '#94a3b8',
  enabled:          '#34d399',
  disabled:         '#f87171',
  api_key_generated:'#fbbf24',
  api_key_revoked:  '#f87171',
  created:          '#34d399',
}

const authFetch = (url: string, opts: RequestInit = {}) => {
  const token = getToken()
  return fetch(url, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(opts.headers || {}),
    },
  })
}

// ── Shared styles ─────────────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  width: '100%', background: '#0d1117', border: '1px solid #2d3748',
  borderRadius: '6px', color: '#e2e8f0', fontSize: '0.875rem',
  padding: '0.5rem 0.65rem', outline: 'none', boxSizing: 'border-box',
}
const labelStyle: React.CSSProperties = {
  display: 'block', color: '#94a3b8', fontSize: '0.78rem',
  fontWeight: 500, marginBottom: '0.3rem',
}
const overlayStyle: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)',
  display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
}
const cardStyle: React.CSSProperties = {
  background: '#1a1f2e', border: '1px solid #2d3748', borderRadius: '12px',
  padding: '1.75rem', width: '420px', maxWidth: '95vw',
  boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
}

function RoleBadge({ role }: { role: string }) {
  const label = role === 'itom_admin' ? 'ITOM Admin' : role
  return (
    <span style={{
      border: '1px solid rgba(255,255,255,0.45)',
      borderRadius: '6px',
      color: '#ffffff',
      fontSize: '0.72rem',
      fontWeight: 600,
      padding: '0.18rem 0.6rem',
      textTransform: 'uppercase',
      letterSpacing: '0.05em',
      whiteSpace: 'nowrap',
      background: 'transparent',
    }}>
      {label}
    </span>
  )
}

function ModalActions({ onClose, onSubmit, saving, submitLabel }: { onClose: () => void; onSubmit?: () => void; saving: boolean; submitLabel: string }) {
  return (
    <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '1.25rem' }}>
      <button type="button" onClick={onClose} style={{ background: 'transparent', border: '1px solid #2d3748', borderRadius: '6px', color: '#94a3b8', padding: '0.5rem 1rem', cursor: 'pointer', fontSize: '0.875rem' }}>Cancel</button>
      <button type={onSubmit ? 'button' : 'submit'} onClick={onSubmit} disabled={saving}
        style={{ background: '#252c3c', border: '1px solid rgba(64, 112, 160, 0.40)', borderRadius: '6px', color: '#a0c4e8', padding: '0.5rem 1.25rem', cursor: saving ? 'not-allowed' : 'pointer', fontSize: '0.875rem', fontWeight: 600, opacity: saving ? 0.7 : 1 }}>
        {saving ? 'Saving…' : submitLabel}
      </button>
    </div>
  )
}

// ── Create modal ──────────────────────────────────────────────────────────────

function CreateModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('operator')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null); setSaving(true)
    try {
      const body: Record<string, string> = { name, role }
      if (email) body.email = email
      if (password) body.password = password
      const res = await authFetch('/api/auth/principals', { method: 'POST', body: JSON.stringify(body) })
      const data = await res.json()
      if (!res.ok) { setError(data.detail || 'Failed to create principal') }
      else { onCreated(); onClose() }
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={cardStyle} onClick={e => e.stopPropagation()}>
        <h3 style={{ color: '#e2e8f0', margin: '0 0 1.25rem', fontSize: '1.05rem', fontWeight: 600 }}>Create Principal</h3>
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '0.85rem' }}>
            <label style={labelStyle}>Name</label>
            <input style={inputStyle} value={name} onChange={e => setName(e.target.value)} required placeholder="Full name" />
          </div>
          <div style={{ marginBottom: '0.85rem' }}>
            <label style={labelStyle}>Role</label>
            <select style={{ ...inputStyle, cursor: 'pointer' }} value={role} onChange={e => setRole(e.target.value as Role)}>
              <option value="admin">admin — full platform access</option>
              <option value="itom_admin">itom_admin — configure automation &amp; runbooks</option>
              <option value="operator">operator — incident operations</option>
              <option value="viewer">viewer — read-only</option>
              <option value="automation">automation — API key account</option>
            </select>
          </div>
          {role !== 'automation' && <>
            <div style={{ marginBottom: '0.85rem' }}>
              <label style={labelStyle}>Email</label>
              <input style={inputStyle} type="email" value={email} onChange={e => setEmail(e.target.value)} required placeholder="user@example.com" />
            </div>
            <div style={{ marginBottom: '1rem' }}>
              <label style={labelStyle}>Password</label>
              <input style={inputStyle} type="password" value={password} onChange={e => setPassword(e.target.value)} required placeholder="Min 8 characters" />
            </div>
          </>}
          {role === 'automation' && <p style={{ color: '#64748b', fontSize: '0.8rem', marginBottom: '1rem' }}>Automation accounts authenticate via API key. Generate one after creation.</p>}
          {error && <div style={{ background: 'rgba(220,38,38,0.12)', border: '1px solid rgba(220,38,38,0.3)', borderRadius: '6px', color: '#f87171', fontSize: '0.8rem', padding: '0.5rem 0.65rem', marginBottom: '0.85rem' }}>{error}</div>}
          <ModalActions onClose={onClose} saving={saving} submitLabel="Create" />
        </form>
      </div>
    </div>
  )
}

// ── Edit modal ────────────────────────────────────────────────────────────────

function EditModal({ principal, onClose, onSaved }: { principal: Principal; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(principal.name)
  const [email, setEmail] = useState(principal.email ?? '')
  const [role, setRole] = useState<Role>(principal.role)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null); setSaving(true)
    try {
      const body: Record<string, string> = { name, role }
      if (email && principal.role !== 'automation') body.email = email
      const res = await authFetch(`/api/auth/principals/${principal.id}`, { method: 'PUT', body: JSON.stringify(body) })
      const data = await res.json()
      if (!res.ok) { setError(data.detail || 'Failed to update') }
      else { onSaved(); onClose() }
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={cardStyle} onClick={e => e.stopPropagation()}>
        <h3 style={{ color: '#e2e8f0', margin: '0 0 1.25rem', fontSize: '1.05rem', fontWeight: 600 }}>Edit Principal</h3>
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '0.85rem' }}>
            <label style={labelStyle}>Name</label>
            <input style={inputStyle} value={name} onChange={e => setName(e.target.value)} required />
          </div>
          {principal.role !== 'automation' && (
            <div style={{ marginBottom: '0.85rem' }}>
              <label style={labelStyle}>Email</label>
              <input style={inputStyle} type="email" value={email} onChange={e => setEmail(e.target.value)} />
            </div>
          )}
          <div style={{ marginBottom: '1rem' }}>
            <label style={labelStyle}>Role</label>
            <select style={{ ...inputStyle, cursor: 'pointer' }} value={role} onChange={e => setRole(e.target.value as Role)}>
              <option value="admin">admin — full platform access</option>
              <option value="itom_admin">itom_admin — configure automation &amp; runbooks</option>
              <option value="operator">operator — incident operations</option>
              <option value="viewer">viewer — read-only</option>
              <option value="automation">automation — API key account</option>
            </select>
          </div>
          {error && <div style={{ background: 'rgba(220,38,38,0.12)', border: '1px solid rgba(220,38,38,0.3)', borderRadius: '6px', color: '#f87171', fontSize: '0.8rem', padding: '0.5rem 0.65rem', marginBottom: '0.85rem' }}>{error}</div>}
          <ModalActions onClose={onClose} saving={saving} submitLabel="Save Changes" />
        </form>
      </div>
    </div>
  )
}

// ── Reset password modal ──────────────────────────────────────────────────────

function ResetPasswordModal({ principal, onClose, onReset }: { principal: Principal; onClose: () => void; onReset: () => void }) {
  const [pw, setPw] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault(); setError(null)
    if (pw !== confirm) { setError('Passwords do not match'); return }
    if (pw.length < 8) { setError('Minimum 8 characters'); return }
    setSaving(true)
    try {
      const res = await authFetch(`/api/auth/principals/${principal.id}/reset-password`, { method: 'POST', body: JSON.stringify({ new_password: pw }) })
      const data = await res.json()
      if (!res.ok) { setError(data.detail || 'Failed to reset password') }
      else { onReset(); onClose() }
    } catch { setError('Network error') } finally { setSaving(false) }
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={cardStyle} onClick={e => e.stopPropagation()}>
        <h3 style={{ color: '#e2e8f0', margin: '0 0 0.4rem', fontSize: '1.05rem', fontWeight: 600 }}>Reset Password</h3>
        <p style={{ color: '#64748b', fontSize: '0.83rem', marginBottom: '1.25rem' }}>Setting new password for <span style={{ color: '#94a3b8' }}>{principal.name}</span></p>
        <form onSubmit={handleSubmit}>
          <div style={{ marginBottom: '0.85rem' }}>
            <label style={labelStyle}>New Password</label>
            <input style={inputStyle} type="password" value={pw} onChange={e => setPw(e.target.value)} required placeholder="Min 8 characters" />
          </div>
          <div style={{ marginBottom: '1rem' }}>
            <label style={labelStyle}>Confirm Password</label>
            <input style={inputStyle} type="password" value={confirm} onChange={e => setConfirm(e.target.value)} required placeholder="Repeat password" />
          </div>
          {error && <div style={{ background: 'rgba(220,38,38,0.12)', border: '1px solid rgba(220,38,38,0.3)', borderRadius: '6px', color: '#f87171', fontSize: '0.8rem', padding: '0.5rem 0.65rem', marginBottom: '0.85rem' }}>{error}</div>}
          <ModalActions onClose={onClose} saving={saving} submitLabel="Reset Password" />
        </form>
      </div>
    </div>
  )
}

// ── API Key modal ─────────────────────────────────────────────────────────────

function ApiKeyModal({ principal, onClose, onGenerated }: { principal: Principal; onClose: () => void; onGenerated: () => void }) {
  const [apiKey, setApiKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)

  const generate = async () => {
    setError(null); setLoading(true)
    try {
      const res = await authFetch(`/api/auth/principals/${principal.id}/api-key`, { method: 'POST' })
      const data = await res.json()
      if (res.ok) { setApiKey(data.api_key); onGenerated() }
      else { setError(data.detail || 'Failed to generate key') }
    } catch { setError('Network error') } finally { setLoading(false) }
  }

  const copy = () => {
    if (apiKey) navigator.clipboard.writeText(apiKey).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000) })
  }

  return (
    <div style={overlayStyle} onClick={onClose}>
      <div style={{ ...cardStyle, width: '460px' }} onClick={e => e.stopPropagation()}>
        <h3 style={{ color: '#e2e8f0', margin: '0 0 0.5rem', fontSize: '1.05rem', fontWeight: 600 }}>Generate API Key</h3>
        <p style={{ color: '#64748b', fontSize: '0.83rem', marginBottom: '1.25rem' }}>For: <span style={{ color: '#94a3b8' }}>{principal.name}</span></p>
        {!apiKey && <>
          <p style={{ color: '#94a3b8', fontSize: '0.85rem', marginBottom: '1.25rem' }}>This will replace any existing key. The raw key is shown once — store it securely.</p>
          {error && <div style={{ background: 'rgba(220,38,38,0.12)', border: '1px solid rgba(220,38,38,0.3)', borderRadius: '6px', color: '#f87171', fontSize: '0.8rem', padding: '0.5rem 0.65rem', marginBottom: '1rem' }}>{error}</div>}
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
            <button onClick={onClose} style={{ background: 'transparent', border: '1px solid #2d3748', borderRadius: '6px', color: '#94a3b8', padding: '0.5rem 1rem', cursor: 'pointer', fontSize: '0.875rem' }}>Cancel</button>
            <button onClick={generate} disabled={loading} style={{ background: '#f59e0b', border: 'none', borderRadius: '6px', color: '#000', padding: '0.5rem 1.25rem', cursor: loading ? 'not-allowed' : 'pointer', fontSize: '0.875rem', fontWeight: 700, opacity: loading ? 0.7 : 1 }}>
              {loading ? 'Generating…' : 'Generate Key'}
            </button>
          </div>
        </>}
        {apiKey && <>
          <div style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: '8px', padding: '0.75rem', marginBottom: '1rem' }}>
            <p style={{ color: '#fbbf24', fontSize: '0.78rem', fontWeight: 600, margin: '0 0 0.5rem' }}>API KEY — shown once, copy now</p>
            <code style={{ display: 'block', color: '#e2e8f0', fontSize: '0.82rem', wordBreak: 'break-all', fontFamily: 'monospace' }}>{apiKey}</code>
          </div>
          <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
            <button onClick={copy} style={{ background: copied ? '#10b981' : '#2d3748', border: 'none', borderRadius: '6px', color: '#e2e8f0', padding: '0.5rem 1rem', cursor: 'pointer', fontSize: '0.875rem', transition: 'background 0.15s' }}>
              {copied ? 'Copied!' : 'Copy Key'}
            </button>
            <button onClick={onClose} style={{ background: '#252c3c', border: '1px solid rgba(64, 112, 160, 0.40)', borderRadius: '6px', color: '#a0c4e8', padding: '0.5rem 1.25rem', cursor: 'pointer', fontSize: '0.875rem', fontWeight: 600 }}>Done</button>
          </div>
        </>}
      </div>
    </div>
  )
}

// ── Principals table ──────────────────────────────────────────────────────────

function PrincipalsTab({ principals, onRefresh }: { principals: Principal[]; onRefresh: () => void }) {
  const [editTarget, setEditTarget]   = useState<Principal | null>(null)
  const [resetTarget, setResetTarget] = useState<Principal | null>(null)
  const [apiKeyTarget, setApiKeyTarget] = useState<Principal | null>(null)
  const [showCreate, setShowCreate]   = useState(false)

  const toggleEnabled = async (p: Principal) => {
    await authFetch(`/api/auth/principals/${p.id}`, { method: 'PUT', body: JSON.stringify({ enabled: !p.enabled }) })
    onRefresh()
  }
  const revokeApiKey = async (p: Principal) => {
    await authFetch(`/api/auth/principals/${p.id}/api-key`, { method: 'DELETE' })
    onRefresh()
  }

  const btn: React.CSSProperties = {
    background: 'transparent',
    border: '1px solid rgba(255,255,255,0.2)',
    borderRadius: '5px',
    color: '#ffffff',
    padding: '0.22rem 0.6rem',
    cursor: 'pointer',
    fontSize: '0.72rem',
    fontWeight: 600,
    whiteSpace: 'nowrap',
    letterSpacing: '0.02em',
  }

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '1rem' }}>
        <button onClick={() => setShowCreate(true)}
          style={{ background: '#252c3c', border: '1px solid rgba(64, 112, 160, 0.40)', borderRadius: '8px', color: '#a0c4e8', padding: '0.55rem 1.1rem', cursor: 'pointer', fontSize: '0.875rem', fontWeight: 600 }}>
          + New Principal
        </button>
      </div>
      <div style={{ background: '#1a1f2e', border: '1px solid #2d3748', borderRadius: '12px', overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #2d3748' }}>
              {['Name / Email', 'Role', 'Status', 'Last Seen', 'API Key', 'Actions'].map(h => (
                <th key={h} style={{ color: '#64748b', fontSize: '0.72rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', padding: '0.75rem 1rem', textAlign: 'left' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {principals.map((p, i) => (
              <tr key={p.id} style={{ borderBottom: i < principals.length - 1 ? '1px solid #1e2536' : 'none' }}>
                <td style={{ padding: '0.85rem 1rem' }}>
                  <div style={{ color: '#e2e8f0', fontWeight: 500, fontSize: '0.875rem' }}>{p.name}</div>
                  {p.email && <div style={{ color: '#64748b', fontSize: '0.75rem', marginTop: '0.1rem' }}>{p.email}</div>}
                </td>
                <td style={{ padding: '0.85rem 1rem' }}><RoleBadge role={p.role} /></td>
                <td style={{ padding: '0.85rem 1rem' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: p.enabled ? '#10b981' : '#64748b', fontSize: '0.83rem' }}>
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: p.enabled ? '#10b981' : '#64748b', flexShrink: 0, display: 'inline-block' }} />
                    {p.enabled ? 'Active' : 'Disabled'}
                  </span>
                </td>
                <td style={{ padding: '0.85rem 1rem', color: '#64748b', fontSize: '0.78rem' }}>
                  {p.last_seen_at ? parseUTC(p.last_seen_at).toLocaleString() : '—'}
                </td>
                <td style={{ padding: '0.85rem 1rem', color: '#64748b', fontSize: '0.78rem', fontFamily: 'monospace' }}>
                  {p.api_key_prefix ? <span title="API key prefix">{p.api_key_prefix}…</span>
                    : p.role === 'automation' ? <span style={{ color: '#f87171' }}>No key</span> : '—'}
                </td>
                <td style={{ padding: '0.85rem 1rem' }}>
                  <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                    <button onClick={() => setEditTarget(p)} style={btn}>Edit</button>
                    {p.role !== 'automation' && (
                      <button onClick={() => setResetTarget(p)} style={btn}>Reset PW</button>
                    )}
                    <button onClick={() => toggleEnabled(p)} style={btn}>
                      {p.enabled ? 'Disable' : 'Enable'}
                    </button>
                    {p.role === 'automation' && (
                      <button onClick={() => setApiKeyTarget(p)} style={btn}>Gen Key</button>
                    )}
                    {p.role === 'automation' && p.api_key_prefix && (
                      <button onClick={() => revokeApiKey(p)} style={btn}>Revoke</button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {showCreate  && <CreateModal onClose={() => setShowCreate(false)} onCreated={onRefresh} />}
      {editTarget  && <EditModal principal={editTarget} onClose={() => setEditTarget(null)} onSaved={onRefresh} />}
      {resetTarget && <ResetPasswordModal principal={resetTarget} onClose={() => setResetTarget(null)} onReset={onRefresh} />}
      {apiKeyTarget && <ApiKeyModal principal={apiKeyTarget} onClose={() => setApiKeyTarget(null)} onGenerated={onRefresh} />}
    </>
  )
}

// ── Audit log tab ─────────────────────────────────────────────────────────────

function AuditLogTab() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  useEffect(() => {
    authFetch('/api/auth/audit-log?limit=100')
      .then(r => r.json())
      .then(setEntries)
      .catch(() => setError('Failed to load audit log'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ color: '#64748b', padding: '2rem', textAlign: 'center' }}>Loading audit log…</div>
  if (error)   return <div style={{ color: '#f87171', padding: '2rem' }}>{error}</div>
  if (!entries.length) return <div style={{ color: '#64748b', padding: '2rem', textAlign: 'center' }}>No audit events recorded yet.</div>

  return (
    <div style={{ background: '#1a1f2e', border: '1px solid #2d3748', borderRadius: '12px', overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid #2d3748' }}>
            {['Time', 'Actor', 'Action', 'Target', 'Detail'].map(h => (
              <th key={h} style={{ color: '#64748b', fontSize: '0.72rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em', padding: '0.75rem 1rem', textAlign: 'left' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {entries.map((e, i) => {
            const color = ACTION_COLORS[e.action] ?? '#94a3b8'
            return (
              <tr key={e.id} style={{ borderBottom: i < entries.length - 1 ? '1px solid #1e2536' : 'none' }}>
                <td style={{ padding: '0.7rem 1rem', color: '#64748b', fontSize: '0.76rem', whiteSpace: 'nowrap' }}>
                  {parseUTC(e.ts).toLocaleString()}
                </td>
                <td style={{ padding: '0.7rem 1rem', color: '#94a3b8', fontSize: '0.82rem' }}>
                  {e.actor_name ?? <span style={{ color: '#4a5568' }}>system</span>}
                </td>
                <td style={{ padding: '0.7rem 1rem' }}>
                  <span style={{ background: `${color}18`, color, borderRadius: '4px', fontSize: '0.72rem', fontWeight: 600, padding: '0.15rem 0.5rem', textTransform: 'uppercase', letterSpacing: '0.03em' }}>
                    {e.action.replace(/_/g, ' ')}
                  </span>
                </td>
                <td style={{ padding: '0.7rem 1rem', color: '#94a3b8', fontSize: '0.82rem' }}>
                  {e.target_name ?? '—'}
                </td>
                <td style={{ padding: '0.7rem 1rem', color: '#64748b', fontSize: '0.78rem', maxWidth: '280px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={e.detail ?? ''}>
                  {e.detail ?? '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function UserManagement() {
  const [principals, setPrincipals] = useState<Principal[]>([])
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState<string | null>(null)
  const [tab, setTab]               = useState<'users' | 'audit'>('users')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const res = await authFetch('/api/auth/principals')
      if (res.ok) setPrincipals(await res.json())
      else setError('Failed to load principals')
    } catch { setError('Network error') } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const tabBtn = (id: typeof tab, label: string) => (
    <button onClick={() => setTab(id)} style={{
      background: 'transparent', border: 'none', cursor: 'pointer',
      color: tab === id ? '#e2e8f0' : '#64748b',
      fontSize: '0.875rem', fontWeight: tab === id ? 600 : 400,
      padding: '0.5rem 0', borderBottom: tab === id ? '2px solid #6366f1' : '2px solid transparent',
      marginRight: '1.5rem', transition: 'color 0.15s',
    }}>{label}</button>
  )

  if (loading) return <div style={{ color: '#64748b', padding: '2rem', textAlign: 'center' }}>Loading…</div>
  if (error)   return <div style={{ color: '#f87171', padding: '2rem' }}>{error}</div>

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ marginBottom: '1.5rem' }}>
        <h2 style={{ color: '#e2e8f0', margin: '0 0 0.25rem', fontSize: '1.35rem', fontWeight: 700 }}>User Management</h2>
        <p style={{ color: '#64748b', margin: 0, fontSize: '0.85rem' }}>Manage platform principals, roles, and API keys</p>
      </div>

      {/* Tabs */}
      <div style={{ borderBottom: '1px solid #2d3748', marginBottom: '1.5rem' }}>
        {tabBtn('users', `Principals (${principals.length})`)}
        {tabBtn('audit', 'Audit Log')}
      </div>

      {tab === 'users' && <PrincipalsTab principals={principals} onRefresh={load} />}
      {tab === 'audit' && <AuditLogTab />}
    </div>
  )
}
