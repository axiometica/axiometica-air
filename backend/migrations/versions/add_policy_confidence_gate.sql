-- Migration: add_policy_confidence_gate
-- Adds the confidence gate columns to the policies table (v1.2.0).
-- When configured on a policy that requires_manual_approval, the gate
-- allows a proven runbook to bypass human approval automatically once
-- its confidence and successful_executions thresholds are both met.

ALTER TABLE policies ADD COLUMN IF NOT EXISTS confidence_gate_threshold FLOAT;
ALTER TABLE policies ADD COLUMN IF NOT EXISTS confidence_gate_min_runs  INTEGER;

COMMENT ON COLUMN policies.confidence_gate_threshold IS
  'Minimum runbook confidence (0.0–1.0) required to bypass manual approval, e.g. 0.90';
COMMENT ON COLUMN policies.confidence_gate_min_runs IS
  'Minimum successful runbook executions required before the confidence gate can trigger';
