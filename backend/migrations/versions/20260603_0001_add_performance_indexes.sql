-- Migration: Add performance indexes for scaling to 100s of incidents
-- Date: 2026-06-03
-- Purpose: Optimize query performance for incident list, filtering, and risk scoring

-- Index for incident lifecycle filtering (most common query)
-- Query: SELECT * FROM workflow_states WHERE workflow_type='incident' AND lifecycle_state IN ('open', 'waiting_approval', ...)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incident_lifecycle
ON workflow_states(lifecycle_state, workflow_type)
WHERE workflow_type = 'incident';

-- Index for recent incidents (dashboard / activity feed)
-- Query: SELECT * FROM workflow_states WHERE workflow_type='incident' ORDER BY created_at DESC LIMIT 100
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incident_created_desc
ON workflow_states(created_at DESC)
WHERE workflow_type = 'incident';

-- Index for risk scoring (sorting by risk_score for dashboard)
-- Query: SELECT * FROM workflow_states WHERE workflow_type='incident' ORDER BY risk_score DESC
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incident_risk_score
ON workflow_states(risk_score DESC)
WHERE workflow_type = 'incident';

-- Index for approval queries (filtering by approval_request_id)
-- Query: SELECT * FROM workflow_states WHERE approval_request_id = '...'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incident_approval_id
ON workflow_states(approval_request_id)
WHERE approval_request_id IS NOT NULL;

-- Index for storm detection (finding children of a parent incident)
-- Query: SELECT * FROM workflow_states WHERE causation_id = '...' AND workflow_type='incident'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incident_causation
ON workflow_states(causation_id, workflow_type)
WHERE causation_id IS NOT NULL AND workflow_type = 'incident';

-- Index for timeline queries (between created_at and resolved_at)
-- Query: SELECT * FROM workflow_states WHERE workflow_type='incident' AND created_at BETWEEN ? AND ?
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incident_timerange
ON workflow_states(workflow_type, created_at, resolved_at);

-- Index for governance/policy lookups
-- Query: SELECT * FROM workflow_states WHERE governance_decision = 'approved' AND workflow_type='incident'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incident_governance
ON workflow_states(governance_decision)
WHERE workflow_type = 'incident' AND governance_decision IS NOT NULL;

-- Index for change workflow (future optimization)
-- Query: SELECT * FROM workflow_states WHERE workflow_type='change' ORDER BY created_at DESC
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_change_created_desc
ON workflow_states(created_at DESC)
WHERE workflow_type = 'change';

-- Partial index on incident_number_str for faster lookups by incident ID
-- Query: SELECT * FROM workflow_states WHERE incident_number_str = 'INC0042'
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_incident_number_str
ON workflow_states(incident_number_str)
WHERE incident_number_str IS NOT NULL;
