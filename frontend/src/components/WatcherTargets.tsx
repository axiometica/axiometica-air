import { useState, useEffect, useCallback } from 'react'
import {
  getWatcherTargets, createWatcherTarget, createWatcherTargetsCidr,
  deleteWatcherTarget, deleteWatcherTargetsCidr,
  approveWatcherTarget, approveAllWatcherTargets,
  getSSHCredentials,
  WatcherTarget, SSHCredential,
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
  padding: '7px 10px',
  borderRadius: 6,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.bg,
  color: DS.txtP,
  fontSize: '0.82rem',
  outline: 'none',
  boxSizing: 'border-box',
}

const btnPrimary: React.CSSProperties = {
  padding: '7px 16px',
  borderRadius: 6,
  border: 'none',
  backgroundColor: DS.accent,
  color: '#fff',
  fontSize: '0.8rem',
  fontWeight: 600,
  cursor: 'pointer',
}

const btnSecondary: React.CSSProperties = {
  padding: '6px 12px',
  borderRadius: 6,
  border: `1px solid ${DS.border}`,
  backgroundColor: DS.raised,
  color: DS.txtP,
  fontSize: '0.78rem',
  fontWeight: 500,
  cursor: 'pointer',
}

const btnDanger: React.CSSProperties = {
  padding: '5px 10px',
  borderRadius: 5,
  border: '1px solid rgba(239,68,68,0.3)',
  backgroundColor: 'rgba(239,68,68,0.1)',
  color: '#f87171',
  fontSize: '0.75rem',
  fontWeight: 600,
  cursor: 'pointer',
}

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  pending:      { bg: 'rgba(107,114,128,0.15)', color: '#9ca3af' },
  approved:     { bg: 'rgba(59,130,246,0.12)',  color: '#60a5fa' },
  port_closed:  { bg: 'rgba(75,85,99,0.2)',     color: '#6b7280' },
  port_open:    { bg: 'rgba(234,179,8,0.12)',   color: '#facc15' },
  active:       { bg: 'rgba(34,197,94,0.12)',   color: '#4ade80' },
  auth_failed:  { bg: 'rgba(239,68,68,0.12)',   color: '#f87171' },
  unreachable:  { bg: 'rgba(249,115,22,0.12)',  color: '#fb923c' },
}

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_COLORS[status] || STATUS_COLORS.pending
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 4,
      fontSize: '0.72rem', fontWeight: 600,
      backgroundColor: s.bg, color: s.color,
    }}>
      {status.replace('_', ' ')}
    </span>
  )
}

interface Props {
  watcherId: string
}

export default function WatcherTargets({ watcherId }: Props) {
  const [targets, setTargets] = useState<WatcherTarget[]>([])
  const [credentials, setCredentials] = useState<SSHCredential[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('')

  // Add host form
  const [addHost, setAddHost] = useState('')
  const [addPort, setAddPort] = useState('22')
  const [addName, setAddName] = useState('')
  const [addCred, setAddCred] = useState('')
  const [adding, setAdding] = useState(false)

  // CIDR form
  const [cidr, setCidr] = useState('')
  const [cidrPort, setCidrPort] = useState('22')
  const [cidrCred, setCidrCred] = useState('')
  const [cidrExpanding, setCidrExpanding] = useState(false)
  const [cidrResult, setCidrResult] = useState<string | null>(null)

  const [collapsed, setCollapsed] = useState(false)

  const load = useCallback(async () => {
    try {
      setLoading(true)
      const [tResp, cResp] = await Promise.all([
        getWatcherTargets(watcherId, statusFilter || undefined),
        getSSHCredentials(),
      ])
      setTargets(tResp.data)
      setCredentials(cResp.data.credentials || [])
      setError(null)
    } catch (e: any) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load targets')
    } finally {
      setLoading(false)
    }
  }, [watcherId, statusFilter])

  useEffect(() => { load() }, [load])

  const handleAddHost = async () => {
    if (!addHost.trim()) return
    setAdding(true)
    try {
      await createWatcherTarget(watcherId, {
        host: addHost.trim(),
        port: parseInt(addPort) || 22,
        name: addName.trim() || undefined,
        credential_name: addCred || undefined,
      })
      setAddHost('')
      setAddName('')
      setAddPort('22')
      setAddCred('')
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to add target')
    } finally {
      setAdding(false)
    }
  }

  const handleCidrExpand = async () => {
    if (!cidr.trim()) return
    setCidrExpanding(true)
    setCidrResult(null)
    try {
      const resp = await createWatcherTargetsCidr(watcherId, {
        cidr: cidr.trim(),
        port: parseInt(cidrPort) || 22,
        credential_name: cidrCred || undefined,
      })
      setCidrResult(`${resp.data.inserted} targets added (${resp.data.skipped} skipped) from ${resp.data.cidr}`)
      setCidr('')
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to expand CIDR')
    } finally {
      setCidrExpanding(false)
    }
  }

  const handleDelete = async (targetId: string) => {
    try {
      await deleteWatcherTarget(watcherId, targetId)
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to delete target')
    }
  }

  const handleApprove = async (targetId: string) => {
    try {
      await approveWatcherTarget(watcherId, targetId)
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to approve target')
    }
  }

  const handleApproveAll = async () => {
    try {
      const resp = await approveAllWatcherTargets(watcherId)
      if (resp.data.approved > 0) await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to approve targets')
    }
  }

  const handleDeleteCidrGroup = async (group: string) => {
    try {
      await deleteWatcherTargetsCidr(watcherId, group)
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to delete CIDR group')
    }
  }

  const pendingCount = targets.filter(t => t.status === 'pending').length
  const cidrGroups = [...new Set(targets.filter(t => t.cidr_group).map(t => t.cidr_group!))]

  return (
    <div style={{
      marginTop: '1.5rem', padding: '1rem', borderRadius: 10,
      border: `1px solid ${DS.border}`, backgroundColor: DS.surface,
    }}>
      {/* Header */}
      <div
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          cursor: 'pointer', userSelect: 'none',
        }}
        onClick={() => setCollapsed(c => !c)}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: '0.72rem', color: DS.txtS }}>{collapsed ? '▶' : '▼'}</span>
          <span style={{ fontSize: '0.88rem', fontWeight: 600, color: DS.txtP }}>
            SSH Targets
          </span>
          <span style={{
            fontSize: '0.72rem', color: DS.txtS, padding: '1px 8px',
            borderRadius: 10, backgroundColor: DS.raised,
          }}>
            {targets.length}
          </span>
        </div>
        {pendingCount > 0 && (
          <span style={{
            fontSize: '0.72rem', padding: '2px 10px', borderRadius: 10,
            backgroundColor: 'rgba(234,179,8,0.12)', color: '#facc15', fontWeight: 600,
          }}>
            {pendingCount} pending
          </span>
        )}
      </div>

      {collapsed && <div />}
      {!collapsed && (
        <div style={{ marginTop: '1rem' }}>
          {error && (
            <div style={{
              padding: '8px 12px', borderRadius: 6, marginBottom: '0.75rem',
              backgroundColor: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)',
              color: '#f87171', fontSize: '0.8rem',
            }}>
              {error}
              <span
                style={{ marginLeft: 8, cursor: 'pointer', fontWeight: 700 }}
                onClick={() => setError(null)}
              >
                x
              </span>
            </div>
          )}

          {/* Add single host */}
          <div style={{
            display: 'flex', gap: 6, alignItems: 'flex-end', flexWrap: 'wrap',
            marginBottom: '0.75rem', padding: '0.75rem',
            borderRadius: 8, backgroundColor: DS.raised,
          }}>
            <div style={{ flex: '1 1 140px' }}>
              <label style={{ fontSize: '0.7rem', color: DS.txtS, display: 'block', marginBottom: 3 }}>Host / IP</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                placeholder="10.0.1.5"
                value={addHost}
                onChange={e => setAddHost(e.target.value)}
              />
            </div>
            <div style={{ flex: '0 0 70px' }}>
              <label style={{ fontSize: '0.7rem', color: DS.txtS, display: 'block', marginBottom: 3 }}>Port</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                value={addPort}
                onChange={e => setAddPort(e.target.value)}
              />
            </div>
            <div style={{ flex: '1 1 120px' }}>
              <label style={{ fontSize: '0.7rem', color: DS.txtS, display: 'block', marginBottom: 3 }}>Name (opt.)</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                placeholder="web-01"
                value={addName}
                onChange={e => setAddName(e.target.value)}
              />
            </div>
            <div style={{ flex: '1 1 120px' }}>
              <label style={{ fontSize: '0.7rem', color: DS.txtS, display: 'block', marginBottom: 3 }}>Credential</label>
              <select
                style={{ ...inputStyle, width: '100%' }}
                value={addCred}
                onChange={e => setAddCred(e.target.value)}
              >
                <option value="">Auto (try all)</option>
                {credentials.map(c => (
                  <option key={c.id} value={c.name}>{c.name}</option>
                ))}
              </select>
            </div>
            <button
              style={{ ...btnPrimary, opacity: adding ? 0.6 : 1 }}
              onClick={handleAddHost}
              disabled={adding || !addHost.trim()}
            >
              {adding ? 'Adding...' : 'Add Host'}
            </button>
          </div>

          {/* CIDR range */}
          <div style={{
            display: 'flex', gap: 6, alignItems: 'flex-end', flexWrap: 'wrap',
            marginBottom: '0.75rem', padding: '0.75rem',
            borderRadius: 8, backgroundColor: DS.raised,
          }}>
            <div style={{ flex: '1 1 150px' }}>
              <label style={{ fontSize: '0.7rem', color: DS.txtS, display: 'block', marginBottom: 3 }}>CIDR Range</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                placeholder="10.0.1.0/24"
                value={cidr}
                onChange={e => setCidr(e.target.value)}
              />
            </div>
            <div style={{ flex: '0 0 70px' }}>
              <label style={{ fontSize: '0.7rem', color: DS.txtS, display: 'block', marginBottom: 3 }}>Port</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                value={cidrPort}
                onChange={e => setCidrPort(e.target.value)}
              />
            </div>
            <div style={{ flex: '1 1 120px' }}>
              <label style={{ fontSize: '0.7rem', color: DS.txtS, display: 'block', marginBottom: 3 }}>Credential</label>
              <select
                style={{ ...inputStyle, width: '100%' }}
                value={cidrCred}
                onChange={e => setCidrCred(e.target.value)}
              >
                <option value="">Auto (try all)</option>
                {credentials.map(c => (
                  <option key={c.id} value={c.name}>{c.name}</option>
                ))}
              </select>
            </div>
            <button
              style={{ ...btnPrimary, opacity: cidrExpanding ? 0.6 : 1 }}
              onClick={handleCidrExpand}
              disabled={cidrExpanding || !cidr.trim()}
            >
              {cidrExpanding ? 'Expanding...' : 'Expand CIDR'}
            </button>
          </div>
          {cidrResult && (
            <div style={{
              padding: '6px 10px', borderRadius: 6, marginBottom: '0.75rem',
              backgroundColor: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.3)',
              color: '#4ade80', fontSize: '0.78rem',
            }}>
              {cidrResult}
            </div>
          )}

          {/* Filters + bulk actions */}
          <div style={{
            display: 'flex', gap: 8, alignItems: 'center', marginBottom: '0.75rem',
            flexWrap: 'wrap',
          }}>
            <select
              style={{ ...inputStyle, width: 'auto', fontSize: '0.78rem' }}
              value={statusFilter}
              onChange={e => setStatusFilter(e.target.value)}
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="port_open">Port Open</option>
              <option value="port_closed">Port Closed</option>
              <option value="active">Active</option>
              <option value="auth_failed">Auth Failed</option>
              <option value="unreachable">Unreachable</option>
            </select>
            {pendingCount > 0 && (
              <button style={btnSecondary} onClick={handleApproveAll}>
                Approve All Pending ({pendingCount})
              </button>
            )}
            <button
              style={{ ...btnSecondary, marginLeft: 'auto' }}
              onClick={load}
              disabled={loading}
            >
              Refresh
            </button>
          </div>

          {/* CIDR group chips */}
          {cidrGroups.length > 0 && (
            <div style={{
              display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: '0.75rem',
            }}>
              {cidrGroups.map(g => {
                const count = targets.filter(t => t.cidr_group === g).length
                return (
                  <span key={g} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 6,
                    padding: '3px 10px', borderRadius: 5,
                    backgroundColor: DS.raised, border: `1px solid ${DS.border}`,
                    fontSize: '0.72rem', color: DS.txtM,
                  }}>
                    {g} ({count})
                    <span
                      style={{ cursor: 'pointer', color: '#f87171', fontWeight: 700, fontSize: '0.75rem' }}
                      title={`Delete all ${count} targets from ${g}`}
                      onClick={() => handleDeleteCidrGroup(g)}
                    >
                      x
                    </span>
                  </span>
                )
              })}
            </div>
          )}

          {/* Target table */}
          {loading ? (
            <div style={{ textAlign: 'center', padding: '2rem', color: DS.txtS, fontSize: '0.82rem' }}>
              Loading targets...
            </div>
          ) : targets.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '2rem', color: DS.txtS, fontSize: '0.82rem' }}>
              No SSH targets configured. Add a host or expand a CIDR range above.
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{
                width: '100%', borderCollapse: 'collapse',
                fontSize: '0.78rem', color: DS.txtP,
              }}>
                <thead>
                  <tr style={{ borderBottom: `1px solid ${DS.border}` }}>
                    {['Name', 'Host', 'Port', 'Status', 'Credential', 'Source', 'Last Probe', 'Error', ''].map(h => (
                      <th key={h} style={{
                        textAlign: 'left', padding: '6px 8px',
                        fontSize: '0.7rem', color: DS.txtS, fontWeight: 600,
                      }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {targets.map(t => (
                    <tr key={t.id} style={{ borderBottom: `1px solid ${DS.border}22` }}>
                      <td style={{ padding: '6px 8px' }}>
                        {t.name || <span style={{ color: DS.txtS }}>—</span>}
                      </td>
                      <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontSize: '0.76rem' }}>
                        {t.host}
                      </td>
                      <td style={{ padding: '6px 8px' }}>{t.port}</td>
                      <td style={{ padding: '6px 8px' }}><StatusBadge status={t.status} /></td>
                      <td style={{ padding: '6px 8px', fontSize: '0.74rem', color: DS.txtM }}>
                        {t.matched_credential || t.credential_name || <span style={{ color: DS.txtS }}>auto</span>}
                      </td>
                      <td style={{ padding: '6px 8px', fontSize: '0.72rem', color: DS.txtS }}>
                        {t.source}
                      </td>
                      <td style={{ padding: '6px 8px', fontSize: '0.72rem', color: DS.txtS }}>
                        {t.last_probe_at ? new Date(t.last_probe_at).toLocaleString() : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', fontSize: '0.72rem', color: '#f87171', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {t.probe_error ? (
                          <span title={t.probe_error}>{t.probe_error}</span>
                        ) : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', whiteSpace: 'nowrap' }}>
                        <div style={{ display: 'flex', gap: 4 }}>
                          {t.status === 'pending' && (
                            <button style={btnSecondary} onClick={() => handleApprove(t.id)}>
                              Approve
                            </button>
                          )}
                          <button style={btnDanger} onClick={() => handleDelete(t.id)}>
                            Del
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
