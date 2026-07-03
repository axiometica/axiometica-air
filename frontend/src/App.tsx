import { useState, useEffect } from 'react'
import { checkReadiness } from './services/api'
import { enableGlobalFetchInterception } from './services/apiInterceptor'
import { IconAlertTriangle, IconX } from './components/icons'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import Dashboard from './components/Dashboard'
import IncidentForm from './components/IncidentForm'
import ChangeForm from './components/ChangeForm'
import WorkflowDetailsPhase6 from './components/WorkflowDetailsPhase6'
import ApprovalQueue from './components/ApprovalQueue'
import PolicyList from './components/PolicyList'
import AdminPanel from './components/AdminPanel'
import Settings from './components/Settings'
import RunbookList from './components/RunbookList'
import RunbookEditor from './components/RunbookEditor'
import RunbookBrowser from './components/RunbookBrowser'
import ApprovedActionsList from './components/ApprovedActionsList'
import ActionEditor from './components/ActionEditor'
import EventsFeed from './components/EventsFeed'
import IncidentList from './components/IncidentList'
import CMDBPage from './components/CMDBPage'
import MonitoringSetup from './components/MonitoringSetup'
import ConnectorHub from './components/ConnectorHub'
import Login from './components/Login'
import UserManagement from './components/UserManagement'
import StormsDashboard from './components/StormsDashboard'
import PlatformIntelligencePage from './components/PlatformIntelligencePage'
import EventTypesPage from './components/EventTypesPage'
import ChatPanel from './components/ChatPanel'
import { useCurrentUser } from './hooks/useCurrentUser'
import ErrorBoundary from './components/ErrorBoundary'

type View = 'dashboard' | 'incident' | 'change' | 'details' | 'approvals' | 'policies' | 'admin' | 'settings' | 'runbooks' | 'runbook-editor' | 'runbook-browser' | 'approved-actions' | 'action-editor' | 'events' | 'incidents' | 'cmdb' | 'monitoring' | 'connectors' | 'users' | 'storms' | 'platform-intelligence' | 'event-types'

export default function App() {
  const { user, loading: authLoading, logout, refetch, isAdmin, isITOMAdmin } = useCurrentUser()
  const [view, setView] = useState<View>('dashboard')
  const [detailsOrigin, setDetailsOrigin] = useState<View>('dashboard')
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null)
  const [selectedRunbookId, setSelectedRunbookId] = useState<string | null>(null)
  const [selectedActionId, setSelectedActionId] = useState<string | null>(null)
  const [isHealthy, setIsHealthy] = useState(true)
  const darkMode = true  // Always use dark mode; theme toggle disabled
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    const saved = localStorage.getItem('sidebarOpen')
    return saved !== null ? JSON.parse(saved) : true // Default to open
  })

  useEffect(() => {
    // Always use dark mode (theme toggle disabled)
    document.documentElement.classList.add('dark')

    // Enable global fetch interception for 401/403 handling
    enableGlobalFetchInterception()
  }, [])

  useEffect(() => {
    localStorage.setItem('sidebarOpen', JSON.stringify(sidebarOpen))
  }, [sidebarOpen])

  useEffect(() => {
    // Check API readiness on startup (comprehensive health check)
    const performHealthCheck = async () => {
      try {
        await checkReadiness()
        setIsHealthy(true)
      } catch (err) {
        console.error('Health check failed:', err)
        setIsHealthy(false)
      }
    }

    performHealthCheck()

    // Periodic comprehensive health checks every 30 seconds
    const interval = setInterval(performHealthCheck, 30000)

    return () => clearInterval(interval)
  }, [])

  const handleWorkflowSubmitted = (workflowId: string) => {
    setDetailsOrigin(view)
    setSelectedWorkflowId(workflowId)
    setView('details')
  }

  const handleViewWorkflow = (workflowId: string) => {
    setDetailsOrigin(view)
    setSelectedWorkflowId(workflowId)
    setView('details')
  }

  const handleNavigateToIncidents = () => {
    setView('incidents')
  }

  // Used by NotificationBell — navigates to a view, optionally selecting a workflow
  const handleHeaderNavigate = (newView: string, workflowId?: string) => {
    if (workflowId) setSelectedWorkflowId(workflowId)
    setView(newView as View)
  }

  // Auth gate — show spinner while checking token, then login page if not authenticated
  if (authLoading) {
    return (
      <div style={{ minHeight: '100vh', background: '#0d1117', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ color: '#64748b', fontSize: '0.9rem' }}>Loading…</div>
      </div>
    )
  }

  if (!user) {
    return <Login onLogin={refetch} />
  }

  return (
    <div className={`min-h-screen transition-colors duration-300 ${darkMode ? 'bg-slate-950' : 'bg-gray-50'}`}>
      <Header
        isHealthy={isHealthy}
        darkMode={darkMode}
        onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
        onNavigate={handleHeaderNavigate}
        user={user}
        onLogout={logout}
      />

      <Sidebar
        isOpen={sidebarOpen}
        currentView={view}
        onNavigate={(newView) => setView(newView as View)}
        darkMode={darkMode}
        userRole={user?.role}
      />

      <main className={`transition-all duration-300 ${sidebarOpen ? 'ml-64' : 'ml-20'} px-4 py-8 min-h-[calc(100vh-4rem)]`}>
        <div className="max-w-7xl mx-auto">
        <ErrorBoundary>
        {!isHealthy && (
          <div className="mb-4 flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-800 border border-slate-600 text-sm" style={{ color: '#a0aec0' }}>
            <IconAlertTriangle size={15} className="flex-shrink-0 text-warning-500" />
            <span>API unavailable — some features may not work</span>
            <button
              onClick={() => setIsHealthy(true)}
              className="ml-auto hover:text-white transition-colors"
              title="Dismiss"
            >
              <IconX size={14} />
            </button>
          </div>
        )}

        {view === 'dashboard' && (
          <Dashboard onViewWorkflow={handleViewWorkflow} onNavigateToIncidents={handleNavigateToIncidents} onNavigate={(v) => setView(v as View)} darkMode={darkMode} />
        )}

        {view === 'incident' && (
          <IncidentForm onSubmitted={handleWorkflowSubmitted} darkMode={darkMode} />
        )}

        {view === 'change' && (
          <ChangeForm onSubmitted={handleWorkflowSubmitted} darkMode={darkMode} />
        )}

        {view === 'details' && selectedWorkflowId && (
          <WorkflowDetailsPhase6
            workflowId={selectedWorkflowId}
            onBack={() => setView(detailsOrigin)}
            onViewWorkflow={handleViewWorkflow}
            darkMode={darkMode}
          />
        )}

        {view === 'approvals' && (
          <ApprovalQueue onApproved={() => {}} />
        )}

        {view === 'policies' && isITOMAdmin && (
          <PolicyList darkMode={darkMode} />
        )}

        {view === 'runbook-browser' && (
          <RunbookBrowser />
        )}

        {view === 'runbooks' && isITOMAdmin && (
          <RunbookList
            darkMode={darkMode}
            onEdit={(id) => { setSelectedRunbookId(id); setView('runbook-editor') }}
            onNew={() => window.open('/editor/', '_blank', 'noopener')}
          />
        )}

        {view === 'runbook-editor' && isITOMAdmin && (
          <RunbookEditor
            darkMode={darkMode}
            runbookId={selectedRunbookId || undefined}
            onSave={() => setView('runbooks')}
            onCancel={() => setView('runbooks')}
          />
        )}

        {view === 'approved-actions' && isITOMAdmin && (
          <ApprovedActionsList
            onEdit={(id) => { setSelectedActionId(id); setView('action-editor') }}
            onNew={() => { setSelectedActionId(null); setView('action-editor') }}
          />
        )}

        {view === 'action-editor' && isITOMAdmin && (
          <ActionEditor
            actionId={selectedActionId}
            onBack={() => setView('approved-actions')}
            onSaved={() => setView('approved-actions')}
          />
        )}

        {view === 'admin' && isAdmin && (
          <AdminPanel darkMode={darkMode} />
        )}

        {view === 'settings' && isAdmin && (
          <Settings />
        )}

        {view === 'events' && (
          <EventsFeed darkMode={darkMode} onViewWorkflow={handleViewWorkflow} />
        )}

        {view === 'incidents' && (
          <IncidentList
            onViewWorkflow={handleViewWorkflow}
            onBack={() => setView('dashboard')}
          />
        )}

        {view === 'cmdb' && (
          <CMDBPage darkMode={darkMode} />
        )}

        {view === 'monitoring' && isITOMAdmin && (
          <MonitoringSetup />
        )}

        {view === 'connectors' && isITOMAdmin && (
          <ConnectorHub darkMode={darkMode} />
        )}

        {view === 'users' && isAdmin && (
          <UserManagement />
        )}

        {view === 'storms' && (
          <StormsDashboard />
        )}

        {view === 'platform-intelligence' && isITOMAdmin && (
          <PlatformIntelligencePage darkMode={darkMode} />
        )}

        {view === 'event-types' && isITOMAdmin && (
          <EventTypesPage />
        )}
        </ErrorBoundary>
        </div>
      </main>

      {/* Floating AI Ops Assistant — rendered outside <main> so it sits above all content */}
      <ChatPanel contextWorkflowId={view === 'details' ? selectedWorkflowId : null} />
    </div>
  )
}
