-- Migration: principal audit log table
-- Immutable event trail for login, password changes, role changes, etc.
-- No FK constraints — records survive principal deletion.
-- Idempotent — safe to run multiple times.

CREATE TABLE IF NOT EXISTS principal_audit_log (
    id          BIGSERIAL    PRIMARY KEY,
    ts          TIMESTAMP    NOT NULL DEFAULT NOW(),
    actor_id    UUID,
    actor_name  VARCHAR(100),
    action      VARCHAR(50)  NOT NULL,
    target_id   UUID,
    target_name VARCHAR(100),
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts     ON principal_audit_log(ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor  ON principal_audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_target ON principal_audit_log(target_id);
