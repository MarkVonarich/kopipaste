-- Hotfix: ensure action_tokens has expires_at and required defaults/indexes
-- Safe to run multiple times.

BEGIN;

-- Ensure table exists at least minimally (for environments with drift)
CREATE TABLE IF NOT EXISTS action_tokens (
    token_id UUID PRIMARY KEY
);

ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS status TEXT;

ALTER TABLE action_tokens ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE action_tokens ALTER COLUMN expires_at SET DEFAULT (NOW() + INTERVAL '10 minutes');
ALTER TABLE action_tokens ALTER COLUMN status SET DEFAULT 'draft';

UPDATE action_tokens SET created_at = NOW() WHERE created_at IS NULL;
UPDATE action_tokens
SET expires_at = created_at + INTERVAL '10 minutes'
WHERE expires_at IS NULL;
UPDATE action_tokens SET status = 'draft' WHERE status IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'action_tokens_status_check'
          AND conrelid = 'action_tokens'::regclass
    ) THEN
        ALTER TABLE action_tokens
            ADD CONSTRAINT action_tokens_status_check
            CHECK (status IN ('draft', 'committed', 'cancelled', 'expired'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_action_tokens_expires_at
    ON action_tokens (expires_at);

COMMIT;
