import { useState, useEffect } from 'react'
import { listConnectors, testConnector, triggerConnectorSync } from '../services/api'
import { ConnectorDef } from '../types'
import { parseUTC } from '../utils/dateFormatter'
import ConnectorConfig from './ConnectorConfig'
import SplunkConnectorConfig from './SplunkConnectorConfig'
import AlertIngestConfig from './AlertIngestConfig'
import SNowCMDBBrowser from './SNowCMDBBrowser'
import './ConnectorHub.css'

interface ConnectorHubProps {
  darkMode?: boolean
}

const STATUS_COLOR: Record<string, string> = {
  ok:      '#6ee7b7',
  partial: '#fbbf24',
  error:   '#fca5a5',
  never:   '#6b7a93',
}

const CAPABILITY_LABELS: Record<string, string> = {
  cmdb_pull:      'CMDB Sync',
  incident_push:  'Incident Push',
  log_pull:       'Log Pull',
  alert_ingest:   'Alert Ingest',
  escalation:     'Escalation',
}

// Connectors that use the shared AlertIngestConfig drawer (no base_url / token)
const ALERT_INGEST_CONNECTORS = new Set(['datadog', 'dynatrace', 'prometheus', 'pagerduty', 'zabbix', 'grafana', 'generic'])

export default function ConnectorHub({ darkMode }: ConnectorHubProps) {
  const [connectors, setConnectors]     = useState<ConnectorDef[]>([])
  const [loading, setLoading]           = useState(true)
  const [configOpen, setConfigOpen]     = useState<string | null>(null)
  const [snowBrowserOpen, setSnowBrowserOpen] = useState(false)
  const [testing, setTesting]           = useState<string | null>(null)
  const [syncing, setSyncing]           = useState<string | null>(null)
  const [testResult, setTestResult]     = useState<Record<string, { ok: boolean; message: string }>>({})
  const [syncResult, setSyncResult]     = useState<Record<string, string>>({})

  const load = async () => {
    try {
      const res = await listConnectors()
      setConnectors(res.data)
    } catch {
      // silently — connectors is non-critical
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleTest = async (id: string) => {
    setTesting(id)
    setTestResult(prev => ({ ...prev, [id]: undefined! }))
    try {
      const res = await testConnector(id)
      setTestResult(prev => ({ ...prev, [id]: { ok: res.data.ok, message: res.data.message } }))
    } catch (e: any) {
      setTestResult(prev => ({ ...prev, [id]: { ok: false, message: e?.response?.data?.detail || 'Network error' } }))
    } finally {
      setTesting(null)
    }
  }

  const handleSync = async (id: string) => {
    setSyncing(id)
    setSyncResult(prev => ({ ...prev, [id]: 'Queuing sync…' }))
    try {
      await triggerConnectorSync(id)
      setSyncResult(prev => ({ ...prev, [id]: '✓ Sync started in background — check back in a moment' }))
      // Refresh card after a short delay so last_sync_at updates once the task lands
      setTimeout(() => load(), 5000)
    } catch (e: any) {
      setSyncResult(prev => ({ ...prev, [id]: `✗ ${e?.response?.data?.detail || 'Failed to queue sync'}` }))
    } finally {
      setSyncing(null)
    }
  }

  return (
    <div className="ch-root">
      {/* ── Header ── */}
      <div className="ch-header">
        <div>
          <h2 className="ch-title">Connector Hub</h2>
          <p className="ch-subtitle">External system integrations — data pull &amp; push</p>
        </div>
      </div>

      {/* ── Connector cards ── */}
      {loading ? (
        <div className="ch-loading">Loading connectors…</div>
      ) : (
        <div className="ch-grid">
          {connectors.map(c => (
            <div
              key={c.id}
              className={`ch-card ${c.configured && c.enabled ? 'active' : ''}`}
            >
              {/* Card header */}
              <div className="ch-card-head">
                <div className="ch-card-title-row">
                  <span className="ch-card-name">{c.display_name}</span>
                  {c.configured && c.enabled ? (
                    <span className="ch-pill connected-pill">Connected</span>
                  ) : c.configured ? (
                    <span className="ch-pill disabled-pill">Disabled</span>
                  ) : (
                    <span className="ch-pill unconfigured-pill">Not Configured</span>
                  )}
                </div>
                <p className="ch-card-desc">{c.description}</p>
              </div>

              {/* Capabilities */}
              <div className="ch-caps">
                {c.capabilities.map(cap => (
                  <span key={cap} className="ch-cap-tag">{CAPABILITY_LABELS[cap] ?? cap}</span>
                ))}
              </div>

              {/* Sync status */}
              {c.last_sync_at && (
                <div className="ch-sync-info">
                  <span
                    className="ch-sync-dot"
                    style={{ background: STATUS_COLOR[c.last_sync_status ?? 'never'] }}
                  />
                  <span className="ch-sync-label">
                    Last sync: {parseUTC(c.last_sync_at).toLocaleString()}
                    {' '}· {c.last_sync_status}
                  </span>
                </div>
              )}
              {!c.last_sync_at && (
                <div className="ch-sync-info">
                  <span className="ch-sync-dot" style={{ background: '#6b7a93' }} />
                  <span className="ch-sync-label">Never synced</span>
                </div>
              )}

              {/* Inline feedback */}
              {testResult[c.id] && (
                <p className="ch-feedback" style={{ color: testResult[c.id].ok ? '#6ee7b7' : '#fca5a5' }}>
                  {testResult[c.id].ok ? '✓' : '✗'} {testResult[c.id].message}
                </p>
              )}
              {syncResult[c.id] && (
                <p className="ch-feedback" style={{ color: syncResult[c.id].startsWith('✓') ? '#6ee7b7' : '#fca5a5' }}>
                  {syncResult[c.id]}
                </p>
              )}

              {/* Actions */}
              <div className="ch-card-actions">
                  <button
                    className="ch-btn ch-btn-config"
                    onClick={() => setConfigOpen(c.id)}
                  >
                    Configure
                  </button>
                  {c.configured && !ALERT_INGEST_CONNECTORS.has(c.id) && (
                    <>
                      <button
                        className="ch-btn ch-btn-test"
                        onClick={() => handleTest(c.id)}
                        disabled={testing === c.id}
                      >
                        {testing === c.id ? 'Testing…' : 'Test'}
                      </button>
                      <button
                        className="ch-btn ch-btn-sync"
                        onClick={() => handleSync(c.id)}
                        disabled={syncing === c.id}
                      >
                        {syncing === c.id ? 'Syncing…' : 'Sync Now'}
                      </button>
                    </>
                  )}
                  {c.id === 'servicenow' && c.configured && (
                    <button
                      className="ch-btn ch-btn-browse"
                      onClick={() => setSnowBrowserOpen(true)}
                    >
                      Browse Data
                    </button>
                  )}
                </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Config drawer — route to connector-specific component ── */}
      {configOpen === 'splunk' && (
        <SplunkConnectorConfig onClose={() => { setConfigOpen(null); load() }} />
      )}
      {configOpen && ALERT_INGEST_CONNECTORS.has(configOpen) && (
        <AlertIngestConfig
          connectorId={configOpen}
          onClose={() => { setConfigOpen(null); load() }}
        />
      )}
      {configOpen && configOpen !== 'splunk' && !ALERT_INGEST_CONNECTORS.has(configOpen) && (
        <ConnectorConfig
          connectorId={configOpen}
          onClose={() => { setConfigOpen(null); load() }}
          darkMode={darkMode}
        />
      )}

      {snowBrowserOpen && (
        <SNowCMDBBrowser
          onClose={() => setSnowBrowserOpen(false)}
          darkMode={darkMode}
        />
      )}

    </div>
  )
}
