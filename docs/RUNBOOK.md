# RUNBOOK

## 1) Deploy merged `main`
```bash
cd /root/bot_finuchet
git switch main
git pull --ff-only origin main

# only if dependencies changed in PR
. .venv/bin/activate
pip install -r requirements.txt

python -m py_compile $(find . -name "*.py" -not -path "./.venv/*")

sudo systemctl restart finuchet
sudo systemctl status finuchet --no-pager -l
sudo journalctl -u finuchet -n 120 --no-pager
```

## 2) Test a PR branch before merge
```bash
cd /root/bot_finuchet
git fetch --all --prune
git switch <branch-name>
git pull --ff-only origin <branch-name>

# if requirements changed
. .venv/bin/activate
pip install -r requirements.txt

sudo systemctl restart finuchet
sudo journalctl -u finuchet -n 120 --no-pager
```

## 3) Health checks / smoke test
```bash
sudo systemctl status finuchet --no-pager -l
sudo journalctl -u finuchet -n 120 --no-pager
```
Manual Telegram smoke test:
1. Send `/start` to bot.
2. Run one basic finance flow (e.g., create a test transaction).
3. Confirm no new errors in logs.

## 4) Rollback
Preferred options:
1. Git rollback to previous stable commit/tag.
2. Service restart with previous revision.
3. If repository contains `tools/rollback.sh`, use it as primary rollback command.
4. If snapshots are used operationally, restore latest known-good snapshot.

Example git rollback:
```bash
cd /root/bot_finuchet
git log --oneline -n 20
# pick previous stable commit
git reset --hard <stable_commit_hash>
sudo systemctl restart finuchet
sudo journalctl -u finuchet -n 120 --no-pager
```

## 5) DB migrations policy
- Schema changes must be delivered as SQL files in `migrations/`.
- Apply migrations explicitly on VPS and verify service logs after restart.
- Avoid destructive migrations without explicit rollback plan.

## 6) Apply DB migrations on VPS (for this PR)
```bash
cd /root/bot_finuchet
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260226_001_action_tokens_pending_op.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260226_002_action_tokens_ttl_cleanup.sql
```


## 7) Troubleshooting migration `action_tokens`
If you see errors like `column "expires_at" does not exist`, re-run migration `001` after pulling latest `main`.

```bash
cd /root/bot_finuchet
git switch main
git pull --ff-only origin main

set -a
. /root/bot_finuchet/.env
set +a

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260226_001_action_tokens_pending_op.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260226_002_action_tokens_ttl_cleanup.sql
```


## 8) Emergency DB fix for `expires_at` errors
If migration 001/002 fails with `column "expires_at" does not exist`, run this hotfix first:

```bash
cd /root/bot_finuchet
git switch main
git pull --ff-only origin main

set -a
. /root/bot_finuchet/.env
set +a

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260226_003_action_tokens_expires_at_hotfix.sql
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/20260226_002_action_tokens_ttl_cleanup.sql
```


## 9) PR #2 UX bug verification (`Новая категория`)
After deploying handler changes, verify with one user flow:
1. Send `DDX 1500`
2. Tap `➖ Расход`
3. Tap `➕ Новая категория`
4. Send `Спорт`
5. Expected: `✅ Записано: ➖ 1500 ₽ • Спорт` (without asking amount again)

Idempotency check:
- tap final save twice rapidly
- expected second response: `✅ Уже записано: ...`
