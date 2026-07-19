# Rollback

## Code Rollback

From MobaXterm:

```bash
cd /root/bot_finuchet
git status --short --branch
git log --oneline -5
git switch main
sudo systemctl restart finuchet
sudo systemctl status finuchet --no-pager -l
```

If you deployed from a different branch, switch back to the last known good branch or commit instead of `main`.

## Database Rollback Guidance

Migration `migrations/20260719_009_pre_miniapp_foundation.sql` is additive. Prefer leaving added columns/tables in place and rolling code back first.

Only after a verified database backup, newly added tables can be dropped in reverse dependency order:

```sql
DROP TABLE IF EXISTS public.user_achievements;
DROP TABLE IF EXISTS public.achievement_definitions;
DROP TABLE IF EXISTS public.notification_events;
DROP TABLE IF EXISTS public.notification_preferences;
DROP TABLE IF EXISTS public.financial_activity_events;
DROP TABLE IF EXISTS public.custom_categories;
DROP TABLE IF EXISTS public.operation_drafts;
DROP TABLE IF EXISTS public.user_workspace_settings;
DROP TABLE IF EXISTS public.workspace_invitations;
DROP TABLE IF EXISTS public.workspace_members;
DROP TABLE IF EXISTS public.workspaces;
```

Do not drop legacy `user_id` or `chat_id` columns.
