-- Migration: add source_steps JSONB column to runbooks
-- Applied: 2026-06-08
-- Stores the visual editor's unified steps array (decisions + run_if preserved)
-- so runbooks can be round-tripped back into the graph editor without data loss.
-- The 3-array DB format (diagnostics/actions/verification_steps) loses decision
-- nodes and run_if conditions; source_steps keeps the canonical editor format.

ALTER TABLE runbooks
    ADD COLUMN IF NOT EXISTS source_steps JSONB;

-- No back-fill needed: existing runbooks will have source_steps = NULL, and
-- the editor falls back to dbRunbookToEditorSteps() for those rows.
