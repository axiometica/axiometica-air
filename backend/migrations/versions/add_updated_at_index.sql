-- Add index on workflow_states.updated_at for the global WS event poll
-- (polls this column every second; without an index it is a full table scan)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_workflow_states_updated_at
    ON workflow_states (updated_at DESC);

-- Also add index on monitoring_events.incident_workflow_id
-- (used by the new workflow_id filter on GET /monitoring-events)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_monitoring_events_workflow_id
    ON monitoring_events (incident_workflow_id)
    WHERE incident_workflow_id IS NOT NULL;
