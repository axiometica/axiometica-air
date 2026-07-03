-- Migration: Add workflow_states columns introduced in v2.10.0 – v2.11.0
-- Date: 2026-05-17
-- Purpose: Add typed context schema field, remediation tracking, and summary columns
--          that were created by init_db() automatically but need explicit migration
--          scripts for environments that are upgrading from v2.9 or earlier.
--
-- Idempotent — uses ADD COLUMN IF NOT EXISTS; safe to re-run.

-- ── context_schema (typed JSONB context, Phase 10) ────────────────────────
ALTER TABLE workflow_states
  ADD COLUMN IF NOT EXISTS context_schema         JSON          NULL;

-- ── technical_summary (per-incident LLM-generated technical detail) ───────
ALTER TABLE workflow_states
  ADD COLUMN IF NOT EXISTS technical_summary      TEXT          NULL;

-- ── remediation tracking columns (v2.11) ──────────────────────────────────
-- remediation_outcome: succeeded | failed | aborted | skipped | pending | resolved_manual | self_resolved | no_action_required | escalated | monitoring_manual
ALTER TABLE workflow_states
  ADD COLUMN IF NOT EXISTS remediation_outcome    VARCHAR(50)   NULL;

-- resolution_source: automated_remediation | watcher_all_clear | manual
ALTER TABLE workflow_states
  ADD COLUMN IF NOT EXISTS resolution_source      VARCHAR(50)   NULL;

-- all_clear_received_at: set when the watcher fires a condition_cleared event
ALTER TABLE workflow_states
  ADD COLUMN IF NOT EXISTS all_clear_received_at  TIMESTAMP     NULL;

-- ── indexes ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_remediation_outcome
  ON workflow_states (remediation_outcome);

CREATE INDEX IF NOT EXISTS idx_resolution_source
  ON workflow_states (resolution_source);
