import { useState, useEffect, useCallback } from 'react'
import {
  getWatcherTargets, createWatcherTarget, createWatcherTargetsCidr,
  deleteWatcherTarget, deleteWatcherTargetsCidr,
  approveWatcherTarget, approveAllWatcherTargets, approveCidrGroup,
  getSSHCredentials,
  WatcherTarget, SSHCredential,
} from '../services/api'
import {
  IconTrash, IconCheck, IconChevronDown, IconChevronRight,
  IconRefresh, IconPlus, IconNetwork, IconRadar, IconX,
} from './icons'

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

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  pending:      { bg: 'rgba(107,114,128,0.15)', color: '#9ca3af' },
  approved:     { bg: 'rgba(59,130,246,0.1)',   color: '#60a5fa' },
  port_closed:  { bg: 'rgba(75,85,99,0.15)',    color: '#6b7280' },
  port_open:    { bg: 'rgba(234,179,8,0.1)',    color: '#d4a017' },
  active:       { bg: 'rgba(34,197,94,0.1)',    color: '#22c55e' },
  auth_failed:  { bg: 'rgba(239,68,68,0.1)',    color: '#ef4444' },
  unreachable:  { bg: 'rgba(249,115,22,0.1)',   color: '#f97316' },
}

function StatusBadge({ status }: { status: string }) {
  const s = STATUS_COLORS[status] || STATUS_COLORS.pending
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 4,
      fontSize: '0.7rem', fontWeight: 600, letterSpacing: '0.02em',
      backgroundColor: s.bg, color: s.color,
    }}>
      {status.replace('_', ' ')}
    </span>
  )
}

const iconBtn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
  width: 28, height: 28, borderRadius: 6,
  border: 'none', backgroundColor: 'transparent',
  cursor: 'pointer', color: DS.txtS, transition: 'color 0.15s, background 0.15s',
}

function TargetRow({ t, onApprove, onDelete }: {
  t: WatcherTarget
  onApprove: (id: string) => void
  onDelete: (id: string) => void
}) {
  const displayName = t.name || t.host
  return (
    <tr style={{ borderBottom: `1px solid ${DS.border}22` }}>
      <td style={{ padding: '5px 8px', fontSize: '0.78rem', color: DS.txtP }}>
        {displayName}
      </td>
      <td style={{ padding: '5px 8px', fontFamily: 'monospace', fontSize: '0.76rem', color: DS.txtM }}>
        {t.host}
      </td>
      <td style={{ padding: '5px 8px', fontSize: '0.78rem', color: DS.txtS }}>{t.port}</td>
      <td style={{ padding: '5px 8px' }}><StatusBadge status={t.status} /></td>
      <td style={{ padding: '5px 8px', fontSize: '0.74rem', color: DS.txtS }}>
        {t.matched_credential || t.credential_name || '—'}
      </td>
      <td style={{ padding: '5px 8px', fontSize: '0.72rem', color: DS.txtS }}>
        {t.last_probe_at ? new Date(t.last_probe_at).toLocaleString() : '—'}
      </td>
      <td style={{ padding: '5px 8px', fontSize: '0.72rem', color: '#ef4444', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {t.probe_error ? <span title={t.probe_error}>{t.probe_error}</span> : ''}
      </td>
      <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>
        <div style={{ display: 'flex', gap: 2, alignItems: 'center' }}>
          {t.status === 'pending' && (
            <button
              style={iconBtn}
              title="Approve"
              onClick={() => onApprove(t.id)}
              onMouseEnter={e => { e.currentTarget.style.color = '#22c55e'; e.currentTarget.style.backgroundColor = 'rgba(34,197,94,0.1)' }}
              onMouseLeave={e => { e.currentTarget.style.color = DS.txtS; e.currentTarget.style.backgroundColor = 'transparent' }}
            >
              <IconCheck size={15} />
            </button>
          )}
          <button
            style={iconBtn}
            title="Delete target"
            onClick={() => onDelete(t.id)}
            onMouseEnter={e => { e.currentTarget.style.color = '#ef4444'; e.currentTarget.style.backgroundColor = 'rgba(239,68,68,0.1)' }}
            onMouseLeave={e => { e.currentTarget.style.color = DS.txtS; e.currentTarget.style.backgroundColor = 'transparent' }}
          >
            <IconTrash size={14} />
          </button>
        </div>
      </td>
    </tr>
  )
}

const TABLE_HEADERS = ['Name', 'Host', 'Port', 'Status', 'Credential', 'Last Probe', 'Error', '']

function TargetTableHeader() {
  return (
    <thead>
      <tr style={{ borderBottom: `1px solid ${DS.border}` }}>
        {TABLE_HEADERS.map(h => (
          <th key={h} style={{
            textAlign: 'left', padding: '5px 8px',
            fontSize: '0.68rem', color: DS.txtS, fontWeight: 600,
            textTransform: 'uppercase', letterSpacing: '0.04em',
          }}>
            {h}
          </th>
        ))}
      </tr>
    </thead>
  )
}

interface CidrGroupData {
  cidr: string
  rangeTarget: WatcherTarget | null
  children: WatcherTarget[]
  activeCount: number
}

function CidrGroupSection({ group, onApproveRange, onDeleteGroup, onApproveTarget, onDeleteTarget }: {
  group: CidrGroupData
  onApproveRange: (cidr: string) => void
  onDeleteGroup: (cidr: string) => void
  onApproveTarget: (id: string) => void
  onDeleteTarget: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const rangeStatus = group.rangeTarget?.status || 'pending'
  const isPending = rangeStatus === 'pending'

  return (
    <div style={{
      marginBottom: 6, borderRadius: 8,
      border: `1px solid ${DS.border}`,
      backgroundColor: DS.surface,
      overflow: 'hidden',
    }}>
      <div
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '7px 10px', cursor: 'pointer', userSelect: 'none',
        }}
        onClick={() => setExpanded(e => !e)}
      >
        <span style={{ color: DS.txtS, flexShrink: 0 }}>
          {expanded
            ? <IconChevronDown size={14} />
            : <IconChevronRight size={14} />}
        </span>
        <IconNetwork size={14} style={{ color: DS.txtS, flexShrink: 0 }} />
        <span style={{
          fontFamily: 'monospace', fontSize: '0.8rem',
          fontWeight: 600, color: DS.txtP,
        }}>
          {group.cidr}
        </span>
        <StatusBadge status={rangeStatus} />
        {group.activeCount > 0 && (
          <span style={{
            fontSize: '0.7rem', fontWeight: 600,
            color: '#22c55e',
          }}>
            {group.activeCount} active
          </span>
        )}
        {group.children.length > 0 && (
          <span style={{
            fontSize: '0.7rem', color: DS.txtS,
          }}>
            {group.children.length} discovered
          </span>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 4, alignItems: 'center' }}
             onClick={e => e.stopPropagation()}>
          {isPending && (
            <button
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '4px 10px', borderRadius: 5,
                border: `1px solid rgba(59,130,246,0.3)`,
                backgroundColor: 'rgba(59,130,246,0.08)',
                color: '#60a5fa', fontSize: '0.72rem', fontWeight: 600,
                cursor: 'pointer',
              }}
              onClick={() => onApproveRange(group.cidr)}
            >
              <IconRadar size={13} /> Scan
            </button>
          )}
          <button
            style={iconBtn}
            title={`Remove ${group.cidr} and all discovered hosts`}
            onClick={() => onDeleteGroup(group.cidr)}
            onMouseEnter={e => { e.currentTarget.style.color = '#ef4444'; e.currentTarget.style.backgroundColor = 'rgba(239,68,68,0.1)' }}
            onMouseLeave={e => { e.currentTarget.style.color = DS.txtS; e.currentTarget.style.backgroundColor = 'transparent' }}
          >
            <IconTrash size={14} />
          </button>
        </div>
      </div>

      {expanded && group.children.length > 0 && (
        <div style={{ padding: '0 6px 6px', overflowX: 'auto' }}>
          <table style={{
            width: '100%', borderCollapse: 'collapse',
            fontSize: '0.78rem', color: DS.txtP,
          }}>
            <TargetTableHeader />
            <tbody>
              {group.children.map(t => (
                <TargetRow
                  key={t.id} t={t}
                  onApprove={onApproveTarget}
                  onDelete={onDeleteTarget}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {expanded && group.children.length === 0 && (
        <div style={{
          padding: '10px 12px', textAlign: 'center',
          fontSize: '0.76rem', color: DS.txtS,
          borderTop: `1px solid ${DS.border}22`,
        }}>
          {isPending
            ? 'Approve this range to start scanning.'
            : 'No hosts discovered yet — waiting for next probe cycle.'}
        </div>
      )}
    </div>
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

  const [addHost, setAddHost] = useState('')
  const [addPort, setAddPort] = useState('22')
  const [addName, setAddName] = useState('')
  const [addCred, setAddCred] = useState('')
  const [adding, setAdding] = useState(false)

  const [cidr, setCidr] = useState('')
  const [cidrPort, setCidrPort] = useState('22')
  const [cidrCred, setCidrCred] = useState('')
  const [cidrAdding, setCidrAdding] = useState(false)

  const [collapsed, setCollapsed] = useState(true)

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

  const handleAddCidr = async () => {
    if (!cidr.trim()) return
    setCidrAdding(true)
    try {
      await createWatcherTargetsCidr(watcherId, {
        cidr: cidr.trim(),
        port: parseInt(cidrPort) || 22,
        credential_name: cidrCred || undefined,
      })
      setCidr('')
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to add CIDR range')
    } finally {
      setCidrAdding(false)
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

  const handleApproveCidr = async (cidrGroup: string) => {
    try {
      await approveCidrGroup(watcherId, cidrGroup)
      await load()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Failed to approve CIDR range')
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

  const standaloneTargets = targets.filter(t => !t.cidr_group)

  const cidrGroupMap = new Map<string, CidrGroupData>()
  for (const t of targets) {
    if (!t.cidr_group) continue
    let group = cidrGroupMap.get(t.cidr_group)
    if (!group) {
      group = { cidr: t.cidr_group, rangeTarget: null, children: [], activeCount: 0 }
      cidrGroupMap.set(t.cidr_group, group)
    }
    if (t.source === 'cidr_range') {
      group.rangeTarget = t
    } else {
      group.children.push(t)
      if (t.status === 'active') group.activeCount++
    }
  }
  const cidrGroups = [...cidrGroupMap.values()]

  const pendingCount = targets.filter(t => t.status === 'pending').length
  const activeCount = targets.filter(t => t.status === 'active').length

  return (
    <div style={{
      marginTop: '1.25rem', marginBottom: '1.25rem', borderRadius: 10,
      border: `1px solid ${DS.border}`, backgroundColor: DS.surface,
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '10px 14px',
          cursor: 'pointer', userSelect: 'none',
        }}
        onClick={() => setCollapsed(c => !c)}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ color: DS.txtS }}>
            {collapsed ? <IconChevronRight size={14} /> : <IconChevronDown size={14} />}
          </span>
          <span style={{ fontSize: '0.85rem', fontWeight: 600, color: DS.txtP }}>
            SSH Targets
          </span>
          <span style={{
            fontSize: '0.7rem', color: DS.txtS, padding: '1px 7px',
            borderRadius: 10, backgroundColor: DS.raised,
          }}>
            {targets.length}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {activeCount > 0 && (
            <span style={{ fontSize: '0.7rem', color: '#22c55e', fontWeight: 600 }}>
              {activeCount} active
            </span>
          )}
          {pendingCount > 0 && (
            <span style={{
              fontSize: '0.7rem', padding: '2px 8px', borderRadius: 10,
              backgroundColor: 'rgba(234,179,8,0.1)', color: '#d4a017', fontWeight: 600,
            }}>
              {pendingCount} pending
            </span>
          )}
        </div>
      </div>

      {!collapsed && (
        <div style={{ padding: '0 14px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          {error && (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '7px 10px', borderRadius: 6,
              backgroundColor: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
              color: '#ef4444', fontSize: '0.78rem',
            }}>
              <span>{error}</span>
              <button
                style={{ ...iconBtn, width: 22, height: 22 }}
                onClick={() => setError(null)}
              >
                <IconX size={13} />
              </button>
            </div>
          )}

          {/* Add single host */}
          <div style={{
            display: 'flex', gap: 6, alignItems: 'flex-end', flexWrap: 'wrap',
            padding: '10px 12px',
            borderRadius: 8, border: `1px solid ${DS.border}`,
          }}>
            <div style={{ flex: '1 1 140px' }}>
              <label style={{ fontSize: '0.68rem', color: DS.txtS, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Host / IP</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                placeholder="10.0.1.5"
                value={addHost}
                onChange={e => setAddHost(e.target.value)}
              />
            </div>
            <div style={{ flex: '0 0 65px' }}>
              <label style={{ fontSize: '0.68rem', color: DS.txtS, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Port</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                value={addPort}
                onChange={e => setAddPort(e.target.value)}
              />
            </div>
            <div style={{ flex: '1 1 120px' }}>
              <label style={{ fontSize: '0.68rem', color: DS.txtS, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Name</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                placeholder="web-01"
                value={addName}
                onChange={e => setAddName(e.target.value)}
              />
            </div>
            <div style={{ flex: '1 1 120px' }}>
              <label style={{ fontSize: '0.68rem', color: DS.txtS, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Credential</label>
              <select
                style={{ ...inputStyle, width: '100%' }}
                value={addCred}
                onChange={e => setAddCred(e.target.value)}
              >
                <option value="">Auto</option>
                {credentials.map(c => (
                  <option key={c.id} value={c.name}>{c.name}</option>
                ))}
              </select>
            </div>
            <button
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '7px 14px', borderRadius: 6,
                border: `1px solid ${DS.accent}`,
                backgroundColor: DS.accent,
                color: '#fff', fontSize: '0.78rem', fontWeight: 600,
                cursor: 'pointer', opacity: adding ? 0.6 : 1,
              }}
              onClick={handleAddHost}
              disabled={adding || !addHost.trim()}
            >
              <IconPlus size={14} />
              {adding ? 'Adding...' : 'Add Host'}
            </button>
          </div>

          {/* CIDR range */}
          <div style={{
            display: 'flex', gap: 6, alignItems: 'flex-end', flexWrap: 'wrap',
            padding: '10px 12px',
            borderRadius: 8, border: `1px solid ${DS.border}`,
          }}>
            <div style={{ flex: '1 1 150px' }}>
              <label style={{ fontSize: '0.68rem', color: DS.txtS, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>CIDR Range</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                placeholder="10.0.1.0/24"
                value={cidr}
                onChange={e => setCidr(e.target.value)}
              />
            </div>
            <div style={{ flex: '0 0 65px' }}>
              <label style={{ fontSize: '0.68rem', color: DS.txtS, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Port</label>
              <input
                style={{ ...inputStyle, width: '100%' }}
                value={cidrPort}
                onChange={e => setCidrPort(e.target.value)}
              />
            </div>
            <div style={{ flex: '1 1 120px' }}>
              <label style={{ fontSize: '0.68rem', color: DS.txtS, display: 'block', marginBottom: 3, textTransform: 'uppercase', letterSpacing: '0.03em' }}>Credential</label>
              <select
                style={{ ...inputStyle, width: '100%' }}
                value={cidrCred}
                onChange={e => setCidrCred(e.target.value)}
              >
                <option value="">Auto</option>
                {credentials.map(c => (
                  <option key={c.id} value={c.name}>{c.name}</option>
                ))}
              </select>
            </div>
            <button
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '7px 14px', borderRadius: 6,
                border: `1px solid ${DS.accent}`,
                backgroundColor: DS.accent,
                color: '#fff', fontSize: '0.78rem', fontWeight: 600,
                cursor: 'pointer', opacity: cidrAdding ? 0.6 : 1,
              }}
              onClick={handleAddCidr}
              disabled={cidrAdding || !cidr.trim()}
            >
              <IconNetwork size={14} />
              {cidrAdding ? 'Adding...' : 'Add Range'}
            </button>
          </div>

          {/* Filters + bulk actions */}
          <div style={{
            display: 'flex', gap: 8, alignItems: 'center',
            flexWrap: 'wrap',
          }}>
            <select
              style={{ ...inputStyle, width: 'auto', fontSize: '0.76rem', backgroundColor: DS.surface }}
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
              <button
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                  padding: '5px 10px', borderRadius: 5,
                  border: `1px solid ${DS.border}`,
                  backgroundColor: 'transparent',
                  color: DS.txtM, fontSize: '0.74rem', fontWeight: 500,
                  cursor: 'pointer',
                }}
                onClick={handleApproveAll}
              >
                <IconCheck size={13} />
                Approve All ({pendingCount})
              </button>
            )}
            <button
              style={{
                ...iconBtn, marginLeft: 'auto',
                width: 'auto', padding: '4px 8px', gap: 4,
                display: 'inline-flex',
              }}
              onClick={load}
              disabled={loading}
              title="Refresh"
              onMouseEnter={e => { e.currentTarget.style.color = DS.accent; e.currentTarget.style.backgroundColor = 'rgba(59,130,246,0.1)' }}
              onMouseLeave={e => { e.currentTarget.style.color = DS.txtS; e.currentTarget.style.backgroundColor = 'transparent' }}
            >
              <IconRefresh size={14} />
            </button>
          </div>

          {/* CIDR group sections */}
          {cidrGroups.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
              {cidrGroups.map(g => (
                <CidrGroupSection
                  key={g.cidr}
                  group={g}
                  onApproveRange={handleApproveCidr}
                  onDeleteGroup={handleDeleteCidrGroup}
                  onApproveTarget={handleApprove}
                  onDeleteTarget={handleDelete}
                />
              ))}
            </div>
          )}

          {/* Standalone target table */}
          {loading ? (
            <div style={{ textAlign: 'center', padding: '1.5rem', color: DS.txtS, fontSize: '0.8rem' }}>
              Loading targets...
            </div>
          ) : standaloneTargets.length === 0 && cidrGroups.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '1.5rem', color: DS.txtS, fontSize: '0.8rem' }}>
              No SSH targets configured. Add a host or CIDR range above.
            </div>
          ) : standaloneTargets.length > 0 ? (
            <div style={{ overflowX: 'auto' }}>
              <table style={{
                width: '100%', borderCollapse: 'collapse',
                fontSize: '0.78rem', color: DS.txtP,
              }}>
                <TargetTableHeader />
                <tbody>
                  {standaloneTargets.map(t => (
                    <TargetRow
                      key={t.id} t={t}
                      onApprove={handleApprove}
                      onDelete={handleDelete}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}
