-- Migration: Incident lifecycle v2 — awaiting_manual state + manual intervention support
-- Date: 2026-05-22
-- Purpose: Adds resolution tracking fields to workflow_states and creates the
--          incident_notes table for operator work-log entries.

-- ── 0. Extend the lifecycle_state PostgreSQL ENUM type ────────────────────
-- PostgreSQL native ENUMs must be explicitly extended when new values are added.
-- 'closed'         — final administrative closure (wont_fix, escalated-out, no_action)
-- 'awaiting_manual'— remediation failed/rejected; human must decide next steps
-- Using DO blocks to avoid errors on re-run (ADD VALUE has no IF NOT EXISTS
-- in PostgreSQL < 9.3; this form is safe on PG 10+).

DO $$ BEGIN
    ALTER TYPE lifecyclestate ADD VALUE IF NOT EXISTS 'closed';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TYPE lifecyclestate ADD VALUE IF NOT EXISTS 'awaiting_manual';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── 1. Resolution fields on workflow_states ───────────────────────────────
-- resolution_category: why/how the incident was closed (manual_fix, wont_fix, etc.)
-- resolution_notes:    free-text written by operator when manually closing
-- resolved_by:         principal name/email (populated once auth lands in Phase B)
-- resolved_at:         explicit close timestamp (complements updated_at)

ALTER TABLE workflow_states
    ADD COLUMN IF NOT EXISTS resolution_category  VARCHAR(50),
    ADD COLUMN IF NOT EXISTS resolution_notes     TEXT,
    ADD COLUMN IF NOT EXISTS resolved_by          VARCHAR(200),
    ADD COLUMN IF NOT EXISTS resolved_at          TIMESTAMP;

-- ── 2. incident_notes table ───────────────────────────────────────────────
-- Append-only work log for operator notes, manual actions, and escalations.
-- Each row is immutable once created (no UPDATE needed or expected).

CREATE TABLE IF NOT EXISTS incident_notes (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_id UUID        NOT NULL REFERENCES workflow_states(workflow_id) ON DELETE CASCADE,
    author      VARCHAR(200) NOT NULL DEFAULT 'operator',   -- principal name once auth lands
    note_type   VARCHAR(20)  NOT NULL DEFAULT 'note'
                    CHECK (note_type IN ('note', 'action', 'escalation', 'system')),
    body        TEXT        NOT NULL CHECK (char_length(body) >= 1),
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_incident_notes_workflow ON incident_notes(workflow_id);
CREATE INDEX IF NOT EXISTS idx_incident_notes_created  ON incident_notes(created_at DESC);

-- ── 3. Backfill: mark existing rejected/failed incidents as awaiting_manual ──
-- Converts legacy lifecycle states to the new state so the UI shows the
-- correct banner and action controls for old incidents.

UPDATE workflow_states
SET lifecycle_state = 'awaiting_manual',
    updated_at      = NOW()
WHERE workflow_type    = 'incident'
  AND lifecycle_state IN ('rejected', 'failed')
  AND resolution_source IS NULL;   -- only if not already resolved some other way
