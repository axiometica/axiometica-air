import { useState, useEffect, useRef, useCallback } from 'react'
import { IconBell, IconX, IconCheck } from './icons'
import { getPendingApprovals, listWorkflows, ApprovalResponse, WorkflowResponse } from '../services/api'
import { useGlobalEvents } from '../hooks/useGlobalEvents'

// ── Types ──────────────────────────────────────────────────────────────────

interface Notification {
  id: string            // workflowId or approvalId
  kind: 'approval' | 'incident' | 'resolved' | 'failed'
  title: string
  subtitle: string
  timeAgo: string
  workflowId: string
  approvalId?: string
  createdAt: string
}

interface NotificationBellProps {
  darkMode: boolean
  onNavigate: (view: string, workflowId?: string) => void
}

// ── Helpers ────────────────────────────────────────────────────────────────

const SEEN_KEY = 'notif_seen_ids'
const POLL_MS  = 30_000   // 30 s

function getSeenIds(): Set<string> {
  try {
    const raw = localStorage.getItem(SEEN_KEY)
    return new Set(raw ? JSON.parse(raw) : [])
  } catch { return new Set() }
}

function markSeen(ids: string[]) {
  try {
    const seen = getSeenIds()
    ids.forEach(id => seen.add(id))
    // Keep at most 500 entries so localStorage doesn't grow forever
    const trimmed = Array.from(seen).slice(-500)
    localStorage.setItem(SEEN_KEY, JSON.stringify(trimmed))
  } catch { /* ignore */ }
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins  = Math.floor(diff / 60_000)
  if (mins < 1)   return 'just now'
  if (mins < 60)  return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)   return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function truncate(s: string, max = 38): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

// ── Build notification list ────────────────────────────────────────────────

function buildNotifications(
  approvals: ApprovalResponse[],
  workflows: WorkflowResponse[]
): Notification[] {
  const notifs: Notification[] = []

  // Pending approvals — highest priority
  for (const a of approvals) {
    const sum    = a.incident_summary
    const wf     = workflows.find(w => w.workflow_id === a.workflow_id)
    const incNum = wf?.incident_number_str || ''
    const type   = sum?.anomaly_type?.replace(/_/g, ' ') || 'incident'
    const subTitle = wf?.title ? truncate(wf.title) : truncate(`${type} · ${sum?.resource || a.workflow_id.slice(0, 8)}`)
    notifs.push({
      id:         a.approval_id,
      kind:       'approval',
      title:      incNum || 'Approval required',
      subtitle:   subTitle,
      timeAgo:    timeAgo(a.requested_at),
      workflowId: a.workflow_id,
      approvalId: a.approval_id,
      createdAt:  a.requested_at,
    })
  }

  // Recent workflows — new incidents (last 15 min), resolved, failed
  const cutoff15 = Date.now() - 15 * 60_000

  for (const w of workflows) {
    const createdMs = new Date(w.created_at).getTime()
    const isNew     = createdMs > cutoff15
    const incNum    = w.incident_number_str || ''
    const subtitle  = truncate(w.title || w.summary || w.lifecycle_state || '—')

    if (w.lifecycle_state === 'resolved') {
      notifs.push({
        id: w.workflow_id + '_resolved', kind: 'resolved',
        title:      incNum || w.workflow_id.slice(0, 8),
        subtitle,
        timeAgo:    timeAgo(w.updated_at),
        workflowId: w.workflow_id,
        createdAt:  w.updated_at,
      })
    } else if (w.lifecycle_state === 'failed') {
      notifs.push({
        id: w.workflow_id + '_failed', kind: 'failed',
        title:      incNum || w.workflow_id.slice(0, 8),
        subtitle,
        timeAgo:    timeAgo(w.updated_at),
        workflowId: w.workflow_id,
        createdAt:  w.updated_at,
      })
    } else if (isNew) {
      notifs.push({
        id:         w.workflow_id,
        kind:       'incident',
        title:      incNum || (w.title ? truncate(w.title) : 'New incident'),
        subtitle,
        timeAgo:    timeAgo(w.created_at),
        workflowId: w.workflow_id,
        createdAt:  w.created_at,
      })
    }
  }

  // Sort: approvals first, then newest-first by createdAt
  return notifs.sort((a, b) => {
    if (a.kind === 'approval' && b.kind !== 'approval') return -1
    if (b.kind === 'approval' && a.kind !== 'approval') return  1
    return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
  })
}

// ── Kind styling ───────────────────────────────────────────────────────────

function kindDot(kind: Notification['kind']) {
  switch (kind) {
    case 'approval':  return '#ef4444'   // red
    case 'incident':  return '#f97316'   // orange
    case 'failed':    return '#f59e0b'   // amber
    case 'resolved':  return '#10b981'   // green
  }
}

function kindLabel(kind: Notification['kind']) {
  switch (kind) {
    case 'approval':  return 'ACTION REQUIRED'
    case 'incident':  return 'NEW INCIDENT'
    case 'failed':    return 'FAILED'
    case 'resolved':  return 'RESOLVED'
  }
}

// ── Component ──────────────────────────────────────────────────────────────

export default function NotificationBell({ darkMode, onNavigate }: NotificationBellProps) {
  const [open, setOpen]           = useState(false)
  const [notifs, setNotifs]       = useState<Notification[]>([])
  const [seenIds, setSeenIds]     = useState<Set<string>>(getSeenIds)
  const [loading, setLoading]     = useState(false)
  const panelRef                  = useRef<HTMLDivElement>(null)

  // ── Fetch ────────────────────────────────────────────────────────────────

  const fetchNotifications = useCallback(async () => {
    try {
      const [apprRes, wfRes] = await Promise.all([
        getPendingApprovals({ limit: 20 }),
        listWorkflows({ workflow_type: 'incident', limit: 20, sort_by: 'created_at', sort_order: 'desc' }),
      ])
      const built = buildNotifications(
        apprRes.data  || [],
        wfRes.data?.workflows || [],
      )
      setNotifs(built)
    } catch { /* silent — bell should never crash the app */ }
  }, [])

  useEffect(() => {
    fetchNotifications()
    const iv = setInterval(fetchNotifications, POLL_MS)
    return () => clearInterval(iv)
  }, [fetchNotifications])

  // Instant refresh on real-time WS events
  useGlobalEvents(useCallback((ev) => {
    if (ev.type === 'incident_created' || ev.type === 'incident_updated' || ev.type === 'approval_requested') {
      fetchNotifications()
    }
  }, [fetchNotifications]))

  // ── Close on outside click ───────────────────────────────────────────────

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // ── Badge count (unseen only) ────────────────────────────────────────────

  const unseenCount = notifs.filter(n => !seenIds.has(n.id)).length

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleOpen = () => {
    setOpen(o => !o)
    setLoading(true)
    fetchNotifications().finally(() => setLoading(false))
  }

  const handleMarkAllRead = () => {
    const ids = notifs.map(n => n.id)
    markSeen(ids)
    setSeenIds(getSeenIds())
  }

  const handleClickNotif = (n: Notification) => {
    markSeen([n.id])
    setSeenIds(getSeenIds())
    setOpen(false)
    if (n.kind === 'approval') {
      onNavigate('approvals', n.workflowId)
    } else {
      onNavigate('details', n.workflowId)
    }
  }

  // ── Styles (inline to keep component self-contained) ─────────────────────

  const panel = {
    position:        'absolute' as const,
    top:             '100%',
    right:           0,
    marginTop:       8,
    width:           340,
    borderRadius:    12,
    boxShadow:       '0 20px 60px rgba(0,0,0,0.5)',
    zIndex:          9999,
    background:      darkMode ? '#1a1f2e' : '#ffffff',
    border:          `1px solid ${darkMode ? '#2d3748' : '#e2e8f0'}`,
    overflow:        'hidden' as const,
  }

  const headerRow = {
    display:         'flex',
    alignItems:      'center',
    justifyContent:  'space-between',
    padding:         '14px 16px 12px',
    borderBottom:    `1px solid ${darkMode ? '#2d3748' : '#f1f5f9'}`,
  }

  const listStyle = {
    maxHeight:  360,
    overflowY:  'auto' as const,
  }

  const footerRow = {
    padding:     '10px 16px',
    borderTop:   `1px solid ${darkMode ? '#2d3748' : '#f1f5f9'}`,
    display:     'flex',
    gap:         8,
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ position: 'relative' }} ref={panelRef}>
      {/* Bell button */}
      <button
        onClick={handleOpen}
        style={{ position: 'relative', padding: 8, borderRadius: 8, border: 'none', cursor: 'pointer',
          background: open ? (darkMode ? '#1e2a3a' : '#f1f5f9') : 'transparent',
          color: darkMode ? '#9ca3af' : '#6b7280',
          transition: 'background 150ms, color 150ms',
        }}
        title="Notifications"
      >
        <IconBell size={20} strokeWidth={2} />

        {/* Unseen badge */}
        {unseenCount > 0 && (
          <span style={{
            position:   'absolute',
            top:        2, right: 2,
            minWidth:   16, height: 16,
            background: '#ef4444',
            color:      '#fff',
            fontSize:   10,
            fontWeight: 700,
            borderRadius: 8,
            display:    'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding:    '0 4px',
            lineHeight: 1,
            boxShadow:  '0 0 0 2px ' + (darkMode ? '#0f1419' : '#ffffff'),
          }}>
            {unseenCount > 99 ? '99+' : unseenCount}
          </span>
        )}
      </button>

      {/* Dropdown panel */}
      {open && (
        <div style={panel}>
          {/* Panel header */}
          <div style={headerRow}>
            <span style={{ fontWeight: 600, fontSize: 14, color: darkMode ? '#e8eef5' : '#1e293b' }}>
              Notifications
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              {loading && (
                <span style={{ fontSize: 11, color: darkMode ? '#6b7a93' : '#94a3b8' }}>refreshing…</span>
              )}
              <button
                onClick={() => setOpen(false)}
                style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 4,
                  color: darkMode ? '#6b7a93' : '#94a3b8', borderRadius: 4 }}
              >
                <IconX size={14} />
              </button>
            </div>
          </div>

          {/* Notification list */}
          <div style={listStyle}>
            {notifs.length === 0 ? (
              <div style={{ padding: '32px 16px', textAlign: 'center',
                color: darkMode ? '#4a5568' : '#94a3b8', fontSize: 13 }}>
                <IconBell size={28} strokeWidth={1.5} style={{ margin: '0 auto 8px', opacity: 0.4 }} />
                <div>No notifications</div>
              </div>
            ) : (
              notifs.map(n => {
                const unseen = !seenIds.has(n.id)
                return (
                  <button
                    key={n.id}
                    onClick={() => handleClickNotif(n)}
                    style={{
                      display:    'flex',
                      alignItems: 'flex-start',
                      gap:        12,
                      width:      '100%',
                      padding:    '11px 16px',
                      border:     'none',
                      borderBottom: `1px solid ${darkMode ? '#1e2535' : '#f8fafc'}`,
                      cursor:     'pointer',
                      textAlign:  'left',
                      background: unseen
                        ? (darkMode ? 'rgba(59,130,246,0.06)' : 'rgba(59,130,246,0.04)')
                        : 'transparent',
                      transition: 'background 150ms',
                    }}
                    onMouseEnter={e => {
                      (e.currentTarget as HTMLButtonElement).style.background =
                        darkMode ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.03)'
                    }}
                    onMouseLeave={e => {
                      (e.currentTarget as HTMLButtonElement).style.background =
                        unseen
                          ? (darkMode ? 'rgba(59,130,246,0.06)' : 'rgba(59,130,246,0.04)')
                          : 'transparent'
                    }}
                  >
                    {/* Colour dot */}
                    <span style={{
                      flexShrink: 0,
                      width: 8, height: 8,
                      borderRadius: '50%',
                      background: kindDot(n.kind),
                      marginTop: 5,
                      boxShadow: unseen ? `0 0 6px ${kindDot(n.kind)}99` : 'none',
                    }} />

                    {/* Text */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'center', gap: 6, marginBottom: 2 }}>
                        <span style={{
                          fontSize: 10, fontWeight: 700, letterSpacing: '0.5px',
                          color: kindDot(n.kind), textTransform: 'uppercase',
                        }}>
                          {kindLabel(n.kind)}
                        </span>
                        <span style={{ fontSize: 11, color: darkMode ? '#4a5568' : '#94a3b8',
                          flexShrink: 0 }}>
                          {n.timeAgo}
                        </span>
                      </div>
                      <div style={{ fontSize: 13, fontWeight: unseen ? 600 : 400,
                        color: darkMode ? '#e8eef5' : '#1e293b',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {n.title}
                      </div>
                      <div style={{ fontSize: 12, color: darkMode ? '#6b7a93' : '#64748b',
                        marginTop: 1,
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {n.subtitle}
                      </div>
                    </div>

                    {/* Unread dot */}
                    {unseen && (
                      <span style={{
                        flexShrink: 0,
                        width: 6, height: 6,
                        borderRadius: '50%',
                        background: '#3b82f6',
                        marginTop: 7,
                      }} />
                    )}
                  </button>
                )
              })
            )}
          </div>

          {/* Footer */}
          {notifs.length > 0 && (
            <div style={footerRow}>
              <button
                onClick={handleMarkAllRead}
                style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  fontSize: 12, fontWeight: 500,
                  color: darkMode ? '#6b7a93' : '#64748b',
                  background: 'none', border: 'none', cursor: 'pointer', padding: '4px 8px',
                  borderRadius: 6,
                }}
              >
                <IconCheck size={12} strokeWidth={2.5} />
                Mark all read
              </button>
              <button
                onClick={() => { setOpen(false); onNavigate('approvals') }}
                style={{
                  marginLeft: 'auto',
                  fontSize: 12, fontWeight: 500,
                  color: '#3b82f6',
                  background: 'none', border: 'none', cursor: 'pointer', padding: '4px 8px',
                  borderRadius: 6,
                }}
              >
                View all approvals →
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
