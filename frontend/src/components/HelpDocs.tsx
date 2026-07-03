import { useState } from 'react'
import { IconX } from './icons'
import MarkdownDoc from './MarkdownDoc'

// Platform guides (curated)
import policyGuide          from '../docs/policy-guide.md?raw'
import runbookGuide         from '../docs/runbook-guide.md?raw'
import connectorSetup       from '../docs/connector-setup.md?raw'
import platformIntelligence from '../docs/platform-intelligence.md?raw'

// Getting started
import quickstart        from '../docs/QUICKSTART.md?raw'
import features          from '../docs/FEATURES.md?raw'
import platformSelection from '../docs/PLATFORM_SELECTION.md?raw'

// Monitoring & watchers
import watcherSetup          from '../docs/WATCHER_SETUP.md?raw'
import watcherQuickStart     from '../docs/WATCHER_QUICK_START.md?raw'
import watcherIntegration    from '../docs/WATCHER_INTEGRATION.md?raw'
import watcherTroubleshooting from '../docs/WATCHER_TROUBLESHOOTING.md?raw'
import healthCheck           from '../docs/HEALTH_CHECK_GUIDE.md?raw'
import stormDetection        from '../docs/storm-detection.md?raw'

// Runbooks & automation
import runbookMonitoring from '../docs/RUNBOOK_AND_MONITORING_SETUP.md?raw'
import visualEditor      from '../docs/VISUAL_RUNBOOK_EDITOR.md?raw'
import mechanicAgent     from '../docs/MECHANIC_AGENT_ARCHITECTURE.md?raw'

// Integrations
import slackSetup from '../docs/SLACK_SETUP.md?raw'

// Reference
import architecture       from '../docs/ARCHITECTURE.md?raw'
import dataFlows          from '../docs/DATA_FLOWS.md?raw'
import apiReference       from '../docs/API_REFERENCE.md?raw'
import technicalReference from '../docs/TECHNICAL_REFERENCE.md?raw'

import './HelpDocs.css'

interface HelpDocsProps {
  onClose: () => void
}

interface Stage {
  num: string
  color: string
  title: string
  desc: string
  gates: Gate[]
}

interface Gate {
  icon: string
  label: string
  detail?: string
  tags?: string[]
  outcome?: { pass?: string; warn?: string; fail?: string[] }
  highlight?: boolean
}

const STAGES: Stage[] = [
  {
    num: '01', color: 'blue',
    title: 'Event Ingestion',
    desc: 'Alert arrives via watcher poll or webhook push',
    gates: [
      {
        icon: '🔌', label: 'Source',
        detail: 'Monitoring watchers (poll loop) or inbound webhook from Datadog / Dynatrace / Prometheus / PagerDuty / Zabbix',
        tags: ['alert_payload.type', 'alert_payload.resource_name', 'alert_payload.severity'],
      },
      {
        icon: '⚙️', label: 'Watcher Config',
        detail: 'Each watcher defines which event types it monitors and at what poll interval. Watchers must be enabled.',
        tags: ['watcher.enabled', 'watcher.event_types[]', 'watcher.poll_interval_seconds'],
      },
    ],
  },
  {
    num: '02', color: 'amber',
    title: 'Dedup Gate',
    desc: 'Prevents duplicate incidents for the same resource + event type',
    gates: [
      {
        icon: '🔑', label: 'Key',
        detail: 'Dedup key = (resource_name, event_type) — one open condition per resource+type at a time',
      },
      {
        icon: '⏱️', label: 'TTL Rules',
        detail: 'Dismissed condition (qualified=false) expires after 15 minutes, then re-evaluates.\nQualified condition (qualified=true) — active incident exists, blocks re-fire for 24 hours.',
        tags: ['DISMISSED_TTL_MINUTES = 15', 'TTL_HOURS = 24'],
      },
      {
        icon: '🔄', label: 'CMDB Change Override',
        detail: "If a CI's environment, criticality, or service_class changes in the CMDB Editor, any dismissed open conditions for that resource are immediately closed so the next watcher cycle re-scores from scratch.",
      },
      {
        icon: '🚦', label: 'Outcome',
        outcome: {
          pass: 'No open condition → proceed to CMDB enrichment',
          fail: ['Open condition exists within TTL → event dropped, no incident created'],
        },
      },
    ],
  },
  {
    num: '03', color: 'sky',
    title: 'CMDB Enrichment (LibrarianAgent)',
    desc: 'Pulls CI context from Neo4j graph',
    gates: [
      {
        icon: '📋', label: 'Data Pulled',
        detail: 'Resource type, environment, criticality, service class, platform (docker/kubernetes/linux/windows), service dependencies, owner teams',
        tags: ['cmdb.environment', 'cmdb.criticality', 'cmdb.service_class', 'cmdb.platform'],
      },
      {
        icon: '🔗', label: 'Source',
        detail: 'Neo4j graph (live); CMDB data kept current via ServiceNow connector sync. Missing CI → defaults applied (environment=dev, criticality=low).',
        tags: ['ConnectorHub → ServiceNow → Sync Now'],
      },
    ],
  },
  {
    num: '04', color: 'amber',
    title: 'Risk & Severity Scoring (SentinelAgent)',
    desc: 'Calculates risk score, blast radius, severity',
    gates: [
      {
        icon: '📊', label: 'Inputs',
        detail: 'Alert severity + CMDB criticality + environment weight + number of downstream dependencies → composite risk score (0–100)',
        tags: ['risk_score', 'blast_radius', 'severity → low/medium/high/critical'],
      },
      {
        icon: '⚖️', label: 'Environment Weight',
        detail: 'prod > staging > dev — same alert in prod scores higher than in dev, potentially crossing a severity tier',
      },
    ],
  },
  {
    num: '05', color: 'purple',
    title: 'Runbook Selection (MechanicAgent)',
    desc: '4-pass cascade lookup — most specific to most generic',
    gates: [
      {
        icon: '🎯', label: 'Lookup Order',
        detail: 'Pass 1: event_type + service + platform (exact)\nPass 2: event_type + service + any platform\nPass 3: event_type + no service + platform\nPass 4: event_type + no service + any platform\nWithin each pass: ranked by success_rate DESC, then confidence DESC',
        tags: ['runbook.event_type', 'runbook.service', 'runbook.platform', 'runbook.enabled'],
      },
      {
        icon: '📈', label: 'Runbook Stats (feed the Confidence Gate)',
        detail: 'Updated after every execution',
        tags: ['runbook.confidence (0–1)', 'runbook.successful_executions', 'runbook.success_rate', 'runbook.total_executions'],
      },
      {
        icon: '🚦', label: 'Outcome',
        outcome: {
          pass: 'Runbook found → steps loaded, stats available for confidence gate',
          warn: 'No runbook → AI generates ad-hoc remediation plan (no confidence gate possible)',
        },
      },
    ],
  },
  {
    num: '06', color: 'red',
    title: 'Policy & Approval Gate (PolicyBrokerAgent)',
    desc: 'Most specific matching policy wins; confidence gate can bypass',
    gates: [
      {
        icon: '📋', label: 'Policy Matching Rules (AND logic)',
        detail: 'All specified rules on a policy must match for it to apply. Multiple matches → sorted by approval_priority (lowest number wins).',
        tags: ['policy.rules.min_severity', 'policy.rules.environment', 'policy.rules.service', 'policy.rules.min_risk_score', 'policy.enabled'],
      },
      {
        icon: '✋', label: 'Manual Approval Flag',
        detail: 'If the winning policy has this set, a human approval step is required before remediation runs.',
        tags: ['policy.requires_manual_approval', 'policy.approval_priority (1–100)'],
      },
      {
        icon: '🎯', label: 'Confidence Gate — bypasses manual approval',
        detail: 'Only checked when requires_manual_approval = true AND a confidence gate is configured on the policy. Looks up the matched runbook\'s live stats.',
        tags: ['policy.confidence_gate_threshold (e.g. 0.90)', 'policy.confidence_gate_min_runs (e.g. 10)'],
        highlight: true,
        outcome: {
          pass: 'runbook.confidence ≥ threshold AND runbook.successful_executions ≥ min_runs → approval bypassed',
          warn: 'No runbook matched → gate skipped, approval required',
          fail: ['Either condition not met → falls through to human approval queue'],
        },
      },
      {
        icon: '🚦', label: 'Outcome',
        outcome: {
          pass: 'No approval needed (or gate passed) → remediation starts immediately',
          warn: 'Approval required → incident enters pending queue; waits for human decision',
          fail: ['No matching policy → conservative default: approval required'],
        },
      },
      {
        icon: '⚙️', label: 'Constraints (from winning policy)',
        tags: ['constraints.max_blast_radius', 'constraints.max_restart_frequency', 'constraints.requires_post_monitoring'],
      },
    ],
  },
  {
    num: '07', color: 'amber',
    title: 'Human Approval Queue',
    desc: 'Only reached if policy requires approval and confidence gate did not pass',
    gates: [
      {
        icon: '👤', label: 'Operator Actions',
        detail: 'Operator reviews proposed action, blast radius, runbook steps, risk score, and policy match reason',
      },
      {
        icon: '🚦', label: 'Outcome',
        outcome: {
          pass: 'Approved → remediation starts',
          warn: 'No response → remains in pending queue indefinitely',
          fail: ['Rejected → incident closed, condition cleared, no remediation'],
        },
      },
    ],
  },
  {
    num: '08', color: 'green',
    title: 'Remediation Execution',
    desc: 'Runbook steps executed against the target resource',
    gates: [
      {
        icon: '🔧', label: 'Execution',
        detail: 'Diagnostics run first, then remediation steps (restart, scale, cleanup…), then validation steps. Each step result logged to workflow timeline.',
        tags: ['runbook.diagnostics[]', 'runbook.steps[]', 'runbook.validation[]'],
      },
      {
        icon: '📤', label: 'ServiceNow Push (if configured)',
        detail: 'If ServiceNow connector is configured with incident_push capability, a SNOW incident is auto-created via Celery task when the platform incident opens, and updated on state changes.',
        tags: ['snow_push_incident_created task', 'snow_push_incident_state task'],
      },
      {
        icon: '📈', label: 'Runbook Stats Update',
        detail: 'Outcome (success/failure) recorded against the runbook. Updates confidence, success_rate, successful_executions — which feeds future confidence gate evaluations.',
      },
    ],
  },
  {
    num: '09', color: 'sky',
    title: 'Post-Remediation Monitoring',
    desc: 'Only active when policy constraint requires it',
    gates: [
      {
        icon: '👁️', label: 'Triggered by Policy Constraint',
        detail: 'If requires_post_monitoring = true on the winning policy, the system watches the resource after remediation to confirm the fix held',
        tags: ['constraints.requires_post_monitoring'],
      },
      {
        icon: '🚦', label: 'Outcome',
        outcome: {
          pass: 'Resource stable → incident closed',
          warn: 'Resource still degraded → may re-trigger a new event cycle',
        },
      },
    ],
  },
  {
    num: '10', color: 'slate',
    title: 'Incident Closure',
    desc: 'Condition cleared — dedup state reset for this resource',
    gates: [
      {
        icon: '🔓', label: 'Dedup Reset',
        detail: 'The event_condition_state row for (resource_name, event_type) is set to status=closed. The 24h qualified TTL is released. A new event for the same resource can now create a new incident.',
      },
      {
        icon: '📤', label: 'SNOW State Sync',
        detail: 'If ServiceNow push is configured, the linked SNOW incident is updated to Resolved/Closed state via snow_push_incident_state Celery task.',
      },
      {
        icon: '📊', label: 'MTTR Recorded',
        detail: 'Time-to-resolve feeds the MTTR metrics dashboard (breakdown by severity, environment, service)',
      },
    ],
  },
]

interface DocEntry {
  id: string
  icon: string
  label: string
  content?: string  // markdown — omit for the custom lifecycle view
}

interface DocSection {
  label: string
  items: DocEntry[]
}

const SECTIONS: DocSection[] = [
  {
    label: 'Platform Guides',
    items: [
      { id: 'lifecycle',  icon: '🔄', label: 'Incident Lifecycle' },
      { id: 'policy',     icon: '📋', label: 'Policy Guide',    content: policyGuide },
      { id: 'runbook',    icon: '📖', label: 'Runbook Guide',   content: runbookGuide },
      { id: 'connector',  icon: '🔌', label: 'Connector Setup', content: connectorSetup },
      { id: 'platform-intelligence', icon: '🧠', label: 'Platform Intelligence', content: platformIntelligence },
    ],
  },
  {
    label: 'Getting Started',
    items: [
      { id: 'quickstart',         icon: '🚀', label: 'Quick Start',         content: quickstart },
      { id: 'features',           icon: '✨', label: 'Feature Catalog',     content: features },
      { id: 'platform-selection', icon: '🎯', label: 'Platform Selection',  content: platformSelection },
    ],
  },
  {
    label: 'Monitoring & Watchers',
    items: [
      { id: 'watcher-setup',           icon: '👁️', label: 'Watcher Setup',          content: watcherSetup },
      { id: 'watcher-quickstart',      icon: '⚡', label: 'Watcher Quick Start',    content: watcherQuickStart },
      { id: 'watcher-integration',     icon: '🔗', label: 'Watcher Integration',    content: watcherIntegration },
      { id: 'watcher-troubleshooting', icon: '🔧', label: 'Troubleshooting',        content: watcherTroubleshooting },
      { id: 'health-check',            icon: '💚', label: 'Health Check Guide',     content: healthCheck },
      { id: 'storm-detection',         icon: '⛈️', label: 'Storm Detection',        content: stormDetection },
    ],
  },
  {
    label: 'Runbooks & Automation',
    items: [
      { id: 'runbook-monitoring', icon: '📋', label: 'Runbook & Monitoring Setup', content: runbookMonitoring },
      { id: 'visual-editor',      icon: '🎨', label: 'Visual Runbook Editor',      content: visualEditor },
      { id: 'mechanic-agent',     icon: '🤖', label: 'Mechanic Agent',             content: mechanicAgent },
    ],
  },
  {
    label: 'Integrations',
    items: [
      { id: 'slack', icon: '💬', label: 'Slack Setup', content: slackSetup },
    ],
  },
  {
    label: 'Reference',
    items: [
      { id: 'architecture',        icon: '🏗️', label: 'Architecture',         content: architecture },
      { id: 'data-flows',          icon: '🔀', label: 'Data Flows',           content: dataFlows },
      { id: 'api-reference',       icon: '📡', label: 'API Reference',        content: apiReference },
      { id: 'technical-reference', icon: '📚', label: 'Technical Reference',  content: technicalReference },
    ],
  },
]

// Flat lookup map for rendering content
const DOC_MAP = new Map<string, DocEntry>(
  SECTIONS.flatMap(s => s.items).map(d => [d.id, d])
)

function StageBlock({ stage }: { stage: Stage }) {
  const [open, setOpen] = useState(true)

  return (
    <div className={`hd-stage hd-c-${stage.color}`}>
      <div className="hd-stage-connector">
        <div className="hd-stage-dot" />
        {stage.num !== '10' && <div className="hd-stage-line" />}
      </div>
      <div className="hd-stage-body">
        <div
          className={`hd-stage-header${open ? '' : ' collapsed'}`}
          onClick={() => setOpen(o => !o)}
        >
          <span className="hd-stage-num">{stage.num}</span>
          <span className="hd-stage-title">{stage.title}</span>
          <span className="hd-stage-desc">{stage.desc}</span>
          <span className={`hd-chevron${open ? ' open' : ''}`}>▾</span>
        </div>

        {open && (
          <div className="hd-gates">
            {stage.gates.map((g, i) => (
              <div key={i} className={`hd-gate${g.highlight ? ' hd-gate-highlight' : ''}`}>
                <span className="hd-gate-icon">{g.icon}</span>
                <div>
                  <div className="hd-gate-label" style={g.highlight ? { color: '#a78bfa' } : undefined}>
                    {g.label}
                  </div>
                  {g.detail && (
                    <div className="hd-gate-detail">
                      {g.detail.split('\n').map((line, li) => (
                        <span key={li}>{line}{li < g.detail!.split('\n').length - 1 && <br />}</span>
                      ))}
                    </div>
                  )}
                  {g.tags && (
                    <div style={{ marginTop: '4px' }}>
                      {g.tags.map(t => <span key={t} className="hd-tag">{t}</span>)}
                    </div>
                  )}
                  {g.outcome && (
                    <div className="hd-gate-outcome">
                      {g.outcome.pass  && <div><span className="hd-pass">✓ {g.outcome.pass}</span></div>}
                      {g.outcome.warn  && <div><span className="hd-warn">⚠ {g.outcome.warn}</span></div>}
                      {g.outcome.fail?.map((f, fi) => (
                        <div key={fi}><span className="hd-fail">✗ {f}</span></div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function HelpDocs({ onClose }: HelpDocsProps) {
  const [activeDoc, setActiveDoc] = useState('lifecycle')

  return (
    <div className="hd-overlay" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="hd-panel">

        {/* Header */}
        <div className="hd-header">
          <div className="hd-header-left">
            <div className="hd-header-icon">?</div>
            <div>
              <div className="hd-header-title">Help &amp; Documentation</div>
              <div className="hd-header-sub">Platform reference — how things work</div>
            </div>
          </div>
          <button className="hd-close" onClick={onClose} title="Close">
            <IconX size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="hd-body">

          {/* Sidebar */}
          <nav className="hd-sidebar">
            {SECTIONS.map(section => (
              <div key={section.label} className="hd-sidebar-section">
                <div className="hd-sidebar-section-label">{section.label}</div>
                {section.items.map(doc => (
                  <button
                    key={doc.id}
                    className={`hd-sidebar-item${activeDoc === doc.id ? ' active' : ''}`}
                    onClick={() => setActiveDoc(doc.id)}
                  >
                    <span className="hd-sidebar-item-icon">{doc.icon}</span>
                    {doc.label}
                  </button>
                ))}
              </div>
            ))}
          </nav>

          {/* Content */}
          <main className="hd-content">
            {activeDoc === 'lifecycle' && (
              <>
                <div className="hd-doc-title">Incident Lifecycle</div>
                <div className="hd-doc-subtitle">
                  Every gate, setting, and branch — from raw alert through remediation to closure.
                  Click any stage to collapse it.
                </div>
                <div className="hd-flow">
                  {STAGES.map(s => <StageBlock key={s.num} stage={s} />)}
                </div>
              </>
            )}
            {activeDoc !== 'lifecycle' && (() => {
              const doc = DOC_MAP.get(activeDoc)
              return doc?.content
                ? <MarkdownDoc content={doc.content} />
                : null
            })()}
          </main>

        </div>
      </div>
    </div>
  )
}
