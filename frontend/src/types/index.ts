export type WorkflowType = 'incident' | 'change' | 'problem' | 'request'

export type LifecycleState =
  | 'open'
  | 'in_progress'
  | 'waiting_approval'
  | 'approved'
  | 'rejected'
  | 'executing'
  | 'awaiting_manual'   // remediation failed/rejected — human must decide
  | 'resolved'
  | 'deployed'
  | 'rolled_back'
  | 'failed'
  | 'monitoring'
  | 'closed'

export type IncidentState =
  | 'OPEN'
  | 'INVESTIGATING'
  | 'APPROVAL_PENDING'
  | 'APPROVAL_APPROVED'
  | 'APPROVAL_REJECTED'
  | 'REMEDIATION_ATTEMPTING'
  | 'REMEDIATION_SUCCESSFUL'
  | 'REMEDIATION_FAILED'
  | 'MONITORING'
  | 'CLOSING'
  | 'CLOSED'

export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'

export type IncidentPriority = 'P1' | 'P2' | 'P3' | 'P4' | 'P5'

export type RemediationTier = 'runbook' | 'playbook' | 'history' | 'llm' | 'fallback'

// ─────────────────────────────────────────────────────────────────
// Phase 10: Typed Context Schema (matches Python backend)
// ─────────────────────────────────────────────────────────────────

export interface AlertPayload {
  type: string
  message: string
  severity?: string
  anomaly_process?: string
}

export interface SentinelContext {
  detected_anomaly: string
  anomaly_type: string
  alert_payload: AlertPayload
  timestamp: string
  confidence: number
}

export interface ResourceInfo {
  name: string
  type: string
  status: string
  owner: string
  environment: string
  criticality?: string
}

export interface CMDBContext {
  resource_name: string
  resource_info: ResourceInfo
  environment: string
  dependencies: Record<string, any>[]
  impacted_services: Record<string, any>[]
  cmdb_context?: Record<string, any>
}

export interface RiskBreakdown {
  severity_score: number
  resource_criticality_score: number
  dependency_impact_score: number
  business_impact_score: number
}

export interface RiskContext {
  risk_score: number
  risk_breakdown: RiskBreakdown
  blast_radius: number
  remediation_complexity: string
}

export interface RunbookStep {
  order: number
  type: string
  name: string
  description: string
  tool: string
  args: Record<string, any>
}

export interface Proposal {
  runbook_id: string
  runbook_name: string
  diagnostics_steps: RunbookStep[]
  remediation_steps: RunbookStep[]
  confidence: number
  blast_radius: number
  approval_required: boolean
  main_args: Record<string, any>
}

export interface GovernanceContext {
  matching_policies: Record<string, any>[]
  approval_required: boolean
  approval_priority: number
  allowed_actions: string[]
  blast_radius_limit?: number
  requires_post_monitoring: boolean
  decision_notes: string
}

export interface VerificationResult {
  step_name: string
  status: string
  metric: string
  actual_value: number
  threshold: number
  message: string
}

export interface VerificationContext {
  verification_results: VerificationResult[]
  overall_success: boolean
  remediation_effective: boolean
  issues_resolved: boolean
}

export interface IncidentWorkflowContext {
  sentinel?: SentinelContext
  cmdb?: CMDBContext
  risk?: RiskContext
  proposal?: Proposal
  governance?: GovernanceContext
  execution_results: Record<string, any>[]
  verification?: VerificationContext
  reasoning_trace: string[]
}

// ─────────────────────────────────────────────────────────────────

export interface Workflow {
  // Primary identifiers
  workflow_id: string
  id?: string  // Alias for convenience (= workflow_id)
  workflow_type: WorkflowType

  // Enumeration (Phase 6.5)
  incident_number?: number | null
  incident_number_str?: string | null  // Formatted as "INC0001", "INC0002", etc.

  // State & Lifecycle
  lifecycle_state: LifecycleState

  // Severity & Risk
  severity: Severity | null
  risk_score: number | null
  priority?: IncidentPriority
  initial_severity?: Severity
  // CMDB-sourced, distinct from severity — severity already factors this in for
  // ranking/triage; this is exposed separately so it can be filtered/grouped on
  // its own ("what does this affect") independently of the computed score.
  business_criticality?: string | null
  ci_tier?: number | null

  // Context & Data
  context: Record<string, any>
  context_schema?: IncidentWorkflowContext  // Phase 10: Typed context

  // Content
  title?: string | null  // Incident title from watcher alert
  summary?: string | null
  technical_summary?: string | null  // LLM-generated technical digest (bullet points)
  summary_generated_at?: string | null

  // Incremented when the same condition (resource + event_type + source) is
  // validated as recurring while this incident is still open. See the dedup
  // branch in backend api/routes/monitoring_events.py.
  duplicate_count?: number

  // Remediation & resolution (decoupled from lifecycle_state)
  // lifecycle_state = overall incident status; these track how it was actually resolved
  remediation_outcome?: 'succeeded' | 'failed' | 'aborted' | 'skipped' | 'pending' | 'rejected' | 'diagnostics_only' | 'manual_fix' | 'self_healed' | 'wont_fix' | 'monitoring_ongoing' | 'escalated' | 'no_action_required' | null
  resolution_source?: 'automated_remediation' | 'watcher_all_clear' | 'manual' | null
  all_clear_received_at?: string | null  // ISO timestamp when watcher confirmed condition cleared
  // Resolution detail (v4.1.0)
  resolution_category?: string | null   // manual_fix/wont_fix/self_healed/escalated/no_action_required
  resolution_notes?: string | null      // operator free-text
  resolved_by?: string | null           // principal who closed it
  resolved_at?: string | null           // ISO timestamp of explicit close

  // Execution
  reasoning_trace: string[]

  // Storm Agent linkage (v1.0.0)
  storm_id?: string | null  // Set on child incidents held in a storm; use to fetch storm detail

  // State transition history — [{state, timestamp, reason}]
  // Populated by backend transition_state(); empty for pre-migration incidents.
  state_history?: Array<{ state: string; timestamp: string; reason?: string }>

  // Metadata
  created_at: string
  updated_at: string

  // Convenience fields (extracted from context)
  service?: string | null  // Resource name (from context.cmdb.resource_name)
  environment?: string | null  // Environment (from context.cmdb.environment)
  resource_info?: any  // Full resource info (from context.cmdb.resource_info)
}

// Phase 6.5: Workflow list response with pagination metadata
export type WorkflowResponse = Workflow

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
  confidence?: number    // 0.0–1.0; undefined = unknown
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

export interface Approval {
  approval_id: string
  workflow_id: string
  approval_type: string
  status: 'pending' | 'approved' | 'rejected' | 'diagnostics_only' | 'cancelled'
  requested_at: string
  decided_at?: string
  decided_by?: string
  decision_notes?: string
  proposed_action?: ProposedAction
  incident_summary?: IncidentSummary
  governance_policy_id?: string
  // Live risk ranking read from the linked incident (not the frozen incident_summary
  // snapshot) — the queue is ordered by this server-side, worst-impact first.
  risk_score?: number | null
  severity?: string | null
}

export interface Policy {
  policy_id: string
  name: string
  description?: string
  rules: Record<string, any>
  requires_manual_approval: boolean
  approved_actions: string[]
  constraints: {
    max_blast_radius?: number
    max_restart_frequency?: number
    requires_post_monitoring?: boolean
  }
  approval_priority: number
  enabled: boolean
  created_at: string
  updated_at: string
  confidence_gate_threshold?: number  // 0.0–1.0 — bypass approval once runbook reaches this confidence
  confidence_gate_min_runs?: number   // minimum successful executions before gate can trigger
  confidence_gate_runbook_id?: string | null  // pin the gate to one specific runbook; null = auto-select via lookup cascade
  // Draft/publish workflow — `enabled` above stays an instant kill-switch outside this.
  status?: 'draft' | 'published'
  published_at?: string | null
  has_unpublished_changes?: boolean
  draft_snapshot?: Record<string, any> | null
  // These are convenience fields, not from API
  id?: string  // Alias for policy_id for component convenience
}

export interface RemediationAction {
  tool: string
  args: Record<string, any>
  timeout?: number
}

export interface RemediationPlan {
  actions: RemediationAction[]
  confidence: number
  source: RemediationTier
  summary: string
  blast_radius: number
  risk_level: Severity
}

export interface Event {
  event_id: string
  anomaly_type: string
  resource_name: string
  severity: Severity
  metrics: Record<string, any>
  detected_at: string
  deduplicated: boolean
}

export interface ClassificationResult {
  is_incident: boolean
  risk_score: number
  risk_level: Severity
  reasoning: string
  baseline?: Record<string, number>
  current?: Record<string, number>
  method?: string
}

export interface TimelineEntry {
  state: IncidentState
  timestamp: string
  duration?: number
  reason?: string
}

export interface IncidentContext {
  event_count: number
  events?: Event[]
  primary_event_id?: string
  classification: ClassificationResult
  policy_evaluation?: {
    matched_policies: Policy[]
    approved_actions: string[]
    requires_manual_approval: boolean
    constraints: any
  }
  remediation?: {
    recommendation: RemediationPlan
    execution_status: 'pending' | 'in_progress' | 'completed' | 'failed'
    actions_executed?: number
  }
  timeline?: TimelineEntry[]
}

export const severityColors: Record<Severity, string> = {
  critical: 'bg-red-100 text-red-800',
  high: 'bg-orange-100 text-orange-800',
  medium: 'bg-yellow-100 text-yellow-800',
  low: 'bg-blue-100 text-blue-800',
  info: 'bg-gray-100 text-gray-800',
}

export const stateColors: Record<LifecycleState, string> = {
  open: 'bg-blue-100 text-blue-800',
  in_progress: 'bg-yellow-100 text-yellow-800',
  waiting_approval: 'bg-purple-100 text-purple-800',
  approved: 'bg-green-100 text-green-800',
  rejected: 'bg-red-100 text-red-800',
  executing: 'bg-orange-100 text-orange-800',
  awaiting_manual: 'bg-orange-100 text-orange-800',
  resolved: 'bg-green-100 text-green-800',
  deployed: 'bg-cyan-100 text-cyan-800',
  rolled_back: 'bg-red-100 text-red-800',
  failed: 'bg-red-100 text-red-800',
  monitoring: 'bg-gray-100 text-gray-800',
  closed: 'bg-gray-100 text-gray-800',
}

/** Work-log entry returned by GET /workflows/{id}/notes */
export interface NoteEntry {
  id: string
  workflow_id: string
  author: string
  note_type: 'note' | 'action' | 'escalation' | 'system'
  body: string
  created_at: string
}

export const incidentStateColors: Record<IncidentState, string> = {
  OPEN: 'bg-blue-100 text-blue-800',
  INVESTIGATING: 'bg-yellow-100 text-yellow-800',
  APPROVAL_PENDING: 'bg-purple-100 text-purple-800',
  APPROVAL_APPROVED: 'bg-green-100 text-green-800',
  APPROVAL_REJECTED: 'bg-red-100 text-red-800',
  REMEDIATION_ATTEMPTING: 'bg-orange-100 text-orange-800',
  REMEDIATION_SUCCESSFUL: 'bg-green-100 text-green-800',
  REMEDIATION_FAILED: 'bg-red-100 text-red-800',
  MONITORING: 'bg-gray-100 text-gray-800',
  CLOSING: 'bg-gray-200 text-gray-800',
  CLOSED: 'bg-gray-300 text-gray-900',
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

// ── Connector Hub ─────────────────────────────────────────────────────────

export interface ConnectorDef {
  id: string
  display_name: string
  description: string
  icon: string
  version: string
  capabilities: string[]
  coming_soon?: boolean
  // Runtime state (enriched by backend)
  configured: boolean
  enabled: boolean
  last_sync_at: string | null
  last_sync_status: 'ok' | 'partial' | 'error' | 'never' | null
  sync_interval_min: number
  // Common (detail response)
  base_url?: string
  recent_sync_logs?: SyncLog[]
  // ServiceNow-specific
  username?: string
  incident_sync?: {
    enabled: boolean
    auto_create?: boolean
    auto_update_on_states?: string[]
    include_ai_summary?: boolean
    append_agent_notes?: boolean
    platform_url?: string
  }
  // Splunk / alert-ingest connectors
  default_criticality?: string
  default_event_type?: string
  webhook_secret_set?: boolean
  allow_auto_remediation?: boolean
  event_type_mappings?: Record<string, string>   // externalName → canonicalType
  // PagerDuty-specific (outbound escalation)
  routing_key_set?: boolean
}

export interface SyncLog {
  id: string
  started_at: string
  finished_at: string | null
  records_pulled: number
  status: 'ok' | 'partial' | 'error'
  error_message: string | null
}

export interface ConnectorIncidentSync {
  enabled: boolean
  auto_create: boolean
  auto_update_on_states: string[]
  include_ai_summary: boolean
  append_agent_notes: boolean
  platform_url: string
}

export interface ConnectorConfigPayload {
  base_url: string
  username: string
  password: string
  sync_interval_min: number
  enabled: boolean
  incident_sync?: ConnectorIncidentSync
}

export interface SplunkConfigPayload {
  base_url:               string
  token:                  string
  webhook_secret?:        string   // blank = keep existing; '-' = clear
  default_criticality:    string
  default_event_type:     string
  enabled:                boolean
  allow_auto_remediation: boolean
}

export interface SNowCI {
  sys_id: string
  ci_class: string
  name: string
  synced_at: string | null
  [key: string]: any          // all raw SN fields
}

export interface SNowCIClass {
  ci_class: string
  label: string
  table: string
  count: number
  display_key: string
}

export interface SNowCMDBSummary {
  classes: SNowCIClass[]
  total_records: number
}

export interface SNowIncidentMap {
  mapped: boolean
  snow_sys_id?: string
  snow_number?: string
  push_status?: string
  last_pushed_at?: string
  snow_record?: Record<string, any>
  error?: string
}

// ── Platform Intelligence ──────────────────────────────────────────────────

export type RecommendationStatus = 'pending' | 'accepted' | 'rejected' | 'expired' | 'auto_applied'
export type RecommendationPriority = 'high' | 'medium' | 'low'
export type RecommendationCategory =
  | 'threshold'
  | 'factor_weight'
  | 'event_multiplier'
  | 'missing_data'
  | 'resource_specific'
  | 'governance'
  | 'runbook_step'
  | 'general'

export interface OptimizationRecommendation {
  id: string
  category: RecommendationCategory
  parameter: string
  current_value: unknown
  suggested_value: unknown
  title: string
  rationale: string
  impact?: string
  confidence: number
  priority: RecommendationPriority
  evidence?: Record<string, unknown>
  status: RecommendationStatus
  reviewed_by?: string
  review_reason?: string
  reviewed_at?: string
  applied: boolean
  applied_at?: string
  auto_apply_eligible?: boolean
  auto_apply_threshold_met_at?: string
  outcome_verified_at?: string
  outcome_improved?: boolean | null
  created_at: string
  expires_at?: string
}

export interface PlatformHealthMetrics {
  period_days: number
  total_incidents: number
  resolved_incidents: number
  automated_resolutions: number
  manual_resolutions: number
  automation_rate: number
  false_positive_count: number
  false_positive_rate: number
  avg_mttr_hours: number | null
  p1p2_avg_mttr_hours: number | null
  pending_recommendations: number
  avg_cmdb_coverage: number | null
  last_analysis_at: string | null
}

export interface ConfigHistoryEntry {
  id: string
  parameter: string
  previous_value: unknown
  new_value: unknown
  title: string
  reviewed_by?: string
  applied_at?: string
  category: string
  priority: string
}
