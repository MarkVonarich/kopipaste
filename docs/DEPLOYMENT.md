# Deployment

Run from MobaXterm on the server, not from the Codex sandbox.

```bash
cd /root/bot_finuchet
git status --short --branch
MIGRATIONS_TO_APPLY="migrations/20260719_009_pre_miniapp_foundation.sql" ./scripts/deploy_check_restart.sh
```

The script:

- checks branch/status;
- refuses unresolved merge conflicts;
- runs whitespace, compile, import, and tests;
- shows migration files;
- applies only migrations explicitly listed in `MIGRATIONS_TO_APPLY` after you type `APPLY`;
- restarts `finuchet` only after you type `RESTART`;
- prints service status and recent logs.

Do not run with a dirty worktree unless you intentionally want to deploy those local changes.
