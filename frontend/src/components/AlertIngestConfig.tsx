import { useState, useEffect } from 'react'
import { getConnector, saveAlertConnectorConfig } from '../services/api'
import { ConnectorDef } from '../types'
import './ConnectorConfig.css'

interface Props {
  connectorId: string
  onClose: () => void
}

type Tab = 'config' | 'webhook' | 'status'

// ── Per-connector metadata ───────────────────────────────────────────────────

interface ConnectorMeta {
  displayName: string
  subtitle: string
  secretHeader: string
  setupSteps: string[]
  fieldRows: [string, string, string][]   // [field, example, maps to]
  examplePayload: string
}

const CONNECTOR_META: Record<string, ConnectorMeta> = {
  datadog: {
    displayName: 'Datadog',
    subtitle:    'Ingest Datadog monitor alerts as monitoring events',
    secretHeader: 'X-Datadog-Webhook-Secret',
    setupSteps: [
      'In Datadog: Integrations → Webhooks → New',
      'Set URL to the webhook URL shown on the Webhook Setup tab',
      'Leave payload as default (or customise — all standard fields are mapped)',
      'If using a webhook secret, add a custom header: X-Datadog-Webhook-Secret: <secret>',
      'Save and link the webhook to one or more monitors',
    ],
    fieldRows: [
      ['hostname',        'prod-web-01',            'resource_name'],
      ['alert_type',      'warning / error',         'criticality'],
      ['alert_metric',    'system.cpu.user',         'event_type (slug)'],
      ['tags.host',       'prod-web-01',             'resource_name (tag override)'],
      ['tags.event_type', 'high_cpu',                'event_type (tag override)'],
      ['tags.service',    'api',                     'anomaly_process'],
      ['alert_transition','Triggered / Recovered',   'drop if Recovered'],
    ],
    examplePayload: `{
  "id": 12345,
  "title": "[Triggered on prod-web-01] CPU is high",
  "hostname": "prod-web-01",
  "alert_type": "error",
  "alert_metric": "system.cpu.user",
  "alert_transition": "Triggered",
  "tags": "env:prod,service:api,host:prod-web-01",
  "priority": "normal"
}`,
  },

  dynatrace: {
    displayName: 'Dynatrace',
    subtitle:    'Ingest Dynatrace problem notifications as monitoring events',
    secretHeader: 'X-Dynatrace-Webhook-Secret',
    setupSteps: [
      'In Dynatrace: Settings → Integrations → Problem notifications',
      'Click Add notification → Custom integration',
      'Set Webhook URL to the URL shown on the Webhook Setup tab',
      'Set the payload template to JSON (see example below)',
      'If using a webhook secret, add a custom header: X-Dynatrace-Webhook-Secret: <secret>',
      'Select the problem types you want to forward and Save',
    ],
    fieldRows: [
      ['title',              'High CPU on HOST-abc',    'event_type (slug)'],
      ['problemSeverity',    'PERFORMANCE / AVAILABILITY', 'criticality'],
      ['problemImpact',      'INFRASTRUCTURE',          'criticality (secondary)'],
      ['state',              'OPEN / RESOLVED',         'drop if RESOLVED'],
      ['affectedEntities[0].name', 'HOST-abc123',      'resource_name'],
      ['tagsOfAffectedEntities[].key/value', 'env:prod', 'tags'],
    ],
    examplePayload: `{
  "title": "High CPU on HOST-abc123",
  "problemUrl": "https://abc123.live.dynatrace.com/...",
  "problemId": "P-12345",
  "problemImpact": "INFRASTRUCTURE",
  "problemSeverity": "PERFORMANCE",
  "state": "OPEN",
  "affectedEntities": [
    {"id": "HOST-abc123", "type": "HOST", "name": "prod-web-01"}
  ],
  "tagsOfAffectedEntities": [
    {"context": "CONTEXTLESS", "key": "env", "value": "prod"}
  ]
}`,
  },

  prometheus: {
    displayName: 'Prometheus',
    subtitle:    'Ingest Prometheus Alertmanager firing alerts as monitoring events',
    secretHeader: 'X-Prometheus-Webhook-Secret',
    setupSteps: [
      'In alertmanager.yml, add a webhook receiver pointing to this platform',
      'Use the URL shown on the Webhook Setup tab',
      'Route the alerts you want to forward to this receiver',
      'If using a webhook secret, configure Alertmanager to send X-Prometheus-Webhook-Secret: <secret>',
      'Reload Alertmanager: amtool config check && killall -HUP alertmanager',
      'Each firing alert in a batch becomes an independent monitoring event',
    ],
    fieldRows: [
      ['alerts[].labels.alertname',   'HighCPUUsage',    'event_type (slug)'],
      ['alerts[].labels.instance',    'host:9100',       'resource_name (port stripped)'],
      ['alerts[].labels.host',        'prod-web-01',     'resource_name (override)'],
      ['alerts[].labels.severity',    'warning / critical', 'criticality'],
      ['alerts[].labels.job',         'node',            'anomaly_process'],
      ['alerts[].status',             'firing / resolved', 'drop if resolved'],
    ],
    examplePayload: `{
  "version": "4",
  "status": "firing",
  "receiver": "platform-webhook",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "HighCPUUsage",
        "instance": "prod-web-01:9100",
        "severity": "warning",
        "job": "node"
      },
      "annotations": {
        "summary": "CPU usage above 80%"
      }
    }
  ]
}`,
  },

  pagerduty: {
    displayName: 'PagerDuty',
    subtitle:    'Ingest PagerDuty incident triggers as monitoring events, and escalate/notify back to PagerDuty for on-call paging',
    secretHeader: 'X-PagerDuty-Webhook-Secret',
    setupSteps: [
      'In PagerDuty: Integrations → Generic Webhooks (v3) → Add webhook',
      'Set Webhook URL to the URL shown on the Webhook Setup tab',
      'Select scope: Account or specific Service',
      'Subscribe to: incident.triggered and incident.acknowledged events',
      'If using a webhook secret, save it here and set it in PagerDuty signing secret field',
      'Save — PagerDuty will begin forwarding matching incidents',
    ],
    fieldRows: [
      ['event.data.title',            'High CPU on prod-web-01', 'event_type (slug)'],
      ['event.data.service.summary',  'My Application Service',  'resource_name'],
      ['event.data.urgency',          'high / low',              'criticality'],
      ['event.data.priority.name',    'P1 / P2',                 'criticality (override)'],
      ['event.event_type',            'incident.triggered',      'drop if incident.resolved'],
    ],
    examplePayload: `{
  "event": {
    "id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
    "event_type": "incident.triggered",
    "data": {
      "id": "Q14VH0BKIKKJKXX",
      "type": "incident",
      "title": "High CPU on prod-web-01",
      "status": "triggered",
      "urgency": "high",
      "service": {
        "summary": "My Application Service"
      },
      "priority": {"id": "P1", "name": "P1"}
    }
  }
}`,
  },

  zabbix: {
    displayName: 'Zabbix',
    subtitle:    'Ingest Zabbix trigger problem notifications as monitoring events',
    secretHeader: 'X-Zabbix-Webhook-Secret',
    setupSteps: [
      'In Zabbix: Administration → Media types → Create media type → Webhook',
      'Name it "Platform" and set the webhook script to call this endpoint',
      'Add parameters mapping Zabbix macros to JSON fields (see field table)',
      'Create a User with this media type assigned, or assign to an existing user',
      'Create an Action that triggers this media type on PROBLEM events',
      'Use the URL shown on the Webhook Setup tab as the endpoint',
    ],
    fieldRows: [
      ['host_name',        '{HOST.NAME}',           'resource_name'],
      ['trigger_name',     '{TRIGGER.NAME}',        'event_type (slug)'],
      ['trigger_severity', '{TRIGGER.SEVERITY}',    'criticality'],
      ['trigger_status',   'PROBLEM / RESOLVED',    'drop if RESOLVED'],
      ['item_name',        '{ITEM.NAME}',           'anomaly_process'],
      ['item_value',       '{ITEM.VALUE}',          'signal_value (numeric)'],
      ['event_tags',       'env:prod,service:api',  'tags dict'],
    ],
    examplePayload: `{
  "event_id": "12345",
  "trigger_name": "High CPU utilization",
  "trigger_severity": "WARNING",
  "trigger_status": "PROBLEM",
  "host_name": "prod-web-01",
  "host_ip": "10.0.0.1",
  "item_name": "CPU utilization",
  "item_value": "87.5%",
  "event_tags": "env:prod,service:api"
}`,
  },

  grafana: {
    displayName: 'Grafana',
    subtitle:    'Ingest Grafana Unified Alerting firing alerts as monitoring events',
    secretHeader: 'X-Grafana-Webhook-Secret',
    setupSteps: [
      'In Grafana: Alerting → Contact points → Add contact point',
      'Select type: Webhook',
      'Set URL to the webhook URL shown on the Webhook Setup tab',
      'If using a secret, add a custom header: X-Grafana-Webhook-Secret: <secret>',
      'Save the contact point, then add it to a Notification policy',
      'Each firing alert in a batch becomes an independent monitoring event — resolved alerts are dropped',
    ],
    fieldRows: [
      ['alerts[].labels.alertname',  'HighCPUUsage',        'event_type (slug)'],
      ['alerts[].labels.instance',   'prod-web-01:9100',    'resource_name (port stripped)'],
      ['alerts[].labels.host',       'prod-web-01',         'resource_name (override)'],
      ['alerts[].labels.severity',   'warning / critical',  'criticality'],
      ['alerts[].labels.job',        'node-exporter',       'anomaly_process'],
      ['alerts[].values',            '{"A": 92.5}',         'signal_value (first numeric)'],
      ['alerts[].status',            'firing / resolved',   'drop if resolved'],
    ],
    examplePayload: `{
  "status": "firing",
  "receiver": "platform-webhook",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "HighCPUUsage",
        "instance": "prod-web-01:9100",
        "severity": "critical",
        "job": "node-exporter"
      },
      "annotations": {
        "summary": "CPU usage above 90%",
        "description": "prod-web-01 CPU is at 92%"
      },
      "values": {"A": 92.5},
      "dashboardURL": "http://grafana:3000/d/...",
      "fingerprint": "abc123"
    }
  ]
}`,
  },

  generic: {
    displayName: 'Generic Webhook',
    subtitle:    'Ingest events from any custom source — accepts any source system passing the required fields',
    secretHeader: 'X-Webhook-Secret',
    setupSteps: [
      'Enable the connector and optionally set a webhook secret',
      'Any system can POST to the webhook URL — no per-source configuration needed',
      'Each request must include: source, event_type, resource_name, severity',
      'The source field identifies the originating system and is stored as-is on the event',
      'Use default_criticality as a fallback when the payload carries no severity field',
      'Multiple different source systems can all POST to the same endpoint simultaneously',
    ],
    fieldRows: [
      ['source',           'my-app / cron-monitor',   'source (required — identifies the system)'],
      ['event_type',       'high_error_rate',          'event_type (required)'],
      ['resource_name',    'payment-service',          'resource_name (required)'],
      ['severity',         'info / warning / critical','criticality (required; alias: raw_criticality)'],
      ['signal_value',     '95.2',                     'signal_value (optional numeric)'],
      ['signal_threshold', '80.0',                     'signal_threshold (optional numeric)'],
      ['title',            'Error rate spiked',        'payload title / summary'],
      ['description',      'Details…',                 'payload description'],
      ['process',          'checkout-handler',         'anomaly_process (optional)'],
      ['metadata',         '{"region": "us-east-1"}', 'stored in raw_payload'],
    ],
    examplePayload: `{
  "source": "payment-service",
  "event_type": "high_error_rate",
  "resource_name": "payment-api-prod",
  "severity": "critical",
  "signal_value": 95.2,
  "signal_threshold": 80.0,
  "title": "Error rate above threshold",
  "description": "5xx responses spiked in the last 5 minutes",
  "process": "checkout-handler",
  "metadata": {
    "region": "us-east-1",
    "deploy": "v1.4.2"
  }
}`,
  },
}

// ── Canonical platform event types (mirrors backend normalizer) ───────────────

const CANONICAL_EVENT_TYPES = [
  'high_cpu', 'high_memory', 'disk_full', 'service_down',
  'service_unresponsive', 'high_error_rate', 'error_rate_spike',
  'high_latency', 'latency_spike', 'network_issue', 'database_error',
  'db_connection_pool_exhausted', 'certificate_expiry', 'pod_crash',
  'high_syscall_intensity', 'queue_depth_critical', 'custom',
] as const

// ── Shared styles ─────────────────────────────────────────────────────────────

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

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Wrap header-looking strings in a code tag for setup-steps display. */
function highlightHeader(step: string): string {
  // Match "X-Something-Header: <value>" patterns
  const re = /X-[A-Za-z-]+: <[^<>]+>/g
  return step.replace(re, m => `<code style="color:#93c5fd;font-size:0.75rem">${m}</code>`)
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function AlertIngestConfig({ connectorId, onClose }: Props) {
  const meta = CONNECTOR_META[connectorId]
  if (!meta) return null

  const [detail, setDetail]                         = useState<ConnectorDef | null>(null)
  const [tab, setTab]                               = useState<Tab>('config')

  // Config fields
  const [webhookSecret, setWebhookSecret]                   = useState('')
  const [clearSecret, setClearSecret]                       = useState(false)
  const [defaultCriticality, setDefaultCriticality]         = useState('warning')
  const [defaultEventType, setDefaultEventType]             = useState('unknown')
  const [enabled, setEnabled]                               = useState(true)
  const [allowAutoRemediation, setAllowAutoRemediation]     = useState(false)
  const [eventTypeMappings, setEventTypeMappings]           = useState<{ from: string; to: string }[]>([])
  const [routingKey, setRoutingKey]                         = useState('')
  const [clearRoutingKey, setClearRoutingKey]               = useState(false)
  const isPagerDuty = connectorId === 'pagerduty'

  // Mapping helpers
  const addMapping    = () => setEventTypeMappings(prev => [...prev, { from: '', to: '' }])
  const removeMapping = (i: number) => setEventTypeMappings(prev => prev.filter((_, idx) => idx !== i))
  const updateMapping = (i: number, field: 'from' | 'to', value: string) =>
    setEventTypeMappings(prev => prev.map((m, idx) => idx === i ? { ...m, [field]: value } : m))

  // Generated token state — cleared after save
  const [generatedToken, setGeneratedToken] = useState('')
  const [tokenCopied, setTokenCopied]       = useState(false)

  // UI state
  const [saving, setSaving]   = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const [copied, setCopied]   = useState(false)

  const webhookUrl = `${window.location.origin}/api/connectors/${connectorId}/webhook`

  useEffect(() => { load() }, [connectorId])

  const load = async () => {
    try {
      const res = await getConnector(connectorId)
      const d   = res.data
      setDetail(d)
      setEnabled(d.enabled)
      setDefaultCriticality(d.default_criticality || 'warning')
      setDefaultEventType(d.default_event_type || 'unknown')
      setAllowAutoRemediation(d.allow_auto_remediation ?? false)
      if (isPagerDuty) {
        setClearRoutingKey(false)
        setRoutingKey('')
      }
      // Hydrate event_type_mappings: stored as {externalName: canonicalType, ...}
      const rawMappings: Record<string, string> = d.event_type_mappings || {}
      setEventTypeMappings(
        Object.entries(rawMappings).map(([from, to]) => ({ from, to }))
      )
    } catch {}
  }

  const handleSave = async () => {
    setSaving(true)
    setSaveMsg('')
    try {
      // Serialize mapping rows → {externalName: canonicalType} dict, skip blanks
      const mappingsObj = Object.fromEntries(
        eventTypeMappings
          .filter(m => m.from.trim() && m.to.trim())
          .map(m => [m.from.trim(), m.to.trim()])
      )
      await saveAlertConnectorConfig(connectorId, {
        webhook_secret:         clearSecret ? '-' : (webhookSecret || ''),
        default_criticality:    defaultCriticality,
        default_event_type:     defaultEventType,
        enabled,
        allow_auto_remediation: allowAutoRemediation,
        event_type_mappings:    mappingsObj,
        ...(isPagerDuty ? { routing_key: clearRoutingKey ? '-' : (routingKey || '') } : {}),
      })
      setSaveMsg('✓ Configuration saved')
      setWebhookSecret('')
      setClearSecret(false)
      setGeneratedToken('')
      setRoutingKey('')
      setClearRoutingKey(false)
      await load()
    } catch (e: any) {
      setSaveMsg(`✗ ${e?.response?.data?.detail || 'Save failed'}`)
    } finally {
      setSaving(false)
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
            <h3 className="cc-title">{meta.displayName} — Settings</h3>
            <p className="cc-subtitle">{meta.subtitle}</p>
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
          <button className={`cc-tab ${tab === 'status' ? 'active' : ''}`} onClick={() => setTab('status')}>
            Status
          </button>
        </div>

        {/* ── Configuration tab ─────────────────────────────────────────────── */}
        {tab === 'config' && (
          <div className="cc-body">

            <div className="cc-caps-row">
              <span className="cc-cap">Alert Ingest</span>
              <span className="cc-cap">Inbound Webhook</span>
              {isPagerDuty && <span className="cc-cap">Outbound Escalation</span>}
            </div>

            <p className="cc-hint" style={{ marginBottom: '1rem' }}>
              {meta.displayName} sends alert payloads to a webhook URL on this platform — just paste the
              webhook URL into {meta.displayName} and optionally set a shared secret.
              {isPagerDuty && ' Outbound escalation (pushing incidents back to PagerDuty) is configured separately below.'}
            </p>

            {/* Webhook Secret */}
            <div className="cc-field-group">
              <label className="cc-label">
                Webhook Secret <span style={{ color: '#4a5568', fontWeight: 400 }}>(optional)</span>
              </label>

              {/* Already set — show status + replace/clear actions */}
              {detail?.webhook_secret_set && !clearSecret && !generatedToken && !webhookSecret && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.4rem', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: '0.78rem', color: '#10b981' }}>✓ Secret is set</span>
                  <button
                    onClick={() => {
                      const bytes = new Uint8Array(24)
                      crypto.getRandomValues(bytes)
                      const token = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('')
                      setGeneratedToken(token)
                      setWebhookSecret(token)
                      setClearSecret(false)
                    }}
                    style={{ fontSize: '0.72rem', color: '#93c5fd', background: 'none', border: '1px solid #3d4557', borderRadius: '5px', cursor: 'pointer', padding: '2px 8px' }}
                  >
                    Regenerate
                  </button>
                  <button
                    onClick={() => setClearSecret(true)}
                    style={{ fontSize: '0.72rem', color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                  >
                    Clear
                  </button>
                </div>
              )}

              {/* Pending clear */}
              {clearSecret && (
                <p style={{ fontSize: '0.78rem', color: '#f59e0b', margin: '0 0 0.4rem' }}>
                  Secret will be removed on save.{' '}
                  <button
                    onClick={() => setClearSecret(false)}
                    style={{ color: '#93c5fd', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.78rem', padding: 0 }}
                  >
                    Cancel
                  </button>
                </p>
              )}

              {/* No secret set — Generate or type */}
              {!clearSecret && !detail?.webhook_secret_set && !generatedToken && (
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.4rem' }}>
                  <input
                    style={{ ...INPUT_STYLE, flex: 1 }}
                    type="password"
                    placeholder="Paste your own secret, or generate one →"
                    value={webhookSecret}
                    onChange={e => setWebhookSecret(e.target.value)}
                    autoComplete="off"
                  />
                  <button
                    onClick={() => {
                      const bytes = new Uint8Array(24)
                      crypto.getRandomValues(bytes)
                      const token = Array.from(bytes).map(b => b.toString(16).padStart(2, '0')).join('')
                      setGeneratedToken(token)
                      setWebhookSecret(token)
                    }}
                    style={{
                      padding: '0.45rem 0.9rem', borderRadius: '6px',
                      border: '1px solid #3d4557', background: '#252c3c',
                      color: '#93c5fd', fontSize: '0.78rem', fontWeight: 600,
                      cursor: 'pointer', whiteSpace: 'nowrap',
                    }}
                  >
                    Generate Token
                  </button>
                </div>
              )}

              {/* Generated token — show in plaintext with copy */}
              {generatedToken && !clearSecret && (
                <div style={{ marginBottom: '0.4rem' }}>
                  <div style={{
                    display: 'flex', gap: '0.5rem', alignItems: 'center',
                    padding: '0.5rem 0.75rem',
                    background: 'rgba(16,185,129,0.06)',
                    border: '1px solid rgba(16,185,129,0.3)',
                    borderRadius: '6px',
                  }}>
                    <code style={{ flex: 1, fontSize: '0.78rem', color: '#6ee7b7', fontFamily: 'monospace', wordBreak: 'break-all', lineHeight: 1.5 }}>
                      {generatedToken}
                    </code>
                    <button
                      onClick={async () => {
                        try {
                          await navigator.clipboard.writeText(generatedToken)
                          setTokenCopied(true)
                          setTimeout(() => setTokenCopied(false), 2500)
                        } catch {}
                      }}
                      style={{
                        padding: '0.35rem 0.75rem', borderRadius: '5px',
                        border: '1px solid rgba(16,185,129,0.4)',
                        background: tokenCopied ? 'rgba(16,185,129,0.2)' : 'transparent',
                        color: tokenCopied ? '#6ee7b7' : '#10b981',
                        fontSize: '0.75rem', fontWeight: 600,
                        cursor: 'pointer', whiteSpace: 'nowrap', transition: 'all 150ms',
                        flexShrink: 0,
                      }}
                    >
                      {tokenCopied ? '✓ Copied' : 'Copy'}
                    </button>
                  </div>
                  <p style={{ fontSize: '0.72rem', color: '#f59e0b', margin: '0.3rem 0 0' }}>
                    Copy this token now — it won't be shown again after you save.
                    Paste it into {meta.displayName}'s webhook secret / custom header field.
                  </p>
                </div>
              )}

              <p className="cc-hint">
                When set, alerts must include the header{' '}
                <code style={{ color: '#93c5fd' }}>{meta.secretHeader}: &lt;secret&gt;</code>.
                Leave blank to accept all POSTs without validation.
                Stored encrypted; never returned to the browser.
              </p>
            </div>

            {/* PagerDuty-only: outbound escalation routing key */}
            {isPagerDuty && (
              <div className="cc-field-group" style={{ borderTop: '1px solid #2d3748', paddingTop: '1rem', marginTop: '0.5rem' }}>
                <label className="cc-label">
                  Outbound Routing Key <span style={{ color: '#4a5568', fontWeight: 400 }}>(for escalations)</span>
                </label>
                <p className="cc-hint" style={{ marginBottom: '0.5rem' }}>
                  Lets this platform <strong style={{ color: '#a0aec0' }}>push</strong> incidents to PagerDuty —
                  used by the <code style={{ color: '#93c5fd' }}>alert_escalate</code> /{' '}
                  <code style={{ color: '#93c5fd' }}>alert_update</code> runbook actions to trigger, acknowledge, or
                  resolve a PagerDuty incident. This is separate from the webhook secret above, which only governs
                  inbound alerts.
                </p>
                {detail?.routing_key_set && !clearRoutingKey && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
                    <span style={{ fontSize: '0.78rem', color: '#10b981' }}>✓ Routing key is set</span>
                    <button
                      onClick={() => setClearRoutingKey(true)}
                      style={{ fontSize: '0.72rem', color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                    >
                      Clear
                    </button>
                  </div>
                )}
                {clearRoutingKey && (
                  <p style={{ fontSize: '0.78rem', color: '#f59e0b', margin: '0 0 0.3rem' }}>
                    Routing key will be removed on save — escalations will fall back to Slack (if configured).{' '}
                    <button
                      onClick={() => setClearRoutingKey(false)}
                      style={{ color: '#93c5fd', background: 'none', border: 'none', cursor: 'pointer', fontSize: '0.78rem', padding: 0 }}
                    >
                      Cancel
                    </button>
                  </p>
                )}
                {!clearRoutingKey && (
                  <input
                    style={INPUT_STYLE}
                    type="password"
                    placeholder={detail?.routing_key_set ? 'Enter a new key to replace it' : 'PagerDuty integration / routing key'}
                    value={routingKey}
                    onChange={e => setRoutingKey(e.target.value)}
                    autoComplete="off"
                  />
                )}
                <p className="cc-hint">
                  Found on the PagerDuty service's <strong>Events API v2</strong> integration page
                  (Service → Integrations → Add Integration → Events API v2). Stored encrypted; never returned to the browser.
                </p>
              </div>
            )}

            {/* Defaults */}
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
                <p className="cc-hint">Used when the alert carries no severity field.</p>
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
                <p className="cc-hint">Fallback when no event_type can be parsed.</p>
              </div>
            </div>

            {/* ── Event Type Mappings ─────────────────────────────────────────── */}
            <div className="cc-field-group" style={{ borderTop: '1px solid #2d3748', paddingTop: '1rem', marginTop: '0.5rem' }}>
              <label className="cc-label">
                Event Type Mappings
              </label>
              <p className="cc-hint" style={{ marginBottom: '0.75rem' }}>
                Translate {meta.displayName} alert names to canonical platform event types so the correct runbook
                is selected. Exact matches are checked first; unmatched alerts fall back to keyword heuristics
                then LLM classification.
              </p>

              {/* Column headers */}
              {eventTypeMappings.length > 0 && (
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 24px 1fr 28px',
                  gap: '6px',
                  padding: '0 2px',
                  marginBottom: '4px',
                }}>
                  <span style={{ fontSize: '0.65rem', color: '#6b7a93', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    {meta.displayName} alert name
                  </span>
                  <span />
                  <span style={{ fontSize: '0.65rem', color: '#6b7a93', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    Platform event type
                  </span>
                  <span />
                </div>
              )}

              {/* Mapping rows */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                {eventTypeMappings.map((m, i) => (
                  <div key={i} style={{
                    display: 'grid',
                    gridTemplateColumns: '1fr 24px 1fr 28px',
                    gap: '6px',
                    alignItems: 'center',
                  }}>
                    <input
                      style={{ ...INPUT_STYLE, fontFamily: 'monospace', fontSize: '0.78rem' }}
                      placeholder={`e.g. ${['CPUThrottlingHigh','DiskAlmostFull','ServiceUnreachable','KubePodCrash'][i % 4]}`}
                      value={m.from}
                      onChange={e => updateMapping(i, 'from', e.target.value)}
                    />
                    <span style={{ color: '#4a5568', textAlign: 'center', fontSize: '0.9rem' }}>→</span>
                    <select
                      style={{ ...INPUT_STYLE, fontSize: '0.78rem', cursor: 'pointer' }}
                      value={m.to}
                      onChange={e => updateMapping(i, 'to', e.target.value)}
                    >
                      <option value="">— select type —</option>
                      {CANONICAL_EVENT_TYPES.map(t => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => removeMapping(i)}
                      title="Remove mapping"
                      style={{
                        background: 'none', border: 'none',
                        color: '#6b7a93', cursor: 'pointer',
                        fontSize: '1rem', padding: '0 4px',
                        lineHeight: 1,
                      }}
                      onMouseEnter={e => (e.currentTarget.style.color = '#ef4444')}
                      onMouseLeave={e => (e.currentTarget.style.color = '#6b7a93')}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>

              {/* Add row button */}
              <button
                type="button"
                onClick={addMapping}
                style={{
                  marginTop: '0.5rem',
                  fontSize: '0.78rem',
                  color: '#93c5fd',
                  background: 'none',
                  border: '1px dashed #3d4557',
                  borderRadius: '6px',
                  padding: '0.3rem 0.9rem',
                  cursor: 'pointer',
                  transition: 'border-color 150ms',
                }}
                onMouseEnter={e => (e.currentTarget.style.borderColor = '#93c5fd')}
                onMouseLeave={e => (e.currentTarget.style.borderColor = '#3d4557')}
              >
                + Add Mapping
              </button>

              {/* Resolution order note */}
              <div style={{
                marginTop: '0.6rem',
                padding: '0.5rem 0.7rem',
                borderRadius: '6px',
                background: '#0d1117',
                border: '1px solid #21262d',
                fontSize: '0.72rem',
                color: '#6b7a93',
                lineHeight: 1.6,
              }}>
                <span style={{ color: '#a0aec0', fontWeight: 600 }}>Resolution order: </span>
                Exact mapping → Case-insensitive mapping → Keyword heuristics → LLM classification → Default event type
              </div>
            </div>

            {/* Enabled toggle */}
            <div className="cc-field-group">
              <label className="cc-label">Status</label>
              <label className="cc-toggle">
                <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
                <span className="cc-toggle-track" />
                <span className="cc-toggle-label">
                  {enabled ? 'Enabled — webhook is active' : 'Disabled — webhook will reject alerts'}
                </span>
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
                  ? '⚠ Auto-remediation is ON. Runbooks will execute without human approval when a governance policy allows it. Only enable if this platform is the sole remediation system for this source.'
                  : '✓ Safe default. Runbook steps are presented as recommendations. An operator must review and approve before any execution begins.'}
              </div>
            </div>

            {saveMsg && (
              <p className="cc-save-msg" style={{
                color: saveMsg.startsWith('✓') ? '#6ee7b7' : saveMsg.startsWith('⚠') ? '#fbbf24' : '#fca5a5',
              }}>
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

        {/* ── Webhook Setup tab ─────────────────────────────────────────────── */}
        {tab === 'webhook' && (
          <div className="cc-body">
            <p style={{ fontSize: '0.82rem', color: '#a0aec0', margin: 0 }}>
              Configure {meta.displayName} to POST alerts to this platform.
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
                    color:      copied ? '#6ee7b7' : '#a0aec0',
                    fontSize:   '0.78rem',
                    fontWeight: 600,
                    cursor:     'pointer',
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
              <label className="cc-label">{meta.displayName} Setup Steps</label>
              <ol style={{ fontSize: '0.82rem', color: '#a0aec0', paddingLeft: '1.2rem', lineHeight: 1.8, margin: 0 }}>
                {meta.setupSteps.map((step, i) => (
                  <li key={i} dangerouslySetInnerHTML={{ __html: highlightHeader(step) }} />
                ))}
              </ol>
            </div>

            {/* Field mapping table */}
            <div className="cc-field-group">
              <label className="cc-label">Field Mapping</label>
              <p style={{ fontSize: '0.78rem', color: '#6b7a93', margin: '0 0 0.4rem' }}>
                How {meta.displayName} payload fields map to platform monitoring event fields:
              </p>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                <thead>
                  <tr>
                    {['Source field', 'Example', 'Maps to'].map(h => (
                      <th key={h} style={{
                        textAlign: 'left', padding: '0.3rem 0.5rem',
                        color: '#6b7a93', borderBottom: '1px solid #3d4557',
                        fontSize: '0.68rem', textTransform: 'uppercase',
                      }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {meta.fieldRows.map(([field, example, maps]) => (
                    <tr key={field}>
                      <td style={{ padding: '0.4rem 0.5rem', color: '#93c5fd', fontFamily: 'monospace', fontSize: '0.76rem', borderBottom: '1px solid #1e2535' }}>{field}</td>
                      <td style={{ padding: '0.4rem 0.5rem', color: '#a0aec0', borderBottom: '1px solid #1e2535' }}>{example}</td>
                      <td style={{ padding: '0.4rem 0.5rem', color: '#6b7a93', borderBottom: '1px solid #1e2535' }}>{maps}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Example payload */}
            <div className="cc-field-group">
              <label className="cc-label">Example {meta.displayName} Payload</label>
              <pre style={{ ...CODE_BOX, color: '#a0aec0', whiteSpace: 'pre-wrap', marginTop: '0.25rem' }}>
                {meta.examplePayload}
              </pre>
            </div>
          </div>
        )}

        {/* ── Status tab ────────────────────────────────────────────────────── */}
        {tab === 'status' && (
          <div className="cc-body">
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>

              <div style={{ padding: '0.75rem 1rem', background: '#252c3c', borderRadius: '8px', border: '1px solid #3d4557' }}>
                <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#6b7a93', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.35rem' }}>
                  Connector status
                </p>
                <p style={{ fontSize: '0.9rem', fontWeight: 700, margin: 0, color: detail?.enabled ? '#10b981' : '#f59e0b' }}>
                  {!detail?.configured
                    ? 'Not configured — save settings first'
                    : detail.enabled
                      ? `Active — receiving ${meta.displayName} alerts`
                      : 'Configured but disabled'}
                </p>
              </div>

              <div style={{ padding: '0.75rem 1rem', background: '#252c3c', borderRadius: '8px', border: '1px solid #3d4557' }}>
                <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#6b7a93', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.35rem' }}>
                  Webhook secret
                </p>
                <p style={{ fontSize: '0.85rem', color: '#a0aec0', margin: 0 }}>
                  {detail?.webhook_secret_set
                    ? `✓ Secret is configured — alerts must include ${meta.secretHeader}`
                    : `⚠ No secret — all POSTs to the webhook URL are accepted`}
                </p>
              </div>

              {isPagerDuty && (
                <div style={{ padding: '0.75rem 1rem', background: '#252c3c', borderRadius: '8px', border: '1px solid #3d4557' }}>
                  <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#6b7a93', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.35rem' }}>
                    Outbound escalation
                  </p>
                  <p style={{ fontSize: '0.85rem', color: '#a0aec0', margin: 0 }}>
                    {detail?.routing_key_set
                      ? '✓ Routing key is configured — alert_escalate / alert_update will trigger real PagerDuty incidents'
                      : '⚠ No routing key — alert_escalate / alert_update will fall back to Slack, or fail if Slack isn\'t configured either'}
                  </p>
                </div>
              )}

              <div style={{ padding: '0.75rem 1rem', background: '#252c3c', borderRadius: '8px', border: '1px solid #3d4557' }}>
                <p style={{ fontSize: '0.7rem', fontWeight: 600, color: '#6b7a93', textTransform: 'uppercase', letterSpacing: '0.05em', margin: '0 0 0.35rem' }}>
                  Webhook URL
                </p>
                <p style={{ ...CODE_BOX as any, margin: 0 }}>
                  {`${window.location.origin}/api/connectors/${connectorId}/webhook`}
                </p>
              </div>

              <p style={{ fontSize: '0.78rem', color: '#6b7a93', margin: '0.5rem 0 0' }}>
                Ingested alerts appear in the <strong style={{ color: '#a0aec0' }}>Events Feed</strong>.
                Qualified alerts automatically create incidents and appear in the{' '}
                <strong style={{ color: '#a0aec0' }}>Incidents</strong> list.
                Recovery / resolved events are automatically dropped.
              </p>
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
