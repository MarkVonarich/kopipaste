# Forecast Intelligence

This document is the canonical architecture and operations contract for Advanced Forecasting & Home Intelligence.

## Spendable Contract

`Свободно` estimates what may be spent inside one selected period. It is not a bank balance and is always presented as approximate.

```text
spendable = max(0, min(R - H - G - RiskForecast(V), B))
```

- `R`: realized income minus realized expense in the selected period.
- `H`: unpaid known future expense commitments due after the local `as_of` date and no later than period end.
- `G`: deterministic active-goal contributions scheduled inside the same horizon.
- `V`: ordinary future variable spend; the downside-aware reserve uses the selected/calibrated q80 estimate.
- `B`: projected enabled general-budget headroom after realized budget spend, future commitments and q80 future variable spend, or unbounded when no applicable budget exists.

Expected future income is returned as a separate explanation scenario. It never increases the primary Spendable amount. Category limits and grouped budgets do not reserve cash globally; they constrain only a category-aware can-spend request.

Every forecast is scoped to authenticated user, one concrete workspace, one currency, one period, and one user-local `as_of` date. Workspaces and currencies are never combined and no FX conversion occurs. Legacy `currency IS NULL` operations belong only to the validated profile/default currency, matching Analytics compatibility behavior. Aggregate workspace, completed-period, and future-only requests return explicit unavailable states instead of a false zero.

## Deterministic Inputs

Commitments reuse active future expense reminders, confirmed/detected subscriptions, and sufficiently confident recurring-spend patterns. Scope, currency, due-date and recorded-reminder filters are applied in SQL. Date, currency and amount alone never prove duplication. Cross-source items collapse only when they also share a compatible privacy-safe identity hash; explicit reminders remain independent. Expected income reminders are separated.

The decomposition is canonical: `future total expense = deterministic commitments + ordinary variable expense`. Reminder-created operations, goal-linked contribution operations, exact canonical subscription/recurring pattern matches, and exact forecast-time commitment-fact matches are deterministic and excluded from `V`. Historical remainders aggregate only the remaining variable operations. Snapshot outcomes use the facts persisted with that prediction rather than reconstructing commitments from mutable current state. No current commitment total is subtracted from an unrelated historical distribution.

Spending coverage is expense-side evidence only. Real expense operations, including deterministic expenses, prove an expense-tracked day. The existing explicit `type='noop'` / `Без операций` marker proves a tracked no-expense day. Income-only dates, app activity and unrelated product events contribute no spending coverage, never dilute variable daily pace, and cannot validate a zero target. The same rule applies to current inputs, historical input/target coverage and finalized outcomes.

Subscription and recurring-pattern eligibility is identical on both sides of the decomposition. A subscription must be detected/confirmed with confidence at least 0.60; a recurring pattern must be detected with confidence at least 0.70. Scope and canonical currency must also match. Low-confidence, suppressed, inactive, subscription-status recurring, hidden or dismissed patterns remain in `V` when they cannot enter `H`.

Goal reserve reuses the existing goal schedule engine. Only active same-workspace/same-currency goals with deterministic occurrences inside the horizon contribute. Paused, achieved, archived, deleted or unscheduled goals do not.

The general budget is an independent future constraint. Current headroom is `budget - realized budget spend`; projected headroom additionally reserves `H` and q80 `V`. Those reserves still occur only once on the resource side, but independently consume future budget capacity. Planned goal contributions are not subtracted from general-budget headroom because the existing goal schedule does not itself create a budget expense operation. Existing category limits and category budget groups remain authoritative for can-spend constraints. Their values are not subtracted as expenses.

## Model Families

The permanent benchmark is the robust historical remaining-spend model, using comparable complete periods and empirical q50/q80/q90. Missing periods are not inserted as zero; valid tracked periods with genuine zero future spend remain evidence.

The seasonal candidate uses horizon, cycle position and weekday proximity. Deterministic bootstrap scenarios provide an empirical downside distribution. Lightweight personal robust/seasonal candidates are compared with rolling-origin results at inference and blended with bootstrap downside estimates. Calibration uses only rolling out-of-sample residuals.

The pooled `HistGradientBoostingRegressor(loss="quantile")` adapter is trained offline from bounded snapshot aggregates for one exact currency at a time. RUB, USD, EUR and other monetary datasets never mix; there is no FX or cross-currency normalization path. It never uses user ID, raw transaction text, comments, names, usernames, Telegram auth data or secrets as features. A currency-specific artifact may become eligible only after offline rolling-origin validation, registry publication, and deployment through the trusted model directory.

All adapters return q50, q80 and q90 and deterministically repair ordering so `q50 <= q80 <= q90`. Risk policy `downside-q80-v1`, model versions and feature schema `forecast-features-v1` are persisted.

## Backtesting And Calibration

Backtesting uses rolling forecasting origin, never random train/test splitting. Origins are grouped by date: a fold trains only on snapshots with strictly earlier `as_of` dates and predicts every snapshot at the current origin before any same-origin row may become history. Stored metrics belong to one exact currency and include MAE, MASE, q50/q80/q90 pinball loss, q80/q90 empirical coverage, interval width and downside breach rate.

The robust family is mandatory in champion selection. A challenger with better average error but a materially worse breach rate is ineligible. Calibration consumes only out-of-sample predictions and actual outcomes. Without enough residuals the state remains `insufficient`, and the UI uses qualitative quality labels. It never presents invented confidence percentages.

Model artifacts are trusted local joblib files only. The loader resolves paths beneath an explicitly configured model directory, verifies SHA-256 before deserialization, and validates feature-schema metadata. Versions and paths include a deterministic currency/dataset fingerprint. Training verifies a temporary artifact, atomically publishes it through a no-overwrite link to a new immutable path, verifies the final checksum, and only then mutates registry state. An identical artifact may be reused only when its checksum matches; a collision fails without touching the existing file. Failed promotion cannot damage the previous champion artifact. Model binaries are not committed.

## Snapshot Lifecycle

Migration `20260813_024_advanced_forecasting_home.sql` adds:

- `forecast_snapshots`: bounded aggregate features, exact scope, source fingerprint, nullable finalized outcomes and explicit target-validity evidence;
- `forecast_predictions`: selected version, monotonic quantiles, reserves, Spendable, risk state and reason codes;
- `forecast_model_registry`: currency-specific candidate/challenger/champion metadata, checksum, cutoff, metrics and calibration, with at most one champion per exact currency;
- `forecast_feedback`: one useful/not-useful decision per user/workspace/fingerprint;
- additive category-limit `display_name` and `alerts_enabled` columns.

Overview calculation writes an idempotent current snapshot. Forecast-time snapshots also preserve privacy-safe commitment/goal facts, canonical legacy-default currency, and timezone. Input-side variable count, expense count, expense-tracked days, trusted noop-marker days, coverage, and variable pace stop at historical `as_of`; target validity is evaluated separately under `target-coverage-v1` and never enters model features. A target needs at least 35% expense-side tracked-day coverage. A genuine zero is valid only when deterministic/other real expense activity or explicit noop markers show expense tracking continued; income-only or empty horizons and one isolated operation in a long horizon remain unknown. Invalid targets remain finalized for audit but are excluded from training, backtesting and calibration.

The scheduler finalizes at most 200 candidates per run with `FOR UPDATE SKIP LOCKED` and checks the user-local date before writing outcomes. NULL-currency operations and deterministic-pattern matching use only the snapshot/profile canonical default currency. `forecast_backfill.py` is dry-run by default. `forecast_backtest.py` and `forecast_train.py --synthetic` provide safe synthetic validation. `--from-snapshots --currency RUB --execute --limit N` enables the bounded finalized-snapshot pipeline behind advisory locking and DSN safety gates; exact currency is mandatory and the pipeline is never invoked by HTTP or scheduler.

Online inference first builds one current observation and a personal robust/seasonal fallback. It may replace that fallback only with the champion registered for the forecast's exact currency when schema, risk policy, training cutoff, guardrail metrics, trusted-directory path, artifact currency metadata, and SHA-256 all validate. Stored calibration is accepted only with at least 12 rolling out-of-sample residuals and empirical coverage metadata. Any absent currency champion, registry error or artifact failure returns the truthful personal fallback and its actual family/version.

Initial production therefore requires no pooled champion: leave `FORECAST_MODEL_DIR` empty and use the personal fallback. After enough trustworthy finalized snapshots exist for a currency, run its offline train/backtest pipeline, promote a validated currency-specific champion, and only then configure the trusted directory for registered inference. No champion is fabricated for first deployment.

## Can-Spend

The backend recalculates consequences for an amount and optional category. It returns `fits`, `borderline`, `does_not_fit`, or `insufficient_data`, plus before/after Spendable, matching general/category/grouped controls, protected goal reserve and risk change. Purchase date is intentionally absent until the product has a defensible date-sensitive policy. Expected income remains unavailable cash. The frontend never reproduces financial calculations.

## Insights And Experiments

Forecast-aware candidates include end-result and Spendable risk/change, projected expense acceleration, general/category/grouped budget breach risk, upcoming and recurring commitment pressure, goal affordability, category projection and unusual-spend evidence. Existing category-mix, merchant, frequency, average-check and persistent-trend detectors carry canonical forecast-family metadata. Every candidate is emitted only when its bounded snapshot evidence crosses the documented significance thresholds. Ranking combines impact, severity, confidence, actionability, active controls, repetition and feedback suppression; Home shows at most two.

`spendable-explanation-v1` assigns stable code-defined UX variants from a hash of experiment key, version and user ID. It changes explanation presentation only, never the financial number or risk policy. Exposure is recorded only after the surface renders. Event payloads contain coarse variant/surface/quality metadata and no amounts, category names, merchant names or financial text.

## Home Layout

Home order is fixed: filters, Activity, `Итог` plus `Свободно`, Income/Expense, What's New, Limits, Goals, Reminders, Insights, Shopping, Recent Operations. Activity opens the existing detail/calendar instead of embedding it. What's New is compact and uses typed navigation. Challenges are absent from Home.

Only Limits, Goals, Reminders, Insights and Shopping can be shown or hidden. Existing saved arbitrary order is accepted for API compatibility but ignored. Fixed surfaces are always visible.

## Privacy And Deletion

Forecast tables contain scoped identifiers and bounded numeric aggregates, never raw transaction text, comments, Telegram metadata, auth payloads or secrets. Account deletion covers feedback and snapshots; prediction rows cascade from snapshots. Financial-history deletion invalidates overlapping snapshots so deleted history cannot remain an authoritative outcome. Pooled artifacts are not reverse-engineered; a future production model lifecycle must rebuild from the post-deletion dataset when registry policy marks retraining required.

## Deployment And Rollback

Migration 024 is required before deploying this code but is not applied by this PR. It is additive, idempotent and transaction-wrapped. Prefer rolling application code back while leaving additive objects in place. If schema removal is later approved, use the manual rollback notes at the end of the migration only after application rollback and data-retention review.

The announcement candidate `advanced-forecasting-home-v1` currently has `released_on=2026-08-13`. Confirm and correct that date before production if deployment occurs on another date.
