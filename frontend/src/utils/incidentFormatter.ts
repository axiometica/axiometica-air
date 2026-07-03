import { Workflow } from '../types'

/**
 * Format incident display name from workflow data
 * Priority: WorkflowState.title (from watcher alert) > context.title > incident_type > alert description > composed display
 * Example: "High Syscall Intensity on api-server (process: node)"
 */
export function getIncidentDisplayName(workflow: Workflow): string {
  // First priority: Use title from watcher alert (stored in WorkflowState.title)
  if (workflow.title) {
    return workflow.title
  }

  // Use context title if available (legacy fallback)
  if (workflow.context?.title) {
    return workflow.context.title
  }

  // Use incident type if available
  if (workflow.context?.incident_type) {
    return workflow.context.incident_type
  }

  // Try alert payload description first (most informative)
  if (workflow.context?.alert_payload?.description) {
    const desc = workflow.context.alert_payload.description
    // Truncate if too long (keep first 60 chars or up to first period)
    const firstSentence = desc.split('.')[0]
    return firstSentence.length > 60 ? firstSentence.substring(0, 57) + '...' : firstSentence
  }

  // Build from components
  const parts: string[] = []

  // Add resource name - check both old and new location
  const resourceName = workflow.context?.resource || workflow.context?.cmdb_context?.resource_name
  if (resourceName) {
    parts.push(resourceName)
  }

  // Add anomaly type - check both old and new location
  const anomalyType = workflow.context?.anomaly_type || workflow.context?.alert_payload?.type
  if (anomalyType) {
    parts.push(formatAnomalyType(anomalyType))
  }

  // Add severity
  if (workflow.severity) {
    parts.push(workflow.severity.charAt(0).toUpperCase() + workflow.severity.slice(1))
  }

  // If we have parts, join them
  if (parts.length > 0) {
    return parts.join(' | ')
  }

  // Fallback
  return `Incident ${workflow.workflow_id.substring(0, 8)}`
}

/**
 * Format anomaly type for display
 * Example: "high_cpu" → "High CPU"
 */
function formatAnomalyType(anomalyType: string): string {
  return anomalyType
    .split('_')
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

/**
 * Get incident short name for compact displays
 * Truncates to ~40 characters
 */
export function getIncidentShortName(workflow: Workflow): string {
  const fullName = getIncidentDisplayName(workflow)
  if (fullName.length > 40) {
    return fullName.substring(0, 37) + '...'
  }
  return fullName
}
