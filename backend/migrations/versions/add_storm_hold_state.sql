-- Migration: Add storm_hold lifecycle state + storm tracking columns
-- Date: 2026-05-24
-- Purpose: Supports the Storm Agent meta-orchestrator.
--   - storm_hold: child incident held pending storm-level CAB decision
--   - storm_id:   FK linking a child incident to its storm parent
--   - is_storm_parent flag lives in context JSONB (no schema change needed)

-- ── 0. Extend the PostgreSQL ENUM ────────────────────────────────────────────
-- storm_hold: incident is part of a detected storm cluster; individual
--             remediation is suppressed until the storm parent is resolved.

DO $$ BEGIN
    ALTER TYPE lifecyclestate ADD VALUE IF NOT EXISTS 'storm_hold';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── 1. storm_id column on workflow_states ─────────────────────────────────
-- Nullable UUID pointing to the parent storm incident (also a workflow_states row).
-- NULL means the incident is not part of any storm cluster.

ALTER TABLE workflow_states
    ADD COLUMN IF NOT EXISTS storm_id UUID REFERENCES workflow_states(workflow_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_workflow_states_storm_id ON workflow_states(storm_id);

-- ── 2. storm_detected_at column ───────────────────────────────────────────
-- Records when the storm was first detected (set on the parent incident row).

ALTER TABLE workflow_states
    ADD COLUMN IF NOT EXISTS storm_detected_at TIMESTAMP;
