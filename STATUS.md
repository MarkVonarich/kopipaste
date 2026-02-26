# Finuchet Bot — Repository Status

## Current baseline
- Repository prepared for clean Git-based workflow.
- Documentation inventory is added (`README.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`).
- Sensitive/runtime files are excluded via `.gitignore`.
- No secrets, database dumps, snapshots, or backup artifacts are tracked.

## Delivery workflow
All further changes follow:
1. Create branch (`feature/<short-name>` or `fix/<short-name>`).
2. Open PR to `main`.
3. Review and merge.
4. Pull merged changes on VPS and restart `finuchet.service`.

## Deployment target
- Server: Ubuntu VPS
- App path: `/root/bot_finuchet`
- Service: `finuchet.service`
- DB: PostgreSQL

## Notes
- Keep secret values only in server `.env` files.
- Keep `.env.example` with key names only.


## Current DB work
- Added migration baseline for subtask 1.1: action tokens/pending operation and TTL cleanup.
- Files: `migrations/20260226_001_action_tokens_pending_op.sql`, `migrations/20260226_002_action_tokens_ttl_cleanup.sql`.

- Added emergency migration for schema drift: `migrations/20260226_003_action_tokens_expires_at_hotfix.sql`.

- Added PR#2 integration contract doc for pending_op UI/handlers flow: `docs/PR2_PENDING_OP_UI_INTEGRATION.md`.
