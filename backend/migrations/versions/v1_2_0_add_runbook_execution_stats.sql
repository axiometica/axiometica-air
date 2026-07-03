-- ════════════════════════════════════════════════════════════════════════════
-- Migration: v1.2.0 Add runbook execution stats and monitoring event signals
-- Date: 2026-06-19
-- Purpose: Add columns defined in RunbookModel and MonitoringEventModel that
--          were never captured in a migration. Idempotent — safe to re-run.
-- ════════════════════════════════════════════════════════════════════════════

-- ── runbooks: execution feedback tracking ─────────────────────────────────
ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS total_executions       INTEGER     NOT NULL DEFAULT 0;
ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS successful_executions  INTEGER     NOT NULL DEFAULT 0;
ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS failed_executions      INTEGER     NOT NULL DEFAULT 0;
ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS success_rate           FLOAT;
ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS recent_outcomes        JSONB       NOT NULL DEFAULT '[]';
ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS confidence_trend       VARCHAR(10);
ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS last_executed_at       TIMESTAMP;

-- ── runbooks: generation_prompt (Alembic 0003 — not run on GCP) ──────────
ALTER TABLE runbooks ADD COLUMN IF NOT EXISTS generation_prompt          TEXT;

-- ── monitoring_events: signal and anomaly detail columns ──────────────────
ALTER TABLE monitoring_events ADD COLUMN IF NOT EXISTS confidence             FLOAT;
ALTER TABLE monitoring_events ADD COLUMN IF NOT EXISTS signal_value           FLOAT;
ALTER TABLE monitoring_events ADD COLUMN IF NOT EXISTS signal_threshold       FLOAT;
ALTER TABLE monitoring_events ADD COLUMN IF NOT EXISTS anomaly_process        VARCHAR(255);
ALTER TABLE monitoring_events ADD COLUMN IF NOT EXISTS qualification_factors  JSONB;
