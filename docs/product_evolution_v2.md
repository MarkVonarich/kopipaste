# Finuchet product evolution v2

This document is the canonical specification for the next four Finuchet / КопиPaste product phases. It is intentionally scoped to product and engineering decisions; it must not contain secrets, tokens, production credentials, raw financial text, or personal data.

## Product goals

- Keep the Telegram bot pleasant and safe in private chats, groups, and the Mini App.
- Reduce accidental financial parsing in group conversations.
- Build analytics, merchant intelligence, and insights on top of the existing workspace, operation, category, budget, goal, reminder, and Mini App architecture.
- Preserve multi-currency correctness: never sum different currencies unless a trusted explicit conversion layer exists and the user selected conversion.
- Prefer deterministic logic and existing services before introducing new AI behavior.

## Phase plan

### PR 1: Group Chat / Workspace UX & Safety

Goal: group and supergroup chats are silent by default. The bot processes ordinary group messages only when explicit bot intent can be established reliably.

Supported explicit intent:

- Telegram mention entity that points to the bot username.
- Telegram text mention entity that points to the bot user id.
- Reply to a message sent by the bot.
- Bot commands registered through command handlers, such as `/start`, `/help`, `/settings`, `/app`, and existing explicit commands.
- Callback and button interactions from bot UI.

Implementation rules:

- Inspect Telegram entities for mention detection; do not rely on substring matching.
- Fail closed when intent cannot be established.
- Preserve natural private-chat input such as `лавка 726`, `зарплата 120000`, and `такси 890`.
- Preserve group workspace resolution, membership checks, and actor attribution when an intentional group operation is recorded.
- Keep media and location handlers silent in groups unless explicit intent is present.

Mini App group limitation:

- The current safe `/app` flow opens the Mini App with signed Telegram user authentication only.
- Telegram does not provide a trustworthy arbitrary group workspace context through a plain button URL that the backend can accept without membership validation.
- The Mini App must not trust client-supplied workspace ids or deep-link parameters. Every requested workspace must be validated against the authenticated user's accessible workspaces and write roles on the server.
- A future Telegram-supported launch path may preselect a group workspace, but it must still use server-side membership validation.

Acceptance criteria:

- Random group text, text with numbers, and financial-looking text without explicit bot intent are ignored without replies.
- Correct bot mention and replies to the bot are processed.
- Mentions of other users or replies to other users are ignored.
- Commands and callbacks remain available in groups.
- Private-chat financial input continues to work.
- Mini App workspace/deep-link forgery is rejected.

### PR 2: Analytics 2.0

Goal: provide richer analytics while reusing existing backend calculations, workspace and period state, Mini App API conventions, and frontend components.

Scope:

- Add analytics endpoints and UI only where the backend can compute deterministic, currency-correct values.
- Keep drill-down scoped by workspace, period, category, and currency.
- Avoid duplicated financial calculations between frontend and backend.
- Preserve current filters and existing operation services.

Acceptance criteria:

- Analytics responses include currency context.
- Mixed-currency totals are grouped or separated unless explicit conversion exists.
- Frontend renders backend-provided values without inventing financial totals.

### PR 3: Merchant Intelligence Foundation

Goal: introduce a durable foundation for merchant normalization and merchant-level views without disrupting operation recording.

Scope:

- Reuse existing operation data and category logic.
- Add deterministic merchant grouping and aliases where needed.
- Keep user/workspace boundaries intact.
- Avoid speculative LLM merchant enrichment unless explicitly introduced in a later phase.

Acceptance criteria:

- Merchant views are scoped to accessible workspaces.
- Merchant aggregates preserve currency.
- Existing operation create/edit/delete flows remain compatible.

### PR 4: Insight Engine v1

Goal: surface useful deterministic insights from the user's own data after analytics and merchant foundations are in place.

Scope:

- Generate insights from existing calculations, budgets, limits, goals, reminders, merchant groups, and notification preferences.
- Avoid raw financial text in logs or analytics events.
- Respect quiet hours and notification preferences for any insight delivery.

Acceptance criteria:

- Insights are explainable, deterministic, and scoped by workspace and currency.
- No insight combines currencies without explicit conversion.
- Users can understand the source period and scope of each insight.

## Shared architecture decisions

- Extend existing services and repositories instead of introducing parallel data models.
- Reuse Mini App authentication, workspace scope resolution, rate limiting, idempotency, and deployment templates.
- Keep Telegram bot command handlers, callback handlers, and free-text handlers separated by intent.
- Prefer small additive migrations only when a phase truly needs durable state.
- Tests must be deterministic, isolated, and must not depend on production data.

## Dependencies

- PR 1 must land before later phases depend on group/Mini App workspace safety assumptions.
- PR 2 should build on existing Mini App API and workspace filters.
- PR 3 should build on the analytics period/workspace/currency model from PR 2.
- PR 4 should build on deterministic analytics and merchant foundations from PR 2 and PR 3.

## Deferred functionality

- No production deploys, migrations, DNS, nginx, certificate, systemd, or BotFather changes are part of these PRs unless explicitly requested after merge.
- No historical data export or external analytics API is required for financial calculations.
- No automatic FX conversion is introduced in this plan.
- No unsupported Telegram group Mini App context is faked through trusted client parameters.
