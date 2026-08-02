import { useState, useEffect } from 'react'
import {
  getSSHCredentials, createSSHCredential, updateSSHCredential,
  deleteSSHCredential, testSSHCredential,
  SSHCredential, SSHCredentialCreate, SSHCredentialUpdate,
} from '../services/api'

const DS = {
  bg:      '#0d1117',
  surface: '#1a1f2e',
  raised:  '#252c3c',
  border:  '#3d4557',
  txtP:    '#e8eef5',
  txtS:    '#7a8ba3',
  txtM:    '#a0aec0',
  accent:  '#3b82f6',
}

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 12px',
  borderRadius: 6,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.bg,
  color: DS.txtP,
  fontSize: '0.85rem',
  outline: 'none',
  boxSizing: 'border-box',
}

const btnPrimary: React.CSSProperties = {
  padding: '8px 20px',
  borderRadius: 7,
  border: 'none',
  backgroundColor: DS.accent,
  color: '#fff',
  fontSize: '0.82rem',
  fontWeight: 600,
  cursor: 'pointer',
}

const btnSecondary: React.CSSProperties = {
  padding: '8px 20px',
  borderRadius: 7,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.raised,
  color: DS.txtP,
  fontSize: '0.82rem',
  fontWeight: 500,
  cursor: 'pointer',
}

const btnDanger: React.CSSProperties = {
  padding: '6px 14px',
  borderRadius: 6,
  border: '1px solid rgba(239,68,68,0.3)',
  backgroundColor: 'rgba(239,68,68,0.1)',
  color: '#f87171',
  fontSize: '0.78rem',
  fontWeight: 600,
  cursor: 'pointer',
}

interface ModalState {
  open: boolean
  mode: 'create' | 'edit'
  editId?: string
  name: string
  host_pattern: string
  username: string
  private_key: string
  port: number
  description: string
  enabled: boolean
}

const emptyModal: ModalState = {
  open: false, mode: 'create',
  name: '', host_pattern: '', username: 'root',
  private_key: '', port: 22, description: '', enabled: true,
}

interface TestState {
  credId: string
  hostname: string
  port: string
  loading: boolean
  result: { success: boolean; output?: string; error?: string } | null
}

const emptyTest: TestState = {
  credId: '', hostname: '', port: '', loading: false, result: null,
}

export default function SSHCredentials() {
  const [credentials, setCredentials] = useState<SSHCredential[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [modal, setModal] = useState<ModalState>(emptyModal)
  const [test, setTest] = useState<TestState>(emptyTest)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)

  const load = async () => {
    try {
      setLoading(true)
      setError(null)
      const res = await getSSHCredentials()
      setCredentials(res.data.credentials)
    } catch {
      setError('Failed to load SSH credentials')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const openCreate = () => setModal({ ...emptyModal, open: true, mode: 'create' })

  const openEdit = (c: SSHCredential) => setModal({
    open: true, mode: 'edit', editId: c.id,
    name: c.name, host_pattern: c.host_pattern, username: c.username,
    private_key: '', port: c.port, description: c.description || '',
    enabled: c.enabled,
  })

  const handleSave = async () => {
    try {
      setSaving(true)
      if (modal.mode === 'create') {
        const body: SSHCredentialCreate = {
          name: modal.name,
          host_pattern: modal.host_pattern,
          username: modal.username,
          private_key: modal.private_key,
          port: modal.port,
          description: modal.description || undefined,
        }
        await createSSHCredential(body)
      } else {
        const body: SSHCredentialUpdate = {
          name: modal.name,
          host_pattern: modal.host_pattern,
          username: modal.username,
          port: modal.port,
          description: modal.description,
          enabled: modal.enabled,
        }
        if (modal.private_key) body.private_key = modal.private_key
        await updateSSHCredential(modal.editId!, body)
      }
      setModal(emptyModal)
      await load()
    } catch (err: any) {
      const msg = err?.response?.data?.detail || 'Save failed'
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await deleteSSHCredential(id)
      setDeleteConfirm(null)
      await load()
    } catch {
      setError('Delete failed')
    }
  }

  const handleTest = async () => {
    try {
      setTest(t => ({ ...t, loading: true, result: null }))
      const port = test.port ? parseInt(test.port, 10) : undefined
      const res = await testSSHCredential(test.credId, test.hostname, port)
      setTest(t => ({ ...t, loading: false, result: res.data }))
    } catch {
      setTest(t => ({ ...t, loading: false, result: { success: false, error: 'Request failed' } }))
    }
  }

  const openTest = (id: string) => setTest({ credId: id, hostname: '', port: '', loading: false, result: null })

  const canSave = modal.name && modal.host_pattern && modal.username &&
    (modal.mode === 'edit' || modal.private_key)

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto', padding: '2rem 1rem' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
        <div>
          <h1 style={{ fontSize: '1.5rem', fontWeight: 700, color: DS.txtP, margin: 0 }}>
            SSH Credentials
          </h1>
          <p style={{ fontSize: '0.85rem', color: DS.txtS, marginTop: 4 }}>
            Manage SSH private keys for agentless remote monitoring. Keys are AES-encrypted at rest.
          </p>
        </div>
        <button style={btnPrimary} onClick={openCreate}>+ Add Credential</button>
      </div>

      {error && (
        <div style={{
          padding: '10px 16px', borderRadius: 8, marginBottom: 16,
          backgroundColor: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
          color: '#f87171', fontSize: '0.85rem',
        }}>
          {error}
          <button onClick={() => setError(null)} style={{
            float: 'right', background: 'none', border: 'none',
            color: '#f87171', cursor: 'pointer', fontWeight: 700,
          }}>x</button>
        </div>
      )}

      {/* Table */}
      <div style={{
        backgroundColor: DS.surface, borderRadius: 10,
        border: `1px solid ${DS.border}`, overflow: 'hidden',
      }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: `1px solid ${DS.border}` }}>
                {['Name', 'Host Pattern', 'Username', 'Port', 'Key', 'Status', 'Actions'].map(h => (
                  <th key={h} style={{
                    padding: '12px 16px', textAlign: 'left', fontSize: '0.75rem',
                    fontWeight: 600, color: DS.txtS, textTransform: 'uppercase',
                    letterSpacing: '0.05em',
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={7} style={{ padding: 32, textAlign: 'center', color: DS.txtS }}>
                    Loading...
                  </td>
                </tr>
              ) : credentials.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ padding: 32, textAlign: 'center', color: DS.txtS }}>
                    No SSH credentials configured. Click "+ Add Credential" to get started.
                  </td>
                </tr>
              ) : credentials.map(c => (
                <tr key={c.id} style={{ borderBottom: `1px solid ${DS.border}` }}>
                  <td style={{ padding: '12px 16px', color: DS.txtP, fontSize: '0.85rem', fontWeight: 600 }}>
                    {c.name}
                  </td>
                  <td style={{ padding: '12px 16px', color: DS.txtM, fontSize: '0.85rem', fontFamily: 'monospace' }}>
                    {c.host_pattern}
                  </td>
                  <td style={{ padding: '12px 16px', color: DS.txtM, fontSize: '0.85rem' }}>
                    {c.username}
                  </td>
                  <td style={{ padding: '12px 16px', color: DS.txtM, fontSize: '0.85rem' }}>
                    {c.port}
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    <span style={{
                      display: 'inline-block', padding: '2px 8px', borderRadius: 4,
                      fontSize: '0.75rem', fontWeight: 600,
                      backgroundColor: c.has_key ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                      color: c.has_key ? '#22c55e' : '#f87171',
                      border: `1px solid ${c.has_key ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
                    }}>
                      {c.has_key ? 'Stored' : 'Missing'}
                    </span>
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    <span style={{
                      display: 'inline-block', padding: '2px 8px', borderRadius: 4,
                      fontSize: '0.75rem', fontWeight: 600,
                      backgroundColor: c.enabled ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                      color: c.enabled ? '#22c55e' : '#f87171',
                      border: `1px solid ${c.enabled ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
                    }}>
                      {c.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </td>
                  <td style={{ padding: '12px 16px' }}>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button
                        style={{ ...btnSecondary, padding: '4px 10px', fontSize: '0.75rem' }}
                        onClick={() => openEdit(c)}
                      >Edit</button>
                      <button
                        style={{ ...btnSecondary, padding: '4px 10px', fontSize: '0.75rem' }}
                        onClick={() => openTest(c.id)}
                      >Test</button>
                      <button
                        style={{ ...btnDanger, padding: '4px 10px', fontSize: '0.75rem' }}
                        onClick={() => setDeleteConfirm(c.id)}
                      >Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Delete confirmation */}
      {deleteConfirm && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={() => setDeleteConfirm(null)}>
          <div style={{
            backgroundColor: DS.surface, borderRadius: 12,
            border: `1px solid ${DS.border}`, padding: '24px 28px',
            maxWidth: 420, width: '90%',
          }} onClick={e => e.stopPropagation()}>
            <h3 style={{ color: DS.txtP, margin: '0 0 12px', fontSize: '1.1rem' }}>
              Delete Credential
            </h3>
            <p style={{ color: DS.txtS, fontSize: '0.85rem', margin: '0 0 20px' }}>
              Are you sure? The SSH adapter will no longer be able to connect to hosts matching this credential's pattern.
            </p>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
              <button style={btnSecondary} onClick={() => setDeleteConfirm(null)}>Cancel</button>
              <button style={btnDanger} onClick={() => handleDelete(deleteConfirm)}>Delete</button>
            </div>
          </div>
        </div>
      )}

      {/* Test modal */}
      {test.credId && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={() => setTest(emptyTest)}>
          <div style={{
            backgroundColor: DS.surface, borderRadius: 12,
            border: `1px solid ${DS.border}`, padding: '24px 28px',
            maxWidth: 480, width: '90%',
          }} onClick={e => e.stopPropagation()}>
            <h3 style={{ color: DS.txtP, margin: '0 0 16px', fontSize: '1.1rem' }}>
              Test SSH Connection
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div>
                <label style={{ display: 'block', color: DS.txtS, fontSize: '0.78rem', marginBottom: 4 }}>
                  Target Hostname / IP
                </label>
                <input
                  style={inputStyle}
                  placeholder="e.g. web-01.prod.internal"
                  value={test.hostname}
                  onChange={e => setTest(t => ({ ...t, hostname: e.target.value, result: null }))}
                />
              </div>
              <div>
                <label style={{ display: 'block', color: DS.txtS, fontSize: '0.78rem', marginBottom: 4 }}>
                  Port Override (optional)
                </label>
                <input
                  style={inputStyle}
                  placeholder="Default from credential"
                  value={test.port}
                  onChange={e => setTest(t => ({ ...t, port: e.target.value, result: null }))}
                />
              </div>
            </div>
            {test.result && (
              <div style={{
                marginTop: 14, padding: '10px 14px', borderRadius: 8,
                backgroundColor: test.result.success ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)',
                border: `1px solid ${test.result.success ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
                color: test.result.success ? '#22c55e' : '#f87171',
                fontSize: '0.82rem',
              }}>
                {test.result.success ? 'Connection successful' : (test.result.error || 'Connection failed')}
              </div>
            )}
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 18 }}>
              <button style={btnSecondary} onClick={() => setTest(emptyTest)}>Close</button>
              <button
                style={{ ...btnPrimary, opacity: (!test.hostname || test.loading) ? 0.5 : 1 }}
                disabled={!test.hostname || test.loading}
                onClick={handleTest}
              >{test.loading ? 'Testing...' : 'Test Connection'}</button>
            </div>
          </div>
        </div>
      )}

      {/* Create / Edit modal */}
      {modal.open && (
        <div style={{
          position: 'fixed', inset: 0, backgroundColor: 'rgba(0,0,0,0.6)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }} onClick={() => setModal(emptyModal)}>
          <div style={{
            backgroundColor: DS.surface, borderRadius: 12,
            border: `1px solid ${DS.border}`, padding: '24px 28px',
            maxWidth: 540, width: '90%', maxHeight: '85vh', overflowY: 'auto',
          }} onClick={e => e.stopPropagation()}>
            <h3 style={{ color: DS.txtP, margin: '0 0 20px', fontSize: '1.1rem' }}>
              {modal.mode === 'create' ? 'Add SSH Credential' : 'Edit SSH Credential'}
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div>
                <label style={{ display: 'block', color: DS.txtS, fontSize: '0.78rem', marginBottom: 4 }}>
                  Name *
                </label>
                <input
                  style={inputStyle}
                  placeholder="e.g. prod-web-servers"
                  value={modal.name}
                  onChange={e => setModal(m => ({ ...m, name: e.target.value }))}
                />
              </div>
              <div>
                <label style={{ display: 'block', color: DS.txtS, fontSize: '0.78rem', marginBottom: 4 }}>
                  Host Pattern * <span style={{ color: DS.txtS, fontWeight: 400 }}>(fnmatch glob)</span>
                </label>
                <input
                  style={inputStyle}
                  placeholder="e.g. web-*.prod.internal or 10.0.1.*"
                  value={modal.host_pattern}
                  onChange={e => setModal(m => ({ ...m, host_pattern: e.target.value }))}
                />
              </div>
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 2 }}>
                  <label style={{ display: 'block', color: DS.txtS, fontSize: '0.78rem', marginBottom: 4 }}>
                    Username *
                  </label>
                  <input
                    style={inputStyle}
                    placeholder="root"
                    value={modal.username}
                    onChange={e => setModal(m => ({ ...m, username: e.target.value }))}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ display: 'block', color: DS.txtS, fontSize: '0.78rem', marginBottom: 4 }}>
                    Port
                  </label>
                  <input
                    style={inputStyle}
                    type="number"
                    value={modal.port}
                    onChange={e => setModal(m => ({ ...m, port: parseInt(e.target.value) || 22 }))}
                  />
                </div>
              </div>
              <div>
                <label style={{ display: 'block', color: DS.txtS, fontSize: '0.78rem', marginBottom: 4 }}>
                  Private Key (PEM) {modal.mode === 'create' ? '*' : '(leave blank to keep existing)'}
                </label>
                <textarea
                  style={{
                    ...inputStyle,
                    height: 140,
                    fontFamily: 'monospace',
                    fontSize: '0.78rem',
                    resize: 'vertical',
                  }}
                  placeholder="-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----"
                  value={modal.private_key}
                  onChange={e => setModal(m => ({ ...m, private_key: e.target.value }))}
                />
              </div>
              <div>
                <label style={{ display: 'block', color: DS.txtS, fontSize: '0.78rem', marginBottom: 4 }}>
                  Description
                </label>
                <input
                  style={inputStyle}
                  placeholder="Optional description"
                  value={modal.description}
                  onChange={e => setModal(m => ({ ...m, description: e.target.value }))}
                />
              </div>
              {modal.mode === 'edit' && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input
                    type="checkbox"
                    checked={modal.enabled}
                    onChange={e => setModal(m => ({ ...m, enabled: e.target.checked }))}
                    style={{ width: 16, height: 16, accentColor: DS.accent }}
                  />
                  <label style={{ color: DS.txtM, fontSize: '0.85rem' }}>Enabled</label>
                </div>
              )}
            </div>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 22 }}>
              <button style={btnSecondary} onClick={() => setModal(emptyModal)}>Cancel</button>
              <button
                style={{ ...btnPrimary, opacity: (!canSave || saving) ? 0.5 : 1 }}
                disabled={!canSave || saving}
                onClick={handleSave}
              >{saving ? 'Saving...' : (modal.mode === 'create' ? 'Create' : 'Save Changes')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
