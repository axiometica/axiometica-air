import { Workflow } from '../types'
import { parseUTC } from './dateFormatter'

/**
 * Workflow Data Transformer
 *
 * Transforms API response to Workflow interface with all convenience fields.
 * Extracts service, environment, and resource info from context.
 */

/**
 * Transform API workflow response to full Workflow interface
 * Extracts convenience fields from context layers
 */
export const transformWorkflow = (data: any): Workflow => {
  // Extract service/resource info from context
  const cmdbContext = data.context?.cmdb
  const service = cmdbContext?.resource_name || data.context?.cmdb_context?.resource_name || null
  const environment = cmdbContext?.environment || data.context?.environment || null
  const resourceInfo = cmdbContext?.resource_info || null

  // Format incident number string if we have a number but not the string
  let incidentNumberStr = data.incident_number_str
  if (!incidentNumberStr && data.incident_number) {
    const num = typeof data.incident_number === 'string'
      ? parseInt(data.incident_number.replace(/\D/g, ''))
      : data.incident_number
    incidentNumberStr = `INC${String(num).padStart(4, '0')}`
  }

  return {
    // Primary identifiers
    workflow_id: data.workflow_id,
    id: data.workflow_id, // Alias for convenience
    workflow_type: data.workflow_type,

    // Enumeration
    incident_number: data.incident_number,
    incident_number_str: incidentNumberStr,

    // State & Lifecycle
    lifecycle_state: data.lifecycle_state,

    // Severity & Risk
    severity: data.severity,
    risk_score: data.risk_score,
    priority: data.priority,
    initial_severity: data.initial_severity,
    business_criticality: data.business_criticality ?? null,
    ci_tier: data.ci_tier ?? null,

    // Context & Data
    context: data.context || {},
    context_schema: data.context_schema,

    // Content
    title: data.title,
    summary: data.summary,
    technical_summary: data.technical_summary,
    summary_generated_at: data.summary_generated_at,

    // Remediation & resolution tracking
    remediation_outcome: data.remediation_outcome ?? null,
    resolution_source: data.resolution_source ?? null,
    all_clear_received_at: data.all_clear_received_at ?? null,

    // Execution
    reasoning_trace: data.reasoning_trace || [],

    // Metadata
    created_at: data.created_at,
    updated_at: data.updated_at,

    // Convenience fields
    service,
    environment,
    resource_info: resourceInfo,
  }
}

/**
 * Transform API list response to Workflow array with all convenience fields
 */
export const transformWorkflowList = (
  data: any
): { workflows: Workflow[]; total_count: number; has_more: boolean } => {
  const workflows = Array.isArray(data.workflows)
    ? data.workflows.map(transformWorkflow)
    : Array.isArray(data)
    ? data.map(transformWorkflow)
    : []

  return {
    workflows,
    total_count: data.total_count || workflows.length,
    has_more: data.has_more !== undefined ? data.has_more : workflows.length >= (data.limit || 10),
  }
}

/**
 * Extract service/resource name from workflow
 * Checks multiple possible locations in context
 */
export const getServiceName = (workflow: Workflow | any): string => {
  return (
    workflow.service ||
    workflow.context?.cmdb?.resource_name ||
    workflow.context?.cmdb_context?.resource_name ||
    workflow.context?.resource_name ||
    'Unknown Service'
  )
}

/**
 * Extract environment from workflow
 * Checks context layers
 */
export const getEnvironment = (workflow: Workflow | any): string => {
  return (
    workflow.environment ||
    workflow.context?.cmdb?.environment ||
    workflow.context?.environment ||
    'Unknown'
  )
}

/**
 * Extract anomaly type from workflow
 * Used for incident type display
 */
export const getAnomalyType = (workflow: Workflow | any): string => {
  return (
    workflow.context?.sentinel?.anomaly_type ||
    workflow.context_schema?.sentinel?.anomaly_type ||
    workflow.title ||
    'Unknown Anomaly'
  )
}

/**
 * Extract resource info from workflow
 */
export const getResourceInfo = (workflow: Workflow | any) => {
  return (
    workflow.resource_info ||
    workflow.context?.cmdb?.resource_info ||
    workflow.context?.cmdb_context?.resource_info ||
    null
  )
}

/**
 * Get lifecycle state display label
 */
export const getLifecycleLabel = (state: string): string => {
  const labels: Record<string, string> = {
    open: 'Open',
    in_progress: 'In Progress',
    waiting_approval: 'Waiting Approval',
    approved: 'Approved',
    rejected: 'Rejected',
    executing: 'Executing',
    resolved: 'Resolved',
    deployed: 'Deployed',
    rolled_back: 'Rolled Back',
    failed: 'Failed',
    monitoring: 'Monitoring',
    closed: 'Closed',
  }
  return labels[state.toLowerCase()] || state
}

/**
 * Get severity display label
 */
export const getSeverityLabel = (severity: string | null): string => {
  if (!severity) return 'Low'
  const labels: Record<string, string> = {
    critical: 'Critical',
    high: 'High',
    medium: 'Medium',
    low: 'Low',
    info: 'Info',
  }
  return labels[severity.toLowerCase()] || severity
}

/** Lifecycle states that are fully terminal — incident is no longer open. */
const TERMINAL_STATES = new Set(['resolved', 'closed', 'failed', 'rolled_back', 'rejected'])

/**
 * Calculate incident duration in minutes.
 *
 * - Open incidents  → elapsed time from created_at to NOW (live, ticking counter).
 * - Terminal states → elapsed time from created_at to updated_at (stable time-to-resolve).
 */
export const calculateDurationMinutes = (
  startDate: string,
  endDate: string,
  lifecycleState?: string,
): number => {
  const start = new Date(startDate).getTime()
  const end = (lifecycleState && TERMINAL_STATES.has(lifecycleState))
    ? new Date(endDate).getTime()   // resolved → time-to-resolve (stable)
    : Date.now()                    // open     → time elapsed since creation (live)
  return Math.floor((end - start) / 60000)
}

/**
 * Format duration minutes to readable string
 */
export const formatDuration = (minutes: number | undefined | null): string => {
  if (minutes == null || isNaN(minutes as number)) return '—'
  if (minutes <= 0) return '< 1m'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  if (mins === 0) return `${hours}h`
  return `${hours}h ${mins}m`
}

/**
 * Format date to readable string
 */
export const formatDate = (isoString: string): string => {
  const date = parseUTC(isoString)
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  })
}

/**
 * Truncate text to max length with ellipsis
 */
export const truncateText = (text: string | null | undefined, maxLength: number = 80): string => {
  if (!text) return 'No summary available'
  if (text.length <= maxLength) return text
  return text.substring(0, maxLength) + '...'
}
