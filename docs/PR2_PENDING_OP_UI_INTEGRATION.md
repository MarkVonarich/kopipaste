# PR #2 — pending_op integration in UI/handlers (implementation contract)

> Current repository snapshot does not include runtime bot source files (`main.py`/handlers modules), so this document provides an exact integration contract for the production codebase at `/root/bot_finuchet`.

## Goal
Fix UX bug: after `➕ Новая категория`, user text (e.g. `Спорт`) must be treated as **category name**, not as new expense text requiring amount.

Expected flow:
1. `DDX 1500`
2. `➖ Расход`
3. `➕ Новая категория`
4. user sends `Спорт`
5. bot saves using same token: `raw_text=DDX`, `amount=1500`, `type=expense`, `category=Спорт`
6. response: `✅ Записано: ➖ 1500 ₽ • Спорт`

## State machine requirements
Use one `token_id` for the entire flow (`action_tokens`).

Required in-memory/UI state per user/chat:
- `mode = awaiting_category_name` after pressing `➕ Новая категория`
- `token_id` of current draft
- `message_id` of editable wizard message

### On free-text parser entrypoint
If `mode == awaiting_category_name`:
- bypass amount parser
- validate category text (non-empty, <= reasonable length)
- update `action_tokens.category_name`
- move to confirmation/commit step for same token

If `mode != awaiting_category_name`:
- run existing amount parser (`DDX 1500`)

## DB write sequence (idempotent)
On final confirm/callback `save:{token_id}`:

1. `INSERT INTO committed_token_events(token_id, user_id, chat_id) ...`
2. if unique violation:
   - do **not** insert operation again
   - reply `✅ Уже записано: ...`
   - return
3. insert operation row into transactions table
4. `UPDATE action_tokens SET status='committed', committed_at=NOW() WHERE token_id=:token_id`

## Minimal callback map
- `type:expense:{token_id}` / `type:income:{token_id}`
- `category:new:{token_id}` -> sets `mode=awaiting_category_name`
- `category:set:{token_id}:{category_id}`
- `save:{token_id}`
- `cancel:{token_id}`

All callbacks must load and mutate the **same token**.

## UI constraints
- Prefer editing previous bot message (`edit_message_text`) instead of sending new spam messages.
- Keep text short.
- For duplicate save tap show only: `✅ Уже записано: ...`

## Logging
On DB errors log at ERROR with context:
- `token_id`
- `user_id`
- callback/action name

## Quick SQL checks on VPS
```sql
-- last draft/committed tokens for user
SELECT token_id, user_id, chat_id, status, amount_minor, op_type, category_name, raw_text, created_at, committed_at
FROM action_tokens
WHERE user_id = :USER_ID
ORDER BY created_at DESC
LIMIT 20;

-- idempotency events
SELECT token_id, user_id, chat_id, committed_at
FROM committed_token_events
WHERE user_id = :USER_ID
ORDER BY committed_at DESC
LIMIT 20;
```
