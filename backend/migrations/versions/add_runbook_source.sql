-- Migration: add source column to runbooks table
-- Applied: 2026-05-23
-- Distinguishes operator_authored runbooks from ai_generated ones

ALTER TABLE runbooks
    ADD COLUMN IF NOT EXISTS source VARCHAR(50) NOT NULL DEFAULT 'operator_authored';

-- All pre-existing runbooks are operator-authored
UPDATE runbooks SET source = 'operator_authored' WHERE source IS NULL OR source = '';
