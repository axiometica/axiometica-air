-- Migration: Add ML/AI tables for RunbookGenerator and learning pipeline
-- Created: May 16, 2026
-- Purpose: Support generated runbook storage and remediation outcome tracking

-- Table for storing generated (AI-created) runbooks
CREATE TABLE IF NOT EXISTS generated_runbooks (
    id VARCHAR(36) PRIMARY KEY,
    workflow_id VARCHAR(36) NOT NULL,
    anomaly_type VARCHAR(255) NOT NULL,

    -- Runbook content
    name VARCHAR(500) NOT NULL,
    description VARCHAR(2000),
    diagnostics_steps JSONB,
    remediation_steps JSONB,
    rollback_steps JSONB,
    verification_steps JSONB,

    -- Generation metadata
    source_runbooks JSONB,
    generated_by VARCHAR(255) DEFAULT 'runbook_generator_agent',

    -- Validation results
    validation_status VARCHAR(50) DEFAULT 'pending',
    validation_issues JSONB,
    validation_warnings JSONB,
    confidence_score FLOAT DEFAULT 0.0,

    -- Approval workflow
    approval_status VARCHAR(50) DEFAULT 'pending_review',
    approved_by VARCHAR(255),
    approval_feedback VARCHAR(2000),

    -- Execution tracking
    total_executions INTEGER DEFAULT 0,
    successful_executions INTEGER DEFAULT 0,
    failed_executions INTEGER DEFAULT 0,
    success_rate FLOAT,

    -- Estimated metrics (from generation)
    estimated_blast_radius INTEGER,
    estimated_duration_seconds INTEGER,
    estimated_time_to_resolution INTEGER,

    -- Actual metrics (from execution)
    actual_avg_duration_seconds INTEGER,
    actual_avg_ttm_seconds INTEGER,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP,
    last_executed_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Metadata
    resource_type VARCHAR(100),
    environment VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,

    INDEX idx_anomaly_type (anomaly_type),
    INDEX idx_approval_status (approval_status),
    INDEX idx_created_at (created_at)
);

-- Table for tracking remediation outcomes (learning pipeline input)
CREATE TABLE IF NOT EXISTS remediation_outcomes (
    id VARCHAR(36) PRIMARY KEY,
    workflow_id VARCHAR(36) NOT NULL,

    -- What was attempted
    applied_remediation_id VARCHAR(36),
    runbook_source VARCHAR(50),  -- 'standard', 'generated', 'manual'

    -- Execution details
    incident_features JSONB,
    applied_steps JSONB,
    execution_duration_seconds INTEGER,
    resolution_time_seconds INTEGER,

    -- Outcomes
    effectiveness_score FLOAT,
    remediation_successful BOOLEAN,
    incident_resolved BOOLEAN,
    side_effects JSONB,

    -- Post-remediation metrics
    system_stable BOOLEAN,
    performance_impact VARCHAR(50),
    resource_usage_change JSONB,

    -- Feedback
    feedback_provided BOOLEAN DEFAULT FALSE,
    feedback_score INTEGER,
    feedback_notes VARCHAR(2000),
    feedback_from VARCHAR(255),

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_workflow_id (workflow_id),
    INDEX idx_effectiveness_score (effectiveness_score),
    INDEX idx_created_at (created_at)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_generated_runbooks_active ON generated_runbooks(is_active, approval_status);
CREATE INDEX IF NOT EXISTS idx_remediation_outcomes_feedback ON remediation_outcomes(feedback_provided);
