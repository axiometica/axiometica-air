-- Migration: add_event_condition_state
-- Adds the event_condition_state table used for backend-side event deduplication.
-- A row per (resource_name, event_type) tracks whether that monitoring condition
-- is currently open (firing) or closed (recovered / incident resolved).

CREATE TABLE IF NOT EXISTS event_condition_state (
    resource_name VARCHAR(255) NOT NULL,
    event_type    VARCHAR(100) NOT NULL,
    status        VARCHAR(20)  NOT NULL DEFAULT 'open',
    qualified     BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE = incident created; FALSE = dismissed
    last_event_id UUID         REFERENCES monitoring_events(event_id) ON DELETE SET NULL,
    opened_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    closed_at     TIMESTAMP,
    updated_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    PRIMARY KEY (resource_name, event_type)
);

-- If upgrading an existing deployment, add the column without recreating the table:
-- ALTER TABLE event_condition_state ADD COLUMN IF NOT EXISTS qualified BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_ecs_resource ON event_condition_state (resource_name);
CREATE INDEX IF NOT EXISTS idx_ecs_status   ON event_condition_state (status);
CREATE INDEX IF NOT EXISTS idx_ecs_opened   ON event_condition_state (opened_at);
