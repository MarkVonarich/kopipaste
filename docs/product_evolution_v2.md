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

- Extend the existing Mini App `/miniapp/api/analytics` endpoint instead of creating a parallel analytics API.
- Keep `/miniapp/api/operations` as the authoritative operation drill-down/detail path, with extra scoped read filters for currency, merchant, and normalized category keys.
- Keep the existing global Mini App context: workspace, period, operation type, and category.
- Add an Analytics-local context only for chart display mode, selected currency, search text, and selected drill-down detail.
- Avoid duplicated authoritative financial calculations in frontend code. The frontend renders backend totals, deltas, shares, and operation scopes.

Acceptance criteria:

- Analytics responses include currency context.
- Mixed-currency totals are grouped or separated unless explicit conversion exists.
- Frontend renders backend-provided values without inventing financial totals.

Implemented architecture:

- `MiniAppAPI.analytics()` returns one compact investigation payload:
  - overview metrics by currency;
  - previous comparable period;
  - category structure;
  - merchant structure using current raw operation descriptions;
  - time dynamics for expenses, income, and financial result;
  - category contribution/change decomposition;
  - optional search results;
  - optional selected category or merchant drill-down;
  - operation scopes for opening the existing Operations screen with the same filters.
- `MiniAppAPI.operations()` accepts read-only scoped filters:
  - `currency`;
  - `merchant`;
  - `category_key`.
- No new schema or migration is required for PR 2 because existing Mini App operation indexes cover workspace/date/type/category/search-oriented query shapes.

Comparable-period rule:

- `current_month` month-to-date compares with previous month-to-date using the same elapsed day count.
- A full selected month compares with the previous full month.
- `previous_month` compares with the month before it.
- Custom and current-week ranges compare with the immediately preceding equal-length range.
- The frontend displays the previous period supplied by the backend and does not calculate comparison windows itself.

Multi-currency behavior:

- Analytics never sums different currencies into one amount.
- Overview, structure, contribution, detail, search, and time dynamics are grouped by currency.
- Analytics 2.0 has one selected analytics currency. The frontend does not maintain separate category, dynamics, and Radar currencies.
- A selected currency limits all chart/detail/search analytics to that currency.
- Stored selected currency is reconciled with each new Analytics response. If a workspace, period, type, or category scope no longer contains that currency, the UI falls back once to the first current currency or clears the selection when the scope has no current data.
- Financial result is computed per currency as `income - expense`.
- If a currency exists only in the comparable period, explicit currency selection can still render comparison data without inventing a current total.

Comparison semantics:

- Backend comparison fields are authoritative; the frontend only formats the signed values it receives.
- Percentage change is signed and uses `delta / abs(previous) * 100`.
- Zero previous totals return explicit zero-baseline or empty-previous states.
- Financial result comparisons that cross zero return `sign_change` with no percentage because a relative percentage would be misleading.

Drill-down hierarchy:

- Analytics overview answers the period-level question.
- Structure can switch between categories and current raw merchants.
- Contribution computes over every canonical category in the selected workspace, period, type, and currency before selecting visible contributors. Reconciliation means the full category delta matches the authoritative total delta; if hidden categories exist, a synthetic "Остальные" row carries the remainder.
- Category drill-down separates full summary metrics from the limited operation preview. Total, operation count, previous total, delta, percentage/state, and merchant shares use the full selected category; the operation preview remains bounded.
- Category merchant breakdown calculates shares against the full selected category total. Top merchants may be followed by a synthetic "Остальные" row.
- Synthetic rollups and presentation fallbacks carry explicit metadata. Synthetic "Остальные" rows and fallback "Без описания" merchant rows are rendered as non-clickable summaries unless a real underlying entity scope is present.
- Merchant drill-down separates full summary metrics from the limited operation preview. Total, operation count, average check, previous total/count, previous average check, and delta use the full selected merchant.
- The "all operations" action opens the existing Operations screen with preserved workspace, custom period, operation type, currency, merchant, and/or normalized category scope. Category drill-down uses the normalized `category_key` as the authoritative scope rather than an exact display label.

Search behavior:

- Analytics search is backend-side and scoped to the authenticated user's accessible workspaces, selected workspace, period, operation type, category, and currency.
- Search covers category, merchant/description, and matching operations without downloading a large frontend dataset.
- Category search results aggregate by canonical category key before display.
- Merchant search results aggregate by current exact merchant text in the selected analytics scope.
- Operation search results are individual operation rows; an operation result never combines `SUM(amount)` with an unrelated `MAX(id)`.
- Search results open existing drill-down or operation-detail paths.

Category normalization rule:

- Category analytics folds semantically equivalent category names through the existing `normalized_category_key()` semantics.
- SQL category-key expressions used by Analytics match the same trim, whitespace-collapse, lowercase, and `ё` to `е` semantics.
- Operation drill-down uses normalized category keys for trim/case/space/`ё`-safe scoped reads.
- Income and expense category calculations remain isolated by operation type.

Primary Analytics UI:

- The screen follows the investigation order: context/currency, overview, search, dynamics, structure, contribution, selected detail, export.
- Legacy Radar may remain in backend payloads for compatibility during the PR, but it is no longer rendered as a primary Analytics section.

Performance decisions:

- Analytics uses grouped SQL queries and folds small result sets in Python.
- No per-category query loop is used for overview, structure, merchant structure, time dynamics, or contribution.
- No cache, Redis, queue, or external analytics API is introduced.
- Existing indexes cover the current read paths; index migrations are deferred until real query plans justify them.

Deferred from PR 2:

- Canonical merchant identity and aliases are deferred to PR 3.
- Weekday/time-of-day behavior analytics is deferred until timezone-safe bucket semantics are designed.
- FX conversion remains intentionally absent.

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

Implemented architecture:

- Merchant Intelligence is a read-time analytical layer over existing `operations.comment`.
- Raw operation descriptions/comments remain untouched. Canonical merchant identity is derived for Analytics only and never overwrites `operations.comment` or category-learning state.
- The reusable service boundary is `services.merchant_intelligence`:
  - `normalize_merchant_key()` derives deterministic keys;
  - `fold_merchant_rows()` groups raw operation rows by merchant identity;
  - `merchant_features()` returns amount, count, average-check, frequency, and share metrics using `Decimal`;
  - `merchant_baseline()` returns a conservative trailing-median personal baseline.
- Mini App Analytics uses `merchant_key` for Merchant Structure, merchant search results, merchant detail, and Operations drilldown. Exact `merchant` filtering remains accepted by `/miniapp/api/operations` for backward compatibility.

Normalization rules:

- Merchant Key V1 is intentionally limited to transformations that Python and PostgreSQL reproduce with the same explicit contract.
- Leading/trailing whitespace is trimmed.
- Internal whitespace is collapsed to one space.
- ASCII uppercase and Russian uppercase letters are translated through an explicit character map to lowercase.
- `ё` is normalized to `е`.
- Any character outside `0-9`, `a-z`, and `а-я` is treated as a separator. This makes safe formatting variants such as `Яндекс Лавка`, `Яндекс-Лавка`, and `Яндекс*Лавка` share one key.
- NFKC, Unicode casefolding, transliteration, and accent folding are not part of Merchant Key V1. Unsupported forms such as full-width Latin letters remain separate exact keys when no supported key characters exist; precomposed accented Latin letters may normalize partially until a future explicit alias feature exists.
- No fuzzy matching, Levenshtein thresholds, embeddings, LLM guessing, global merchant dictionaries, or shared-word merging is used. `lavka` and `Яндекс Лавка` remain different keys unless a future explicit alias system maps them.

Alias scope decision:

- PR 3 introduces deterministic global normalization only.
- It does not persist semantic aliases and does not reuse category `user_aliases`/`global_aliases`, because those tables have category-learning semantics.
- Future user/workspace-confirmed merchant aliases should use separate merchant-specific state and must not silently affect unrelated users or inaccessible workspaces.

Canonical display behavior:

- Display labels are chosen deterministically from the raw variants inside the authorized current scope.
- The selector prefers cleaner raw forms over all-uppercase or punctuation-heavy forms, then uses usage/amount/name ordering for deterministic ties.
- Merchant Structure, merchant search, and merchant detail use the same display-name selection semantics.
- Raw aliases are a separate explainability field and preserve the actual trimmed source forms, such as `Яндекс Лавка`, `ЯНДЕКС*ЛАВКА`, and `Яндекс-Лавка`. They are deduplicated exactly and bounded for display.

Empty merchant semantics:

- Missing comments remain a fallback identity with key `__empty_merchant__`.
- The fallback displays as `Без описания` but is not a real merchant and is non-drillable.
- A real raw merchant named `Без описания` normalizes to `без описания` and remains distinct from the fallback key.
- Synthetic `Остальные` rows keep reserved synthetic keys and remain non-drillable.

Merchant features:

- Merchant detail exposes backend-calculated:
  - current total;
  - operation count;
  - average check;
  - comparable previous total/count/average check;
  - amount delta and percentage when valid;
  - frequency delta and percentage when previous count is non-zero;
  - average-check delta and percentage when previous average is non-zero;
  - share of the full selected category denominator;
  - share of the full selected type/currency scope.
- All monetary math uses `Decimal`.
- Multi-currency amounts are never combined; merchant aggregates remain currency-scoped.
- Custom categories are supported because category denominators use existing stored category values and normalized category keys.

Baseline method:

- V1 baseline uses trailing completed comparable periods with a median statistic.
- Current month/month-to-date compares the same month position in prior months, for example Aug 1-10 to Jul 1-10, Jun 1-10, and May 1-10.
- Full month and `previous_month` scopes compare full prior months.
- Current week/week-to-date compares the same weekday progress in previous weeks.
- Custom periods use immediately preceding non-overlapping equal-length windows.
- Minimum threshold is 3 prior non-empty comparable periods and at least 3 merchant observations.
- Insufficient history returns `sufficient_data=false` and must not be presented as "usually" behavior.

Time and weekday decision:

- Time-of-day insights are deferred. The existing model has `op_date` as the transaction date selected/recorded by the user and `created_at` as recording time; `created_at` must not be treated as purchase occurrence time.
- Weekday analysis may use `op_date` in a later phase because it represents the operation date, not the entry timestamp.

Storage and migration decision:

- No migration is required for PR 3.
- Merchant keys are derived at read time from existing comments. No merchant key is backfilled into historical operations.
- Functional indexes are deferred until production-like query plans show the expression filter needs one.

Performance decisions:

- Merchant Structure folds grouped raw-comment SQL rows in Python; it does not query per merchant.
- Merchant detail uses scoped grouped SQL for full-denominator category and total shares.
- Operations drilldown filters by a SQL expression over `operations.comment`, while authorization still comes from authenticated Mini App user plus existing workspace filters.
- Search groups canonical merchant results before applying the final merchant result limit, so search aggregate totals reconcile with merchant detail. Operation search remains individual rows.

Reusable for PR 4:

- PR 4 Insight Engine can consume `services.merchant_intelligence` directly for deterministic merchant keys, feature math, raw-alias explanation, and baseline semantics without parsing Mini App JSON.

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
