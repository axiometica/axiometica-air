import { useState, useEffect } from 'react'
import { getConnector, testConnector, saveSplunkConfig } from '../services/api'
import { ConnectorDef } from '../types'
import './ConnectorConfig.css'   // shared drawer styles

interface Props {
  onClose: () => void
}

type Tab = 'config' | 'webhook' | 'logs'

const INPUT_STYLE = {
  background: '#1a1f2e',
  border: '1px solid #3d4557',
  color: '#e8eef5',
  borderRadius: '6px',
  padding: '0.5rem 0.75rem',
  fontSize: '0.85rem',
  width: '100%',
  outline: 'none',
  boxSizing: 'border-box' as const,
}

const CODE_BOX: React.CSSProperties = {
  background: '#0f1419',
  border: '1px solid #3d4557',
  borderRadius: '6px',
  padding: '0.6rem 0.85rem',
  fontFamily: 'monospace',
  fontSize: '0.78rem',
  color: '#93c5fd',
  wordBreak: 'break-all',
  lineHeight: 1.6,
}

export default function SplunkConnectorConfig({ onClose }: Props) {
  const [detail, setDetail]       = useState<ConnectorDef | null>(null)
  const [tab, setTab]             = useState<Tab>('config')

  // Config fields
  const [baseUrl, setBaseUrl]                               = useState('')
  const [token, setToken]                                   = useState('')
  const [webhookSecret, setWebhookSecret]                   = useState('')
  const [clearSecret, setClearSecret]                       = useState(false)
  const [defaultCriticality, setDefaultCriticality]         = useState('warning')
  const [defaultEventType, setDefaultEventType]             = useState('unknown')
  const [enabled, setEnabled]                               = useState(true)
  const [allowAutoRemediation, setAllowAutoRemediation]     = useState(false)

  // UI state
  const [saving, setSaving]         = useState(false)
  const [saveMsg, setSaveMsg]       = useState('')
  const [testing, setTesting]       = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string; latency?: number } | null>(null)
  const [copied, setCopied]         = useState(false)

  const webhookUrl = `${window.location.origin}/api/connectors/splunk/webhook`

  useEffect(() => { load() }, [])

  const load = async () => {
    try {
      const res = await getConnector('splunk')
      const d = res.data
      setDetail(d)
      setBaseUrl(d.base_url || '')
      setEnabled(d.enabled)
      setDefaultCriticality(d.default_criticality || 'warning')
      setDefaultEventType(d.default_event_type || 'unknown')
      setAllowAutoRemediation(d.allow_auto_remediation ?? false)
      // token is never returned; webhook_secret_set indicates if one is saved
    } catch {}
  }

  const handleSave = async () => {
    if (!baseUrl) { setSaveMsg('⚠ Splunk API URL is required'); return }
    if (!token && !detail?.configured) { setSaveMsg('⚠ API token is required for initial setup'); return }
    setSaving(true)
    setSaveMsg('')
    try {
      await saveSplunkConfig({
        base_url:               baseUrl,
        token,
        webhook_secret:         clearSecret ? '-' : (webhookSecret || ''),
        default_criticality:    defaultCriticality,
        default_event_type:     defaultEventType,
        enabled,
        allow_auto_remediation: allowAutoRemediation,
      })
      setSaveMsg('✓ Configuration saved')
      setToken('')
      setWebhookSecret('')
      setClearSecret(false)
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
      const res = await testConnector('splunk')
      setTestResult({ ok: res.data.ok, msg: res.data.message, latency: res.data.latency_ms })
    } catch (e: any) {
      setTestResult({ ok: false, msg: e?.response?.data?.detail || 'Test failed' })
    } finally {
      setTesting(false)
    }
  }

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(webhookUrl)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {}
  }

  return (
    <div className="cc-overlay" onClick={onClose}>
      <div className="cc-drawer" onClick={e => e.stopPropagation()}>

        {/* Header */}
        <div className="cc-head">
          <div>
            <h3 className="cc-title">Splunk — Settings</h3>
            <p className="cc-subtitle">Ingest Splunk saved-search alerts as monitoring events</p>
          </div>
          <button className="cc-close" onClick={onClose}>✕</button>
        </div>

        {/* Tabs */}
        <div className="cc-tabs">
          <button className={`cc-tab ${tab === 'config' ? 'active' : ''}`} onClick={() => setTab('config')}>
            Configuration
          </button>
          <button className={`cc-tab ${tab === 'webhook' ? 'active' : ''}`} onClick={() => setTab('webhook')}>
            Webhook Setup
          </button>
          <button className={`cc-tab ${tab === 'logs' ? 'active' : ''}`} onClick={() => setTab('logs')}>
            Status
          </button>
        </div>

        {/* ── Configuration tab ───────────────────────────────────────────── */}
        {tab === 'config' && (
          <div className="cc-body">

            <div className="cc-caps-row">
              <span className="cc-cap">Alert Ingest</span>
            </div>

            <div className="cc-field-group">
              <label className="cc-label">Splunk API URL</label>
              <input
                style={INPUT_STYLE}
                type="text"
                placeholder="https://splunk.example.com:8089"
                value={baseUrl}
                onChange={e => setBaseUrl(e.target.value)}
                autoComplete="off"
              />
              <p className="cc-hint">
                Management API URL — used only for the Test Connection call.
                Defaults to port 8089 for Splunk Enterprise.
              </p>
            </div>

            <div className="cc-field-group">
              <label className="cc-label">API Token</label>
              <input
                style={INPUT_STYLE}
                type="password"
                placeholder={detail?.configured ? '(leave blank to keep existing)' : 'eyJraWQiOi...'}
                value={token}
                onChange={e => setToken(e.target.value)}
                autoComplete="off"
              />
              <p className="cc-hint">
                Generate in Splunk: Settings → Tokens → New Token.
                Stored server-side and never returned to the browser.
              </p>
            </div>

            <div className="cc-field-group">
              <label className="cc-label">Webhook Secret <span style={{ color: '#4a5568', fontWeight: 400 }}>(optional)</span></label>
              {detail?.webhook_secret_set && !clearSecret && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
                  <span style={{ fontSize: '0.78rem', color: '#10b981' }}>✓ Secret is set</span>
                  <button
                    onClick={() => setClearSecret(true)}
                    style={{ fontSize: '0.72rem', color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                  >
                    Clear
                  </button>
                </div>
              )}
              {clearSecret && (
                <p style={{ fontSize: '0.78rem', color: '#f59e0b', margin: '0 0 0.3rem' }}>
                  Secret will be removed on save.{' '}
                  <button onClick={() => setClearSecret(false)} style={{ color: '#93c5fd', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.78rem', padding: 0 }}>
                    Cancel
                  </button>
                </p>
              )}
              {!clearSecret && (
                <input
                  style={INPUT_STYLE}
                  type="password"
                  placeholder="Optional shared secret"
                  value={webhookSecret}
                  onChange={e => setWebhookSecret(e.target.value)}
                  autoComplete="off"
                />
              )}
              <p className="cc-hint">
                When set, alerts must include the header{' '}
                <code style={{ color: '#93c5fd' }}>X-Splunk-Webhook-Token: &lt;secret&gt;</code>.
                Leave blank to accept alerts without validation.
              </p>
            </div>

            <div className="cc-row-2">
              <div className="cc-field-group">
                <label className="cc-label">Default Criticality</label>
                <select
                  style={{ ...INPUT_STYLE, width: 'auto' }}
                  value={defaultCriticality}
                  onChange={e => setDefaultCriticality(e.target.value)}
                >
                  <option value="info">info</option>
                  <option value="warning">warning</option>
                  <option value="critical">critical</option>
                </select>
                <p className="cc-hint">Used when the alert payload has no severity field.</p>
              </div>

              <div className="cc-field-group">
                <label className="cc-label">Default Event Type</label>
                <input
                  style={INPUT_STYLE}
                  type="text"
                  placeholder="unknown"
                  value={defaultEventType}
                  onChange={e => setDefaultEventType(e.target.value)}
                />
                <p className="cc-hint">Fallback when no event_type SPL field or search name.</p>
              </div>
            </div>

            <div className="cc-field-group">
              <label className="cc-label">Status</label>
              <label className="cc-toggle">
                <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
                <span className="cc-toggle-track" />
                <span className="cc-toggle-label">{enabled ? 'Enabled — webhook is active' : 'Disabled — webhook will reject alerts'}</span>
              </label>
            </div>

            {/* Auto-remediation toggle */}
            <div className="cc-field-group" style={{ borderTop: '1px solid #2d3748', paddingTop: '1rem', marginTop: '0.25rem' }}>
              <label className="cc-label">Auto-Remediation</label>
              <label className="cc-toggle">
                <input
                  type="checkbox"
                  checked={allowAutoRemediation}
                  onChange={e => setAllowAutoRemediation(e.target.checked)}
                />
                <span className="cc-toggle-track" />
                <span className="cc-toggle-label" style={{ color: allowAutoRemediation ? '#f59e0b' : '#6ee7b7' }}>
                  {allowAutoRemediation ? 'Enabled — runbooks execute automatically' : 'Disabled — steps shown as recommendations only'}
                </span>
              </label>
              <div style={{
                marginTop: '0.5rem',
                padding: '0.6rem 0.75rem',
                borderRadius: '6px',
                background: allowAutoRemediation ? '#1c0f00' : '#0d1f0d',
                border: `1px solid ${allowAutoRemediation ? '#f59e0b40' : '#10b98140'}`,
                fontSize: '0.78rem',
                color: allowAutoRemediation ? '#fbbf24' : '#6ee7b7',
                lineHeight: 1.5,
              }}>
                {allowAutoRemediation
                  ? '⚠ Auto-remediation is ON. Runbooks will execute without human approval when a governance policy allows it. Only enable if this platform is the sole remediation system for Splunk alerts.'
                  : '✓ Safe default. Runbook steps are presented as recommendations. An operator must review and approve before any execution begins.'}
              </div>
            </div>

            {testResult && (
              <div className="cc-test-result" style={{ color: testResult.ok ? '#6ee7b7' : '#fca5a5' }}>
                {testResult.ok ? '✓' : '✗'} {testResult.msg}
                {testResult.latency != null && <span className="cc-latency">{testResult.latency}ms</span>}
              </div>
            )}

            {saveMsg && (
              <p className="cc-save-msg" style={{ color: saveMsg.startsWith('✓') ? '#6ee7b7' : saveMsg.startsWith('⚠') ? '#fbbf24' : '#fca5a5' }}>
                {saveMsg}
              </p>
            )}

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
              Credentials are stored encrypted in the platform database.
              The API token is never returned to the browser after saving.
            </p>
          </div>
        )}

        {/* ── Webhook Setup tab ────────────────────────────────────────────── */}
        {tab === 'webhook' && (
          <div className="cc-body">
            <p style={{ fontSize: '0.82rem', color: '#a0aec0', margin: 0 }}>
              Configure Splunk to POST to this platform when a saved search fires.
              Alerts are parsed, qualified, and can automatically open incidents.
            </p>

            {/* Webhook URL */}
            <div className="cc-field-group">
              <label className="cc-label">Webhook URL</label>
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                <div style={{ ...CODE_BOX, flex: 1 }}>{webhookUrl}</div>
                <button
                  onClick={handleCopy}
                  style={{
                    padding: '0.45rem 0.85rem',
                    borderRadius: '6px',
                    border: '1px solid #3d4557',
                    background: copied ? '#064e3b' : '#252c3c',
                    color: copied ? '#6ee7b7' : '#a0aec0',
                    fontSize: '0.78rem',
                    fontWeight: 600,
                    cursor: 'pointer',
                    whiteSpace: 'nowrap',
                    transition: 'all 150ms',
                  }}
                >
                  {copied ? '✓ Copied' : 'Copy'}
                </button>
              </div>
            </div>

            {/* Setup steps */}
            <div className="cc-field-group">
              <label className="cc-label">Splunk Setup Steps</label>
              <ol style={{ fontSize: '0.82rem', color: '#a0aec0', paddingLeft: '1.2rem', lineHeight: 1.8, margin: 0 }}>
                <li>In Splunk: open a saved search → <strong style={{ color: '#e8eef5' }}>Edit → Edit Alert</strong></li>
                <li>Under <strong style={{ color: '#e8eef5' }}>Trigger Actions</strong> → <strong style={{ color: '#e8eef5' }}>Add Actions → Webhook</strong></li>
                <li>Paste the URL above into the <strong style={{ color: '#e8eef5' }}>URL</strong> field</li>
                <li>If using a webhook secret, add a custom header:<br />
                  <code style={{ color: '#93c5fd', fontSize: '0.75rem' }}>X-Splunk-Webhook-Token: &lt;your_secret&gt;</code>
                </li>
                <li>Save the alert action</li>
              </ol>
            </div>

            {/* Recommended SPL fields */}
            <div className="cc-field-group">
              <label className="cc-label">Recommended SPL Fields</label>
              <p style={{ fontSize: '0.78rem', color: '#6b7a93', margin: '0 0 0.4rem' }}>
                Add these fields to your search results for the best incident quality:
              </p>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left', padding: '0.3rem 0.5rem', color: '#6b7a93', borderBottom: '1px solid #3d4557', fontSize: '0.68rem', textTransform: 'uppercase' }}>Field</th>
                    <th style={{ textAlign: 'left', padding: '0.3rem 0.5rem', color: '#6b7a93', borderBottom: '1px solid #3d4557', fontSize: '0.68rem', textTransform: 'uppercase' }}>Example</th>
                    <th style={{ textAlign: 'left', padding: '0.3rem 0.5rem', color: '#6b7a93', borderBottom: '1px solid #3d4557', fontSize: '0.68rem', textTransform: 'uppercase' }}>Maps to</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['host',           'prod-web-01',  'resource_name'],
                    ['event_type',     'high_cpu',     'event_type'],
                    ['severity',       'critical',     'criticality'],
                    ['threshold',      '80',           'signal_threshold'],
                    ['cpu_pct',        '95.2',         'signal_value (auto)'],
                    ['process',        'nginx',        'anomaly_process'],
                  ].map(([field, example, maps]) => (
                    <tr key={field}>
                      <td style={{ padding: '0.4rem 0.5rem', color: '#93c5fd', fontFamily: 'monospace', borderBottom: '1px solid #252c3c' }}>{field}</td>
                      <td style={{ padding: '0.4rem 0.5rem', color: '#a0aec0', borderBottom: '1px solid #252c3c' }}>{example}</td>
                      <td style={{ padding: '0.4rem 0.5rem', color: '#6b7a93', borderBottom: '1px solid #252c3c' }}>{maps}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Example payload */}
            <div className="cc-field-group">
              <label className="cc-label">Example Splunk Payload</label>
              <pre style={{ ...CODE_BOX, color: '#a0aec0', whiteSpace: 'pre-wrap', marginTop: '0.25rem' }}>{`{
  "search_name": "High CPU on prod-servers",
  "result": {
    "host":       "prod-web-01",
    "event_type": "high_cpu",
    "severity":   "critical",
    "cpu_pct":    "95.4",
    "threshold":  "80",
    "process":    "nginx"
  }
}`}</pre>
            </div>
          </div>
        )}

        {/* ── Status tab ───────────────────────────────────────────────────── */}
        {tab === 'logs' && (
          <div className="cc-body">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
              <div style={{ padding: '0.75rem 1rem', background: '#252c3c', borderRadius: '8px', border: '1px solid #3d4557' }}>
                <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#6b7a93', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.35rem' }}>Connector status</p>
                <p style={{ fontSize: '0.9rem', fontWeight: 700, color: detail?.configured && detail?.enabled ? '#10b981' : '#f59e0b', margin: 0 }}>
                  {!detail?.configured ? 'Not configured' : detail.enabled ? 'Active — receiving alerts' : 'Configured but disabled'}
                </p>
              </div>

              <div style={{ padding: '0.75rem 1rem', background: '#252c3c', borderRadius: '8px', border: '1px solid #3d4557' }}>
                <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#6b7a93', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.35rem' }}>Webhook secret</p>
                <p style={{ fontSize: '0.85rem', color: '#a0aec0', margin: 0 }}>
                  {detail?.webhook_secret_set
                    ? '✓ Secret is configured — alerts must include X-Splunk-Webhook-Token'
                    : '⚠ No secret — all POSTs to the webhook URL are accepted'}
                </p>
              </div>

              <p style={{ fontSize: '0.78rem', color: '#6b7a93', margin: '0.5rem 0 0' }}>
                Ingested alerts appear in the <strong style={{ color: '#a0aec0' }}>Events Feed</strong>.
                Qualified alerts automatically create incidents and appear in the <strong style={{ color: '#a0aec0' }}>Incidents</strong> list.
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
