import { useState, useEffect } from 'react'
import { getConnector, saveConnectorConfig, testConnector, getConnectorSyncLogs } from '../services/api'
import { ConnectorDef, SyncLog } from '../types'
import './ConnectorConfig.css'
import { parseUTC } from '../utils/dateFormatter'

interface ConnectorConfigProps {
  connectorId: string
  onClose: () => void
  darkMode?: boolean
}

export default function ConnectorConfig({ connectorId, onClose, darkMode: _darkMode }: ConnectorConfigProps) {
  const [detail, setDetail]     = useState<ConnectorDef | null>(null)
  const [baseUrl, setBaseUrl]   = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [interval, setInterval] = useState(0)
  const [enabled, setEnabled]   = useState(true)
  const [saving, setSaving]     = useState(false)
  const [testing, setTesting]   = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string; latency?: number } | null>(null)
  const [saveMsg, setSaveMsg]   = useState('')
  const [logs, setLogs]         = useState<SyncLog[]>([])
  const [tab, setTab]           = useState<'config' | 'incident_sync' | 'logs'>('config')

  // Incident sync settings
  const [syncEnabled,        setSyncEnabled]        = useState(false)
  const [autoCreate,         setAutoCreate]         = useState(true)
  const [includeAiSummary,   setIncludeAiSummary]   = useState(true)
  const [appendAgentNotes,   setAppendAgentNotes]   = useState(true)
  const [platformUrl,        setPlatformUrl]        = useState('http://localhost:3000')
  const ALL_STATES = ['in_progress', 'waiting_approval', 'resolved', 'failed', 'rejected']
  const [updateOnStates, setUpdateOnStates] = useState<string[]>(['in_progress', 'waiting_approval', 'resolved', 'failed', 'rejected'])

  useEffect(() => {
    load()
  }, [connectorId])

  const load = async () => {
    try {
      const res  = await getConnector(connectorId)
      const data = res.data
      setDetail(data)
      setBaseUrl(data.base_url || '')
      setUsername(data.username || '')
      setEnabled(data.enabled)
      setInterval(data.sync_interval_min)
      const is = data.incident_sync
      if (is) {
        setSyncEnabled(is.enabled ?? false)
        setAutoCreate(is.auto_create ?? true)
        setIncludeAiSummary(is.include_ai_summary ?? true)
        setAppendAgentNotes(is.append_agent_notes ?? true)
        setPlatformUrl(is.platform_url ?? 'http://localhost:3000')
        setUpdateOnStates(is.auto_update_on_states ?? ALL_STATES)
      }
    } catch {}
    try {
      const logRes = await getConnectorSyncLogs(connectorId, 20)
      setLogs(logRes.data.items)
    } catch {}
  }

  const handleSave = async () => {
    if (!baseUrl || !username) {
      setSaveMsg('⚠ Base URL and username are required')
      return
    }
    if (!password && !detail?.configured) {
      setSaveMsg('⚠ Password is required for initial setup')
      return
    }
    setSaving(true)
    setSaveMsg('')
    try {
      await saveConnectorConfig(connectorId, {
        base_url:          baseUrl,
        username,
        password,
        sync_interval_min: interval,
        enabled,
        incident_sync: {
          enabled:               syncEnabled,
          auto_create:           autoCreate,
          auto_update_on_states: updateOnStates,
          include_ai_summary:    includeAiSummary,
          append_agent_notes:    appendAgentNotes,
          platform_url:          platformUrl,
        },
      })
      setSaveMsg('✓ Configuration saved')
      setPassword('')
      await load()
    } catch (e: any) {
      setSaveMsg(`✗ ${e?.response?.data?.detail || 'Save failed'}`)
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await testConnector(connectorId)
      setTestResult({ ok: res.data.ok, msg: res.data.message, latency: res.data.latency_ms })
    } catch (e: any) {
      setTestResult({ ok: false, msg: e?.response?.data?.detail || 'Test failed' })
    } finally {
      setTesting(false)
    }
  }

  const inputStyle = {
    background: '#1a1f2e',
    border:     '1px solid #3d4557',
    color:      '#e8eef5',
    borderRadius: '6px',
    padding:    '0.5rem 0.75rem',
    fontSize:   '0.85rem',
    width:      '100%',
    outline:    'none',
  } as const

  return (
    <div className="cc-overlay" onClick={onClose}>
      <div className="cc-drawer" onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="cc-head">
          <div>
            <h3 className="cc-title">{detail?.display_name ?? connectorId} — Settings</h3>
            <p className="cc-subtitle">Configure connection credentials and sync schedule</p>
          </div>
          <button className="cc-close" onClick={onClose}>✕</button>
        </div>

        {/* Tabs */}
        <div className="cc-tabs">
          <button className={`cc-tab ${tab === 'config' ? 'active' : ''}`} onClick={() => setTab('config')}>Configuration</button>
          <button className={`cc-tab ${tab === 'incident_sync' ? 'active' : ''}`} onClick={() => setTab('incident_sync')}>Incident Sync</button>
          <button className={`cc-tab ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>Sync History</button>
        </div>

        {tab === 'config' && (
          <div className="cc-body">

            {/* Capabilities info */}
            {detail?.capabilities && (
              <div className="cc-caps-row">
                {detail.capabilities.map(c => (
                  <span key={c} className="cc-cap">{c.replace(/_/g, ' ')}</span>
                ))}
              </div>
            )}

            {/* Fields */}
            <div className="cc-field-group">
              <label className="cc-label">Instance URL</label>
              <input
                style={inputStyle}
                type="text"
                placeholder="https://your-instance.service-now.com"
                value={baseUrl}
                onChange={e => setBaseUrl(e.target.value)}
                autoComplete="off"
              />
              <p className="cc-hint">Base URL of your ServiceNow instance — no trailing slash</p>
            </div>

            <div className="cc-field-group">
              <label className="cc-label">Username</label>
              <input
                style={inputStyle}
                type="text"
                placeholder="service-account-username"
                value={username}
                onChange={e => setUsername(e.target.value)}
                autoComplete="username"
              />
            </div>

            <div className="cc-field-group">
              <label className="cc-label">Password</label>
              <input
                style={inputStyle}
                type="password"
                placeholder={detail?.configured ? '(leave blank to keep existing)' : 'Password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                autoComplete="current-password"
              />
              <p className="cc-hint">Stored server-side. Use a read/write service account.</p>
            </div>

            <div className="cc-row-2">
              <div className="cc-field-group">
                <label className="cc-label">Sync Interval (minutes)</label>
                <select
                  style={{ ...inputStyle, width: 'auto' }}
                  value={interval}
                  onChange={e => setInterval(parseInt(e.target.value))}
                >
                  <option value={0}>Manual only</option>
                  <option value={60}>Every hour</option>
                  <option value={360}>Every 6 hours</option>
                  <option value={720}>Every 12 hours</option>
                  <option value={1440}>Daily</option>
                </select>
              </div>

              <div className="cc-field-group">
                <label className="cc-label">Status</label>
                <label className="cc-toggle">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={e => setEnabled(e.target.checked)}
                  />
                  <span className="cc-toggle-track" />
                  <span className="cc-toggle-label">{enabled ? 'Enabled' : 'Disabled'}</span>
                </label>
              </div>
            </div>

            {/* Test result */}
            {testResult && (
              <div className="cc-test-result" style={{ color: testResult.ok ? '#6ee7b7' : '#fca5a5' }}>
                {testResult.ok ? '✓' : '✗'} {testResult.msg}
                {testResult.latency && <span className="cc-latency">{testResult.latency}ms</span>}
              </div>
            )}

            {/* Save message */}
            {saveMsg && (
              <p className="cc-save-msg" style={{ color: saveMsg.startsWith('✓') ? '#6ee7b7' : saveMsg.startsWith('⚠') ? '#fbbf24' : '#fca5a5' }}>
                {saveMsg}
              </p>
            )}

            {/* Actions */}
            <div className="cc-actions">
              <button className="cc-btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save Configuration'}
              </button>
              {detail?.configured && (
                <button className="cc-btn-secondary" onClick={handleTest} disabled={testing}>
                  {testing ? 'Testing…' : 'Test Connection'}
                </button>
              )}
              <button className="cc-btn-ghost" onClick={onClose}>Cancel</button>
            </div>

            <p className="cc-security-note">
              Credentials are stored in the platform database.
              Use a dedicated service account with read-only CMDB + read-write Incident table access.
            </p>
          </div>
        )}

        {tab === 'incident_sync' && (
          <div className="cc-body">
            <p className="cc-hint" style={{ marginBottom: '1rem' }}>
              When enabled, platform incidents are automatically created and updated in ServiceNow
              as they move through their lifecycle.
            </p>

            {/* Master enable */}
            <div className="cc-field-group">
              <label className="cc-label">Incident Sync</label>
              <label className="cc-toggle">
                <input type="checkbox" checked={syncEnabled} onChange={e => setSyncEnabled(e.target.checked)} />
                <span className="cc-toggle-track" />
                <span className="cc-toggle-label">{syncEnabled ? 'Enabled' : 'Disabled'}</span>
              </label>
            </div>

            {syncEnabled && (<>
              {/* Auto-create */}
              <div className="cc-field-group">
                <label className="cc-label">Auto-create on new incident</label>
                <label className="cc-toggle">
                  <input type="checkbox" checked={autoCreate} onChange={e => setAutoCreate(e.target.checked)} />
                  <span className="cc-toggle-track" />
                  <span className="cc-toggle-label">{autoCreate ? 'Yes' : 'No'}</span>
                </label>
                <p className="cc-hint">Creates a ServiceNow incident as soon as a platform incident opens.</p>
              </div>

              {/* States that trigger update */}
              <div className="cc-field-group">
                <label className="cc-label">Auto-update on lifecycle states</label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
                  {ALL_STATES.map(s => (
                    <label key={s} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.84rem', color: '#cbd5e1', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={updateOnStates.includes(s)}
                        onChange={e => setUpdateOnStates(prev =>
                          e.target.checked ? [...prev, s] : prev.filter(x => x !== s)
                        )}
                      />
                      {s.replace(/_/g, ' ')}
                    </label>
                  ))}
                </div>
                <p className="cc-hint">Updates the SN incident whenever the platform incident enters one of these states.</p>
              </div>

              {/* AI summary */}
              <div className="cc-field-group">
                <label className="cc-label">Include AI summary in description</label>
                <label className="cc-toggle">
                  <input type="checkbox" checked={includeAiSummary} onChange={e => setIncludeAiSummary(e.target.checked)} />
                  <span className="cc-toggle-track" />
                  <span className="cc-toggle-label">{includeAiSummary ? 'Yes' : 'No'}</span>
                </label>
                <p className="cc-hint">Writes the AI-generated executive summary into the SN incident description field.</p>
              </div>

              {/* Agent notes */}
              <div className="cc-field-group">
                <label className="cc-label">Append agent work notes</label>
                <label className="cc-toggle">
                  <input type="checkbox" checked={appendAgentNotes} onChange={e => setAppendAgentNotes(e.target.checked)} />
                  <span className="cc-toggle-track" />
                  <span className="cc-toggle-label">{appendAgentNotes ? 'Yes' : 'No'}</span>
                </label>
                <p className="cc-hint">Appends the last 10 agent trace steps to SN work_notes on each update.</p>
              </div>

              {/* Platform URL */}
              <div className="cc-field-group">
                <label className="cc-label">Platform URL</label>
                <input
                  style={inputStyle}
                  type="url"
                  placeholder="https://platform.example.com"
                  value={platformUrl}
                  onChange={e => setPlatformUrl(e.target.value)}
                />
                <p className="cc-hint">Base URL included in SN work notes as a back-link to this platform.</p>
              </div>
            </>)}

            {/* Lifecycle → SN state mapping preview */}
            <div className="cc-field-group" style={{ marginTop: '1rem' }}>
              <label className="cc-label">Lifecycle → ServiceNow state mapping</label>
              <table className="cc-log-table" style={{ marginTop: 6 }}>
                <thead><tr><th>Platform state</th><th>SN state</th></tr></thead>
                <tbody>
                  {[
                    ['open',             'New (1)'],
                    ['in_progress',      'In Progress (2)'],
                    ['waiting_approval', 'In Progress (2)'],
                    ['resolved',         'Resolved (6)'],
                    ['failed',           'In Progress (2)'],
                    ['rejected',         'Closed (7)'],
                  ].map(([platform, sn]) => (
                    <tr key={platform}>
                      <td style={{ color: '#94a3b8' }}>{platform.replace(/_/g, ' ')}</td>
                      <td style={{ color: '#6ee7b7' }}>{sn}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {saveMsg && (
              <p className="cc-save-msg" style={{ color: saveMsg.startsWith('✓') ? '#6ee7b7' : '#fca5a5' }}>
                {saveMsg}
              </p>
            )}

            <div className="cc-actions">
              <button className="cc-btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save Configuration'}
              </button>
              <button className="cc-btn-ghost" onClick={onClose}>Cancel</button>
            </div>
          </div>
        )}

        {tab === 'logs' && (
          <div className="cc-body">
            {logs.length === 0 ? (
              <p className="cc-empty">No sync history yet. Run a sync to see logs here.</p>
            ) : (
              <table className="cc-log-table">
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Status</th>
                    <th>Records</th>
                    <th>Duration</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map(l => {
                    const duration = l.finished_at
                      ? Math.round((new Date(l.finished_at).getTime() - new Date(l.started_at).getTime()) / 1000)
                      : null
                    return (
                      <tr key={l.id}>
                        <td>{parseUTC(l.started_at).toLocaleString()}</td>
                        <td>
                          <span className="cc-log-status" style={{
                            color: l.status === 'ok' ? '#6ee7b7' : l.status === 'partial' ? '#fbbf24' : '#fca5a5'
                          }}>
                            {l.status}
                          </span>
                        </td>
                        <td>{l.records_pulled.toLocaleString()}</td>
                        <td>{duration != null ? `${duration}s` : '—'}</td>
                        <td className="cc-log-error">{l.error_message || '—'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
