-- Migration: principals table + audit prep
-- Idempotent — safe to run multiple times.

CREATE TABLE IF NOT EXISTS principals (
    id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    name           VARCHAR(100) NOT NULL,
    email          VARCHAR(200) UNIQUE,
    role           VARCHAR(20)  NOT NULL
                       CHECK (role IN ('admin','itom_admin','operator','viewer','automation')),
    hashed_pw      VARCHAR(200),         -- bcrypt; NULL for API-key-only accounts
    api_key_hash   VARCHAR(64)  UNIQUE,  -- SHA-256 of raw key; NULL for human accounts
    api_key_prefix VARCHAR(16),          -- shown in UI (e.g. "ak_a1b2c3d4")
    enabled        BOOLEAN      NOT NULL DEFAULT true,
    created_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    last_seen_at   TIMESTAMP,
    created_by_id  UUID REFERENCES principals(id) ON DELETE SET NULL
);

-- Expand api_key_prefix column if table already exists with smaller width (idempotent)
ALTER TABLE principals ALTER COLUMN api_key_prefix TYPE VARCHAR(16);

-- Expand role CHECK constraint to include itom_admin (idempotent)
DO $$
BEGIN
  -- Drop old constraint (any name; find by definition content)
  DECLARE cname text;
  BEGIN
    SELECT conname INTO cname FROM pg_constraint
    WHERE conrelid = 'principals'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%operator%'
    LIMIT 1;
    IF cname IS NOT NULL THEN
      EXECUTE format('ALTER TABLE principals DROP CONSTRAINT %I', cname);
    END IF;
  END;
  -- Add updated constraint
  BEGIN
    ALTER TABLE principals ADD CONSTRAINT principals_role_check
      CHECK (role IN ('admin','itom_admin','operator','viewer','automation'));
  EXCEPTION WHEN duplicate_object THEN NULL;
  END;
END $$;

CREATE INDEX IF NOT EXISTS idx_principals_role    ON principals(role);
CREATE INDEX IF NOT EXISTS idx_principals_email   ON principals(email);
CREATE INDEX IF NOT EXISTS idx_principals_api_key ON principals(api_key_hash);
