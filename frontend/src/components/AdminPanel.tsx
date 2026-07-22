import { useState, useEffect, useCallback } from 'react'
import { getAdminStatistics, getSystemStatus, platformReset, getComprehensiveHealthCheck, ComprehensiveHealthResponse, triggerBackup, getBackupStatus, BackupStatus, getWorkerHealth, WorkerHealthResponse } from '../services/api'
import { IconCheck, IconAlertTriangle, IconAlertCircle, IconClipboardList, IconBolt, IconRefresh, IconDatabase, IconActivity, IconServer } from './icons'
import { parseUTC, formatRelativeTime } from '../utils/dateFormatter'

interface AdminPanelProps {
  darkMode?: boolean
}

interface Statistics {
  total_incidents: number
  total_workflows: number
  active_incidents: number
  timestamp: string
}

interface SystemStatus {
  database_status: string
  redis_status: string
  timestamp: string
}

export default function AdminPanel({ darkMode = true }: AdminPanelProps) {
  const [statistics, setStatistics] = useState<Statistics | null>(null)
  const [systemStatus, setSystemStatus] = useState<SystemStatus | null>(null)
  const [healthCheck, setHealthCheck] = useState<ComprehensiveHealthResponse | null>(null)
  const [workerHealth, setWorkerHealth] = useState<WorkerHealthResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [healthCheckLoading, setHealthCheckLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleteConfirmInput, setDeleteConfirmInput] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteSuccess, setDeleteSuccess] = useState<string | null>(null)

  // Backup state
  const [backupStatus, setBackupStatus] = useState<BackupStatus | null>(null)
  const [backupLoading, setBackupLoading] = useState(false)
  const [backupError, setBackupError] = useState<string | null>(null)
  const [backupTriggered, setBackupTriggered] = useState(false)

  const loadBackupStatus = useCallback(async () => {
    try {
      const res = await getBackupStatus()
      setBackupStatus(res.data)
    } catch {
      // Non-fatal — backup status is informational
    }
  }, [])

  useEffect(() => {
    loadData()
    loadBackupStatus()
    const interval = setInterval(loadData, 30000) // Refresh every 30 seconds
    return () => clearInterval(interval)
  }, [loadBackupStatus])

  // Poll backup status while a backup is in progress
  useEffect(() => {
    if (backupStatus?.last_backup_status !== 'in_progress') return
    const poll = setInterval(loadBackupStatus, 5000)
    return () => clearInterval(poll)
  }, [backupStatus?.last_backup_status, loadBackupStatus])

  const loadData = async () => {
    try {
      setLoading(true)
      const [statsRes, statusRes, healthRes, workerRes] = await Promise.all([
        getAdminStatistics(),
        getSystemStatus(),
        getComprehensiveHealthCheck(),
        getWorkerHealth().catch(() => null),
      ])
      setStatistics(statsRes.data)
      setSystemStatus(statusRes.data)
      setHealthCheck(Array.isArray(healthRes.data) ? healthRes.data[0] : healthRes.data)
      if (workerRes) setWorkerHealth(workerRes.data)
      setError(null)
    } catch (err) {
      setError('Failed to load admin data')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const refreshHealthCheck = async () => {
    try {
      setHealthCheckLoading(true)
      const healthRes = await getComprehensiveHealthCheck()
      setHealthCheck(Array.isArray(healthRes.data) ? healthRes.data[0] : healthRes.data)
    } catch (err) {
      setError('Failed to refresh health check')
      console.error(err)
    } finally {
      setHealthCheckLoading(false)
    }
  }

  const handleRunBackup = async () => {
    try {
      setBackupLoading(true)
      setBackupError(null)
      setBackupTriggered(false)
      await triggerBackup()
      setBackupTriggered(true)
      // Start polling — useEffect above will detect in_progress and poll
      setTimeout(loadBackupStatus, 1500)
    } catch (err: any) {
      const msg = err.response?.data?.detail || 'Failed to trigger backup'
      setBackupError(msg)
    } finally {
      setBackupLoading(false)
    }
  }

  const handleDeleteIncidents = async () => {
    if (deleteConfirmInput !== 'DELETE') {
      setError('Please type "DELETE" to confirm')
      return
    }

    try {
      setIsDeleting(true)
      await platformReset()
      setDeleteSuccess('Platform reset complete')
      setShowDeleteConfirm(false)
      setDeleteConfirmInput('')
      setTimeout(() => {
        setDeleteSuccess(null)
        loadData()
      }, 3000)
    } catch (err) {
      setError('Failed to delete incidents')
      console.error(err)
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold mb-2" style={{ color: '#e8eef5' }}>
          Admin Panel
        </h1>
        <p style={{ color: '#a0aec0' }}>System utilities and management tools</p>
      </div>

      {/* Messages */}
      {error && (
        <div className="mb-6 p-4 rounded-lg bg-critical-900/40 border border-critical-500/30 text-critical-400 flex items-center gap-2">
          <IconAlertTriangle size={20} className="flex-shrink-0" />
          {error}
        </div>
      )}

      {deleteSuccess && (
        <div className="mb-6 p-4 rounded-lg bg-success-900/40 border border-success-500/30 text-success-400 flex items-center gap-2">
          <IconCheck size={20} className="flex-shrink-0" />
          {deleteSuccess}
        </div>
      )}

      {/* Statistics Section */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold mb-4" style={{ color: '#e8eef5' }}>
          System Statistics
        </h2>
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {[1, 2, 3].map((i) => (
              <div key={i} className="metric-card h-32 skeleton-pulse" />
            ))}
          </div>
        ) : statistics ? (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <StatisticCard
              title="Total Incidents"
              value={statistics.total_incidents}
              icon={<IconAlertCircle size={40} />}
              darkMode={darkMode}
            />
            <StatisticCard
              title="Total Workflows"
              value={statistics.total_workflows}
              icon={<IconClipboardList size={40} />}
              darkMode={darkMode}
            />
            <StatisticCard
              title="Active Incidents"
              value={statistics.active_incidents}
              icon={<IconBolt size={40} />}
              color={statistics.active_incidents > 0 ? 'warning' : 'success'}
              darkMode={darkMode}
            />
          </div>
        ) : null}
      </div>

      {/* Worker Health Section */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold mb-4" style={{ color: '#e8eef5' }}>
          Worker Health
        </h2>
        {workerHealth ? (
          <div className="space-y-4">
            {/* Stuck incidents warning */}
            {workerHealth.stuck_incidents.length > 0 && (
              <div className="p-4 rounded-lg flex items-start gap-3" style={{
                backgroundColor: 'rgba(239,68,68,0.1)',
                border: '1px solid rgba(239,68,68,0.3)',
              }}>
                <IconAlertTriangle size={20} style={{ color: '#ef4444', flexShrink: 0, marginTop: '2px' }} />
                <div>
                  <p className="font-semibold mb-1" style={{ color: '#ef4444' }}>
                    {workerHealth.stuck_incidents.length} stuck incident{workerHealth.stuck_incidents.length !== 1 ? 's' : ''} detected
                  </p>
                  <div className="space-y-1">
                    {workerHealth.stuck_incidents.map(inc => (
                      <p key={inc.workflow_id} style={{ fontSize: '12px', color: '#fca5a5' }}>
                        {inc.incident_number} — {inc.state} — stuck {Math.round(inc.stuck_minutes)}m
                      </p>
                    ))}
                  </div>
                </div>
              </div>
            )}

            {/* Queue depths */}
            <div className="grid grid-cols-3 gap-3">
              {Object.entries(workerHealth.queue_depths).map(([queue, depth]) => (
                <div key={queue} className="metric-card p-4 flex items-center gap-3">
                  <IconDatabase size={24} style={{ color: depth > 0 ? '#f59e0b' : '#10b981', flexShrink: 0 }} />
                  <div>
                    <p style={{ fontSize: '11px', color: '#a0aec0', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{queue}</p>
                    <p style={{ fontSize: '22px', fontWeight: 700, color: depth > 0 ? '#f59e0b' : '#e8eef5' }}>{depth}</p>
                    <p style={{ fontSize: '10px', color: '#6b7280' }}>pending tasks</p>
                  </div>
                </div>
              ))}
            </div>

            {/* Worker cards */}
            {!workerHealth.celery_reachable ? (
              <div className="p-4 rounded-lg flex items-center gap-3" style={{
                backgroundColor: 'rgba(239,68,68,0.08)',
                border: '1px solid rgba(239,68,68,0.25)',
              }}>
                <IconServer size={20} style={{ color: '#ef4444' }} />
                <p style={{ color: '#fca5a5', fontSize: '13px' }}>Celery broker unreachable — worker status unavailable</p>
              </div>
            ) : workerHealth.workers.length === 0 ? (
              <div className="p-4 rounded-lg" style={{ backgroundColor: '#1a1f2e', border: '1px solid #3d4557' }}>
                <p style={{ color: '#6b7280', fontSize: '13px' }}>No workers online</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {workerHealth.workers.map(worker => (
                  <div key={worker.name} className="metric-card p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <span style={{
                        width: '8px', height: '8px', borderRadius: '50%', flexShrink: 0,
                        backgroundColor: worker.status === 'online' ? '#10b981' : '#ef4444',
                        boxShadow: worker.status === 'online' ? '0 0 6px #10b981' : undefined,
                      }} />
                      <span style={{ fontWeight: 600, color: '#e8eef5' }}>{worker.short_name}</span>
                      <span style={{
                        fontSize: '10px', fontWeight: 700, padding: '1px 6px', borderRadius: '4px',
                        backgroundColor: worker.status === 'online' ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)',
                        color: worker.status === 'online' ? '#10b981' : '#ef4444',
                        border: `1px solid ${worker.status === 'online' ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.3)'}`,
                        marginLeft: 'auto',
                      }}>
                        {worker.status.toUpperCase()}
                      </span>
                    </div>
                    <div className="flex gap-4" style={{ fontSize: '12px', color: '#a0aec0' }}>
                      <span>
                        <span style={{ color: worker.active_tasks > 0 ? '#f59e0b' : '#e8eef5', fontWeight: 600 }}>
                          {worker.active_tasks}
                        </span> active
                      </span>
                      <span>
                        <span style={{ color: '#e8eef5', fontWeight: 600 }}>{worker.processed.toLocaleString()}</span> processed
                      </span>
                    </div>
                    {worker.queues.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2">
                        {worker.queues.map(q => (
                          <span key={q} style={{
                            fontSize: '10px', padding: '1px 6px', borderRadius: '4px',
                            backgroundColor: 'rgba(59,130,246,0.1)', color: '#60a5fa',
                            border: '1px solid rgba(59,130,246,0.2)',
                          }}>{q}</span>
                        ))}
                      </div>
                    )}
                    {worker.active_task_names.length > 0 && (
                      <div className="mt-2">
                        {worker.active_task_names.slice(0, 2).map((t, i) => (
                          <p key={i} style={{ fontSize: '10px', color: '#6b7280' }}>▶ {t}</p>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {[1, 2].map(i => <div key={i} className="metric-card h-28 skeleton-pulse" />)}
          </div>
        ) : null}
      </div>

      {/* Health Check Section */}
      <div className="mb-8">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-2xl font-bold" style={{ color: '#e8eef5' }}>
            Service Health Check
          </h2>
          <button
            onClick={refreshHealthCheck}
            disabled={healthCheckLoading}
            className="p-2 rounded-lg transition-all"
            style={{
              backgroundColor: darkMode ? '#2d3748' : '#e5e7eb',
              color: darkMode ? '#e8eef5' : '#1f2937',
              border: `1px solid ${darkMode ? '#3d4557' : '#d1d5db'}`,
            }}
            title="Refresh health check"
          >
            <IconRefresh size={18} className={healthCheckLoading ? 'animate-spin' : ''} />
          </button>
        </div>

        {healthCheckLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[1, 2, 3, 4, 5, 6, 7].map((i) => (
              <div key={i} className="h-20 rounded-lg skeleton-pulse" style={{ backgroundColor: darkMode ? '#2d3748' : '#f3f4f6' }} />
            ))}
          </div>
        ) : healthCheck ? (
          <div>
            {/* Summary Bar */}
            <div className="mb-4 p-4 rounded-lg" style={{
              backgroundColor: darkMode ? '#2d3748' : '#f3f4f6',
              borderColor: darkMode ? '#3d4557' : '#d1d5db',
              border: '1px solid',
            }}>
              <div className="grid grid-cols-3 gap-4 text-center">
                <div>
                  <p className="text-sm mb-1" style={{ color: '#a0aec0' }}>Total Checks</p>
                  <p className="text-2xl font-bold" style={{ color: '#e8eef5' }}>{healthCheck.summary.total_checks}</p>
                </div>
                <div>
                  <p className="text-sm mb-1" style={{ color: '#a0aec0' }}>Passed</p>
                  <p className="text-2xl font-bold text-green-400">{healthCheck.summary.passed}</p>
                </div>
                <div>
                  <p className="text-sm mb-1" style={{ color: '#a0aec0' }}>Failed</p>
                  <p className="text-2xl font-bold" style={{ color: healthCheck.summary.failed > 0 ? '#f87171' : '#a0aec0' }}>
                    {healthCheck.summary.failed}
                  </p>
                </div>
              </div>
            </div>

            {/* Health Check Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              <HealthCheckItem
                name="Database"
                check={healthCheck.checks.database}
                darkMode={darkMode}
              />
              <HealthCheckItem
                name="Database Tables"
                check={healthCheck.checks.database_tables}
                darkMode={darkMode}
              />
              <HealthCheckItem
                name="Event Bus (Redis)"
                check={healthCheck.checks.redis}
                darkMode={darkMode}
              />
              <HealthCheckItem
                name="Neo4j CMDB"
                check={healthCheck.checks.neo4j}
                darkMode={darkMode}
              />
              <HealthCheckItem
                name="Workflow Engine"
                check={healthCheck.checks.workflow_engine}
                darkMode={darkMode}
              />
              <HealthCheckItem
                name="Agent Registry"
                check={healthCheck.checks.agents_registered}
                darkMode={darkMode}
              />
              <HealthCheckItem
                name="API Routes"
                check={healthCheck.checks.api_routes}
                detail={`${healthCheck.checks.api_routes.endpoints?.length || 0} endpoints available`}
                darkMode={darkMode}
              />
              <HealthCheckItem
                name="Repositories"
                check={healthCheck.checks.repositories}
                detail={`${healthCheck.checks.repositories.count || 0} repositories loaded`}
                darkMode={darkMode}
              />
            </div>

            {/* Overall Status */}
            <div className="mt-4 flex items-center justify-between p-4 rounded-lg" style={{
              backgroundColor: healthCheck.summary.failed === 0 ? 'rgba(16, 185, 129, 0.1)' : 'rgba(248, 113, 113, 0.1)',
              borderColor: healthCheck.summary.failed === 0 ? '#10b981' : '#f87171',
              border: '1px solid',
            }}>
              <div className="flex items-center gap-2">
                {healthCheck.summary.failed === 0 ? (
                  <IconCheck size={20} className="text-green-400" />
                ) : (
                  <IconAlertTriangle size={20} className="text-red-400" />
                )}
                <span style={{ color: healthCheck.summary.failed === 0 ? '#10b981' : '#f87171' }} className="font-semibold">
                  {healthCheck.status === 'ready' ? 'All systems healthy' : 'System degraded'}
                </span>
              </div>
              <span className="text-xs" style={{ color: '#a0aec0' }}>
                Last checked: {parseUTC(healthCheck.timestamp).toLocaleTimeString()}
              </span>
            </div>
          </div>
        ) : null}
      </div>

      {/* System Status Section */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold mb-4" style={{ color: '#e8eef5' }}>
          System Status
        </h2>
        {systemStatus ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <StatusCard
              title="Database"
              status={systemStatus.database_status}
              darkMode={darkMode}
            />
            <StatusCard
              title="Redis Cache"
              status={systemStatus.redis_status}
              darkMode={darkMode}
            />
          </div>
        ) : null}
        {systemStatus && (
          <div className="mt-4 text-xs text-center" style={{ color: '#7a8ba3' }}>
            Last checked: {parseUTC(systemStatus.timestamp).toLocaleTimeString()}
          </div>
        )}
      </div>

      {/* Backup Section */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <IconDatabase size={24} style={{ color: '#e8eef5' }} />
          <h2 className="text-2xl font-bold" style={{ color: '#e8eef5' }}>
            Backup
          </h2>
        </div>
        <div
          className="rounded-lg border p-6"
          style={{
            backgroundColor: darkMode ? '#2d3748' : '#f3f4f6',
            borderColor: darkMode ? '#3d4557' : '#d1d5db',
          }}
        >
          {/* Last backup status */}
          {backupStatus && (
            <div className="mb-5">
              <div className="flex items-center gap-3 mb-2">
                {backupStatus.last_backup_status === 'ok' && (
                  <IconCheck size={18} style={{ color: '#10b981', flexShrink: 0 }} />
                )}
                {backupStatus.last_backup_status === 'error' && (
                  <IconAlertTriangle size={18} style={{ color: '#f87171', flexShrink: 0 }} />
                )}
                {backupStatus.last_backup_status === 'in_progress' && (
                  <IconRefresh size={18} className="animate-spin" style={{ color: '#60a5fa', flexShrink: 0 }} />
                )}
                <span className="font-semibold text-sm" style={{
                  color: backupStatus.last_backup_status === 'ok'          ? '#10b981'
                       : backupStatus.last_backup_status === 'error'       ? '#f87171'
                       : backupStatus.last_backup_status === 'in_progress' ? '#60a5fa'
                       : '#a0aec0'
                }}>
                  {backupStatus.last_backup_status === 'ok'          ? 'Last backup succeeded'
                   : backupStatus.last_backup_status === 'error'     ? 'Last backup failed'
                   : backupStatus.last_backup_status === 'in_progress' ? 'Backup in progress…'
                   : 'No backup on record'}
                </span>
                {backupStatus.last_backup_at && (
                  <span className="text-xs" style={{ color: '#7a8ba3' }}>
                    {formatRelativeTime(backupStatus.last_backup_at)}
                  </span>
                )}
              </div>
              {backupStatus.last_backup_message && backupStatus.last_backup_status === 'error' && (
                <p className="text-xs ml-7 mt-1" style={{ color: '#f87171' }}>
                  {backupStatus.last_backup_message}
                </p>
              )}
              <p className="text-xs ml-7" style={{ color: '#7a8ba3' }}>
                Retention: {backupStatus.retention_days} days · Files stored in{' '}
                <code className="font-mono">./backups/</code>
              </p>
            </div>
          )}

          {backupError && (
            <div className="mb-4 p-3 rounded-lg text-sm flex items-center gap-2"
              style={{ backgroundColor: 'rgba(248,113,113,0.1)', color: '#f87171', border: '1px solid rgba(248,113,113,0.3)' }}>
              <IconAlertTriangle size={15} />
              {backupError}
            </div>
          )}

          {backupTriggered && !backupError && (
            <div className="mb-4 p-3 rounded-lg text-sm flex items-center gap-2"
              style={{ backgroundColor: 'rgba(96,165,250,0.1)', color: '#60a5fa', border: '1px solid rgba(96,165,250,0.3)' }}>
              <IconRefresh size={15} className="animate-spin" />
              Backup queued — PostgreSQL, Neo4j CMDB, and Watcher config will be backed up in the background.
            </div>
          )}

          <div>
            <p className="mb-4 text-sm" style={{ color: '#a0aec0' }}>
              Runs a full backup: PostgreSQL database, Neo4j CMDB topology, and Watcher
              configuration. Files are written to <code className="font-mono">./backups/</code> on
              the host and pruned according to the <strong>Backup Retention</strong> setting in
              General Settings. The nightly cron backup runs automatically; use this for
              on-demand snapshots (e.g. before an upgrade).
            </p>
            <button
              onClick={handleRunBackup}
              disabled={backupLoading || backupStatus?.last_backup_status === 'in_progress'}
              className="px-6 py-2 rounded-lg transition-colors flex items-center gap-2"
              style={{
                backgroundColor: '#252c3c',
                border: '1px solid rgba(64, 112, 160, 0.40)',
                color: '#a0c4e8',
                opacity: (backupLoading || backupStatus?.last_backup_status === 'in_progress') ? 0.5 : 1,
                cursor: (backupLoading || backupStatus?.last_backup_status === 'in_progress')
                  ? 'not-allowed' : 'pointer',
              }}
            >
              {backupLoading || backupStatus?.last_backup_status === 'in_progress' ? (
                <><IconRefresh size={16} className="animate-spin" /> Running…</>
              ) : (
                <><IconDatabase size={16} /> Run Backup Now</>
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Log Download Section */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <IconActivity size={24} style={{ color: '#e8eef5' }} />
          <h2 className="text-2xl font-bold" style={{ color: '#e8eef5' }}>Diagnostic Logs</h2>
        </div>
        <div className="rounded-lg border p-6" style={{ backgroundColor: darkMode ? '#2d3748' : '#f3f4f6', borderColor: darkMode ? '#3d4557' : '#d1d5db' }}>
          <p className="mb-4" style={{ color: '#a0aec0', fontSize: '0.9rem' }}>
            Downloads the last 5 000 log lines from the backend (all modules) as a plain-text file.
            Use this when reporting issues — attach the file to your support ticket.
          </p>
          <button
            onClick={() => {
              const token = localStorage.getItem('ap_token')
              const a = document.createElement('a')
              a.href = `/api/admin/logs/download`
              // Trigger via fetch so we can attach the auth header
              fetch('/api/admin/logs/download', { headers: { Authorization: `Bearer ${token}` } })
                .then(r => r.blob())
                .then(blob => {
                  const url = URL.createObjectURL(blob)
                  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
                  a.href = url
                  a.download = `agentic_platform_logs_${ts}.txt`
                  a.click()
                  URL.revokeObjectURL(url)
                })
                .catch(e => alert('Log download failed: ' + e.message))
            }}
            className="px-6 py-2 rounded-lg transition-colors text-white flex items-center gap-2"
            style={{ backgroundColor: '#7c3aed', cursor: 'pointer' }}
          >
            <IconActivity size={16} /> Download Logs
          </button>
        </div>
      </div>

      {/* Data Management Section */}
      <div className="mb-8">
        <div className="flex items-center gap-2 mb-4">
          <IconAlertTriangle size={24} style={{ color: '#e8eef5' }} />
          <h2 className="text-2xl font-bold" style={{ color: '#e8eef5' }}>
            Data Management
          </h2>
        </div>
        <div
          className="rounded-lg border p-6"
          style={{
            backgroundColor: darkMode ? '#2d3748' : '#f3f4f6',
            borderColor: darkMode ? '#3d4557' : '#d1d5db',
          }}
        >
          <div className="mb-4">
            <h3 className="font-semibold mb-2" style={{ color: '#e8eef5' }}>
              Platform Reset
            </h3>
            <p className="mb-4" style={{ color: '#a0aec0' }}>
              Permanently delete all operational data: incidents, changes, events, approvals,
              monitoring events, agent execution logs, ServiceNow mappings, and Platform Intel
              recommendations. Configuration (settings, policies, runbooks, users) is preserved.
              This action cannot be undone.
            </p>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="px-6 py-2 rounded-lg bg-critical-500 text-white hover:bg-critical-600 transition-colors"
              disabled={isDeleting}
            >
              {isDeleting ? 'Resetting...' : 'Reset Platform Data'}
            </button>
          </div>
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div
            className="rounded-lg p-6 max-w-md w-full"
            style={{
              backgroundColor: darkMode ? '#1a1f2e' : '#fff',
            }}
          >
            <div className="flex items-center gap-2 mb-4">
              <IconAlertTriangle size={24} style={{ color: '#e8eef5' }} />
              <h3 className="text-xl font-bold" style={{ color: '#e8eef5' }}>
                Reset Platform Data
              </h3>
            </div>
            <p className="mb-4" style={{ color: '#a0aec0' }}>
              This will permanently delete all operational data — incidents, changes, approvals,
              monitoring events, agent logs, ServiceNow mappings, and Platform Intel recommendations.
              Configuration is preserved. This cannot be undone.
            </p>
            <p className="mb-4 text-sm" style={{ color: '#7a8ba3' }}>
              Type <span className="font-mono font-bold">DELETE</span> to confirm:
            </p>
            <input
              type="text"
              value={deleteConfirmInput}
              onChange={(e) => setDeleteConfirmInput(e.target.value)}
              placeholder='Type "DELETE"'
              className="input w-full mb-6"
              style={{ borderColor: '#3d4557' }}
            />
            <div className="flex gap-4">
              <button
                onClick={() => {
                  setShowDeleteConfirm(false)
                  setDeleteConfirmInput('')
                }}
                className="flex-1 px-4 py-2 rounded-lg transition-all duration-200"
                style={{
                  backgroundColor: darkMode ? '#2d3748' : '#e5e7eb',
                  color: darkMode ? '#e8eef5' : '#1f2937',
                  border: `1px solid ${darkMode ? '#3d4557' : '#d1d5db'}`,
                }}
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteIncidents}
                disabled={deleteConfirmInput !== 'DELETE' || isDeleting}
                className="flex-1 px-4 py-2 rounded-lg bg-critical-500 text-white hover:bg-critical-600 transition-colors disabled:opacity-50"
              >
                {isDeleting ? 'Resetting...' : 'Confirm Reset'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

interface StatisticCardProps {
  title: string
  value: number
  icon: React.ReactNode
  color?: 'info' | 'success' | 'warning' | 'critical'
  darkMode?: boolean
}

function StatisticCard({ title, value, icon, color = 'info', darkMode = true }: StatisticCardProps) {
  const colorClass = {
    info: 'from-info-500 to-info-400',
    success: 'from-success-500 to-success-400',
    warning: 'from-warning-500 to-warning-400',
    critical: 'from-critical-500 to-critical-400',
  }[color]

  return (
    <div
      className="metric-card"
      style={{
        backgroundColor: darkMode ? '#2d3748' : '#f3f4f6',
      }}
    >
      <div className="flex items-start justify-between mb-4">
        <span className="text-4xl">{icon}</span>
      </div>
      <h4 className="text-sm font-medium mb-2" style={{ color: '#a0aec0' }}>
        {title}
      </h4>
      <p className={`text-3xl font-bold bg-gradient-to-r ${colorClass} bg-clip-text text-transparent`}>
        {value}
      </p>
    </div>
  )
}

interface StatusCardProps {
  title: string
  status: string
  darkMode?: boolean
}

function StatusCard({ title, status, darkMode = true }: StatusCardProps) {
  const isHealthy = status === 'healthy'

  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: darkMode ? '#2d3748' : '#f3f4f6',
        borderColor: darkMode ? '#3d4557' : '#d1d5db',
      }}
    >
      <div className="flex items-center justify-between">
        <div>
          <h4 className="font-semibold" style={{ color: '#e8eef5' }}>
            {title}
          </h4>
          <p className="text-sm mt-1 capitalize" style={{ color: '#a0aec0' }}>
            {status}
          </p>
        </div>
        <div className="text-2xl">
          {isHealthy ? (
            <IconCheck size={28} className="text-green-400" />
          ) : (
            <IconAlertTriangle size={28} className="text-yellow-400" />
          )}
        </div>
      </div>
    </div>
  )
}

interface HealthCheckItemProps {
  name: string
  check: any
  detail?: string
  darkMode?: boolean
}

function HealthCheckItem({ name, check, detail, darkMode = true }: HealthCheckItemProps) {
  const isHealthy = check.status && check.error === null
  const statusText = check.status || 'unknown'

  return (
    <div
      className="rounded-lg border p-4"
      style={{
        backgroundColor: darkMode ? '#2d3748' : '#f3f4f6',
        borderColor: isHealthy ? (darkMode ? '#10b98144' : '#d1d5db') : (darkMode ? '#f8717144' : '#d1d5db'),
        borderWidth: '1px',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          width: '4px',
          height: '100%',
          backgroundColor: isHealthy ? '#10b981' : '#f87171',
        }}
      />
      <div className="flex items-start justify-between pl-2">
        <div className="flex-1">
          <h4 className="font-semibold" style={{ color: '#e8eef5' }}>
            {name}
          </h4>
          <p className="text-sm mt-1 capitalize" style={{ color: '#a0aec0' }}>
            {statusText}
          </p>
          {detail && (
            <p className="text-xs mt-2" style={{ color: '#7a8ba3' }}>
              {detail}
            </p>
          )}
          {check.error && (
            <p className="text-xs mt-2 text-red-400">
              {check.error}
            </p>
          )}
        </div>
        <div className="text-2xl flex-shrink-0">
          {isHealthy ? (
            <IconCheck size={24} className="text-green-400" />
          ) : (
            <IconAlertTriangle size={24} className="text-red-400" />
          )}
        </div>
      </div>
    </div>
  )
}
