# Finuchet Bot — Repository Status

## Current baseline
- Repository prepared for clean Git-based workflow.
- Documentation inventory is added (`README.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`).
- Sensitive/runtime files are excluded via `.gitignore`.
- No secrets, database dumps, snapshots, or backup artifacts are tracked.

## Production migration status (May 2026)
- **Current production VPS:** `s66b5610e.fastvps-server.com` (`45.67.128.94`).
- **Project path:** `/root/bot_finuchet`.
- **Systemd service:** `finuchet.service` (enabled + active).
- **ExecStart:** `/root/bot_finuchet/.venv/bin/python -u /root/bot_finuchet/main.py`.
- **PostgreSQL DB:** `finance_bot` (restored from `finance_bot` dump).
- **Telegram API reachability from prod:** `curl https://api.telegram.org/` returns HTTP 302.
- Bot confirms healthy baseline: commands respond and expense writes succeed.

## Previous host status
- Old server `82.146.47.28` (`maximchiranov`) is **not production** anymore.
- `finuchet` on old host is stopped and disabled.
- Previous Telegram API timeout on old server was an **infrastructure/network issue**, not a bot runtime/business-logic defect.

## Reproducibility updates
- `requirements.txt` added/updated to pin runtime dependency list used on production.
- Includes missing packages discovered during migration (`requests`, `dateparser`) plus core runtime libs.
- No secrets were added to repository.

## Delivery workflow
All further changes follow:
1. Create branch (`feature/<short-name>` or `fix/<short-name>`).
2. Open PR to `main`.
3. Review and merge.
4. Pull merged changes on VPS and restart `finuchet.service`.

## Notes
- Keep secret values only in server `.env` files.
- Keep `.env.example` with key names only.
- Do not run old and new servers simultaneously with the same Telegram token.

## Stage: Notifications cleanup + Weekly/Monthly reports v1
- `day_nudge` scheduling is disabled by default (`ENABLE_DAY_NUDGE=false`).
- `evening_reminder` remains active by default (`ENABLE_EVENING_REMINDER=true`).
- `fx_update` remains active.
- Added weekly report job (Monday 12:00 local per user) and monthly report job (day 1, 10:00 local per user).
- Dedup implemented without schema changes via `reminders_log.kind` period keys (`weekly_report:<start>:<end>`, `monthly_report:<start>:<end>`).
