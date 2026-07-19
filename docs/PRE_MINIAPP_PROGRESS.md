# Pre-Mini-App Progress

Last updated: 2026-07-19 UTC

## Completed

- Baseline checks run on branch start:
  - `git status --short --branch`;
  - `git diff`;
  - `git diff --check`;
  - compile of project source directories.
- Confirmed existing reminder baseline commit:
  - `c949abf Fix reminder wizard and persistence`.
- Created branch:
  - `codex/pre-miniapp-readiness`.
- Phase 1 command/menu/help stabilization:
  - commit `97f33d2 Stabilize public menu and command protections`;
  - public command registration now exposes `/start`, `/settings`, `/help`;
  - admin diagnostics are scoped to `ADMIN_USER_IDS`;
  - `/help` exists and `/about` routes to help;
  - main menu exposes Add operation, Budgets and limits, Reminders, Export, Settings, Help.
- Restored missing `cleanup_action_tokens` helper used by `jobs.tokens_cleanup`.

## In Progress In This Working Tree

- Additive workspace/notification/activity/achievement migration:
  - `migrations/20260719_009_pre_miniapp_foundation.sql`.
- Backend services:
  - `services/workspaces.py`;
  - `services/operations.py`;
  - `services/activity.py`;
  - `services/notifications.py`;
  - `services/achievements.py`;
  - `services/categories.py`;
  - `services/i18n.py`.
- Canonical operation integration for text, voice, OCR, and reminder recording paths.
- Export period expansion.
- Deployment and architecture documentation.

## Pending

- Run final compile/import/pytest checks for the full working tree.
- Commit backend foundation phase.
- Apply migrations only from MobaXterm after explicit confirmation.
- Complete full group workspace setup UX.
- Add deeper database-backed tests for workspace isolation, reminders, notifications, and achievements.
- Push branch only if requested.

## Migrations Not Applied

- `migrations/20260719_009_pre_miniapp_foundation.sql`
  - Purpose: workspaces, workspace members/invitations/settings, operation drafts, custom categories, financial activity events, notification preferences/events, achievement tables, and additive workspace/source/currency columns.
  - Applied: no.
  - Rollback: prefer code rollback first; see `docs/ROLLBACK.md`.

## Resume Command

```bash
cd /root/bot_finuchet
git status --short --branch
/root/bot_finuchet/.venv/bin/python -m compileall main.py routers db services jobs utils ui ai
/root/bot_finuchet/.venv/bin/python -c "import main; import routers.commands; import routers.callbacks; import routers.messages; import db.database; import db.queries; import jobs.daily; import jobs.scheduler; import jobs.tokens_cleanup"
```
