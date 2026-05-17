# Subtask 1.1 — pending_op / action_tokens / idempotency

## Scope delivered in this PR
Because the current repository snapshot does not include runtime bot source files (`main.py`, handlers, DB adapter code), this PR adds the **database foundation** for subtask 1.1:

1. `migrations/20260226_001_action_tokens_pending_op.sql`
   - `action_tokens` table with 10-minute TTL (`expires_at`)
   - minimal `pending_op` fields: `user_id`, `chat_id`, `op_type`, `amount_minor`, `op_at`, `raw_text`, `norm_text`, `token_id`, `status`
   - indexes for draft lookup and cleanup
   - `committed_token_events` table with `PRIMARY KEY(token_id)` for idempotent commit guard

2. `migrations/20260226_002_action_tokens_ttl_cleanup.sql`
   - cleanup query for expired tokens

## Runtime integration checklist (to implement in next code PR where bot code exists)
- On free-text parse (`"кофе 320"`), create one `action_tokens` row with `status='draft'` and reuse `token_id` through all callbacks.
- UI callbacks must carry `token_id`.
- On final "✅ Записать":
  1. Insert into `committed_token_events(token_id, ...)` first.
  2. If unique violation -> respond `✅ Уже записано: ...` and return.
  3. Otherwise insert operation and update `action_tokens.status='committed'`.
- Run TTL cleanup by scheduler/cron using `20260226_002_action_tokens_ttl_cleanup.sql`.

## Migration apply commands (VPS)
```bash
cd /root/bot_finuchet
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260226_001_action_tokens_pending_op.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260226_002_action_tokens_ttl_cleanup.sql
```
