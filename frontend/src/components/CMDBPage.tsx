import React, { useState, useEffect, useRef, useCallback, lazy, Suspense } from 'react'
import { NodeObject, LinkObject } from 'react-force-graph-2d'
const ForceGraph2D = lazy(() => import('react-force-graph-2d'))
import { getToken } from '../hooks/useCurrentUser'
import {
  IconRefresh,
  IconX,
  IconAlertTriangle,
  IconNetwork,
  IconClipboardList,
  IconEye,
  IconDatabase,
} from './icons'
import CMDBEditor from './CMDBEditor'

// ─── Types ────────────────────────────────────────────────────────────────────

// CI class — derived from Neo4j sub-labels by the backend
type CIClass = 'Service' | 'Server' | 'Container' | 'Database' | 'Application'

interface CINode extends NodeObject {
  id: string
  name: string
  ci_class?: CIClass
  tier?: number
  is_spof?: boolean
  business_criticality?: string
  type?: string
  docker_image?: string
  platform?: string
  cpu_limit_cores?: number | null
  memory_limit_mb?: number | null
  ip_address?: string | null
  exposed_ports?: string | null
  container_status?: string
  health_status?: string | null
  cpu_percent?: number | null
  memory_mb?: number | null
  memory_pct?: number | null
  pids?: number | null
  last_metrics_update?: string | null
  last_discovered_at?: string | null
  sla_percent?: number
  failover_available?: boolean
  user_count?: number
  environment?: string
  owner?: string
  depends_on?: Array<{ name: string; tier: number; status: string; health: string }>
  depended_on_by?: Array<{ name: string; tier: number; status: string; health: string }>
  incident_count?: number
  max_incident_severity?: string | null
  x?: number
  y?: number
}

type RelType = 'DEPENDS_ON' | 'RUNS_ON' | 'HOSTED_ON' | 'PART_OF'

interface CILink extends LinkObject {
  source: string | CINode
  target: string | CINode
  rel_type?: RelType
}

interface GraphData {
  nodes: CINode[]
  links: CILink[]
  meta?: {
    total_nodes: number
    total_links: number
    db_total?: number
    truncated?: boolean
    service_filter?: string | null
    max_nodes?: number
    tier_counts: Record<string, number>
  }
}

interface CMDBPageProps {
  darkMode?: boolean
}

type LayoutMode  = 'force' | 'hierarchy' | 'radial'
type DagMode     = 'td' | 'bu' | 'lr' | 'rl' | 'radialout' | 'radialin' | undefined
type RingOverlay = 'none' | 'health' | 'tier' | 'incidents'

// ─── Constants ────────────────────────────────────────────────────────────────

const TIER_COLORS: Record<number, string> = {
  1: '#ef4444',
  2: '#f59e0b',
  3: '#10b981',
}

const TIER_LABELS: Record<number, string> = {
  1: 'Tier 1 — Critical',
  2: 'Tier 2 — Important',
  3: 'Tier 3 — Supporting',
}

const NODE_RADIUS     = 14
const HIT_RADIUS      = 20
const AUTO_REFRESH_MS = 30_000

// ─── Canvas icon drawing ──────────────────────────────────────────────────────

// Accent colour per CI class — used for node gradient and class badge
function classAccent(ciClass?: CIClass): string {
  switch (ciClass) {
    case 'Server':      return '#6366f1'  // indigo
    case 'Application': return '#a855f7'  // purple
    case 'Container':   return '#06b6d4'  // cyan
    case 'Database':    return '#f59e0b'  // amber
    default:            return '#334155'  // slate (Service)
  }
}

// Inner gradient start colour per class (lighter focal point)
function classGradientStart(ciClass?: CIClass): string {
  switch (ciClass) {
    case 'Server':      return '#4338ca'
    case 'Application': return '#7c3aed'
    case 'Container':   return '#0891b2'
    case 'Database':    return '#b45309'
    default:            return '#334155'
  }
}

function getIconKey(node: CINode): string {
  const ciClass = node.ci_class
  const name = (node.name || '').toLowerCase()
  const type = (node.type || '').toLowerCase()
  const plat = (node.platform || '').toLowerCase()

  // Class-driven icons take priority
  if (ciClass === 'Server')      return plat.includes('windows') ? 'windows' : 'server-rack'
  if (ciClass === 'Application') return 'application'
  if (ciClass === 'Container')   return plat.includes('windows') ? 'windows' : 'linux'
  if (ciClass === 'Database')    return 'database'

  // Fall back to name/type heuristics for legacy nodes
  if (name.includes('postgres') || name.includes('neo4j') || type.includes('database')) return 'database'
  if (name.includes('redis') || type.includes('cache') || type.includes('broker'))       return 'database'
  if (name.includes('sentinel'))  return 'radar'
  if (name.includes('flower'))    return 'activity'
  if (name.includes('watcher'))   return 'eye'
  if (name.includes('celery') || name.includes('worker')) return 'worker'
  if (name.includes('frontend'))  return 'frontend'
  if (plat.includes('windows'))   return 'windows'
  if (plat.includes('linux'))     return 'linux'
  return 'server'
}

function drawIcon(ctx: CanvasRenderingContext2D, key: string, cx: number, cy: number, r: number) {
  const s  = r * 0.52
  const lw = Math.max(1.4, r * 0.075)

  ctx.save()
  ctx.strokeStyle = 'rgba(255,255,255,0.88)'
  ctx.fillStyle   = 'rgba(255,255,255,0.88)'
  ctx.lineWidth   = lw
  ctx.lineCap     = 'round'
  ctx.lineJoin    = 'round'

  switch (key) {

    case 'database': {
      const ew = s, eh = s * 0.3, top = cy - s * 0.6, bot = cy + s * 0.6
      ctx.beginPath(); ctx.ellipse(cx, top, ew, eh, 0, 0, Math.PI * 2); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(cx - ew, top); ctx.lineTo(cx - ew, bot); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(cx + ew, top); ctx.lineTo(cx + ew, bot); ctx.stroke()
      ctx.beginPath(); ctx.ellipse(cx, bot, ew, eh, 0, 0, Math.PI); ctx.stroke()
      break
    }

    case 'server': {
      const rw = s * 1.5, rh = s * 0.55
      ctx.strokeRect(cx - rw / 2, cy - s * 0.75, rw, rh)
      ctx.strokeRect(cx - rw / 2, cy - s * 0.05, rw, rh)
      ctx.beginPath()
      ctx.arc(cx + rw / 2 - s * 0.28, cy - s * 0.47, lw * 1.1, 0, Math.PI * 2)
      ctx.arc(cx + rw / 2 - s * 0.28, cy + s * 0.23, lw * 1.1, 0, Math.PI * 2)
      ctx.fill()
      break
    }

    case 'linux': {
      ctx.beginPath(); ctx.arc(cx, cy, s * 0.88, 0, Math.PI * 2); ctx.stroke()
      ctx.beginPath(); ctx.arc(cx, cy, s * 0.38, 0, Math.PI * 2); ctx.stroke()
      for (let i = 0; i < 3; i++) {
        const a = (i * 2 * Math.PI) / 3 - Math.PI / 2
        ctx.beginPath()
        ctx.arc(cx + s * 0.63 * Math.cos(a), cy + s * 0.63 * Math.sin(a), lw * 1.3, 0, Math.PI * 2)
        ctx.fill()
      }
      break
    }

    case 'windows': {
      const g = s * 0.42, gap = s * 0.18
      ctx.strokeRect(cx - g - gap / 2, cy - g - gap / 2, g, g)
      ctx.strokeRect(cx + gap / 2,     cy - g - gap / 2, g, g)
      ctx.strokeRect(cx - g - gap / 2, cy + gap / 2,     g, g)
      ctx.strokeRect(cx + gap / 2,     cy + gap / 2,     g, g)
      break
    }

    case 'eye': {
      ctx.beginPath()
      ctx.moveTo(cx - s, cy)
      ctx.quadraticCurveTo(cx, cy - s * 0.72, cx + s, cy)
      ctx.quadraticCurveTo(cx, cy + s * 0.72, cx - s, cy)
      ctx.closePath(); ctx.stroke()
      ctx.beginPath(); ctx.arc(cx, cy, s * 0.3, 0, Math.PI * 2); ctx.fill()
      break
    }

    case 'radar': {
      ctx.beginPath(); ctx.arc(cx, cy, s * 0.88, -2.2, -0.94); ctx.stroke()
      ctx.beginPath(); ctx.arc(cx, cy, s * 0.50, -2.2, -0.94); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx, cy - s * 0.88); ctx.stroke()
      ctx.beginPath(); ctx.arc(cx, cy, lw * 1.4, 0, Math.PI * 2); ctx.fill()
      break
    }

    case 'worker': {
      ctx.lineWidth = lw * 1.5
      ctx.beginPath()
      ctx.moveTo(cx - s * 0.55, cy + s * 0.65)
      ctx.lineTo(cx + s * 0.25, cy - s * 0.3)
      ctx.stroke()
      ctx.lineWidth = lw
      ctx.beginPath(); ctx.arc(cx + s * 0.48, cy - s * 0.55, s * 0.36, 0, Math.PI * 2); ctx.stroke()
      break
    }

    case 'activity': {
      ctx.beginPath()
      ctx.moveTo(cx - s,        cy)
      ctx.lineTo(cx - s * 0.3,  cy)
      ctx.lineTo(cx - s * 0.1,  cy - s * 0.72)
      ctx.lineTo(cx + s * 0.15, cy + s * 0.72)
      ctx.lineTo(cx + s * 0.35, cy)
      ctx.lineTo(cx + s,        cy)
      ctx.stroke()
      break
    }

    case 'frontend': {
      ctx.beginPath()
      ctx.moveTo(cx + s * 0.1,  cy - s)
      ctx.lineTo(cx - s * 0.38, cy - s * 0.05)
      ctx.lineTo(cx + s * 0.08, cy - s * 0.05)
      ctx.lineTo(cx - s * 0.1,  cy + s)
      ctx.lineTo(cx + s * 0.52, cy + s * 0.05)
      ctx.lineTo(cx + s * 0.05, cy + s * 0.05)
      ctx.closePath(); ctx.stroke()
      break
    }

    // Server rack — 3 rack-mount units stacked
    case 'server-rack': {
      const rw = s * 1.5, rh = s * 0.38, gap = s * 0.12
      const top = cy - rh * 1.5 - gap
      for (let i = 0; i < 3; i++) {
        const y = top + i * (rh + gap)
        ctx.strokeRect(cx - rw / 2, y, rw, rh)
        ctx.beginPath()
        ctx.arc(cx + rw / 2 - s * 0.22, y + rh / 2, lw * 1.0, 0, Math.PI * 2)
        ctx.fill()
      }
      break
    }

    // Application — diamond / app icon
    case 'application': {
      ctx.beginPath()
      ctx.moveTo(cx,        cy - s)
      ctx.lineTo(cx + s,    cy)
      ctx.lineTo(cx,        cy + s)
      ctx.lineTo(cx - s,    cy)
      ctx.closePath()
      ctx.stroke()
      ctx.beginPath()
      ctx.arc(cx, cy, s * 0.22, 0, Math.PI * 2)
      ctx.fill()
      break
    }

    default: {
      ctx.strokeRect(cx - s * 0.75, cy - s * 0.75, s * 1.5, s * 1.5)
      ctx.beginPath(); ctx.arc(cx, cy, s * 0.2, 0, Math.PI * 2); ctx.fill()
    }
  }

  ctx.restore()
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function formatMB(mb?: number | null): string {
  if (mb == null) return '—'
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`
}

function timeAgo(iso?: string | null): string {
  if (!iso) return '—'
  const parsed = new Date(iso)
  if (isNaN(parsed.getTime())) return '—'  // Invalid date
  const s = Math.floor((Date.now() - parsed.getTime()) / 1000)
  if (s < 0) return 'now'  // Future date or clock skew
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

function healthColor(status?: string | null): string {
  if (status === 'healthy')   return '#10b981'
  if (status === 'degraded')  return '#f97316'
  if (status === 'unhealthy') return '#ef4444'
  return '#4b5563'
}

function incidentSeverityColor(severity?: string | null): string {
  if (severity === 'critical') return '#ef4444'
  if (severity === 'high')     return '#f97316'
  if (severity === 'medium')   return '#f59e0b'
  if (severity === 'low')      return '#3b82f6'
  return '#4b5563'
}

function getRingColor(node: CINode, overlay: RingOverlay): string {
  switch (overlay) {
    case 'health':    return healthColor(node.health_status)
    case 'tier':      return TIER_COLORS[node.tier ?? 0] ?? '#6b7280'
    case 'incidents': return incidentSeverityColor(node.max_incident_severity)
    default:          return ''
  }
}

function drawIncidentBadge(
  ctx: CanvasRenderingContext2D,
  count: number, severity: string | undefined | null,
  cx: number, cy: number, r: number,
) {
  const bx    = cx - r * 0.68
  const by    = cy - r * 0.68
  const label = count > 99 ? '99+' : String(count)
  const fs    = r * 0.33
  const br    = Math.max(r * 0.28, fs * 0.75)

  ctx.save()
  ctx.beginPath()
  ctx.arc(bx, by, br, 0, Math.PI * 2)
  ctx.fillStyle   = incidentSeverityColor(severity)
  ctx.fill()
  ctx.strokeStyle = 'rgba(0,0,0,0.35)'
  ctx.lineWidth   = 1
  ctx.stroke()

  ctx.fillStyle    = '#ffffff'
  ctx.font         = `bold ${fs}px -apple-system, sans-serif`
  ctx.textAlign    = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(label, bx, by)
  ctx.restore()
}

// ─── Design system tokens (always dark) ───────────────────────────────────────

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

// ─── Small reusable atoms ─────────────────────────────────────────────────────

const Chip: React.FC<{ label: string; color: string }> = ({ label, color }) => (
  <span style={{
    display: 'inline-block',
    padding: '2px 8px',
    borderRadius: 6,
    fontSize: 11,
    fontWeight: 700,
    color,
    backgroundColor: `${color}18`,
    border: `1px solid ${color}50`,
    letterSpacing: '0.03em',
    textTransform: 'uppercase',
  }}>{label}</span>
)

const TierChip: React.FC<{ tier?: number }> = ({ tier }) => {
  const map: Record<number, string> = { 1: '#ef4444', 2: '#f59e0b', 3: '#10b981' }
  const c = map[tier ?? 3] ?? '#6b7280'
  return <Chip label={`Tier ${tier ?? '?'}`} color={c} />
}

const HealthDot: React.FC<{ status?: string | null }> = ({ status }) => (
  <span style={{
    display: 'inline-block',
    width: 7,
    height: 7,
    borderRadius: '50%',
    backgroundColor: healthColor(status),
    marginRight: 5,
    verticalAlign: 'middle',
    flexShrink: 0,
  }} />
)

// ─── Sliding Side Panel ───────────────────────────────────────────────────────

const SidePanel: React.FC<{ node: CINode; onClose: () => void }> = ({ node, onClose }) => {
  const displayName = node.name.replace(/^agentic_os_/, '')

  const SectionHeader: React.FC<{ label: string }> = ({ label }) => (
    <p style={{
      fontSize: '0.65rem',
      fontWeight: 700,
      color: DS.txtS,
      textTransform: 'uppercase',
      letterSpacing: '0.07em',
      margin: '1.125rem 0 0.375rem',
      paddingBottom: '0.375rem',
      borderBottom: `1px solid ${DS.border}`,
    }}>{label}</p>
  )

  const Row: React.FC<{ label: string; value: React.ReactNode; mono?: boolean }> = ({ label, value, mono }) => (
    <div style={{
      display: 'flex',
      justifyContent: 'space-between',
      alignItems: 'flex-start',
      padding: '0.35rem 0',
      borderBottom: `1px solid ${DS.border}20`,
    }}>
      <span style={{ fontSize: '0.75rem', color: DS.txtS, flexShrink: 0, minWidth: 90 }}>{label}</span>
      <span style={{
        fontSize: '0.8rem',
        color: DS.txtP,
        textAlign: 'right',
        wordBreak: 'break-all',
        fontFamily: mono ? "'Monaco','Courier New',monospace" : 'inherit',
      }}>{value ?? '—'}</span>
    </div>
  )

  const DepRow: React.FC<{ d: { name: string; tier: number; health: string } }> = ({ d }) => (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      padding: '0.35rem 0.625rem',
      borderRadius: 6,
      backgroundColor: DS.raised,
      marginBottom: 3,
    }}>
      <span style={{
        width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
        backgroundColor: TIER_COLORS[d.tier] ?? '#6b7280',
      }} />
      <span style={{ fontSize: '0.8rem', color: DS.txtP, flex: 1 }}>
        {d.name.replace(/^agentic_os_/, '')}
      </span>
      {d.health === 'unhealthy' && <HealthDot status="unhealthy" />}
    </div>
  )

  return (
    <div style={{ padding: '1.25rem', height: '100%', boxSizing: 'border-box' }}>

      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '0.875rem' }}>
        <div>
          <p style={{ fontSize: '1rem', fontWeight: 700, color: DS.txtP, margin: '0 0 0.375rem' }}>{displayName}</p>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            <TierChip tier={node.tier} />
            {node.is_spof && <Chip label="SPoF" color="#f59e0b" />}
            {(node.incident_count ?? 0) > 0 && (
              <Chip label={`${node.incident_count} incident${node.incident_count !== 1 ? 's' : ''}`} color={incidentSeverityColor(node.max_incident_severity)} />
            )}
          </div>
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'none',
            border: `1px solid ${DS.border}`,
            color: DS.txtS,
            cursor: 'pointer',
            borderRadius: 6,
            padding: '3px 5px',
            display: 'flex',
            alignItems: 'center',
            flexShrink: 0,
            marginLeft: 8,
          }}
        >
          <IconX size={14} />
        </button>
      </div>

      <SectionHeader label="Live Status" />
      <Row label="Container"  value={node.container_status} />
      <Row label="Health"     value={<span style={{ display: 'flex', alignItems: 'center' }}><HealthDot status={node.health_status} />{node.health_status ?? '—'}</span>} />
      <Row label="CPU %"      value={node.cpu_percent != null ? `${node.cpu_percent.toFixed(1)}%` : null} />
      <Row label="Memory"     value={node.memory_mb != null ? `${formatMB(node.memory_mb)}` : null} />
      <Row label="PIDs"       value={node.pids} />
      <Row label="Updated"    value={timeAgo(node.last_metrics_update)} />

      <SectionHeader label="Configuration" />
      <Row label="Image"     value={node.docker_image} mono />
      <Row label="Platform"  value={node.platform} />
      <Row label="CPU limit" value={node.cpu_limit_cores != null ? `${node.cpu_limit_cores} cores` : null} />
      <Row label="Mem limit" value={formatMB(node.memory_limit_mb)} />
      <Row label="IP"        value={node.ip_address} mono />
      <Row label="Ports"     value={node.exposed_ports} />

      <SectionHeader label="Governance" />
      <Row label="SLA"         value={node.sla_percent != null ? `${node.sla_percent}%` : null} />
      <Row label="Owner"       value={node.owner} />
      <Row label="Failover"    value={node.failover_available ? 'Yes' : 'No'} />
      <Row label="Users"       value={node.user_count != null ? node.user_count.toLocaleString() : null} />
      <Row label="Criticality" value={node.business_criticality} />

      {(node.depends_on?.length ?? 0) > 0 && (
        <>
          <SectionHeader label={`Depends On (${node.depends_on!.length})`} />
          {node.depends_on!.map(d => <DepRow key={d.name} d={d} />)}
        </>
      )}

      {(node.depended_on_by?.length ?? 0) > 0 && (
        <>
          <SectionHeader label={`Used By (${node.depended_on_by!.length})`} />
          {node.depended_on_by!.map(d => <DepRow key={d.name} d={d} />)}
        </>
      )}
    </div>
  )
}

// ─── Main Component ───────────────────────────────────────────────────────────

const MAX_NODES_PRESETS = [25, 50, 75, 150, 300] as const

const CMDBPage: React.FC<CMDBPageProps> = ({ darkMode }) => {
  const [graphData, setGraphData]         = useState<GraphData>({ nodes: [], links: [] })
  const [loading, setLoading]             = useState(true)
  const [error, setError]                 = useState<string | null>(null)
  const [selectedNode, setSelectedNode]   = useState<CINode | null>(null)
  const [showLabels, setShowLabels]       = useState(true)
  const [filterTier, setFilterTier]       = useState<number | null>(null)
  const [activeTab, setActiveTab]         = useState<'graph' | 'table' | 'editor'>('graph')
  const [layoutMode, setLayoutMode]       = useState<LayoutMode>('force')
  const [ringOverlay, setRingOverlay]     = useState<RingOverlay>('health')
  // ── Service filter + max nodes ─────────────────────────────────────────────
  const [serviceInput, setServiceInput]   = useState<string>('')
  const [serviceFilter, setServiceFilter] = useState<string>('')
  const [maxNodes, setMaxNodes]           = useState<number>(75)
  const [classFilter, setClassFilter]     = useState<CIClass | ''>('')
  const serviceNamesRef = useRef<string[]>([])

  const [lastUpdated, setLastUpdated]   = useState<Date | null>(null)
  const [, setDisplayTick]              = useState(0)
  const lastUpdatedRef                  = useRef<Date | null>(null)

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef             = useRef<any>(undefined)
  const graphContainerRef = useRef<HTMLDivElement>(null)
  const wasDragged        = useRef(false)
  const zoomLevel         = useRef(1)
  const [graphDims, setGraphDims] = useState({ width: 800, height: 520 })

  const selectedNodeRef = useRef<CINode | null>(null)
  useEffect(() => { selectedNodeRef.current = selectedNode }, [selectedNode])

  // ── Resize observer ──────────────────────────────────────────────────────
  useEffect(() => {
    const el = graphContainerRef.current
    if (!el) return
    const obs = new ResizeObserver(entries => {
      const r = entries[0]?.contentRect
      if (r) setGraphDims({ width: Math.floor(r.width), height: Math.floor(r.height) })
    })
    obs.observe(el)
    return () => obs.disconnect()
  }, [])

  // ── Fetch graph data ─────────────────────────────────────────────────────
  const fetchGraph = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params = new URLSearchParams()
      if (serviceFilter.trim()) {
        params.set('service', serviceFilter.trim())
      } else {
        params.set('max_nodes', String(maxNodes))
      }
      const res = await fetch(`/api/cmdb/graph?${params}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
      const data: GraphData = await res.json()
      setGraphData(data)
      // Grow the service datalist — only :Service nodes, never shrink
      const incoming = data.nodes.filter(n => ['Service', 'Application'].includes(n.ci_class ?? 'Service')).map(n => n.name)
      const existing = new Set(serviceNamesRef.current)
      const merged = [...serviceNamesRef.current]
      for (const n of incoming) { if (!existing.has(n)) merged.push(n) }
      serviceNamesRef.current = merged.sort()
      const now = new Date()
      lastUpdatedRef.current = now
      setLastUpdated(now)
    } catch (e: any) {
      setError(e.message ?? 'Failed to load CMDB data')
    } finally {
      setLoading(false)
    }
  }, [serviceFilter, maxNodes])

  useEffect(() => { fetchGraph() }, [fetchGraph])

  // ── Smart polling ────────────────────────────────────────────────────────
  useEffect(() => {
    const tickTimer = setInterval(() => setDisplayTick(n => n + 1), 10_000)
    let pollTimer: ReturnType<typeof setInterval>
    const startPoll = () => { pollTimer = setInterval(fetchGraph, AUTO_REFRESH_MS) }
    const handleVisibility = () => {
      if (document.hidden) {
        clearInterval(pollTimer)
      } else {
        const stale = !lastUpdatedRef.current || Date.now() - lastUpdatedRef.current.getTime() > AUTO_REFRESH_MS
        if (stale) fetchGraph()
        startPoll()
      }
    }
    startPoll()
    document.addEventListener('visibilitychange', handleVisibility)
    return () => { clearInterval(pollTimer); clearInterval(tickTimer); document.removeEventListener('visibilitychange', handleVisibility) }
  }, [fetchGraph])

  // ── Apply D3 forces ──────────────────────────────────────────────────────
  useEffect(() => {
    if (graphData.nodes.length === 0) return
    const t = setTimeout(() => {
      const fg = fgRef.current as any
      if (!fg) return
      if (layoutMode === 'force') {
        try {
          // Stronger repulsion so hub nodes (agenticplatform-host, agentic-platform)
          // don't collapse all their HOSTED_ON / PART_OF children into a ball.
          fg.d3Force('charge')?.strength(-1200)
          fg.d3Force('link')?.distance(120)
        } catch { /* ignore */ }
      }
    }, 50)
    const t2 = setTimeout(() => { fgRef.current?.zoomToFit(400, 100) }, 250)
    return () => { clearTimeout(t); clearTimeout(t2) }
  }, [graphData, layoutMode])

  const handleEngineStop = useCallback(() => { fgRef.current?.zoomToFit(600, 100) }, [])

  // ── Drag detection ───────────────────────────────────────────────────────
  const handleNodeDrag = useCallback((_: NodeObject, translate: { x: number; y: number }) => {
    if (Math.abs(translate.x) > 3 || Math.abs(translate.y) > 3) wasDragged.current = true
  }, [])

  const handleNodeDragEnd = useCallback(() => { setTimeout(() => { wasDragged.current = false }, 60) }, [])

  // ── Node click ───────────────────────────────────────────────────────────
  const handleNodeClick = useCallback(async (rawNode: NodeObject) => {
    if (wasDragged.current) return
    const node = rawNode as CINode
    try {
      const res = await fetch(`/api/cmdb/nodes/${encodeURIComponent(node.name)}`, {
        headers: { Authorization: `Bearer ${getToken()}` },
      })
      if (res.ok) {
        const detail: CINode = await res.json()
        detail.x = node.x; detail.y = node.y
        setSelectedNode(detail)
      } else {
        setSelectedNode(node)
      }
    } catch {
      setSelectedNode(node)
    }
    if (node.x != null && node.y != null) {
      fgRef.current?.centerAt(node.x, node.y, 400)
      fgRef.current?.zoom(2.5, 400)
    }
  }, [])

  // ── Canvas paint ─────────────────────────────────────────────────────────
  const paintNode = useCallback((rawNode: NodeObject, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const node       = rawNode as CINode
    const cx         = node.x ?? 0
    const cy         = node.y ?? 0
    const r          = NODE_RADIUS
    const isSelected = selectedNodeRef.current?.id === node.id

    if (isSelected) {
      ctx.save()
      ctx.beginPath()
      ctx.arc(cx, cy, r + 12, 0, Math.PI * 2)
      ctx.strokeStyle = 'rgba(59,130,246,0.85)'
      ctx.lineWidth   = 2
      ctx.stroke()
      ctx.restore()
    }

    if (node.is_spof) {
      ctx.save()
      ctx.beginPath()
      ctx.arc(cx, cy, r + 8, 0, Math.PI * 2)
      ctx.strokeStyle = 'rgba(245,158,11,0.85)'
      ctx.lineWidth   = 1.4
      ctx.setLineDash([3, 2])
      ctx.stroke()
      ctx.setLineDash([])
      ctx.restore()
    }

    const ringColor = getRingColor(node, ringOverlay)
    if (ringColor) {
      ctx.save()
      ctx.beginPath()
      ctx.arc(cx, cy, r + 4, 0, Math.PI * 2)
      ctx.strokeStyle = ringColor
      ctx.lineWidth   = 2.5
      ctx.stroke()
      ctx.restore()
    }

    ctx.save()
    const grad = ctx.createRadialGradient(cx - r * 0.3, cy - r * 0.3, r * 0.05, cx, cy, r)
    grad.addColorStop(0, classGradientStart(node.ci_class))
    grad.addColorStop(1, '#1e293b')
    ctx.beginPath()
    ctx.arc(cx, cy, r, 0, Math.PI * 2)
    ctx.fillStyle = grad
    ctx.fill()
    ctx.restore()

    drawIcon(ctx, getIconKey(node), cx, cy, r)

    if (ringOverlay === 'incidents' && (node.incident_count ?? 0) > 0) {
      drawIncidentBadge(ctx, node.incident_count!, node.max_incident_severity, cx, cy, r)
    }

    if (showLabels && globalScale > 0.5) {
      const label    = node.name.replace(/^agentic_os_/, '')
      const fontSize = Math.max(8, Math.min(12, 10 / Math.max(globalScale * 0.8, 0.5)))
      ctx.save()
      ctx.font         = `${fontSize}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`
      ctx.textAlign    = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle    = 'rgba(0,0,0,0.65)'
      ctx.fillText(label, cx + 0.5, cy + r + 8.5)
      ctx.fillText(label, cx - 0.5, cy + r + 7.5)
      ctx.fillStyle    = '#e8eef5'
      ctx.fillText(label, cx, cy + r + 8)
      ctx.restore()
    }
  }, [showLabels, ringOverlay])

  const paintPointerArea = useCallback((rawNode: NodeObject, color: string, ctx: CanvasRenderingContext2D) => {
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(rawNode.x ?? 0, rawNode.y ?? 0, HIT_RADIUS, 0, Math.PI * 2)
    ctx.fill()
  }, [])

  // ── Filtered graph data (client-side tier + class filters) ───────────────
  const filteredData = React.useMemo<GraphData>(() => {
    let nodes: CINode[] = graphData.nodes
    if (filterTier)  nodes = nodes.filter(n => n.tier === filterTier)
    if (classFilter) nodes = nodes.filter(n => (n.ci_class ?? 'Service') === classFilter)

    // Pre-seed initial positions in a circle so the force simulation starts
    // spread out rather than collapsing all nodes from a single origin point.
    // Nodes already positioned by the d3 sim (x != null) keep their coords;
    // freshly-fetched nodes get evenly-spaced starting positions.
    const count = nodes.length
    const radius = Math.max(160, count * 20)
    const seeded = nodes.map((node, i) => {
      if (node.x != null && node.y != null) return node
      const angle = (2 * Math.PI * i) / count
      return { ...node, x: radius * Math.cos(angle), y: radius * Math.sin(angle) }
    })

    const ids = new Set(seeded.map(n => n.id))
    return {
      nodes: seeded,
      links: graphData.links.filter(l => {
        const s = typeof l.source === 'object' ? (l.source as CINode).id : l.source as string
        const t = typeof l.target === 'object' ? (l.target as CINode).id : l.target as string
        return ids.has(s) && ids.has(t)
      }),
      meta: graphData.meta,
    }
  }, [graphData, filterTier, classFilter])

  const dagMode: DagMode =
    layoutMode === 'hierarchy' ? 'td' :
    layoutMode === 'radial'    ? 'radialout' :
    undefined

  const meta               = graphData.meta
  const spoFCount          = graphData.nodes.filter(n => n.is_spof).length
  const activeIncidentCount = graphData.nodes.filter(n => n.incident_count && n.incident_count > 0).length

  // ── Small shared button style helper ──────────────────────────────────────
  const segBtn = (active: boolean) => ({
    padding: '4px 10px',
    borderRadius: 5,
    fontSize: '0.74rem',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'all 150ms ease',
    border: active ? `1px solid ${DS.accent}` : `1px solid transparent`,
    backgroundColor: active ? DS.accent : 'transparent',
    color: active ? '#fff' : DS.txtS,
    display: 'flex', alignItems: 'center', gap: 4,
  } as React.CSSProperties)

  // ── Controls bar style helpers ─────────────────────────────────────────────
  // Label for a group of controls
  const lblSt: React.CSSProperties = {
    fontSize: '0.68rem', fontWeight: 600, color: DS.txtS,
    whiteSpace: 'nowrap', letterSpacing: '0.03em', userSelect: 'none',
  }
  // Vertical hairline divider
  const divSt: React.CSSProperties = {
    width: 1, alignSelf: 'stretch', margin: '2px 0',
    backgroundColor: DS.border, flexShrink: 0,
  }
  // Compact chevron select — accent colour when active
  const selSt = (accent?: string): React.CSSProperties => ({
    height: 28, padding: '0 22px 0 8px',
    fontSize: '0.74rem', fontWeight: 600,
    color: accent ?? DS.txtP,
    backgroundColor: DS.raised,
    border: `1px solid ${accent ?? DS.border}`,
    borderRadius: 6, cursor: 'pointer',
    appearance: 'none' as any, WebkitAppearance: 'none' as any,
    backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2.5'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E")`,
    backgroundRepeat: 'no-repeat', backgroundPosition: 'right 6px center',
    outline: 'none', minWidth: 0, flexShrink: 0,
  })
  // Small icon/text button
  const iconBtn = (active: boolean, disabled = false): React.CSSProperties => ({
    height: 28, padding: '0 10px',
    display: 'flex', alignItems: 'center', gap: 5,
    borderRadius: 6, fontSize: '0.74rem', fontWeight: 600,
    cursor: disabled ? 'not-allowed' : 'pointer',
    border: active ? `1px solid ${DS.accent}` : `1px solid ${DS.border}`,
    backgroundColor: active ? DS.accent : DS.raised,
    color: active ? '#fff' : DS.txtS,
    opacity: disabled ? 0.5 : 1, flexShrink: 0,
    transition: 'all 150ms ease',
  })

  // ── Updated-at label ──────────────────────────────────────────────────────
  const updatedLabel = lastUpdated
    ? (() => {
        const s = Math.floor((Date.now() - lastUpdated.getTime()) / 1000)
        if (s < 5)    return 'just now'
        if (s < 60)   return `${s}s ago`
        if (s < 3600) return `${Math.floor(s / 60)}m ago`
        return `${Math.floor(s / 3600)}h ago`
      })()
    : null

  // ── Render ────────────────────────────────────────────────────────────────
  return (
    <div style={{
      height: '100vh',
      backgroundColor: DS.bg,
      padding: '0.75rem 1.25rem',
      fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
      boxSizing: 'border-box',
      display: 'flex',
      flexDirection: 'column',
      gap: '0.4rem',
      overflow: 'hidden',
    }}>

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div style={{
        flexShrink: 0,
        backgroundColor: DS.surface,
        border: `1px solid ${DS.border}`,
        borderLeft: `4px solid ${DS.accent}`,
        borderRadius: 10,
        padding: '0.6rem 1.125rem',
        display: 'flex',
        alignItems: 'center',
        gap: 10,
      }}>
        <IconNetwork size={18} color={DS.accent} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <h1 style={{ margin: 0, fontSize: '0.975rem', fontWeight: 700, color: DS.txtP, whiteSpace: 'nowrap' }}>
              CMDB — Configuration Item Graph
            </h1>
            <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.62rem', color: '#10b981', fontWeight: 700, letterSpacing: '0.04em' }}>
              <span style={{
                width: 6, height: 6, borderRadius: '50%', backgroundColor: '#10b981',
                display: 'inline-block', flexShrink: 0,
              }} />
              LIVE
            </span>
          </div>
        </div>
      </div>

      {/* ── Controls bar ─────────────────────────────────────────────────── */}
      <div style={{
        flexShrink: 0,
        backgroundColor: DS.surface,
        border: `1px solid ${DS.border}`,
        borderRadius: 10,
        padding: '0.45rem 0.875rem',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
      }}>

        {/* ── Row 1: view controls + legend + actions ───────────────────── */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>

          {/* Graph-only controls — hidden on Editor tab */}
          {activeTab !== 'editor' && (<>
          {/* Layout dropdown */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={lblSt}>Layout</span>
            <select
              value={layoutMode}
              onChange={e => { setLayoutMode(e.target.value as LayoutMode); setSelectedNode(null) }}
              style={selSt()}
            >
              <option value="force">Force</option>
              <option value="hierarchy">Hierarchy</option>
              <option value="radial">Radial</option>
            </select>
          </div>

          {/* Overlay dropdown */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={lblSt}>Overlay</span>
            <select
              value={ringOverlay}
              onChange={e => setRingOverlay(e.target.value as RingOverlay)}
              style={selSt()}
            >
              <option value="none">Off</option>
              <option value="health">Health</option>
              <option value="tier">Tier</option>
              <option value="incidents">Incidents</option>
            </select>
          </div>

          <div style={divSt} />

          {/* Labels toggle */}
          <button onClick={() => setShowLabels(v => !v)} style={iconBtn(showLabels)}>
            <IconEye size={12} />
            Labels
          </button>
          </>)}

          {/* Spacer pushes legend + actions to the right */}
          <div style={{ flex: 1 }} />

          {/* ── Inline legend (graph + table only) ───────────────────── */}
          {activeTab !== 'editor' && <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
            {ringOverlay === 'health' && (
              <>
                {[['#10b981','Healthy'],['#f97316','Degraded'],['#ef4444','Unhealthy'],['#4b5563','Unknown']].map(([c,l]) => (
                  <span key={l} style={{ display:'flex', alignItems:'center', gap:4, fontSize:'0.69rem', color: DS.txtS, whiteSpace:'nowrap' }}>
                    <span style={{ width:8, height:8, borderRadius:'50%', border:`2px solid ${c}`, flexShrink:0 }} />{l}
                  </span>
                ))}
              </>
            )}
            {ringOverlay === 'tier' && (
              <>
                {Object.entries(TIER_COLORS).map(([tier, color]) => (
                  <span key={tier} style={{ display:'flex', alignItems:'center', gap:4, fontSize:'0.69rem', color: DS.txtS, whiteSpace:'nowrap' }}>
                    <span style={{ width:8, height:8, borderRadius:'50%', border:`2px solid ${color}`, flexShrink:0 }} />
                    {TIER_LABELS[Number(tier)]}
                  </span>
                ))}
              </>
            )}
            {ringOverlay === 'incidents' && (
              <>
                {[['#ef4444','Critical'],['#f97316','High'],['#f59e0b','Medium'],['#3b82f6','Low']].map(([c,l]) => (
                  <span key={l} style={{ display:'flex', alignItems:'center', gap:4, fontSize:'0.69rem', color: DS.txtS, whiteSpace:'nowrap' }}>
                    <span style={{ width:8, height:8, borderRadius:'50%', border:`2px solid ${c}`, flexShrink:0 }} />{l}
                  </span>
                ))}
              </>
            )}
            {spoFCount > 0 && (
              <span style={{ display:'flex', alignItems:'center', gap:4, fontSize:'0.69rem', color:'#f59e0b', whiteSpace:'nowrap' }}>
                <span style={{ width:8, height:8, borderRadius:'50%', border:'1.5px dashed #f59e0b', flexShrink:0 }} />
                {spoFCount} SPoF
              </span>
            )}
            {activeIncidentCount > 0 && (
              <span style={{ display:'flex', alignItems:'center', gap:4, fontSize:'0.69rem', color:'#ef4444', whiteSpace:'nowrap' }}>
                <span style={{
                  width:14, height:14, borderRadius:'50%', backgroundColor:'#ef4444', flexShrink:0,
                  display:'inline-flex', alignItems:'center', justifyContent:'center',
                  fontSize:8, fontWeight:700, color:'#fff',
                }}>{activeIncidentCount}</span>
                Active incidents
              </span>
            )}
            {layoutMode !== 'force' && (
              <span style={{ fontSize:'0.69rem', color: DS.accent, fontStyle:'italic', whiteSpace:'nowrap' }}>
                {layoutMode === 'hierarchy' ? 'Top → user-facing' : 'Centre → user-facing'}
              </span>
            )}
          </div>}

          {activeTab !== 'editor' && <div style={divSt} />}

          {/* Graph / Table / Editor segment */}
          <div style={{ display:'flex', gap:1, padding:2, borderRadius:6, backgroundColor: DS.raised, border:`1px solid ${DS.border}` }}>
            <button onClick={() => setActiveTab('graph')} style={segBtn(activeTab === 'graph')}>
              <IconNetwork size={12} />Graph
            </button>
            <button onClick={() => setActiveTab('table')} style={segBtn(activeTab === 'table')}>
              <IconClipboardList size={12} />Table
            </button>
            <button onClick={() => setActiveTab('editor')} style={segBtn(activeTab === 'editor')}>
              <IconDatabase size={12} />Editor
            </button>
          </div>

          {/* Refresh */}
          <button onClick={fetchGraph} disabled={loading} style={iconBtn(false, loading)}>
            <IconRefresh size={12} />
          </button>

        </div>

        {/* ── Thin hairline + filters — hidden on Editor tab ─────────────── */}
        {activeTab !== 'editor' && <div style={{ height: 1, backgroundColor: DS.border, opacity: 0.5 }} />}

        {activeTab !== 'editor' && <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>

          {/* Service focus search */}
          <datalist id="cmdb-service-list">
            {serviceNamesRef.current.map(n => <option key={n} value={n} />)}
          </datalist>
          <div style={{ position: 'relative', display: 'flex', alignItems: 'center', flexShrink: 0 }}>
            <input
              list="cmdb-service-list"
              value={serviceInput}
              onChange={e => setServiceInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') { setServiceFilter(serviceInput.trim()); setSelectedNode(null) }
                if (e.key === 'Escape') { setServiceInput(''); setServiceFilter(''); setSelectedNode(null) }
              }}
              onBlur={() => {
                const v = serviceInput.trim()
                if (v !== serviceFilter) { setServiceFilter(v); setSelectedNode(null) }
              }}
              placeholder="Focus service…"
              style={{
                height: 28, width: 190,
                padding: '0 24px 0 9px',
                fontSize: '0.74rem',
                backgroundColor: DS.raised,
                border: `1px solid ${serviceFilter ? '#c084fc' : DS.border}`,
                borderRadius: 6,
                color: serviceFilter ? '#c084fc' : DS.txtP,
                outline: 'none',
              }}
            />
            {serviceInput && (
              <button
                onClick={() => { setServiceInput(''); setServiceFilter(''); setSelectedNode(null) }}
                style={{
                  position: 'absolute', right: 5, top: '50%', transform: 'translateY(-50%)',
                  background: 'none', border: 'none', cursor: 'pointer',
                  color: DS.txtS, display: 'flex', alignItems: 'center', padding: 0,
                }}
              >
                <IconX size={10} />
              </button>
            )}
          </div>

          {/* Tier filter */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={lblSt}>Tier</span>
            <select
              value={filterTier ?? ''}
              onChange={e => setFilterTier(e.target.value ? Number(e.target.value) : null)}
              style={selSt()}
            >
              <option value="">All</option>
              <option value="1">1 — Critical</option>
              <option value="2">2 — Important</option>
              <option value="3">3 — Supporting</option>
            </select>
          </div>

          {/* Class filter */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span style={lblSt}>Class</span>
            <select
              value={classFilter}
              onChange={e => { setClassFilter(e.target.value as CIClass | ''); setSelectedNode(null) }}
              style={selSt(classFilter ? classAccent(classFilter as CIClass) : undefined)}
            >
              <option value="">All</option>
              <option value="Application">Application</option>
              <option value="Service">Service</option>
              <option value="Database">Database</option>
              <option value="Server">Server</option>
              <option value="Container">Container</option>
            </select>
          </div>

          {/* Max nodes — hidden when service filter is active */}
          {!serviceFilter && (
            <>
              <div style={divSt} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                <span style={lblSt}>Max</span>
                <div style={{ display:'flex', gap:1, padding:2, borderRadius:6, backgroundColor: DS.raised, border:`1px solid ${DS.border}` }}>
                  {MAX_NODES_PRESETS.map(n => (
                    <button key={n} onClick={() => { setMaxNodes(n); setSelectedNode(null) }} style={segBtn(maxNodes === n)}>
                      {n}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>}
      </div>

      {/* ── Error ──────────────────────────────────────────────────────────── */}
      {error && (
        <div style={{
          flexShrink: 0,
          padding: '0.5rem 0.875rem',
          borderRadius: 7,
          backgroundColor: 'rgba(239,68,68,0.1)',
          border: '1px solid rgba(239,68,68,0.3)',
          color: '#ef4444',
          fontSize: '0.8rem',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
        }}>
          <IconAlertTriangle size={15} />
          {error}
        </div>
      )}

      {/* ── Graph tab ──────────────────────────────────────────────────────── */}
      {activeTab === 'graph' && (
        <div style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          border: `1px solid ${DS.border}`,
          borderRadius: 10,
          overflow: 'hidden',
          backgroundColor: DS.surface,
        }}>
          {/* Canvas — flex: 1, shrinks when sidebar opens */}
          <div ref={graphContainerRef} style={{ flex: 1, minWidth: 0, position: 'relative' }}>

            {/* ── Status overlay — top-right of map ── */}
            {meta && (
              <div style={{
                position: 'absolute', top: 8, right: 8, zIndex: 20,
                backgroundColor: 'rgba(15,23,42,0.72)',
                backdropFilter: 'blur(6px)',
                border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: 7,
                padding: '4px 10px',
                fontSize: '0.68rem',
                lineHeight: 1.6,
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                flexWrap: 'wrap',
                pointerEvents: 'none',
              }}>
                {meta.service_filter
                  ? <span style={{ color: '#c084fc', fontWeight: 600 }}>
                      {meta.total_nodes} CIs around '{meta.service_filter}'
                    </span>
                  : <span style={{ color: '#94a3b8' }}>
                      {meta.total_nodes}{meta.truncated ? ` of ${meta.db_total}` : ''} CIs
                      {meta.truncated && <span style={{ color: '#f59e0b' }}> (limited)</span>}
                    </span>
                }
                <span style={{ color: '#475569' }}>·</span>
                <span style={{ color: '#94a3b8' }}>{meta.total_links} deps</span>
                {spoFCount > 0 && <>
                  <span style={{ color: '#475569' }}>·</span>
                  <span style={{ color: '#f59e0b', fontWeight: 600 }}>{spoFCount} SPoF</span>
                </>}
                {meta.tier_counts?.['1'] && <>
                  <span style={{ color: '#475569' }}>·</span>
                  <span style={{ color: '#ef4444', fontWeight: 600 }}>{meta.tier_counts['1']} tier-1</span>
                </>}
                {updatedLabel && <>
                  <span style={{ color: '#475569' }}>·</span>
                  <span style={{ color: '#64748b' }}>updated {updatedLabel}</span>
                </>}
              </div>
            )}

            {loading && (
              <div style={{
                position: 'absolute', inset: 0, display: 'flex',
                alignItems: 'center', justifyContent: 'center',
                color: DS.txtS, fontSize: '0.875rem', zIndex: 10,
                backgroundColor: DS.surface,
              }}>
                Loading CMDB data…
              </div>
            )}
            {filteredData.nodes.length > 0 && (
              <Suspense fallback={<div style={{color:'#64748b',padding:'20px',textAlign:'center'}}>Loading graph…</div>}>
              <ForceGraph2D
                ref={fgRef}
                width={graphDims.width}
                height={graphDims.height}
                graphData={filteredData}
                nodeCanvasObject={paintNode}
                nodeCanvasObjectMode={() => 'replace'}
                nodePointerAreaPaint={paintPointerArea}
                linkColor={(l: object) => {
                  const rel = (l as CILink).rel_type
                  if (rel === 'RUNS_ON')   return 'rgba(99,102,241,0.55)'   // indigo
                  if (rel === 'HOSTED_ON') return 'rgba(6,182,212,0.45)'    // cyan
                  if (rel === 'PART_OF')   return 'rgba(168,85,247,0.45)'   // purple
                  return 'rgba(148,163,184,0.28)'                           // slate (DEPENDS_ON)
                }}
                linkWidth={(l: object) => {
                  const rel = (l as CILink).rel_type
                  return (rel === 'RUNS_ON' || rel === 'HOSTED_ON') ? 1 : 1.5
                }}
                linkLineDash={(l: object) => {
                  const rel = (l as CILink).rel_type
                  if (rel === 'PART_OF') return [4, 3]
                  if (rel === 'HOSTED_ON' || rel === 'RUNS_ON') return [2, 2]
                  return null
                }}
                linkDirectionalArrowLength={6}
                linkDirectionalArrowRelPos={1}
                linkDirectionalArrowColor={(l: object) => {
                  const rel = (l as CILink).rel_type
                  if (rel === 'RUNS_ON')   return 'rgba(99,102,241,0.7)'
                  if (rel === 'HOSTED_ON') return 'rgba(6,182,212,0.6)'
                  if (rel === 'PART_OF')   return 'rgba(168,85,247,0.6)'
                  return 'rgba(148,163,184,0.6)'
                }}
                dagMode={dagMode}
                dagLevelDistance={90}
                onNodeClick={handleNodeClick}
                onNodeDrag={handleNodeDrag}
                onNodeDragEnd={handleNodeDragEnd}
                onBackgroundClick={() => setSelectedNode(null)}
                onZoom={({ k }) => { zoomLevel.current = k }}
                onEngineStop={handleEngineStop}
                cooldownTicks={200}
                d3AlphaDecay={layoutMode === 'force' ? 0.02 : 0.05}
                d3VelocityDecay={0.3}
                warmupTicks={200}
                backgroundColor={DS.surface}
              />
              </Suspense>
            )}
          </div>

          {/* Vertical separator */}
          <div style={{
            width: selectedNode ? 1 : 0,
            backgroundColor: DS.border,
            flexShrink: 0,
            transition: 'width 300ms cubic-bezier(0.4, 0, 0.2, 1)',
          }} />

          {/* Sliding sidebar */}
          <div style={{
            width: selectedNode ? 380 : 0,
            flexShrink: 0,
            overflow: 'hidden',
            transition: 'width 300ms cubic-bezier(0.4, 0, 0.2, 1)',
          }}>
            <div style={{ width: 380, height: '100%', overflowY: 'auto', backgroundColor: DS.surface }}>
              {selectedNode && (
                <SidePanel node={selectedNode} onClose={() => setSelectedNode(null)} />
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── Table tab ──────────────────────────────────────────────────────── */}
      {activeTab === 'table' && (
        <div style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: DS.surface,
          border: `1px solid ${DS.border}`,
          borderRadius: 10,
          overflow: 'hidden',
        }}>
          <div style={{ flex: 1, overflowY: 'auto', overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
              <thead>
                <tr style={{ backgroundColor: DS.raised }}>
                  {['Name', 'Tier', 'SPoF', 'Status', 'Health', 'CPU limit', 'Mem limit', 'IP', 'Ports', 'Live CPU%', 'Live Mem', 'Discovered'].map(col => (
                    <th key={col} style={{
                      padding: '10px 12px',
                      textAlign: 'left',
                      fontWeight: 600,
                      fontSize: '0.68rem',
                      color: DS.txtS,
                      textTransform: 'uppercase',
                      letterSpacing: '0.05em',
                      borderBottom: `1px solid ${DS.border}`,
                      whiteSpace: 'nowrap',
                      position: 'sticky',
                      top: 0,
                      backgroundColor: DS.raised,
                      zIndex: 1,
                    }}>{col}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {graphData.nodes.map((node, i) => (
                  <tr
                    key={node.id}
                    onClick={() => { setActiveTab('graph'); handleNodeClick(node) }}
                    style={{
                      backgroundColor: i % 2 === 0 ? DS.surface : '#1e2434',
                      cursor: 'pointer',
                      borderLeft: `3px solid ${TIER_COLORS[node.tier ?? 3] ?? '#6b7280'}`,
                      transition: 'background-color 100ms ease',
                    }}
                    onMouseEnter={e => (e.currentTarget.style.backgroundColor = DS.raised)}
                    onMouseLeave={e => (e.currentTarget.style.backgroundColor = i % 2 === 0 ? DS.surface : '#1e2434')}
                  >
                    <td style={{ padding: '8px 12px', color: DS.txtP, fontWeight: 600, whiteSpace: 'nowrap' }}>
                      {node.name.replace(/^agentic_os_/, '')}
                    </td>
                    <td style={{ padding: '8px 12px' }}><TierChip tier={node.tier} /></td>
                    <td style={{ padding: '8px 12px' }}>
                      {node.is_spof
                        ? <span style={{ display: 'flex', alignItems: 'center', gap: 4, color: '#f59e0b', fontWeight: 600, fontSize: '0.75rem' }}>
                            <IconAlertTriangle size={13} /> Yes
                          </span>
                        : <span style={{ color: DS.txtS, fontSize: '0.75rem' }}>No</span>
                      }
                    </td>
                    <td style={{ padding: '8px 12px', color: DS.txtS }}>{node.container_status ?? '—'}</td>
                    <td style={{ padding: '8px 12px' }}>
                      <span style={{ display: 'flex', alignItems: 'center' }}>
                        <HealthDot status={node.health_status} />
                        <span style={{ color: DS.txtS }}>{node.health_status ?? '—'}</span>
                      </span>
                    </td>
                    <td style={{ padding: '8px 12px', color: DS.txtS }}>{node.cpu_limit_cores != null ? `${node.cpu_limit_cores}c` : '—'}</td>
                    <td style={{ padding: '8px 12px', color: DS.txtS }}>{formatMB(node.memory_limit_mb)}</td>
                    <td style={{ padding: '8px 12px', color: DS.txtS, fontFamily: 'monospace', fontSize: '0.75rem' }}>{node.ip_address ?? '—'}</td>
                    <td style={{ padding: '8px 12px', color: DS.txtS }}>{node.exposed_ports ?? '—'}</td>
                    <td style={{ padding: '8px 12px', color: (node.current_cpu_percent ?? 0) > 70 ? '#f59e0b' : DS.txtS, fontWeight: (node.current_cpu_percent ?? 0) > 70 ? 600 : 400 }}>
                      {node.current_cpu_percent != null ? `${node.current_cpu_percent.toFixed(1)}%` : '—'}
                    </td>
                    <td style={{ padding: '8px 12px', color: DS.txtS }}>{node.current_memory_mb != null ? formatMB(node.current_memory_mb) : '—'}</td>
                    <td style={{ padding: '8px 12px', color: DS.txtS, whiteSpace: 'nowrap' }}>{timeAgo(node.last_discovered_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Editor tab ─────────────────────────────────────────────────────── */}
      {activeTab === 'editor' && (
        <div style={{
          flex: 1,
          minHeight: 0,
          display: 'flex',
          backgroundColor: DS.surface,
          border: `1px solid ${DS.border}`,
          borderRadius: 10,
          overflow: 'hidden',
        }}>
          <CMDBEditor />
        </div>
      )}

    </div>
  )
}

export default CMDBPage
