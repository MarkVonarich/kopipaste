-- Finuchet: Subtask 1.1 (pending_op / action_tokens + idempotency)
-- PostgreSQL migration (compatible with pre-existing partial action_tokens table)

BEGIN;

CREATE TABLE IF NOT EXISTS action_tokens (
    token_id         UUID PRIMARY KEY,
    user_id          BIGINT NOT NULL,
    chat_id          BIGINT NOT NULL,
    op_type          TEXT,
    amount_minor     BIGINT,
    currency_code    TEXT NOT NULL DEFAULT 'RUB',
    category_name    TEXT,
    op_at            TIMESTAMPTZ,
    raw_text         TEXT,
    norm_text        TEXT,
    status           TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'committed', 'cancelled', 'expired')),
    message_id       BIGINT,
    committed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '10 minutes')
);

-- Compatibility patch for already existing action_tokens tables.
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS token_id UUID;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS user_id BIGINT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS chat_id BIGINT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS op_type TEXT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS amount_minor BIGINT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS currency_code TEXT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS category_name TEXT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS op_at TIMESTAMPTZ;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS raw_text TEXT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS norm_text TEXT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS status TEXT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS message_id BIGINT;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS committed_at TIMESTAMPTZ;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE action_tokens ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;

ALTER TABLE action_tokens ALTER COLUMN currency_code SET DEFAULT 'RUB';
ALTER TABLE action_tokens ALTER COLUMN status SET DEFAULT 'draft';
ALTER TABLE action_tokens ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE action_tokens ALTER COLUMN expires_at SET DEFAULT (NOW() + INTERVAL '10 minutes');

UPDATE action_tokens
SET currency_code = 'RUB'
WHERE currency_code IS NULL;

UPDATE action_tokens
SET status = 'draft'
WHERE status IS NULL;

UPDATE action_tokens
SET created_at = NOW()
WHERE created_at IS NULL;

UPDATE action_tokens
SET expires_at = created_at + INTERVAL '10 minutes'
WHERE expires_at IS NULL;

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

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'action_tokens_pkey'
          AND conrelid = 'action_tokens'::regclass
    ) AND NOT EXISTS (
        SELECT 1
        FROM action_tokens
        WHERE token_id IS NULL
    ) THEN
        ALTER TABLE action_tokens ADD PRIMARY KEY (token_id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_action_tokens_user_created_at
    ON action_tokens (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_action_tokens_status
    ON action_tokens (status);

CREATE INDEX IF NOT EXISTS idx_action_tokens_expires_at
    ON action_tokens (expires_at);

-- Fast lookup for pending draft by chat/user
CREATE INDEX IF NOT EXISTS idx_action_tokens_chat_user_draft
    ON action_tokens (chat_id, user_id, created_at DESC)
    WHERE status = 'draft';

-- Optional helper table for idempotent commit audit (1 commit per token)
CREATE TABLE IF NOT EXISTS committed_token_events (
    token_id      UUID PRIMARY KEY,
    user_id       BIGINT NOT NULL,
    chat_id       BIGINT NOT NULL,
    committed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMIT;
