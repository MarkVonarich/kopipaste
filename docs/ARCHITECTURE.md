# ARCHITECTURE

## Current repository inventory (fact-based)
At the moment of this inventory PR, this repository snapshot contains process/documentation baseline and no checked-in application Python modules.

Tracked items include:
- repo hygiene files (`.gitignore`)
- project state files (`STATE.yml`, `STATUS.md`)
- operational documentation (`README.md`, `docs/*`)

## Runtime architecture (production)
- Telegram bot process runs under `systemd` unit `finuchet.service`.
- Application is deployed in `/root/bot_finuchet`.
- Data layer is PostgreSQL (persistent business data is stored in DB, not in git).
- Configuration/secrets are loaded from `.env` on the server.

## Expected code architecture (for subsequent PRs)
When source code is present, document and keep updated:
1. Entry point module (bot bootstrap/startup).
2. Handlers/routers split by domain features.
3. Database access layer (queries/repositories/ORM).
4. Cache/session layer (if used).
5. Scheduled/background jobs.

Any structural change must be mirrored in this file and in `STATE.yml`/`STATUS.md`.


## Stage 1.3 ML v1 suggestions (PR1)
- Input `<text> <amount>` defaults to expense and opens 2x2 category suggestion screen.
- Buttons: `✅ cat1`, `✅ cat2`, `🗂 Другая категория`, `↔️ Это доход`.
- `↔️ Это доход` toggles type (`Расходы` <-> `Доходы`) and redraws same message.
- Suggestions source: `global_aliases` by popularity for selected op type; fallback: `Продукты`, `Другое`.
