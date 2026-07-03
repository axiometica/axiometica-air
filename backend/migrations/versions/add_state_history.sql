-- Migration: Add state_history column to workflow_states
-- Date: 2026-05-19
-- Purpose: Track full lifecycle state transition history so the Timeline UI
--          can show real timestamps for each phase (OPEN → INVESTIGATING →
--          APPROVAL_PENDING → REMEDIATION_ATTEMPTING → RESOLVED, etc.)
--          Previously only the current lifecycle_state was persisted; every
--          transition overwrote it.  This column stores the complete ordered
--          list as [{state, timestamp, reason}, ...].
--
-- Idempotent — uses ADD COLUMN IF NOT EXISTS; safe to re-run.

ALTER TABLE workflow_states
  ADD COLUMN IF NOT EXISTS state_history JSON NOT NULL DEFAULT '[]';
