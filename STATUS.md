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
