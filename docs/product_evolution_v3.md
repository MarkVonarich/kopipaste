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

### Category deletion

- Protected categories cannot be renamed or removed.
- An unused custom category can be removed after explicit confirmation.
- A category with operations or other references requires a replacement category.
- Transfer updates operations, limits, grouped-budget membership, reminders, aliases, learning observations, and active drafts through the existing transactional category service, then archives the old custom category.
- Financial operations are never deleted by the Mini App category removal path.
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

## Explicitly deferred

PR 1 does not implement Home widgets, Home customization, shopping lists, What's New, Smart Planning calculations, Reports 2.0, Vacation Mode, new privacy controls, category priority, credits as a separate product, FX conversion, or new Telegram group behavior.
