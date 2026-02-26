-- Finuchet: Subtask 1.1 (pending_op / action_tokens + idempotency)
-- PostgreSQL migration

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
