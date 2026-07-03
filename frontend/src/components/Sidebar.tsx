import { useState, useEffect, useCallback } from 'react'
import {
  DashboardIcon,
  IncidentsIcon,
  CheckCircleIcon,
  SettingsIcon,
  RunbookIcon,
  ShieldCheckIcon,
  ActivityIcon,
  CMDBIcon,
  MonitorIcon,
  ConnectorIcon,
  PoliciesIcon,
  AdminIcon,
  UsersNavIcon,
  StormIcon,
  PlatformIntelIcon,
  EventTypesIcon,
  type IconProps,
} from './IconWrappers'
import { countPendingRecommendations, getNavBadgeCounts } from '../services/api'
import { useGlobalEvents } from '../hooks/useGlobalEvents'

interface SidebarProps {
  isOpen: boolean
  currentView: string
  onNavigate: (view: string) => void
  darkMode: boolean
  userRole?: string
}

type IconComponent = (props: IconProps) => JSX.Element

interface MenuItem {
  icon?: IconComponent
  label?: string
  view?: string
  divider?: boolean
  allowedRoles?: string[]
  badge?: number   // numeric badge shown on the item (0 = hidden)
}

// Role constants for clarity
const ALL_HUMANS  = ['admin', 'itom_admin', 'operator', 'viewer']
const OPS_UP      = ['admin', 'itom_admin', 'operator']   // can act on incidents
const ITOM_UP     = ['admin', 'itom_admin']                // can configure automation
const ADMIN_ONLY  = ['admin']

// Event types that should trigger an immediate badge refetch
const BADGE_EVENTS = new Set([
  'incident_created', 'incident_updated',
  'approval_requested', 'approval_resolved',
  'monitoring_event_new', 'storm_changed',
])

export default function Sidebar({
  isOpen,
  currentView,
  onNavigate,
  darkMode,
  userRole = 'viewer',
}: SidebarProps) {
  const [pendingRecs,       setPendingRecs]       = useState(0)
  const [activeIncidents,   setActiveIncidents]   = useState(0)
  const [pendingApprovals,  setPendingApprovals]  = useState(0)
  const [newEvents,         setNewEvents]         = useState(0)
  const [activeStorms,      setActiveStorms]      = useState(0)

  // Poll pending recommendation count every 2 minutes (Platform Intel only)
  useEffect(() => {
    if (!ITOM_UP.includes(userRole)) return
    const fetchRecs = async () => {
      try {
        const res = await countPendingRecommendations()
        setPendingRecs(res.data.pending)
      } catch { /* silently ignore */ }
    }
    fetchRecs()
    const id = setInterval(fetchRecs, 120_000)
    return () => clearInterval(id)
  }, [userRole])

  // Fetch all nav badge counts from the server.
  // Stable reference — deps are only state setters (never change).
  const fetchCounts = useCallback(async () => {
    try {
      const res = await getNavBadgeCounts()
      setActiveIncidents(res.data.active_incidents)
      setPendingApprovals(res.data.pending_approvals)
      setNewEvents(res.data.new_events)
      setActiveStorms(res.data.active_storms)
    } catch { /* silently ignore */ }
  }, [])

  // Initial load + re-fetch whenever the user navigates to a new page
  // (keeps badges sharp after the user takes an action on any page).
  // Also keeps a 2-min fallback poll so drift is corrected when the
  // WebSocket is temporarily disconnected.
  useEffect(() => {
    fetchCounts()
    const id = setInterval(fetchCounts, 120_000)
    return () => clearInterval(id)
  }, [currentView, fetchCounts])

  // WebSocket-driven instant updates: any badge-relevant server event
  // triggers an immediate refetch so badges update in under 1 second.
  useGlobalEvents(useCallback((event) => {
    if (BADGE_EVENTS.has(event.type)) fetchCounts()
  }, [fetchCounts]))

  const allItems: MenuItem[] = [
    { icon: DashboardIcon,      label: 'Dashboard',          view: 'dashboard',           allowedRoles: ALL_HUMANS },
    { icon: IncidentsIcon,      label: 'Incidents',          view: 'incidents',           allowedRoles: ALL_HUMANS, badge: currentView === 'incidents' ? 0 : activeIncidents },
    { icon: IncidentsIcon,      label: 'Create Incident',    view: 'incident',            allowedRoles: OPS_UP },
    { icon: CheckCircleIcon,    label: 'Approvals',          view: 'approvals',           allowedRoles: ALL_HUMANS, badge: currentView === 'approvals' ? 0 : pendingApprovals },
    { icon: ActivityIcon,       label: 'Events',             view: 'events',              allowedRoles: ALL_HUMANS, badge: currentView === 'events'    ? 0 : newEvents        },
    { icon: StormIcon,          label: 'Event Storms',       view: 'storms',              allowedRoles: ALL_HUMANS, badge: currentView === 'storms'    ? 0 : activeStorms     },
    { icon: RunbookIcon,        label: 'Runbook Library',    view: 'runbook-browser',     allowedRoles: ALL_HUMANS },
    { icon: PoliciesIcon,       label: 'Policies',           view: 'policies',            allowedRoles: ITOM_UP },
    { icon: RunbookIcon,        label: 'Runbook Editor',     view: 'runbooks',            allowedRoles: ITOM_UP },
    { icon: EventTypesIcon,     label: 'Event Types',        view: 'event-types',         allowedRoles: ITOM_UP },
    { icon: ShieldCheckIcon,    label: 'Actions',            view: 'approved-actions',    allowedRoles: ITOM_UP },
    { icon: CMDBIcon,           label: 'CMDB',               view: 'cmdb',                allowedRoles: ALL_HUMANS },
    { icon: MonitorIcon,        label: 'Monitoring',         view: 'monitoring',          allowedRoles: ITOM_UP },
    { icon: ConnectorIcon,      label: 'Connectors',         view: 'connectors',          allowedRoles: ITOM_UP },
    { icon: PlatformIntelIcon,  label: 'Platform Intel',     view: 'platform-intelligence', allowedRoles: ITOM_UP, badge: pendingRecs },
    { divider: true },
    { icon: AdminIcon,          label: 'Admin',              view: 'admin',               allowedRoles: ADMIN_ONLY },
    { icon: UsersNavIcon,       label: 'Users',              view: 'users',               allowedRoles: ADMIN_ONLY },
    { icon: SettingsIcon,       label: 'Settings',           view: 'settings',            allowedRoles: ADMIN_ONLY },
  ]

  // Filter to items this role can see; also hide dividers with nothing visible below them
  const menuItems = allItems.reduce<MenuItem[]>((acc, item) => {
    if (item.divider) {
      acc.push(item)
      return acc
    }
    if (!item.allowedRoles || item.allowedRoles.includes(userRole)) {
      acc.push(item)
    }
    return acc
  }, [])

  // Remove trailing or lonely dividers
  const visibleItems = menuItems.filter((item, i, arr) => {
    if (!item.divider) return true
    const nextNonDivider = arr.slice(i + 1).find(x => !x.divider)
    return !!nextNonDivider
  })

  return (
    <aside
      className={`fixed left-0 top-16 h-[calc(100vh-4rem)] transition-all duration-300 z-40 overflow-hidden ${
        darkMode ? 'bg-slate-900 border-r border-slate-800' : 'bg-gray-50 border-r border-gray-200'
      } ${isOpen ? 'w-64' : 'w-20'}`}
    >
      <nav className="p-4 space-y-2 h-full overflow-y-auto scrollbar-hide">
        {visibleItems.map((item, index) => {
          if (item.divider) {
            return (
              <div
                key={`divider-${index}`}
                className="my-4"
                style={{ borderTop: darkMode ? '1px solid #3d4557' : '1px solid #d1d5db' }}
              />
            )
          }

          if (!item.view || !item.icon || !item.label) return null

          const Icon = item.icon as IconComponent
          const label = item.label as string
          const view = item.view as string
          const isActive = currentView === view
          const badgeCount = item.badge ?? 0

          return (
            <button
              key={view}
              onClick={() => onNavigate(view)}
              className={`w-full flex items-center gap-4 px-4 py-3 rounded-lg transition-all duration-200 ${
                isActive
                  ? darkMode
                    ? 'bg-info-500/20 text-info-400 border border-info-500/30'
                    : 'bg-info-50 text-info-600 border border-info-200'
                  : darkMode
                  ? 'text-gray-400 hover:bg-slate-800 hover:text-gray-200'
                  : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
              }`}
              title={isOpen ? '' : label}
            >
              {/* Icon — with dot badge when collapsed */}
              <span className="relative flex-shrink-0">
                <Icon className="w-5 h-5" />
                {!isOpen && badgeCount > 0 && (
                  <span
                    style={{
                      position: 'absolute', top: '-4px', right: '-4px',
                      width: '8px', height: '8px', borderRadius: '50%',
                      backgroundColor: '#f97316', display: 'block',
                    }}
                  />
                )}
              </span>
              {isOpen && (
                <>
                  <span className="font-medium text-sm flex-1 text-left">{label}</span>
                  {badgeCount > 0 && (
                    <span style={{
                      fontSize: '10px', fontWeight: 700,
                      backgroundColor: '#f97316', color: '#fff',
                      borderRadius: '10px', padding: '1px 6px',
                      minWidth: '18px', textAlign: 'center',
                    }}>
                      {badgeCount > 99 ? '99+' : badgeCount}
                    </span>
                  )}
                </>
              )}
            </button>
          )
        })}
      </nav>

    </aside>
  )
}
