import axios from 'axios'
import { transformWorkflow, transformWorkflowList } from '../utils/workflowTransformer'
import { Workflow } from '../types'
import { getToken, clearToken } from '../hooks/useCurrentUser'

const API_BASE_URL = '/api'

// Add auth header to every request
axios.interceptors.request.use(config => {
  const token = getToken()
  if (token) config.headers.set('Authorization', `Bearer ${token}`)
  return config
})

// On 401, clear token (redirect handled by App.tsx via useCurrentUser)
axios.interceptors.response.use(
  res => res,
  err => {
    if (err.response?.status === 401) clearToken()
    return Promise.reject(err)
  }
)

export interface IncidentSubmit {
  severity: string
  type: string
  resource_name: string
  description?: string
  service_url?: string    // Full URL of the affected service (e.g. http://api:8080)
  service_port?: number   // Port, if URL not provided
}

export interface ChangeSubmit {
  change_type: string
  description: string
  affected_services: string[]
  rollback_plan: string
}

export interface WorkflowResponse {
  workflow_id: string
  workflow_type: string
  lifecycle_state: string
  incident_number?: number | null       // Raw integer (1, 2, 3 …)
  incident_number_str?: string | null   // Formatted: INC000001, INC000002 …
  severity: string | null
  risk_score: number | null
  context: Record<string, any>
  reasoning_trace: string[]
  created_at: string
  updated_at: string
  title?: string | null
  summary?: string | null
  technical_summary?: string | null
  context_schema?: Record<string, any>
  // Remediation & resolution tracking
  remediation_outcome?: string | null
  resolution_source?: string | null
  all_clear_received_at?: string | null
}

export interface WorkflowListResponse {
  workflows: WorkflowResponse[]
  total_count: number
  limit: number
  offset: number
  has_more: boolean
}

export interface RunbookStep {
  order: number
  type: string
  name: string
  description: string
  tool: string
  args_json?: Record<string, unknown>
}

export interface ProposedAction {
  runbook: string
  runbook_id?: string
  action?: string
  target?: string
  blast_radius?: number
  confidence?: number    // 0.0–1.0; undefined = pre-fix legacy record
  source?: 'runbook_library' | 'cmdb_playbook' | 'fallback_escalation' | 'llm_generated'
  requires_post_monitoring?: boolean
  decision_notes?: string
  remediation_steps?: RunbookStep[]
  diagnostics_steps?: RunbookStep[]
}

export interface IncidentSummary {
  anomaly_type?: string
  severity?: string
  risk_score?: number
  resource?: string
  environment?: string
}

export interface ApprovalResponse {
  approval_id: string
  workflow_id: string
  approval_type: string
  status: string
  requested_at: string
  decided_at?: string
  decided_by?: string
  decision_notes?: string
  proposed_action?: ProposedAction
  incident_summary?: IncidentSummary
  governance_policy_id?: string
}

export interface HealthResponse {
  status: string
  timestamp: string
  service: string
  version: string
}

export interface HealthCheck {
  status: string
  error: string | null
}

export interface ComprehensiveHealthResponse {
  status: string
  timestamp: string
  service: string
  version: string
  checks: {
    database: HealthCheck
    database_tables: HealthCheck
    redis: HealthCheck
    neo4j: HealthCheck
    workflow_engine: HealthCheck
    agents_registered: HealthCheck
    api_routes: HealthCheck & { endpoints?: string[] }
    repositories: HealthCheck & { count?: number }
  }
  summary: {
    total_checks: number
    passed: number
    failed: number
  }
}

// Draft/publish workflow — shared by RunbookResponse and PolicyResponse.
// `enabled` (on each response type) stays an instant kill-switch outside
// this workflow; everything else sits in draft_snapshot until published.
export interface DraftPublishFields {
  status: 'draft' | 'published'
  published_at: string | null
  has_unpublished_changes: boolean
  draft_snapshot: Record<string, any> | null
}

export interface VersionEntry {
  version: number
  created_at: string
  created_by: string | null
  change_note: string | null
  snapshot: Record<string, any>
}

export interface PolicyResponse extends DraftPublishFields {
  policy_id: string
  name: string
  rules: Record<string, any>
  requires_manual_approval: boolean
  approved_actions: string[]
  constraints: Record<string, any>
  approval_priority: number
}

export interface RunbookResponse extends DraftPublishFields {
  runbook_id: string
  name: string
  description?: string
  event_type: string
  service?: string
  environment?: string
  diagnostics: any[]
  actions: any[]
  verification_steps: any[]
  confidence: number
  blast_radius: number
  enabled?: boolean
  source?: string  // operator_authored | ai_generated
  // Execution feedback stats
  total_executions?: number
  successful_executions?: number
  failed_executions?: number
  success_rate?: number | null
  confidence_trend?: 'up' | 'down' | 'stable' | 'new' | null
  last_executed_at?: string | null
  // Non-blocking lint from POST .../publish — e.g. a path with no validation step.
  // Only ever present on the publish response, not on GET/PUT.
  warnings?: string[]
  created_at: string
  updated_at: string
}

export interface IncidentMetricsResponse {
  total_incidents: number
  active_incidents: number
  resolved_today: number
  avg_resolution_time: number
  approval_rate: number
  remediation_success_rate: number
  severity_breakdown?: Record<string, number>  // active incidents per severity
}

// Workflow endpoints
export const submitIncident = (data: IncidentSubmit) =>
  axios.post<WorkflowResponse>(`${API_BASE_URL}/workflows/incident`, data)

export const submitChange = (data: ChangeSubmit) =>
  axios.post<WorkflowResponse>(`${API_BASE_URL}/workflows/change`, data)

export const getWorkflow = (workflowId: string) =>
  axios.get<WorkflowResponse>(`${API_BASE_URL}/workflows/${workflowId}`)

// Phase 6.5: Enhanced list with sorting, filtering, pagination
export const listWorkflows = (params?: {
  workflow_type?: string
  lifecycle_state?: string
  severity?: string
  service?: string
  business_criticality?: string
  q?: string
  limit?: number
  offset?: number
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}) => axios.get<WorkflowListResponse>(`${API_BASE_URL}/workflows`, { params })

// Approval endpoints
export const getPendingApprovals = (params?: {
  approval_type?: string
  limit?: number
}) => axios.get<ApprovalResponse[]>(`${API_BASE_URL}/approvals/pending`, { params })

export const getApproval = (approvalId: string) =>
  axios.get<ApprovalResponse>(`${API_BASE_URL}/approvals/${approvalId}`)

export const getApprovalHistory = (params?: {
  status?: string
  limit?: number
  offset?: number
}) => axios.get<ApprovalResponse[]>(`${API_BASE_URL}/approvals/history`, { params })

export const submitApprovalDecision = (
  approvalId: string,
  decision: string,
  notes: string,
  decidedBy: string
) =>
  axios.post<ApprovalResponse>(
    `${API_BASE_URL}/approvals/${approvalId}/approve`,
    {
      decision,
      notes,
      decided_by: decidedBy,
    }
  )

// Event type taxonomy — used to populate the Event Type matching rule in PolicyEditor
export interface EventTypeTaxonomyEntry {
  code: string
  label: string
  description: string | null
  category: string
  aliases: string[]
  is_system: boolean
  enabled: boolean
  default_severity: string | null   // info | warning | critical | null — watcher-native types only
}

export const listEventTypeTaxonomy = (params?: { category?: string; enabled_only?: boolean }) =>
  axios.get<EventTypeTaxonomyEntry[]>(`${API_BASE_URL}/event-types`, { params })

// Policy endpoints (Phase 6)
export const getPolicies = (params?: { enabled?: boolean; limit?: number }) =>
  axios.get<PolicyResponse[]>(`${API_BASE_URL}/policies`, { params })

export const getPolicy = (policyId: string) =>
  axios.get<PolicyResponse>(`${API_BASE_URL}/policies/${policyId}`)

export const createPolicy = (data: Partial<PolicyResponse>) =>
  axios.post<PolicyResponse>(`${API_BASE_URL}/policies`, data)

export const updatePolicy = (policyId: string, data: Partial<PolicyResponse>) =>
  axios.put<PolicyResponse>(`${API_BASE_URL}/policies/${policyId}`, data)

export const deletePolicy = (policyId: string) =>
  axios.delete(`${API_BASE_URL}/policies/${policyId}`)

export const publishPolicy = (policyId: string, changeNote?: string) =>
  axios.post<PolicyResponse>(`${API_BASE_URL}/policies/${policyId}/publish`, { change_note: changeNote })

export const discardPolicyDraft = (policyId: string) =>
  axios.post<PolicyResponse>(`${API_BASE_URL}/policies/${policyId}/discard-draft`)

export const setPolicyEnabled = (policyId: string, enabled: boolean) =>
  axios.patch<PolicyResponse>(`${API_BASE_URL}/policies/${policyId}/enabled`, { enabled })

export const getPolicyVersions = (policyId: string) =>
  axios.get<VersionEntry[]>(`${API_BASE_URL}/policies/${policyId}/versions`)

export const restorePolicyVersion = (policyId: string, version: number) =>
  axios.post<PolicyResponse>(`${API_BASE_URL}/policies/${policyId}/versions/${version}/restore`)

// Runbook endpoints (Phase 6)
export const getRunbooks = (params?: { event_type?: string; limit?: number }) =>
  axios.get<RunbookResponse[]>(`${API_BASE_URL}/runbooks`, { params })

export const getRunbook = (runbookId: string) =>
  axios.get<RunbookResponse>(`${API_BASE_URL}/runbooks/${runbookId}`)

export const createRunbook = (data: Partial<RunbookResponse>) =>
  axios.post<RunbookResponse>(`${API_BASE_URL}/runbooks`, data)

export const updateRunbook = (runbookId: string, data: Partial<RunbookResponse>) =>
  axios.put<RunbookResponse>(`${API_BASE_URL}/runbooks/${runbookId}`, data)

export const deleteRunbook = (runbookId: string) =>
  axios.delete(`${API_BASE_URL}/runbooks/${runbookId}`)

export const publishRunbook = (runbookId: string, changeNote?: string) =>
  axios.post<RunbookResponse>(`${API_BASE_URL}/runbooks/${runbookId}/publish`, { change_note: changeNote })

export const discardRunbookDraft = (runbookId: string) =>
  axios.post<RunbookResponse>(`${API_BASE_URL}/runbooks/${runbookId}/discard-draft`)

export const setRunbookEnabled = (runbookId: string, enabled: boolean) =>
  axios.patch<RunbookResponse>(`${API_BASE_URL}/runbooks/${runbookId}/enabled`, { enabled })

export const getRunbookVersions = (runbookId: string) =>
  axios.get<VersionEntry[]>(`${API_BASE_URL}/runbooks/${runbookId}/versions`)

export const restoreRunbookVersion = (runbookId: string, version: number) =>
  axios.post<RunbookResponse>(`${API_BASE_URL}/runbooks/${runbookId}/versions/${version}/restore`)

// Metrics endpoints (Phase 6)
export const getIncidentMetrics = () =>
  axios.get<IncidentMetricsResponse>(`${API_BASE_URL}/metrics/incidents`)

export interface NavBadgeCountsResponse {
  active_incidents: number
  pending_approvals: number
  new_events: number
  active_storms: number
}

export const getNavBadgeCounts = () =>
  axios.get<NavBadgeCountsResponse>(`${API_BASE_URL}/metrics/nav-counts`)

// ─────────────────────────────────────────────────────────────────
// Wrapper functions with data transformation (Phase 7: UI Redesign)
// ─────────────────────────────────────────────────────────────────

/**
 * Get single workflow with transformed data (convenience fields extracted)
 */
export const getWorkflowTransformed = async (workflowId: string): Promise<Workflow> => {
  const response = await getWorkflow(workflowId)
  return transformWorkflow(response.data)
}

/**
 * List workflows with transformed data
 */
export const listWorkflowsTransformed = async (params?: {
  workflow_type?: string
  lifecycle_state?: string
  severity?: string
  service?: string
  business_criticality?: string
  q?: string
  limit?: number
  offset?: number
  sort_by?: string
  sort_order?: 'asc' | 'desc'
}): Promise<{ workflows: Workflow[]; total_count: number; has_more: boolean }> => {
  const response = await listWorkflows(params)
  return transformWorkflowList(response.data)
}

export const getPolicyMetrics = () =>
  axios.get<Record<string, number>>(`${API_BASE_URL}/metrics/policies`)

export interface TrendPoint {
  date: string
  label: string
  created: number
  resolved: number
  avg_mttr_seconds?: number | null  // average resolution time for incidents closed on this day
}

export const getIncidentTrend = (days = 7) =>
  axios.get<TrendPoint[]>(`${API_BASE_URL}/metrics/trend`, { params: { days } })

export const getRemediationMetrics = () =>
  axios.get<Record<string, number>>(`${API_BASE_URL}/metrics/remediation`)

// ── MTTR breakdown (Phase 1) ──────────────────────────────────────────────────
export interface MttrSeverityBucket {
  active:                number
  resolved:              number
  no_approval_count:     number          // auto-remediated, no governance gate
  no_approval_avg_s:     number | null
  with_approval_count:   number          // auto-remediated, went through a governance approval
  with_approval_avg_s:   number | null
  manual_count:          number          // operator resolved/closed it directly
  manual_avg_s:          number | null
  oldest_open_age_s:     number | null
}

export interface MttrPathBucket {
  count:      number
  avg_mttr_s: number | null
}

export interface MttrStuckItem {
  workflow_id:     string
  incident_number: string
  title:           string
  severity:        string
  age_s:           number
  waiting_s:       number
}

export interface MttrBreakdownResponse {
  period_days:       number
  by_severity:       Record<string, MttrSeverityBucket>
  by_path:           Record<string, MttrPathBucket>
  stuck_in_approval: MttrStuckItem[]
}

export const getMttrBreakdown = (days = 30) =>
  axios.get<MttrBreakdownResponse>(`${API_BASE_URL}/metrics/mttr-breakdown`, { params: { days } })

// ── Operator Chat ────────────────────────────────────────────────────────────

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ChatResponse {
  reply: string
}

export const postChat = (message: string, history: ChatMessage[] = []) =>
  axios.post<ChatResponse>(`${API_BASE_URL}/chat`, { message, history })

// ── Streaming chat (Phase 2 + 3) ─────────────────────────────────────────────

export interface PendingAction {
  type:             'approve' | 'reject'
  incident_number:  string
  workflow_id:      string
  notes?:           string
}

export interface StreamChatOptions {
  contextWorkflowId?: string | null   // Phase 3A: incident open in UI
  onAction?: (action: PendingAction) => void  // Phase 3B: called when action detected
}

/**
 * Streaming chat — yields text chunks as they arrive from the LLM.
 * When the backend detects an action intent it emits an action metadata event
 * after the text stream; this is passed to onAction() rather than yielded as text.
 */
export async function* streamChat(
  message: string,
  history: ChatMessage[] = [],
  options: StreamChatOptions = {},
): AsyncGenerator<string> {
  const token = getToken()
  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      message,
      history,
      context_workflow_id: options.contextWorkflowId ?? null,
    }),
  })

  if (!response.ok || !response.body) {
    throw new Error(`Chat stream failed: ${response.status}`)
  }

  const reader  = response.body.getReader()
  const decoder = new TextDecoder()
  let   buffer  = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const data = line.slice(6).trim()
      if (data === '[DONE]') return
      try {
        const parsed = JSON.parse(data) as { chunk?: string; action?: PendingAction }
        if (parsed.chunk) {
          yield parsed.chunk
        } else if (parsed.action && options.onAction) {
          options.onAction(parsed.action)
        }
      } catch { /* skip malformed SSE lines */ }
    }
  }
}

// ── Approval actions (Phase 3B) ───────────────────────────────────────────────

export const decideApprovalByWorkflow = (
  workflowId: string,
  decision: 'approved' | 'rejected',
  notes  = '',
  decidedBy = 'operator_chat',
) =>
  axios.post(`${API_BASE_URL}/approvals/by-workflow/${workflowId}/decide`, {
    decision,
    notes,
    decided_by: decidedBy,
  })

// Enhanced workflow endpoint for Phase 6
export const getEnhancedWorkflow = (workflowId: string) =>
  axios.get<WorkflowResponse>(`${API_BASE_URL}/workflows/${workflowId}?include=all`)

// Health endpoints
export const checkHealth = () =>
  axios.get<HealthResponse>(`${API_BASE_URL}/health`)

export const checkReadiness = () =>
  axios.get<HealthResponse>(`${API_BASE_URL}/ready`)

export const getComprehensiveHealthCheck = () =>
  axios.get<ComprehensiveHealthResponse[]>(`${API_BASE_URL}/ready`)

// Admin endpoints (Phase 8)
export const getAdminStatistics = () =>
  axios.get(`${API_BASE_URL}/admin/statistics`)

export const getSystemStatus = () =>
  axios.get(`${API_BASE_URL}/admin/system-status`)

/** @deprecated Use platformReset instead */
export const deleteAllIncidents = () =>
  axios.post(`${API_BASE_URL}/admin/incidents/delete-all`)

export const platformReset = () =>
  axios.post(`${API_BASE_URL}/admin/platform/reset`)

export interface WorkerInfo {
  name: string
  short_name: string
  status: 'online' | 'offline'
  active_tasks: number
  active_task_names: string[]
  queues: string[]
  processed: number
}

export interface WorkerHealthResponse {
  workers: WorkerInfo[]
  queue_depths: { workflows: number; default: number; approvals: number }
  stuck_incidents: Array<{
    workflow_id: string
    incident_number: string
    state: string
    stuck_minutes: number
  }>
  celery_reachable: boolean
  timestamp: string
}

export const getWorkerHealth = () =>
  axios.get<WorkerHealthResponse>(`${API_BASE_URL}/admin/workers`)

export const vacuumDatabase = () =>
  axios.post(`${API_BASE_URL}/admin/database/vacuum`)

export const getHealthDetailed = () =>
  axios.get(`${API_BASE_URL}/admin/health-detailed`)

// ── Backup endpoints ──────────────────────────────────────────────────────────

export interface BackupStatus {
  last_backup_at:      string | null
  last_backup_status:  'never' | 'in_progress' | 'ok' | 'error'
  last_backup_message: string
  retention_days:      number
  timestamp:           string
}

export interface BackupRunResponse {
  task_id:        string
  status:         string
  retention_days: number
  message:        string
  timestamp:      string
}

/** Trigger an immediate full backup (PostgreSQL + Neo4j + Watcher config). */
export const triggerBackup = () =>
  axios.post<BackupRunResponse>(`${API_BASE_URL}/admin/backup/run`)

/** Return the status and timestamp of the most recent backup. */
export const getBackupStatus = () =>
  axios.get<BackupStatus>(`${API_BASE_URL}/admin/backup/status`)

// Risk Configuration endpoints (Phase K)
export interface RiskConfigResponse {
  config_key: string
  weights: Record<string, any>
  created_at?: string
  updated_at?: string
}

export const getRiskConfig = (configKey: string = 'default') =>
  axios.get<RiskConfigResponse>(`${API_BASE_URL}/risk-config?config_key=${encodeURIComponent(configKey)}`)

export const updateRiskConfig = (configKey: string, weights: Record<string, any>) =>
  axios.put<RiskConfigResponse>(`${API_BASE_URL}/risk-config?config_key=${encodeURIComponent(configKey)}`, {
    weights
  })

export const resetRiskConfig = (configKey: string = 'default') =>
  axios.post<RiskConfigResponse>(`${API_BASE_URL}/risk-config/reset?config_key=${encodeURIComponent(configKey)}`)

export const getAllRiskConfigs = () =>
  axios.get<RiskConfigResponse[]>(`${API_BASE_URL}/risk-config/all`)

// ─────────────────────────────────────────────────────────────────
// Platform Settings — watcher thresholds
// ─────────────────────────────────────────────────────────────────

export interface WatcherSetting {
  key: string
  value: number | boolean | string
  value_type: 'int' | 'float' | 'bool' | 'str'
  label: string
  description: string | null
  updated_at: string | null
}

export interface WatcherSettingsResponse {
  category: string
  settings: WatcherSetting[]
}

export interface WatcherSettingsUpdate {
  poll_interval?: number
  cpu_threshold?: number
  memory_threshold?: number
  disk_threshold?: number
  syscall_threshold?: number
  connection_threshold?: number
  cooldown_seconds?: number
  min_consecutive_polls?: number
  discovery_enabled?: boolean
  discovery_interval_polls?: number
}

export const getWatcherSettings = () =>
  axios.get<WatcherSettingsResponse>(`${API_BASE_URL}/settings/watcher`)

export const updateWatcherSettings = (update: WatcherSettingsUpdate) =>
  axios.put<{ saved: WatcherSettingsUpdate; watcher_live_applied: boolean; message: string }>(
    `${API_BASE_URL}/settings/watcher`,
    update,
  )

export const resetWatcherSettings = () =>
  axios.post<{ message: string; defaults: WatcherSettingsUpdate }>(
    `${API_BASE_URL}/settings/watcher/reset`,
  )

// ─────────────────────────────────────────────────────────────────
// Platform Settings — Storm Agent
// ─────────────────────────────────────────────────────────────────

export interface StormSetting {
  key: string
  value: number | boolean | string
  value_type: 'int' | 'float' | 'bool' | 'str'
  label: string
  description: string | null
  updated_at: string | null
}

export interface StormSettingsResponse {
  category: string
  settings: StormSetting[]
}

export interface StormSettingsUpdate {
  // Detection parameters
  enabled?: boolean
  window_seconds?: number
  min_incidents?: number
  min_resources?: number
  merge_window_minutes?: number
  pipeline_hold_seconds?: number
  // Behaviour parameters
  require_cab_approval?: boolean
  auto_hold_children?: boolean
  exclude_external_events?: boolean
  llm_hypothesis_enabled?: boolean
  neo4j_topology_enabled?: boolean
}

export const getStormSettings = () =>
  axios.get<StormSettingsResponse>(`${API_BASE_URL}/settings/storm`)

export const updateStormSettings = (update: StormSettingsUpdate) =>
  axios.put<{ saved: StormSettingsUpdate; message: string }>(
    `${API_BASE_URL}/settings/storm`,
    update,
  )

export const resetStormSettings = () =>
  axios.post<{ message: string; defaults: StormSettingsUpdate }>(
    `${API_BASE_URL}/settings/storm/reset`,
  )

// ─────────────────────────────────────────────────────────────────
// Platform Settings — Platform Intelligence
// ─────────────────────────────────────────────────────────────────

export interface PlatformIntelligenceSetting {
  key: string
  value: number | boolean | string
  value_type: 'int' | 'float' | 'bool' | 'str'
  label: string
  description: string | null
  updated_at: string | null
}

export interface PlatformIntelligenceSettingsResponse {
  category: string
  settings: PlatformIntelligenceSetting[]
}

export interface PlatformIntelligenceSettingsUpdate {
  auto_apply_enabled?: boolean
  auto_apply_min_cycles?: number
  verification_delay_days?: number
  analysis_schedule_enabled?: boolean
  analysis_schedule?: string
}

export const getPlatformIntelligenceSettings = () =>
  axios.get<PlatformIntelligenceSettingsResponse>(`${API_BASE_URL}/settings/platform-intelligence`)

export const updatePlatformIntelligenceSettings = (update: PlatformIntelligenceSettingsUpdate) =>
  axios.put<{ saved: PlatformIntelligenceSettingsUpdate; message: string }>(
    `${API_BASE_URL}/settings/platform-intelligence`,
    update,
  )

export const resetPlatformIntelligenceSettings = () =>
  axios.post<{ message: string; defaults: PlatformIntelligenceSettingsUpdate }>(
    `${API_BASE_URL}/settings/platform-intelligence/reset`,
  )

// ─────────────────────────────────────────────────────────────────
// Platform Settings — General
// ─────────────────────────────────────────────────────────────────

export interface GeneralSetting {
  key: string
  value: number | boolean | string
  value_type: 'int' | 'float' | 'bool' | 'str'
  label: string
  description: string | null
  updated_at: string | null
}

export interface GeneralSettingsResponse {
  category: string
  settings: GeneralSetting[]
}

export const getGeneralSettings = () =>
  axios.get<GeneralSettingsResponse>(`${API_BASE_URL}/settings/general`)

export const updateGeneralSettings = (update: Record<string, any>) =>
  axios.put<{ saved: Record<string, any>; message: string }>(
    `${API_BASE_URL}/settings/general`,
    update,
  )

// ─────────────────────────────────────────────────────────────────
// Platform Settings — SMTP
// ─────────────────────────────────────────────────────────────────

export interface SmtpSetting {
  key: string
  value: number | boolean | string
  value_type: 'int' | 'float' | 'bool' | 'str'
  label: string
  description: string | null
  updated_at: string | null
  is_set?: boolean
}

export interface SmtpSettingsResponse {
  category: string
  settings: SmtpSetting[]
}

export const getSmtpSettings = () =>
  axios.get<SmtpSettingsResponse>(`${API_BASE_URL}/settings/smtp`)

export const updateSmtpSettings = (update: Record<string, any>) =>
  axios.put<{ saved: Record<string, any>; message: string }>(
    `${API_BASE_URL}/settings/smtp`,
    update,
  )

// ─────────────────────────────────────────────────────────────────
// Platform Settings — Slack ChatOps
// ─────────────────────────────────────────────────────────────────

export interface SlackSetting {
  key: string
  value: number | boolean | string
  value_type: 'int' | 'float' | 'bool' | 'str'
  label: string
  description: string | null
  updated_at: string | null
  is_set?: boolean
}

export interface SlackSettingsResponse {
  category: string
  settings: SlackSetting[]
}

export const getSlackSettings = () =>
  axios.get<SlackSettingsResponse>(`${API_BASE_URL}/settings/slack`)

export const updateSlackSettings = (update: Record<string, any>) =>
  axios.put<{ saved: Record<string, any>; message: string }>(
    `${API_BASE_URL}/settings/slack`,
    update,
  )

export const testSlackConnection = () =>
  axios.post<{ ok: boolean; bot_user?: string; workspace?: string; message: string }>(
    `${API_BASE_URL}/settings/slack/test`,
  )

export const testSlackCredentials = (creds: { bot_token: string; channel?: string }) =>
  axios.post<{ status: string; message: string; team?: string; bot?: string }>(
    `${API_BASE_URL}/webhooks/slack/test-credentials`,
    creds,
  )

// ─────────────────────────────────────────────────────────────────
// Storms
// ─────────────────────────────────────────────────────────────────

export interface StormChild {
  workflow_id: string
  title: string | null
  lifecycle_state: string
  severity: string | null
  resource_name: string | null
  event_type: string | null
  created_at: string
  // Enrichment (v1.0.1)
  incident_number_str?: string | null
  source_connector?: string | null
  signal_value?: number | null
  signal_threshold?: number | null
}

export interface StormSummary {
  storm_id: string
  incident_number: string | null
  title: string
  lifecycle_state: string
  severity: string | null
  pattern: string | null
  confidence: number | null
  hypothesis: string | null
  affected_count: number
  child_count: number
  detected_at: string | null
  created_at: string
}

export interface StormDetail extends StormSummary {
  children: StormChild[]
  root_cause_candidates: Array<{
    name?: string
    ci_name?: string
    type?: string
    criticality?: string
    status?: string
    environment?: string
    confidence?: number
    shared_count?: number
    affected_count?: number
    affected_resources?: string[]
  }>
  topology_evidence: Record<string, Array<string | { name: string; type?: string; criticality?: string; status?: string; environment?: string }>>
  affected_resources: string[]
  event_types: string[]
  llm_used: boolean
  neo4j_available: boolean
}

export interface StormActionResponse {
  status: string
  storm_id: string
  children_released?: number
  children_resolved?: number
  message: string
}

export const listStorms = (activeOnly = true) =>
  axios.get<StormSummary[]>(`${API_BASE_URL}/storms?active_only=${activeOnly}`)

export const getStorm = (stormId: string) =>
  axios.get<StormDetail>(`${API_BASE_URL}/storms/${stormId}`)

export const releaseStorm = (stormId: string, notes?: string) =>
  axios.post<StormActionResponse>(`${API_BASE_URL}/storms/${stormId}/release`, { notes })

export const resolveStorm = (stormId: string, resolution_note?: string) =>
  axios.post<StormActionResponse>(`${API_BASE_URL}/storms/${stormId}/resolve`, { resolution_note })

// ── Monitoring Checks ─────────────────────────────────────────────────────────

export interface WatcherInfo {
  /** Immutable platform-assigned UUID — stable across renames */
  watcher_id: string | null
  watcher_name: string
  display_name: string
  host: string
  poll_interval: number
  sentinel_container: string | null
  /** Registration approval state */
  registration_status: 'pending' | 'approved' | 'rejected' | 'disabled'
  nginx_url: string
  kill_api_url: string
  approved_at: string | null
  approved_by: string
  /** Detected runtime environment */
  environment: string
  /** Execution adapter in use */
  adapter_mode: string
  /** Watcher agent version string */
  watcher_version: string | null
  /** Rolling metrics history [{ts,cpu,mem,disk,alerts}] — last 20 poll points */
  metrics_history: Array<{ ts: string; cpu: number; mem: number; disk: number; alerts: number }>
  /** Heartbeat liveness: active if last_seen < 150 s ago */
  status: 'active' | 'inactive'
  last_seen: string | null
  last_seen_seconds_ago: number | null
  registered_at: string | null
}

export interface ExternalCheck {
  id: string
  watcher_name: string
  check_type: 'ping' | 'http' | 'https' | 'tcp' | 'dns' | 'tls'
  target: string
  name: string
  port: number | null
  expected_status: number
  timeout_ms: number
  latency_threshold_ms: number
  tls_expiry_warning_days: number
  enabled: boolean
  created_at: string | null
  updated_at: string | null
}

export interface ExternalCheckPayload {
  check_type: 'ping' | 'http' | 'https' | 'tcp' | 'dns' | 'tls'
  target: string
  name?: string
  port?: number | null
  expected_status?: number
  timeout_ms?: number
  latency_threshold_ms?: number
  tls_expiry_warning_days?: number
  enabled?: boolean
}

export interface ExternalCheckTestResult {
  success: boolean
  status?: 'healthy' | 'unhealthy' | 'degraded' | 'unknown'
  status_code?: number | null
  response_time_ms?: number
  response_body?: string | null
  tls_days_remaining?: number | null
  error?: string | null
}

export const listWatchers = () =>
  axios.get<WatcherInfo[]>(`${API_BASE_URL}/monitoring/watchers`)

export const listWatcherChecks = (watcherName: string) =>
  axios.get<ExternalCheck[]>(`${API_BASE_URL}/monitoring/watchers/${watcherName}/checks`)

export const createWatcherCheck = (watcherName: string, payload: ExternalCheckPayload) =>
  axios.post<ExternalCheck>(`${API_BASE_URL}/monitoring/watchers/${watcherName}/checks`, payload)

export const updateWatcherCheck = (watcherName: string, checkId: string, payload: Partial<ExternalCheckPayload>) =>
  axios.put<ExternalCheck>(`${API_BASE_URL}/monitoring/watchers/${watcherName}/checks/${checkId}`, payload)

export const deleteWatcherCheck = (watcherName: string, checkId: string) =>
  axios.delete(`${API_BASE_URL}/monitoring/watchers/${watcherName}/checks/${checkId}`)

export const seedWatcherChecks = (watcherName: string) =>
  axios.post<{ seeded: number; watcher_name: string }>(
    `${API_BASE_URL}/monitoring/watchers/${watcherName}/checks/seed`,
  )

export const testWatcherCheck = (watcherName: string, payload: ExternalCheckPayload) =>
  axios.post<ExternalCheckTestResult>(
    `${API_BASE_URL}/monitoring/watchers/${watcherName}/checks/test`,
    payload,
  )

export const approveWatcher = (watcherName: string) =>
  axios.post<{ ok: boolean; registration_status: string }>(
    `${API_BASE_URL}/monitoring/watchers/${watcherName}/approve`,
  )

export const rejectWatcher = (watcherName: string) =>
  axios.post<{ ok: boolean; registration_status: string }>(
    `${API_BASE_URL}/monitoring/watchers/${watcherName}/reject`,
  )

export const deleteWatcher = (watcherName: string) =>
  axios.delete(`${API_BASE_URL}/monitoring/watchers/${watcherName}`)

export const disableWatcher = (watcherName: string) =>
  axios.post<{ ok: boolean; registration_status: string }>(
    `${API_BASE_URL}/monitoring/watchers/${watcherName}/disable`,
  )

export const enableWatcher = (watcherName: string) =>
  axios.post<{ ok: boolean; registration_status: string }>(
    `${API_BASE_URL}/monitoring/watchers/${watcherName}/enable`,
  )

export const resetWatcher = (watcherName: string) =>
  axios.post<{ ok: boolean; kill_api_reached: boolean; message: string }>(
    `${API_BASE_URL}/monitoring/watchers/${watcherName}/reset`,
  )

export const invalidateWatcher = (watcherName: string) =>
  axios.post<{ ok: boolean; message: string }>(
    `${API_BASE_URL}/monitoring/watchers/${watcherName}/invalidate`,
  )

// ── Connector Hub ─────────────────────────────────────────────────────────

import type {
  ConnectorDef, ConnectorConfigPayload, SplunkConfigPayload,
  SNowCMDBSummary, SNowCI, SNowIncidentMap, SyncLog,
} from '../types'

export const listConnectors = () =>
  axios.get<ConnectorDef[]>(`${API_BASE_URL}/connectors`)

export const getConnector = (id: string) =>
  axios.get<ConnectorDef>(`${API_BASE_URL}/connectors/${id}`)

export const saveConnectorConfig = (id: string, payload: ConnectorConfigPayload) =>
  axios.post<{ status: string }>(`${API_BASE_URL}/connectors/${id}/config`, payload)

export const saveSplunkConfig = (payload: SplunkConfigPayload) =>
  axios.post<{ status: string }>(`${API_BASE_URL}/connectors/splunk/config`, payload)

export const saveAlertConnectorConfig = (
  id: string,
  payload: {
    webhook_secret?: string
    default_criticality: string
    default_event_type: string
    enabled: boolean
    allow_auto_remediation: boolean
    event_type_mappings?: Record<string, string>   // externalName → canonicalType
    routing_key?: string   // PagerDuty Events API v2 integration key (outbound escalation)
  },
) => axios.post<{ status: string }>(`${API_BASE_URL}/connectors/${id}/alert-config`, payload)

export const testConnector = (id: string) =>
  axios.post<{ ok: boolean; latency_ms: number; message: string }>(
    `${API_BASE_URL}/connectors/${id}/test`,
  )

export const triggerConnectorSync = (id: string) =>
  axios.post<{ status: string; total_records: number; duration_seconds: number }>(
    `${API_BASE_URL}/connectors/${id}/sync`,
  )

export const getConnectorSyncLogs = (id: string, limit = 20, offset = 0) =>
  axios.get<{ total: number; items: SyncLog[] }>(
    `${API_BASE_URL}/connectors/${id}/sync-logs`,
    { params: { limit, offset } },
  )

// ServiceNow CMDB
export const getSnowCMDBSummary = () =>
  axios.get<SNowCMDBSummary>(`${API_BASE_URL}/connectors/servicenow/cmdb`)

export const getSnowCMDBList = (ciClass: string, limit = 100, offset = 0) =>
  axios.get<{ ci_class: string; total: number; items: SNowCI[] }>(
    `${API_BASE_URL}/connectors/servicenow/cmdb/${ciClass}`,
    { params: { limit, offset } },
  )

export const searchSnowCMDB = (q: string, ciClass?: string, limit = 50) =>
  axios.get<SNowCI[]>(`${API_BASE_URL}/connectors/servicenow/cmdb/search`, {
    params: { q, ci_class: ciClass, limit },
  })

export const getSnowCIRecord = (sysId: string) =>
  axios.get<SNowCI>(`${API_BASE_URL}/connectors/servicenow/cmdb/record/${sysId}`)

// ServiceNow Incident push
export const pushSnowIncident = (workflowId: string, payload: {
  title: string; summary?: string; severity?: string;
  lifecycle_state?: string; service_name?: string; work_notes?: string;
}) =>
  axios.post<{ snow_sys_id: string; snow_number: string; status: string }>(
    `${API_BASE_URL}/connectors/servicenow/push-incident/${workflowId}`, payload,
  )

export const updateSnowIncident = (workflowId: string, payload: {
  severity?: string; lifecycle_state?: string;
  title?: string; summary?: string; work_notes?: string;
}) =>
  axios.put<{ snow_number: string; status: string }>(
    `${API_BASE_URL}/connectors/servicenow/push-incident/${workflowId}`, payload,
  )

export const getSnowIncidentMap = (workflowId: string) =>
  axios.get<SNowIncidentMap>(
    `${API_BASE_URL}/connectors/servicenow/incident-map/${workflowId}`,
  )

export interface MonitoringEvent {
  event_id: string
  source: string
  event_type: string
  resource_name: string
  raw_criticality: string
  qualification_score: number
  qualified_as_incident: boolean
  incident_workflow_id: string | null
  status: string
  detected_at: string
  created_at: string
  updated_at: string
  qualification_reason: string | null
  confidence: number | null
  signal_value: number | null
  signal_threshold: number | null
  anomaly_process: string | null
  payload_title: string | null
  payload_description: string | null
  raw_payload: Record<string, unknown>
}

export const getMonitoringEventForWorkflow = async (workflowId: string): Promise<MonitoringEvent | null> => {
  const res = await axios.get<MonitoringEvent[]>(
    `${API_BASE_URL}/monitoring-events?workflow_id=${workflowId}&limit=1`,
  )
  return res.data.length > 0 ? res.data[0] : null
}


// ── Platform Intelligence ─────────────────────────────────────────────────

import type {
  OptimizationRecommendation,
  PlatformHealthMetrics,
  ConfigHistoryEntry,
} from '../types'

export const listRecommendations = (status?: string, limit = 50) =>
  axios.get<OptimizationRecommendation[]>(`${API_BASE_URL}/platform-intelligence/recommendations`, {
    params: { status, limit },
  })

export const countPendingRecommendations = () =>
  axios.get<{ pending: number }>(`${API_BASE_URL}/platform-intelligence/recommendations/count`)

export const acceptRecommendation = (id: string, reviewedBy = 'admin') =>
  axios.put<OptimizationRecommendation>(
    `${API_BASE_URL}/platform-intelligence/recommendations/${id}/accept`,
    { reviewed_by: reviewedBy },
  )

export const rejectRecommendation = (id: string, reviewedBy = 'admin', reason = '') =>
  axios.put<OptimizationRecommendation>(
    `${API_BASE_URL}/platform-intelligence/recommendations/${id}/reject`,
    { reviewed_by: reviewedBy, reason },
  )

export const getPlatformHealth = (periodDays = 30) =>
  axios.get<PlatformHealthMetrics>(`${API_BASE_URL}/platform-intelligence/health`, {
    params: { period_days: periodDays },
  })

export const getConfigHistory = (limit = 20) =>
  axios.get<ConfigHistoryEntry[]>(`${API_BASE_URL}/platform-intelligence/config-history`, {
    params: { limit },
  })

// ── Log Monitors ──────────────────────────────────────────────────────────────

export interface LogMonitor {
  id: string
  watcher_name: string
  name: string
  source: string
  file: string
  container: string
  pattern: string
  event_type: string
  interval_sec: number
  enabled: boolean
  created_at: string | null
  updated_at: string | null
}

export interface LogMonitorPayload {
  name: string
  source?: string
  file?: string
  container?: string
  pattern: string
  event_type: string
  interval_sec?: number
  enabled?: boolean
}

export interface PatternValidationRequest {
  pattern: string
}

export interface PatternValidationResponse {
  valid: boolean
  error?: string
}

export const listLogMonitors = (watcherName: string) =>
  axios.get<LogMonitor[]>(`${API_BASE_URL}/monitoring/watchers/${watcherName}/log-monitors`)

export const createLogMonitor = (watcherName: string, payload: LogMonitorPayload) =>
  axios.post<LogMonitor>(`${API_BASE_URL}/monitoring/watchers/${watcherName}/log-monitors`, payload)

export const updateLogMonitor = (watcherName: string, monitorId: string, payload: Partial<LogMonitorPayload>) =>
  axios.put<LogMonitor>(`${API_BASE_URL}/monitoring/watchers/${watcherName}/log-monitors/${monitorId}`, payload)

export const deleteLogMonitor = (watcherName: string, monitorId: string) =>
  axios.delete(`${API_BASE_URL}/monitoring/watchers/${watcherName}/log-monitors/${monitorId}`)

export const validateLogPattern = (pattern: string) =>
  axios.post<PatternValidationResponse>(`${API_BASE_URL}/monitoring/log-monitors/validate-pattern`, { pattern })

// ── Approved Actions ────────────────────────────────────────────────────────

export interface ApprovedAction {
  action_id:         string
  tool_name:         string
  name:              string
  description:       string
  category:          string  // diagnostic | remediation_safe | remediation_intrusive | notify
  blast_radius:      number  // 1 (read-only) -> 3 (disruptive)
  requires_approval: boolean
  enabled:           boolean
  is_builtin:        boolean
}

export const getApprovedActions = (enabledOnly = true, category?: string) =>
  axios.get<ApprovedAction[]>(`${API_BASE_URL}/approved-actions`, { params: { enabled_only: enabledOnly, category } })

// ── Platform Intelligence ────────────────────────────────────────────────────

// Run Analysis / Force Refresh now runs as a background Celery job — this
// kicks it off and returns a job_id; poll getAnalysisStatus() until the
// job's state is SUCCESS or FAILURE (see PlatformIntelligencePage's spinner).
export const triggerAnalysis = (periodDays = 30, ignoreCooldown = false) =>
  axios.post<{ status: string; job_id: string }>(
    `${API_BASE_URL}/platform-intelligence/analyze`,
    { period_days: periodDays, ignore_cooldown: ignoreCooldown },
  )

export interface AnalysisJobResult {
  incidents_analysed: number
  recommendations_generated: number
  recommendations_skipped_duplicate?: number
  recommendations_expired?: number
  min_incidents_needed?: number
  reason?: string
  source?: string
}

export const getAnalysisStatus = (jobId: string) =>
  axios.get<{
    job_id: string
    state: 'PENDING' | 'STARTED' | 'SUCCESS' | 'FAILURE' | string
    result: AnalysisJobResult | { reason: string } | null
  }>(`${API_BASE_URL}/platform-intelligence/analyze/status/${jobId}`)

export interface PlatformIntelRun {
  id: string
  created_at: string
  period_days: number
  trigger: string
  source: string
  incidents_analysed: number
  recommendations_generated: number
  recommendations_skipped: number
  llm_raw_response: string | null
  kpis: Record<string, number | null | Record<string, number>>
}

export const listRuns = (limit = 20, offset = 0) =>
  axios.get<{ runs: PlatformIntelRun[]; total_count: number }>(
    `${API_BASE_URL}/platform-intelligence/runs`,
    { params: { limit, offset } },
  )

export interface KpiSeriesPoint {
  created_at: string
  kpis: Record<string, number | null | Record<string, number>>
}

export const getKpiSeries = (days = 90) =>
  axios.get<{ points: KpiSeriesPoint[] }>(
    `${API_BASE_URL}/platform-intelligence/kpis`,
    { params: { days } },
  )

// ── Notification Teams ──────────────────────────────────────────────────────

export interface NotificationTeam {
  team_id: string
  name: string
  pagerduty_routing_key_set: boolean
  slack_channel: string | null
  email_recipients: string | null
  webhook_url: string | null
  webhook_secret_set: boolean
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface NotificationTeamPayload {
  name: string
  pagerduty_routing_key?: string   // "-"=clear, blank/omitted=keep (update only), value=replace
  slack_channel?: string
  email_recipients?: string
  webhook_url?: string
  webhook_secret?: string          // "-"=clear, blank/omitted=keep (update only), value=replace
  enabled?: boolean
}

export const listNotificationTeams = () =>
  axios.get<NotificationTeam[]>(`${API_BASE_URL}/notification-teams`)

export const createNotificationTeam = (payload: NotificationTeamPayload) =>
  axios.post<NotificationTeam>(`${API_BASE_URL}/notification-teams`, payload)

export const updateNotificationTeam = (teamId: string, payload: Partial<NotificationTeamPayload>) =>
  axios.put<NotificationTeam>(`${API_BASE_URL}/notification-teams/${teamId}`, payload)

export const deleteNotificationTeam = (teamId: string) =>
  axios.delete(`${API_BASE_URL}/notification-teams/${teamId}`)

// ── Synthetic Monitors ────────────────────────────────────────────────────────

import type { SyntheticMonitor } from '../types'

export interface SyntheticMonitorPayload {
  name: string
  har_filename?: string
  script?: string
  pages?: any[]
  credentials?: Record<string, string>
  schedule_mins?: number
  enabled?: boolean
}

export const listSyntheticMonitors = () =>
  axios.get<SyntheticMonitor[]>(`${API_BASE_URL}/synthetics`)

export const getSyntheticMonitor = (id: string) =>
  axios.get<SyntheticMonitor>(`${API_BASE_URL}/synthetics/${id}`)

export const createSyntheticMonitor = (payload: SyntheticMonitorPayload) =>
  axios.post<SyntheticMonitor>(`${API_BASE_URL}/synthetics`, payload)

export const updateSyntheticMonitor = (id: string, payload: Partial<SyntheticMonitorPayload>) =>
  axios.put<SyntheticMonitor>(`${API_BASE_URL}/synthetics/${id}`, payload)

export const deleteSyntheticMonitor = (id: string) =>
  axios.delete(`${API_BASE_URL}/synthetics/${id}`)

export const runSyntheticMonitor = (id: string) =>
  axios.post(`${API_BASE_URL}/synthetics/${id}/run`)

export const generateSyntheticScript = (payload: {
  current_script: string
  error_output: string
}) =>
  axios.post<{ script: string }>(`${API_BASE_URL}/synthetics/generate`, payload)

export const testSyntheticScript = (payload: {
  script: string
  credentials?: Record<string, string>
}) =>
  axios.post<{ status: string; output: string }>(`${API_BASE_URL}/synthetics/test`, payload)

