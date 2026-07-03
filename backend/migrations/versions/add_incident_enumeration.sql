-- Migration: Add Incident Enumeration (INC0001, INC0002, etc.)
-- Date: 2026-05-13
-- Purpose: Add incident number sequence and columns for auto-generated incident IDs

-- Create sequence for incident enumeration
-- Starts at 1, increments by 1
CREATE SEQUENCE IF NOT EXISTS incident_seq START 1 INCREMENT 1;

-- Add columns to workflow_states table
ALTER TABLE workflow_states
ADD COLUMN IF NOT EXISTS incident_number INTEGER UNIQUE NULL,
ADD COLUMN IF NOT EXISTS incident_number_str VARCHAR(20) UNIQUE NULL;

-- Add indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_incident_number ON workflow_states(incident_number);
CREATE INDEX IF NOT EXISTS idx_incident_number_str ON workflow_states(incident_number_str);

-- Populate existing incident workflows with enumeration (optional - backfill)
-- Uncomment if you want to retroactively assign incident numbers to existing workflows
-- UPDATE workflow_states
-- SET incident_number = ROW_NUMBER() OVER (ORDER BY created_at),
--     incident_number_str = 'INC' || LPAD(ROW_NUMBER() OVER (ORDER BY created_at)::text, 4, '0')
-- WHERE workflow_type = 'incident' AND incident_number IS NULL;
