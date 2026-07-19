# Pre-Mini-App Architecture

## Current Goal

Bring Finuchet to a backend-ready state for a future Telegram Mini App without building the Mini App frontend.

## Main Decisions

- Keep Telegram as the first interface.
- Preserve existing `user_id` and `chat_id` fields for backward compatibility.
- Add workspaces with additive migrations only.
- Use a canonical operation service for text, voice, OCR, reminder, import, Mini App, and API writes.
- Track financial activity by successful record creation time, not only by operation date.
- Group analytics by currency unless a real conversion layer is explicitly used.
- Keep notification rules deterministic and explainable.
- Implement achievement rewards as safe no-op hooks until an entitlement system exists.

## Workspace Foundation

Tables:

- `workspaces`
- `workspace_members`
- `workspace_invitations`
- `user_workspace_settings`
- `operation_drafts`

Roles:

- owner;
- admin;
- member;
- viewer.

Private chat defaults to the user's personal workspace. Group chats need a configured group workspace before full isolation is active.

## Operation Foundation

`services.operations.record_financial_operation` is the canonical write boundary. It preserves the legacy insert path and adds workspace/source/activity metadata after the migration is applied.

## Notifications

`services.notifications` creates deterministic notification candidates and deduplicated events. The legacy evening reminder now uses successful created-today activity, so voice and OCR recordings suppress inactivity nudges.

## Analytics

`services.analytics` now exposes JSON-serializable dashboard, time-series, and category contracts for future Mini App screens.

## Global Foundation

`services.i18n` provides RU/EN translation and money formatting helpers. Existing Russian behavior remains the default.

## Remaining Work

- Apply migration in production through the deployment script.
- Complete group workspace setup UX.
- Move all edit/delete permission checks to workspace-aware services.
- Expand automated tests around database-backed flows.
- Add Mini App init data verification when the Mini App backend endpoint exists.
