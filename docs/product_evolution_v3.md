# Finuchet product evolution v3

This document is the canonical product and engineering specification for the five Product Evolution v3 pull requests. It extends the completed v2 cycle without changing its history. It must not contain secrets, production credentials, raw financial text, or personal data.

## Roadmap

### PR 1: Plans 2.0

Finish the existing Plans product before adding recommendations. The scope is information architecture, progressive category management, safe category deletion, complete goal archive lifecycle, deterministic goal preview/edit behavior, and concise Russian product copy.

### PR 2: Custom Home

Introduce a shared Home widget registry, user-controlled Home composition, a workspace-scoped shopping list, and a What's New carousel. Widgets must use existing authenticated workspace state and must not create a second Home data model.

### PR 3: Smart Planning Studio

Add deterministic planning assistance for limits, goals, and grouped budgets. Suggestions may calculate required pace, comfortable pace, and historical context, but existing goals, limits, and grouped budgets remain the source of truth. No parallel "smart" entities are introduced.

### PR 4: Reports 2.0

Build richer reports on the existing Analytics 2.0 period, workspace, category, merchant, and currency contracts. Reports must preserve explicit multi-currency separation and must not invent FX conversion.

### PR 5: Profile & Behaviour Controls

Complete user controls for privacy, Vacation Mode, and category priority/relevance. Behavior settings must have explicit scope, safe defaults, and predictable interaction with notifications and existing personalization.

## Plans 2.0 architecture

Plans keeps four primary destinations: Goals, Limits and Budgets, Reminders, and Categories. Each destination explains why it is useful without turning the screen into onboarding. Existing services remain authoritative; the Mini App does not duplicate calculations or persistence.

Workspace authorization is resolved by the existing Mini App read/write scope. The `all` scope remains read-only for management, and a read-only workspace never renders mutation controls. Amounts keep their stored currencies and no FX conversion is added.

## Category experience

The first category level is a compact name-and-chevron list. Reference counts, source, type, rename, and deletion controls move into one existing-style bottom sheet opened from the row. Back closes the sheet and returns to the same category type list.

Categories are addressed by their canonical normalized token. Successful mutation reloads managed categories and clears a matching global category filter so Analytics, Operations, Home insights, and future requests never keep a stale category selection.

Custom-category ownership follows scope: personal categories require the authenticated owner, while workspace categories are shared inside that workspace and mutations additionally require the existing workspace write role. Plans 2.0 corrects the previously inverted owner predicate that blocked write-authorized workspace members and exposed personal category rows too broadly.

Category identity uses the existing Python `normalized_category_key()` contract: trim, collapse whitespace, lowercase/case-fold, and replace `ё` with `е`. Text-bearing PostgreSQL scans and mutations use one conservative equivalent expression built from `btrim`, `regexp_replace`, `lower`, and `replace`; tables with a canonical `normalized_category_name` are rewritten to the same destination key. No extension or database migration is required.

### Category deletion

- Protected categories cannot be renamed or removed.
- An unused custom category can be removed after explicit confirmation. "Unused" means zero known references in every audited table, not merely zero financial operations.
- A category with operations or other references requires a replacement category.
- Direct removal calls the same strict empty-category archive service. It never deletes limits, grouped-budget membership, reminders, drafts, aliases, or learning state to make the category appear empty.
- In a shared workspace, rename and transfer maintain textual references for every member in that workspace. This includes operations, category limits and their alert state, grouped-budget membership, reminders, operation drafts, workspace-attributed user aliases, subscription patterns, and recurring-spend patterns. The mutation changes only the category identity; it does not expose or edit another member's amounts, schedules, or rule values.
- In a personal workspace, those same references remain owner-scoped even though the personal workspace has a numeric `workspace_id`. Other users' personal rows and every other workspace remain unchanged.
- `user_aliases` rows are migrated across members only when their explicit `workspace_id` proves the scope. Legacy/global `user_aliases` rows are not attributed to a shared workspace by text alone.
- `ml_observations`, legacy `category_aliases`, and `category_feedback` do not have a reliable shared-workspace identity contract. Shared category mutations intentionally leave those user-global learning/history rows untouched instead of rewriting another context by category text. They are not managed-category sources and cannot make an archived category reappear in the category list.
- Transfer performs all reference maintenance and source archival in one database transaction. Financial operations are recategorized but never deleted by the Mini App category removal path.
- If no replacement exists, the UI explains that another category must be created first.
- API errors distinguish protected, referenced, missing-destination, and invalid replacement cases.

## Goal lifecycle

The normal Goals view contains active, paused, and achieved goals. Archiving changes status transactionally, suppresses pending goal notifications through the existing service, and removes the goal from the active response.

`Архив целей` opens a dedicated archive view. An archived goal can be restored to `active`. Permanent deletion is available only from the archived goal detail and requires a separate destructive confirmation.

Permanent deletion locks and verifies the selected owner/workspace goal, suppresses its pending notifications, removes that goal's movement ledger and goal-specific drafts, and deletes the goal. It does not delete or mutate linked or unrelated financial operations. Existing personal-data deletion ownership remains unchanged.

## Goal edit and preview contract

Goal balance is ledger-derived. It is displayed during editing but is not submitted as an editable goal field; users change it through goal contributions/adjustments. Preview and save therefore use the same persisted balance.

Fields that affect planning are submitted explicitly. An empty deadline means no deadline in both preview and save. The preview endpoint returns a hash over the authenticated user, workspace, goal, persisted balance, and submitted planning fields. Saving requires that exact hash. Any subsequent field change invalidates the visible preview and requires recalculation, preventing stale plan math from being saved. Create idempotency remains unchanged.

## Plans copy

- Goals: `Копите на крупную покупку или планируйте закрытие кредита или долга. Укажите сумму и срок и следите за прогрессом.`
- Limits: `Задайте границу расходов для категории или всех трат и следите, насколько быстро она расходуется.`
- Grouped budgets: `Объединяйте несколько категорий в один бюджет и контролируйте их общую сумму.`
- Reminders: `Запланируйте будущий расход или доход, чтобы не забыть о платеже и быстро записать его в операции.`
- Categories: `Настройте структуру доходов и расходов: создавайте, переименовывайте и управляйте своими категориями.`

## Migration decision

PR 1 requires no migration. Existing `financial_goals` statuses, timestamps, `goal_movements`, category storage, and reference tables already support the final lifecycle.

## Custom Home architecture

PR 2 evolves the existing Home instead of adding a second dashboard. The existing authenticated `overview` response remains the aggregate read model for financial result, activity, income and expenses, challenges, goals, limits, reminders, insights, and recent operations. Shopping summary, Home preferences, the canonical widget registry, and eligible announcements are added to that response, so Home does not issue one request per widget.

The canonical registry order is `financial_result`, `activity`, `income_expense`, `whats_new`, `challenges`, `goals`, `limits`, `reminders`, `insights`, `shopping_list`, and `recent_operations`. Registry entries declare stable identity, Russian label/description, default enabled/order metadata, and `compact` or `wide` layout. User preferences are global to the user, not workspace-specific. Reads reconcile saved state by removing unknown/deprecated keys, de-duplicating keys, and appending newly registered keys with their registry defaults. Saving all widgets disabled is valid and renders `Главная настроена минимально` with a configuration action.

The Profile editor keeps an unsaved local draft. Every widget has a toggle, a Pointer Events drag handle, and explicit up/down fallback controls. `Включить все`, `Сбросить`, and `Сохранить` are separate actions. The Home renderer consumes the reconciled order directly and uses flex wrapping: wide widgets use a full row, while compact widgets share a row and grow when a sibling is absent. Disabled or unavailable widgets never reserve cells. Goals and limits are separate views over the existing focus candidates and existing Plans services; no duplicate goal or limit calculations were introduced.

## Shopping list

Shopping items belong to one concrete workspace and are shared with every workspace member. Existing workspace access controls authorize reads; existing write roles authorize create, edit, complete, restore, delete, and confirmed clear-completed operations. `all` and legacy scope without a concrete workspace are read-only/unavailable. Viewer UI contains no mutation controls.

Item text is normalized, rejects control characters, and is limited to 200 characters. Product events record only bounded action/result metadata and never item text. Personal-workspace rows are removed by workspace cascade during account deletion. Non-personal workspace rows remain, while only matching `created_by` and `updated_by` attribution is set to `NULL` before the member row is removed, regardless of whether another active member exists. Other users' actor IDs are preserved.

## What's New

What's New candidates are immutable code-defined release metadata, not remotely supplied HTML. Each candidate has a stable ID, family, release date, Russian title/description, a typed action, and optional plain-text detail copy. Allowed targets are `OPEN_HOME_SETTINGS`, `OPEN_SHOPPING_LIST`, `OPEN_PLANS`, `OPEN_PROFILE`, `OPEN_ANALYTICS`, and `OPEN_DETAIL`; navigation targets map only to existing product surfaces, while `OPEN_DETAIL` opens the existing Bottom Sheet and never accepts HTML, executable content, URLs, or remote copy.

The resolver excludes dismissed candidates and uses an explicit half-open freshness interval: a card is eligible while `0 <= age_in_days < 21`, and expires at age 21. It then keeps the newest candidate in each family, sorts newest first, and returns at most five. Dismissal is user-scoped. The carousel moves only through swipe, keyboard arrows, card/CTA activation, or dots; it never advances automatically. The first release includes Custom Home, Shopping List, and Plans 2.0 cards.

The resolver accepts a candidate collection before applying eligibility and ranking. Reports 2.0 can therefore concatenate user-specific report-ready candidates with the static catalogue and reuse the same dismissal, family replacement, TTL, ordering, and limit policy without changing the carousel.

Product analytics uses the existing outbox and bounded event properties for customization open/save, shopping mutations, and announcement impression/open/dismiss actions. Shopping text, financial amounts, operation descriptions, and arbitrary target data are never included.

Permanent release rule: every future user-visible release must add or deliberately update a code-defined What's New candidate in the same pull request. Reuse a family only when the new card supersedes an older message. Candidate copy must contain no personal data, financial values, secrets, raw user text, or arbitrary navigation URL.

`released_on` is the date a candidate becomes user-visible in production, not the date its code is written. When several unreleased pull requests are deployed together later, the final release pull request must adjust those candidates to the actual production visibility date. Already released old cards are never renewed automatically.

## Smart Planning Studio

PR 3 adds one deterministic, on-demand planning layer in `services/planning.py`. It supports `category_limit`, `general_limit`, `category_budget`, and `goal`; the Mini App API only validates access and input, invokes the service, and serializes structured evidence. Existing goals, category limits, general limits, and grouped category budgets remain the only persisted planning products. Smart Planning stores no snapshots, recommendation prose, or historical calculations, so it adds no personal-data deletion surface and requires no migration.

Every estimate belongs to one concrete workspace and one exact currency. `workspace=all` returns `Выберите пространство для расчёта.` and never aggregates history or controls across workspaces. Shared-workspace history uses the existing shared operation scope. A viewer may inspect an estimate, but `can_apply=false`; all create, update, category-selection, and apply controls continue to follow current workspace write roles. RUB, USD, and other currencies are never added together, and there is no FX conversion. Planning deliberately matches the existing Analytics and Overview compatibility rule for legacy operations: `currency IS NULL` is interpreted as the user's validated profile/default currency. The profile currency is passed separately from the requested calculation currency, so a RUB profile includes legacy NULL operations only in a RUB estimate and excludes them from USD or EUR estimates; explicitly stored currencies remain unchanged.

Spending baselines use the four immediately preceding complete calendar periods. A monthly estimate excludes the current month and uses the four preceding calendar months. A weekly estimate excludes the current Monday-through-Sunday week and uses the four preceding Monday-through-Sunday weeks, matching the current product week definition. This intentionally differs from Analytics month-to-date comparable windows. PostgreSQL performs one bounded aggregate over the date range and returns one row per period; Python never loads full operation history and grouped budgets do not query once per category.

A period is valid only when the selected workspace and currency have at least one operation in that period. Within a covered period, zero target-category spend is a factual zero; a period with no operation coverage is omitted rather than manufactured as zero. Four valid periods produce `good` confidence, two or three produce `limited`, and zero or one produce `insufficient`. The response includes each valid period, operation count, income, expense, net, target amount, arithmetic mean, and confidence. Spending recommendations are available for `good` or `limited` history; `insufficient` history has no suggested amount.

Category limits average expenses for the selected canonical category. General limits average all expenses in the selected currency. Grouped budgets canonicalize and de-duplicate selected categories with the existing `normalized_category_key()` and equivalent SQL expression, sum matching operations once within each complete period, and then average those period totals. The example `15 400 + 17 900 + 12 300 + 21 400` therefore produces exactly `16 750`. Applying a suggestion fills the existing amount field; the user can freely edit it before the normal existing create/update request.

The grouped-budget picker is mobile-first. Available categories have a dedicated Pointer Events drag handle whose `touch-action` is restricted to the handle, leaving WebView scrolling usable. The selected drop zone is visually distinct. Tapping a category is the accessible add/remove fallback, selected chips remove on tap, canonical variants cannot appear twice, and hidden `categories` inputs preserve the existing CategoryBudget save contract.

Historical spend and financial controls are separate. Active same-workspace, same-currency, same-period general limits, category limits, and grouped budgets are inspected only after the objective historical average is calculated. Conflicts can be informational, warning, or blocking, but PR 3 adds no arbitrary blocker. Duplicate category limits, grouped-budget overlaps, and a recommendation above a general limit are shown as context. Limits and budgets are never counted as spending, subtracted from history, or added together as if they were operations. Other workspaces, currencies, periods, disabled controls, and the entity currently being edited are ignored.

Goal Smart Planning extends the existing goal form and does not replace goal preview. Required pace calls the existing deadline/schedule contribution math over target, persisted current balance, deadline, and contribution opportunities. Comfortable pace uses the arithmetic mean of monthly `income - expense` over the four complete valid months, then subtracts only deterministically known monthly commitments from other active goals in the same workspace and currency. The current goal, archived, achieved, paused, deleted, other-workspace, and other-currency goals are excluded. Ordinary limits and grouped budgets are not subtracted because their underlying spending is already present in expenses.

Required monthly pace at or below comfortable monthly pace is `compatible`; required pace above it is `stretched`, with the exact gap returned; fewer than two valid months is `insufficient_history`, while mathematical required pace remains visible. When comfortable pace is positive and the schedule supports deterministic occurrences, the existing contribution schedule math provides an approximate completion date. These states are neutral estimates, not guarantees or financial advice.

Applying a goal recommendation fills the existing `comfortable_amount` draft and explicitly changes the existing strategy to `contribution`, so the next preview uses the established contribution-first calculation. Target, deadline, frequency, schedule, and reminders remain unchanged. Apply clears the current goal preview and preview hash. The user must run the normal preview again, and save still requires the exact authenticated preview hash over the changed planning fields. Recommendations never authorize stale saves and are never saved automatically.

Product events are `smart_planning_opened`, `smart_planning_calculated`, `smart_planning_applied`, and `smart_planning_warning_seen`. Their bounded metadata is limited to planning kind, period kind, history confidence, warning kind, source, and workspace type. No recommendation, history amount, target, category text, merchant text, raw operation text, or identifier is sent.

What's New adds the static `smart-planning-v1` candidate in family `smart-planning`, kind `feature`, targeting `OPEN_PLANS`. Exact copy is `Планируйте суммы по своим данным` and `КопиPaste анализирует прошлые расходы и помогает подобрать лимит, общий бюджет или темп для цели.` Its development `released_on` is intentionally future-dated; the final production release pull request must replace it with the actual first-visibility date without renewing older released cards.

Category priority and system relevance may later affect presentation order, but never historical arithmetic. Vacation Mode may later soften the existing goal reminders and limit/budget alerts; Smart Planning does not create another notification engine or enable notifications implicitly. Reports 2.0, report-ready announcement cards, Privacy page work, ML/LLM forecasting, seasonality, FX, bank integrations, automatic plan mutation, and proactive pushes remain deferred to their designated pull requests.

## PR 2 storage and privacy

Migration `20260811_022_custom_home_shopping_announcements.sql` adds `user_home_preferences`, `shopping_items`, and `user_announcement_state`. It is additive and is not applied by this pull request. Home preferences and announcement dismissals are user-owned deletion data. Shopping content follows workspace ownership, with shared actor attribution anonymized as described above.

## Explicitly deferred

PR 2 does not implement Smart Planning calculations, Reports 2.0, Vacation Mode, category priority, credits as a separate product, FX conversion, new Telegram group behavior, or any production deployment/configuration change.
