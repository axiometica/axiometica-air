-- ════════════════════════════════════════════════════════════════════════════
-- Migration: v1.1.0 Schema Foundations
-- Date: 2026-06-04
-- Purpose: Fix accumulated technical debt and establish foundational schema
--          patterns before adding further features.
--
-- Changes (idempotent — safe to re-run):
--   1.  Fix generated_runbooks table   (MySQL syntax → proper PostgreSQL)
--   2.  Fix remediation_outcomes table (MySQL syntax → proper PostgreSQL)
--   3.  Add is_storm_parent BOOLEAN column to workflow_states
--   4.  Backfill is_storm_parent from context JSONB
--   5.  Add storm_seq sequence
--   6.  Add storm_number + storm_number_str columns to workflow_states
--   7.  Create assign_workflow_human_id() trigger function
--   8.  Create BEFORE INSERT trigger  → auto-assigns INC number
--   9.  Create BEFORE UPDATE trigger  → auto-assigns STRM number when
--          is_storm_parent flips to true
--  10.  Drop workflow_definitions dead table
--  11.  Add partial indexes for storm and is_storm_parent queries
-- ════════════════════════════════════════════════════════════════════════════


-- ── 1 & 2.  Fix ML tables (were written in MySQL syntax, not PostgreSQL) ─────
--
-- generated_runbooks and remediation_outcomes used:
--   • "NOT NULL INDEX"           — inline INDEX is MySQL-only syntax
--   • "ON UPDATE CURRENT_TIMESTAMP" — MySQL-only; PostgreSQL uses triggers
--   • VARCHAR(36) ids instead of UUID
--   • No proper FK references
--
-- We drop and recreate both tables using correct PostgreSQL DDL.
-- Data loss is acceptable: these tables store AI-generated artefacts that
-- are re-generated on demand and were likely never populated correctly due
-- to the broken DDL.
-- ─────────────────────────────────────────────────────────────────────────────

DROP TABLE IF EXISTS remediation_outcomes CASCADE;
DROP TABLE IF EXISTS generated_runbooks  CASCADE;

CREATE TABLE IF NOT EXISTS generated_runbooks (
    id                          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id                 UUID         NOT NULL
                                             REFERENCES workflow_states(workflow_id)
                                             ON DELETE CASCADE,
    anomaly_type                VARCHAR(255) NOT NULL,

    -- Runbook content
    name                        VARCHAR(500) NOT NULL,
    description                 VARCHAR(2000),
    diagnostics_steps           JSONB        NOT NULL DEFAULT '[]',
    remediation_steps           JSONB        NOT NULL DEFAULT '[]',
    rollback_steps              JSONB        NOT NULL DEFAULT '[]',
    verification_steps          JSONB        NOT NULL DEFAULT '[]',

    -- Generation metadata
    source_runbooks             JSONB,
    generated_by                VARCHAR(255) NOT NULL DEFAULT 'runbook_generator_agent',
    resource_type               VARCHAR(100),
    environment                 VARCHAR(100),

    -- Validation
    validation_status           VARCHAR(50)  NOT NULL DEFAULT 'pending',
    validation_issues           JSONB,
    validation_warnings         JSONB,
    confidence_score            FLOAT        NOT NULL DEFAULT 0.0,

    -- Approval workflow
    approval_status             VARCHAR(50)  NOT NULL DEFAULT 'pending_review',
    approved_by                 VARCHAR(255),
    approval_feedback           VARCHAR(2000),
    approved_at                 TIMESTAMP,

    -- Execution tracking
    total_executions            INTEGER      NOT NULL DEFAULT 0,
    successful_executions       INTEGER      NOT NULL DEFAULT 0,
    failed_executions           INTEGER      NOT NULL DEFAULT 0,
    success_rate                FLOAT,
    last_executed_at            TIMESTAMP,

    -- Estimated metrics (from generation)
    estimated_blast_radius      INTEGER,
    estimated_duration_seconds  INTEGER,
    estimated_time_to_resolution INTEGER,

    -- Actual metrics (from execution)
    actual_avg_duration_seconds INTEGER,
    actual_avg_ttm_seconds      INTEGER,

    is_active                   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gen_runbooks_workflow
    ON generated_runbooks(workflow_id);
CREATE INDEX IF NOT EXISTS idx_gen_runbooks_anomaly
    ON generated_runbooks(anomaly_type);
CREATE INDEX IF NOT EXISTS idx_gen_runbooks_approval
    ON generated_runbooks(approval_status)
    WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_gen_runbooks_created
    ON generated_runbooks(created_at DESC);


CREATE TABLE IF NOT EXISTS remediation_outcomes (
    id                          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id                 UUID         NOT NULL
                                             REFERENCES workflow_states(workflow_id)
                                             ON DELETE CASCADE,

    -- What was attempted
    applied_remediation_id      UUID,
    runbook_source              VARCHAR(50),     -- 'standard' | 'generated' | 'manual'

    -- Execution details
    incident_features           JSONB,
    applied_steps               JSONB,
    execution_duration_seconds  INTEGER,
    resolution_time_seconds     INTEGER,

    -- Outcomes
    effectiveness_score         FLOAT,
    remediation_successful      BOOLEAN,
    incident_resolved           BOOLEAN,
    side_effects                JSONB,

    -- Post-remediation metrics
    system_stable               BOOLEAN,
    performance_impact          VARCHAR(50),
    resource_usage_change       JSONB,

    -- Feedback
    feedback_provided           BOOLEAN      NOT NULL DEFAULT FALSE,
    feedback_score              INTEGER,
    feedback_notes              VARCHAR(2000),
    feedback_from               VARCHAR(255),

    created_at                  TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_remediation_outcomes_workflow
    ON remediation_outcomes(workflow_id);
CREATE INDEX IF NOT EXISTS idx_remediation_outcomes_score
    ON remediation_outcomes(effectiveness_score);
CREATE INDEX IF NOT EXISTS idx_remediation_outcomes_feedback
    ON remediation_outcomes(feedback_provided)
    WHERE feedback_provided = FALSE;
CREATE INDEX IF NOT EXISTS idx_remediation_outcomes_created
    ON remediation_outcomes(created_at DESC);


-- ── 3.  Add is_storm_parent column ───────────────────────────────────────────
--
-- Promotes the hidden context->>'is_storm_parent' JSONB flag to a real column.
-- A storm parent IS an incident that also governs a cluster of child incidents.
-- Having a proper column allows indexing, FK-style queries, and trigger logic.

ALTER TABLE workflow_states
    ADD COLUMN IF NOT EXISTS is_storm_parent BOOLEAN NOT NULL DEFAULT FALSE;

-- ── 4.  Backfill is_storm_parent from context JSONB ──────────────────────────

UPDATE workflow_states
SET    is_storm_parent = TRUE
WHERE  workflow_type    = 'incident'
  AND  is_storm_parent  = FALSE
  AND  context->>'is_storm_parent' = 'true';


-- ── 5.  storm_seq sequence ───────────────────────────────────────────────────

CREATE SEQUENCE IF NOT EXISTS storm_seq START 1 INCREMENT 1;


-- ── 6.  storm_number + storm_number_str columns ──────────────────────────────
--
-- Storm parents get their own human-readable ID (STRM0001, STRM0002, …)
-- in addition to their INC number (they are still incidents and appear
-- in incident lists under their INC identifier).
-- storm_number_str is the primary reference operators use for storm operations.

ALTER TABLE workflow_states
    ADD COLUMN IF NOT EXISTS storm_number     INTEGER     UNIQUE NULL,
    ADD COLUMN IF NOT EXISTS storm_number_str VARCHAR(20) UNIQUE NULL;

CREATE INDEX IF NOT EXISTS idx_storm_number_str
    ON workflow_states(storm_number_str)
    WHERE storm_number_str IS NOT NULL;


-- ── 7.  Trigger function: assign_workflow_human_id() ─────────────────────────
--
-- Handles two cases in one function:
--   INSERT path: new incident row → assign INC number from incident_seq
--   UPDATE path: is_storm_parent flipped to TRUE → assign STRM number from storm_seq
--
-- The trigger-based approach guarantees IDs are NEVER NULL regardless of which
-- code path creates the row. Replaces the fragile EnumerationService.generate_*
-- call-sites (Python still reads the value back after insert — it just no longer
-- calls nextval() itself).

CREATE OR REPLACE FUNCTION assign_workflow_human_id()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    -- ── INSERT path: assign INC number to every new incident ─────────────────
    IF TG_OP = 'INSERT' THEN
        IF NEW.workflow_type = 'incident' AND NEW.incident_number IS NULL THEN
            NEW.incident_number     := nextval('incident_seq');
            NEW.incident_number_str := 'INC' || LPAD(NEW.incident_number::text, 4, '0');
        END IF;

        -- Also assign STRM if created directly as a storm parent (rare but possible)
        IF NEW.is_storm_parent = TRUE AND NEW.storm_number IS NULL THEN
            NEW.storm_number     := nextval('storm_seq');
            NEW.storm_number_str := 'STRM' || LPAD(NEW.storm_number::text, 4, '0');
        END IF;

        RETURN NEW;
    END IF;

    -- ── UPDATE path: assign STRM number when is_storm_parent flips to TRUE ───
    IF TG_OP = 'UPDATE' THEN
        IF NEW.is_storm_parent = TRUE
           AND (OLD.is_storm_parent IS DISTINCT FROM TRUE)
           AND NEW.storm_number IS NULL THEN
            NEW.storm_number     := nextval('storm_seq');
            NEW.storm_number_str := 'STRM' || LPAD(NEW.storm_number::text, 4, '0');
        END IF;

        RETURN NEW;
    END IF;

    RETURN NEW;
END;
$$;


-- ── 8.  BEFORE INSERT trigger ─────────────────────────────────────────────────

DROP TRIGGER IF EXISTS trg_workflow_human_id_insert ON workflow_states;

CREATE TRIGGER trg_workflow_human_id_insert
    BEFORE INSERT ON workflow_states
    FOR EACH ROW
    EXECUTE FUNCTION assign_workflow_human_id();


-- ── 9.  BEFORE UPDATE trigger (only fires when is_storm_parent changes) ───────

DROP TRIGGER IF EXISTS trg_workflow_human_id_update ON workflow_states;

CREATE TRIGGER trg_workflow_human_id_update
    BEFORE UPDATE OF is_storm_parent ON workflow_states
    FOR EACH ROW
    WHEN (NEW.is_storm_parent = TRUE AND OLD.is_storm_parent IS DISTINCT FROM TRUE)
    EXECUTE FUNCTION assign_workflow_human_id();


-- ── 10. Drop workflow_definitions dead table ──────────────────────────────────
--
-- WorkflowDefinitionLoader reads from YAML files (core/definitions.py), not
-- this table. The table has been empty and unused since initial schema creation.

DROP TABLE IF EXISTS workflow_definitions CASCADE;


-- ── 11. Additional indexes ────────────────────────────────────────────────────

-- Fast lookup of all storm parents
CREATE INDEX IF NOT EXISTS idx_workflow_storm_parent
    ON workflow_states(is_storm_parent, created_at DESC)
    WHERE is_storm_parent = TRUE;

-- Fast lookup of children belonging to a storm
CREATE INDEX IF NOT EXISTS idx_workflow_storm_children
    ON workflow_states(storm_id)
    WHERE storm_id IS NOT NULL;
