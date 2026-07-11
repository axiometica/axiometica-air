import { useState, useEffect, useRef, useCallback } from 'react'
import { IconBell, IconX, IconCheck } from './icons'
import { getPendingApprovals, listWorkflows, listWatchers, ApprovalResponse, WorkflowResponse, WatcherInfo } from '../services/api'
import { useGlobalEvents } from '../hooks/useGlobalEvents'

// ── Types ──────────────────────────────────────────────────────────────────

interface Notification {
  id: string
  kind: 'approval' | 'watcher' | 'incident' | 'resolved' | 'failed'
  title: string
  subtitle: string
  timeAgo: string
  workflowId: string
  approvalId?: string
  createdAt: string
  navTarget?: string
}

interface NotificationBellProps {
  darkMode: boolean
  onNavigate: (view: string, workflowId?: string) => void
}

// ── localStorage helpers ───────────────────────────────────────────────────

const SEEN_KEY      = 'notif_seen_ids'
const DISMISSED_KEY = 'notif_dismissed_ids'
const POLL_MS       = 30_000

// Actionable kinds are never dismissed from the list — they must be acted on.
const ACTIONABLE: Set<Notification['kind']> = new Set(['approval', 'watcher'])

function loadSet(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key)
    return new Set(raw ? JSON.parse(raw) : [])
  } catch { return new Set() }
}

function saveSet(key: string, set: Set<string>) {
  try {
    const trimmed = Array.from(set).slice(-500)
    localStorage.setItem(key, JSON.stringify(trimmed))
  } catch { /* ignore */ }
}

function getSeenIds():      Set<string> { return loadSet(SEEN_KEY) }
function getDismissedIds(): Set<string> { return loadSet(DISMISSED_KEY) }

function markSeen(ids: string[]) {
  const seen = getSeenIds()
  ids.forEach(id => seen.add(id))
  saveSet(SEEN_KEY, seen)
}

function dismissIds(ids: string[]) {
  const dismissed = getDismissedIds()
  ids.forEach(id => dismissed.add(id))
  saveSet(DISMISSED_KEY, dismissed)
}

// ── Time helper ────────────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1)  return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24)  return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function truncate(s: string, max = 38): string {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

// ── Build notification list ────────────────────────────────────────────────

function buildNotifications(
  approvals: ApprovalResponse[],
  workflows: WorkflowResponse[],
  watchers:  WatcherInfo[],
): Notification[] {
  const notifs: Notification[] = []
  const cutoff15  = Date.now() - 15 * 60_000
  const cutoff24h = Date.now() - 24 * 60 * 60_000

  // Pending approvals — always shown, no age limit
  for (const a of approvals) {
    const sum      = a.incident_summary
    const wf       = workflows.find(w => w.workflow_id === a.workflow_id)
    const incNum   = wf?.incident_number_str || ''
    const type     = sum?.anomaly_type?.replace(/_/g, ' ') || 'incident'
    const subtitle = wf?.title
      ? truncate(wf.title)
      : truncate(`${type} · ${sum?.resource || a.workflow_id.slice(0, 8)}`)
    notifs.push({
      id:         a.approval_id,
      kind:       'approval',
      title:      incNum || 'Approval required',
      subtitle,
      timeAgo:    timeAgo(a.requested_at),
      workflowId: a.workflow_id,
      approvalId: a.approval_id,
      createdAt:  a.requested_at,
    })
  }

  // Pending watcher registrations — always shown, no age limit
  for (const w of watchers) {
    if (w.registration_status !== 'pending') continue
    const ts = w.registered_at || new Date().toISOString()
    notifs.push({
      id:         `watcher_${w.watcher_id || w.watcher_name}`,
      kind:       'watcher',
      title:      w.display_name || w.watcher_name,
      subtitle:   'Pending approval',
      timeAgo:    timeAgo(ts),
      workflowId: '',
      createdAt:  ts,
      navTarget:  'monitoring',
    })
  }

  // Workflows — new incidents (15 min), resolved/failed (24 h)
  for (const w of workflows) {
    const createdMs = new Date(w.created_at).getTime()
    const updatedMs = new Date(w.updated_at).getTime()
    const incNum    = w.incident_number_str || ''
    const subtitle  = truncate(w.title || w.summary || w.lifecycle_state || '—')

    if (w.lifecycle_state === 'resolved' && updatedMs > cutoff24h) {
      notifs.push({
        id: w.workflow_id + '_resolved', kind: 'resolved',
        title:      incNum || w.workflow_id.slice(0, 8),
        subtitle,
        timeAgo:    timeAgo(w.updated_at),
        workflowId: w.workflow_id,
        createdAt:  w.updated_at,
      })
    } else if (w.lifecycle_state === 'failed' && updatedMs > cutoff24h) {
      notifs.push({
        id: w.workflow_id + '_failed', kind: 'failed',
        title:      incNum || w.workflow_id.slice(0, 8),
        subtitle,
        timeAgo:    timeAgo(w.updated_at),
        workflowId: w.workflow_id,
        createdAt:  w.updated_at,
      })
    } else if (createdMs > cutoff15 &&
               w.lifecycle_state !== 'resolved' &&
               w.lifecycle_state !== 'failed') {
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

  // Sort: actionable (approvals + watchers) first, then newest-first
  return notifs.sort((a, b) => {
    const aUrgent = ACTIONABLE.has(a.kind)
    const bUrgent = ACTIONABLE.has(b.kind)
    if (aUrgent && !bUrgent) return -1
    if (bUrgent && !aUrgent) return  1
    return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
  })
}

// ── Kind styling ───────────────────────────────────────────────────────────

function kindDot(kind: Notification['kind']): string {
  switch (kind) {
    case 'approval': return '#ef4444'
    case 'watcher':  return '#a855f7'
    case 'incident': return '#f97316'
    case 'failed':   return '#f59e0b'
    case 'resolved': return '#10b981'
  }
}

function kindLabel(kind: Notification['kind']): string {
  switch (kind) {
    case 'approval': return 'ACTION REQUIRED'
    case 'watcher':  return 'WATCHER PENDING'
    case 'incident': return 'NEW INCIDENT'
    case 'failed':   return 'FAILED'
    case 'resolved': return 'RESOLVED'
  }
}

// ── Component ──────────────────────────────────────────────────────────────

export default function NotificationBell({ darkMode, onNavigate }: NotificationBellProps) {
  const [open, setOpen]               = useState(false)
  const [allNotifs, setAllNotifs]     = useState<Notification[]>([])
  const [seenIds, setSeenIds]         = useState<Set<string>>(getSeenIds)
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(getDismissedIds)
  const [loading, setLoading]         = useState(false)
  const panelRef                      = useRef<HTMLDivElement>(null)

  // Non-actionable notifications hidden after mark-all-read
  const notifs = allNotifs.filter(n =>
    ACTIONABLE.has(n.kind) || !dismissedIds.has(n.id)
  )

  // ── Fetch ────────────────────────────────────────────────────────────────

  const fetchNotifications = useCallback(async () => {
    try {
      const [apprRes, wfRes, watcherRes] = await Promise.all([
        getPendingApprovals({ limit: 20 }),
        listWorkflows({ workflow_type: 'incident', limit: 20, sort_by: 'created_at', sort_order: 'desc' }),
        listWatchers(),
      ])
      setAllNotifs(buildNotifications(
        apprRes.data          || [],
        wfRes.data?.workflows || [],
        watcherRes.data       || [],
      ))
    } catch { /* silent — bell should never crash the app */ }
  }, [])

  useEffect(() => {
    fetchNotifications()
    const iv = setInterval(fetchNotifications, POLL_MS)
    return () => clearInterval(iv)
  }, [fetchNotifications])

  // Instant refresh on real-time WS events
  useGlobalEvents(useCallback((ev) => {
    if (
      ev.type === 'incident_created'  ||
      ev.type === 'incident_updated'  ||
      ev.type === 'approval_requested'||
      ev.type === 'watcher_registered'
    ) {
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

  // ── Badge: unseen + non-dismissed ────────────────────────────────────────

  const unseenCount = notifs.filter(n => !seenIds.has(n.id)).length

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleOpen = () => {
    setOpen(o => !o)
    setLoading(true)
    fetchNotifications().finally(() => setLoading(false))
  }

  const handleMarkAllRead = () => {
    // Mark everything as seen
    markSeen(notifs.map(n => n.id))
    setSeenIds(getSeenIds())

    // Dismiss non-actionable items from the list
    const toDismiss = notifs.filter(n => !ACTIONABLE.has(n.kind)).map(n => n.id)
    if (toDismiss.length > 0) {
      dismissIds(toDismiss)
      setDismissedIds(getDismissedIds())
    }
  }

  const handleClickNotif = (n: Notification) => {
    markSeen([n.id])
    setSeenIds(getSeenIds())
    setOpen(false)
    if (n.navTarget) {
      onNavigate(n.navTarget)
    } else if (n.kind === 'approval') {
      onNavigate('approvals', n.workflowId)
    } else {
      onNavigate('details', n.workflowId)
    }
  }

  // ── Styles ────────────────────────────────────────────────────────────────

  const panel = {
    position:    'absolute' as const,
    top:         '100%',
    right:       0,
    marginTop:   8,
    width:       340,
    borderRadius: 12,
    boxShadow:   '0 20px 60px rgba(0,0,0,0.5)',
    zIndex:      9999,
    background:  darkMode ? '#1a1f2e' : '#ffffff',
    border:      `1px solid ${darkMode ? '#2d3748' : '#e2e8f0'}`,
    overflow:    'hidden' as const,
  }

  const headerRow = {
    display:        'flex',
    alignItems:     'center',
    justifyContent: 'space-between',
    padding:        '14px 16px 12px',
    borderBottom:   `1px solid ${darkMode ? '#2d3748' : '#f1f5f9'}`,
  }

  const listStyle = { maxHeight: 360, overflowY: 'auto' as const }

  const footerRow = {
    padding:   '10px 16px',
    borderTop: `1px solid ${darkMode ? '#2d3748' : '#f1f5f9'}`,
    display:   'flex',
    gap:       8,
  }

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div style={{ position: 'relative' }} ref={panelRef}>
      {/* Bell button */}
      <button
        onClick={handleOpen}
        style={{
          position: 'relative', padding: 8, borderRadius: 8,
          border: 'none', cursor: 'pointer',
          background: open ? (darkMode ? '#1e2a3a' : '#f1f5f9') : 'transparent',
          color: darkMode ? '#9ca3af' : '#6b7280',
          transition: 'background 150ms, color 150ms',
        }}
        title="Notifications"
      >
        <IconBell size={20} strokeWidth={2} />

        {unseenCount > 0 && (
          <span style={{
            position: 'absolute', top: 2, right: 2,
            minWidth: 16, height: 16,
            background: '#ef4444', color: '#fff',
            fontSize: 10, fontWeight: 700,
            borderRadius: 8,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            padding: '0 4px', lineHeight: 1,
            boxShadow: '0 0 0 2px ' + (darkMode ? '#0f1419' : '#ffffff'),
          }}>
            {unseenCount > 99 ? '99+' : unseenCount}
          </span>
        )}
      </button>

      {/* Dropdown panel */}
      {open && (
        <div style={panel}>
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
                const dot    = kindDot(n.kind)
                return (
                  <button
                    key={n.id}
                    onClick={() => handleClickNotif(n)}
                    style={{
                      display: 'flex', alignItems: 'flex-start', gap: 12,
                      width: '100%', padding: '11px 16px',
                      border: 'none',
                      borderBottom: `1px solid ${darkMode ? '#1e2535' : '#f8fafc'}`,
                      cursor: 'pointer', textAlign: 'left',
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
                    <span style={{
                      flexShrink: 0, width: 8, height: 8,
                      borderRadius: '50%', background: dot,
                      marginTop: 5,
                      boxShadow: unseen ? `0 0 6px ${dot}99` : 'none',
                    }} />

                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between',
                        alignItems: 'center', gap: 6, marginBottom: 2 }}>
                        <span style={{
                          fontSize: 10, fontWeight: 700, letterSpacing: '0.5px',
                          color: dot, textTransform: 'uppercase',
                        }}>
                          {kindLabel(n.kind)}
                        </span>
                        <span style={{ fontSize: 11, color: darkMode ? '#4a5568' : '#94a3b8', flexShrink: 0 }}>
                          {n.timeAgo}
                        </span>
                      </div>
                      <div style={{
                        fontSize: 13, fontWeight: unseen ? 600 : 400,
                        color: darkMode ? '#e8eef5' : '#1e293b',
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        {n.title}
                      </div>
                      <div style={{
                        fontSize: 12, color: darkMode ? '#6b7a93' : '#64748b',
                        marginTop: 1,
                        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                      }}>
                        {n.subtitle}
                      </div>
                    </div>

                    {unseen && (
                      <span style={{
                        flexShrink: 0, width: 6, height: 6,
                        borderRadius: '50%', background: '#3b82f6', marginTop: 7,
                      }} />
                    )}
                  </button>
                )
              })
            )}
          </div>

          {notifs.length > 0 && (
            <div style={footerRow}>
              <button
                onClick={handleMarkAllRead}
                style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  fontSize: 12, fontWeight: 500,
                  color: darkMode ? '#6b7a93' : '#64748b',
                  background: 'none', border: 'none', cursor: 'pointer',
                  padding: '4px 8px', borderRadius: 6,
                }}
              >
                <IconCheck size={12} strokeWidth={2.5} />
                Mark all read
              </button>
              <button
                onClick={() => { setOpen(false); onNavigate('approvals') }}
                style={{
                  marginLeft: 'auto',
                  fontSize: 12, fontWeight: 500, color: '#3b82f6',
                  background: 'none', border: 'none', cursor: 'pointer',
                  padding: '4px 8px', borderRadius: 6,
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
