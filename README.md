# Finuchet Bot

Telegram finance bot (Finuchet) with production deployment on Ubuntu VPS and process management via `systemd`.

The repository includes the Python Telegram bot, PostgreSQL domain services, the authenticated Mini App API in `miniapp/`, and the Vite/TypeScript Mini App in `frontend/`.

## What the bot does
- Handles Telegram commands and user interactions for personal finance flows.
- Persists business data in PostgreSQL.
- Runs as long-lived service `finuchet.service` on VPS.
- Shares operations, categories, goals, limits and reminders between chat and Mini App.
- Supports manual cashflow scenarios and category presentation; see `docs/CASHFLOW_AND_CATEGORY_STARTER.md` for scope and local acceptance checks.

## Production runtime
- Server path: `/root/bot_finuchet`
- Service: `finuchet.service`
- DB: PostgreSQL
- Secrets location (server only): `/root/bot_finuchet/.env` and/or `/root/.env`

## Deploy workflow (branch -> PR -> merge)
1. Create branch: `feature/<short-name>` or `fix/<short-name>`.
2. Commit and open PR to `main`.
3. Merge PR.
4. Pull on VPS and restart service.

After merge to `main`:

```bash
cd /root/bot_finuchet
git switch main
git pull --ff-only origin main

# if requirements changed:
. .venv/bin/activate
pip install -r requirements.txt

python -m py_compile $(find . -name "*.py" -not -path "./.venv/*")

sudo systemctl restart finuchet
sudo systemctl status finuchet --no-pager -l
sudo journalctl -u finuchet -n 120 --no-pager
```

## Configuration
- Keep only variable names in `.env.example`.
- Never commit `.env` or any secret values.
- If a new variable is required, add key name to `.env.example` and set value directly on server.

## Docs map
- `docs/ARCHITECTURE.md` — project structure and data flow.
- `docs/RUNBOOK.md` — operations: deploy, logs, smoke test, rollback.
- `STATUS.md` / `STATE.yml` — current repository/runtime state.
