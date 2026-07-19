-- Finuchet Pre-Mini-App foundation: workspaces, activity, notifications, achievements.
-- Additive and idempotent. Do not apply from Codex; run through the deployment process.

BEGIN;

CREATE TABLE IF NOT EXISTS public.workspaces (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'personal',
    owner_user_id BIGINT,
    telegram_chat_id BIGINT,
    locale TEXT NOT NULL DEFAULT 'ru',
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    default_currency TEXT NOT NULL DEFAULT 'RUB',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ,
    CONSTRAINT workspaces_kind_check CHECK (kind IN ('personal', 'group', 'family', 'work', 'trip', 'shared', 'other'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_workspaces_personal_owner
    ON public.workspaces (owner_user_id)
    WHERE kind = 'personal' AND owner_user_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ux_workspaces_telegram_chat
    ON public.workspaces (telegram_chat_id)
    WHERE telegram_chat_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.workspace_members (
    workspace_id BIGINT NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id),
    CONSTRAINT workspace_members_role_check CHECK (role IN ('owner', 'admin', 'member', 'viewer')),
    CONSTRAINT workspace_members_status_check CHECK (status IN ('active', 'invited', 'removed'))
);

CREATE TABLE IF NOT EXISTS public.workspace_invitations (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT NOT NULL REFERENCES public.workspaces(id) ON DELETE CASCADE,
    inviter_user_id BIGINT NOT NULL,
    invitee_user_id BIGINT,
    token TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    accepted_at TIMESTAMPTZ,
    CONSTRAINT workspace_invitations_role_check CHECK (role IN ('admin', 'member', 'viewer')),
    CONSTRAINT workspace_invitations_status_check CHECK (status IN ('pending', 'accepted', 'revoked', 'expired'))
);

CREATE TABLE IF NOT EXISTS public.user_workspace_settings (
    user_id BIGINT PRIMARY KEY,
    active_workspace_id BIGINT REFERENCES public.workspaces(id),
    locale TEXT NOT NULL DEFAULT 'ru',
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    default_currency TEXT NOT NULL DEFAULT 'RUB',
    first_day_of_week SMALLINT NOT NULL DEFAULT 1,
    date_format TEXT NOT NULL DEFAULT 'DD.MM.YYYY',
    money_format TEXT NOT NULL DEFAULT 'ru_RU',
    smart_notifications_enabled BOOLEAN NOT NULL DEFAULT true,
    achievement_notifications_enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.operation_drafts (
    draft_id TEXT PRIMARY KEY DEFAULT md5(random()::text || clock_timestamp()::text),
    workspace_id BIGINT REFERENCES public.workspaces(id),
    chat_id BIGINT NOT NULL,
    actor_user_id BIGINT NOT NULL,
    source TEXT NOT NULL DEFAULT 'text',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '30 minutes'),
    committed_operation_id BIGINT,
    CONSTRAINT operation_drafts_status_check CHECK (status IN ('draft', 'committed', 'cancelled', 'expired'))
);

CREATE INDEX IF NOT EXISTS idx_operation_drafts_scope
    ON public.operation_drafts (workspace_id, chat_id, actor_user_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS public.custom_categories (
    id BIGSERIAL PRIMARY KEY,
    workspace_id BIGINT REFERENCES public.workspaces(id),
    user_id BIGINT,
    type TEXT NOT NULL DEFAULT 'Расходы',
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    icon TEXT,
    color TEXT,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_custom_categories_workspace_norm
    ON public.custom_categories (workspace_id, type, normalized_name)
    WHERE workspace_id IS NOT NULL AND archived_at IS NULL;

CREATE TABLE IF NOT EXISTS public.financial_activity_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    operation_id BIGINT,
    source TEXT NOT NULL,
    activity_type TEXT NOT NULL DEFAULT 'operation_recorded',
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    local_date DATE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_financial_activity_user_time
    ON public.financial_activity_events (user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_financial_activity_workspace_time
    ON public.financial_activity_events (workspace_id, occurred_at DESC);

CREATE TABLE IF NOT EXISTS public.notification_preferences (
    user_id BIGINT PRIMARY KEY,
    timezone TEXT NOT NULL DEFAULT 'Europe/Moscow',
    notification_hour SMALLINT NOT NULL DEFAULT 20,
    quiet_start TIME,
    quiet_end TIME,
    max_per_day SMALLINT NOT NULL DEFAULT 2,
    smart_enabled BOOLEAN NOT NULL DEFAULT true,
    enabled_types JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.notification_events (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    workspace_id BIGINT,
    notification_type TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    reason TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    read_at TIMESTAMPTZ,
    action_at TIMESTAMPTZ,
    delivery_error TEXT,
    CONSTRAINT notification_events_status_check CHECK (status IN ('pending', 'sent', 'skipped', 'failed', 'read', 'acted'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_notification_events_dedupe
    ON public.notification_events (user_id, notification_type, dedupe_key);

CREATE TABLE IF NOT EXISTS public.achievement_definitions (
    key TEXT PRIMARY KEY,
    title_ru TEXT NOT NULL,
    title_en TEXT NOT NULL,
    group_key TEXT NOT NULL,
    target INTEGER NOT NULL DEFAULT 1,
    reward JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_achievements (
    user_id BIGINT NOT NULL,
    workspace_id BIGINT NOT NULL DEFAULT 0,
    achievement_key TEXT NOT NULL REFERENCES public.achievement_definitions(key),
    progress INTEGER NOT NULL DEFAULT 0,
    earned_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, workspace_id, achievement_key)
);

ALTER TABLE public.operations ADD COLUMN IF NOT EXISTS workspace_id BIGINT;
ALTER TABLE public.operations ADD COLUMN IF NOT EXISTS actor_user_id BIGINT;
ALTER TABLE public.operations ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'text';
ALTER TABLE public.operations ADD COLUMN IF NOT EXISTS currency TEXT NOT NULL DEFAULT 'RUB';

ALTER TABLE public.budgets ADD COLUMN IF NOT EXISTS workspace_id BIGINT;
ALTER TABLE public.category_limits ADD COLUMN IF NOT EXISTS workspace_id BIGINT;
ALTER TABLE public.category_limit_state ADD COLUMN IF NOT EXISTS workspace_id BIGINT;
ALTER TABLE public.user_aliases ADD COLUMN IF NOT EXISTS workspace_id BIGINT;
ALTER TABLE public.category_aliases ADD COLUMN IF NOT EXISTS workspace_id BIGINT;
ALTER TABLE public.notifications_log ADD COLUMN IF NOT EXISTS workspace_id BIGINT;
ALTER TABLE public.reminders_log ADD COLUMN IF NOT EXISTS workspace_id BIGINT;

DO $$
BEGIN
    IF to_regclass('public.user_reminders') IS NOT NULL THEN
        ALTER TABLE public.user_reminders ADD COLUMN IF NOT EXISTS workspace_id BIGINT;
        ALTER TABLE public.user_reminders ADD COLUMN IF NOT EXISTS actor_user_id BIGINT;
    END IF;
END $$;

INSERT INTO public.workspaces (name, kind, owner_user_id, telegram_chat_id, locale, timezone, default_currency)
SELECT
    'Personal',
    'personal',
    u.user_id,
    NULL,
    COALESCE(u.locale, 'ru'),
    CASE COALESCE(u.tz_offset_min, 180)
        WHEN 0 THEN 'UTC'
        WHEN 60 THEN 'Europe/Berlin'
        WHEN 120 THEN 'Europe/Helsinki'
        WHEN 180 THEN 'Europe/Moscow'
        ELSE 'UTC'
    END,
    COALESCE(u.currency, 'RUB')
FROM public.users u
ON CONFLICT DO NOTHING;

INSERT INTO public.workspace_members (workspace_id, user_id, role, status)
SELECT w.id, w.owner_user_id, 'owner', 'active'
FROM public.workspaces w
WHERE w.kind = 'personal' AND w.owner_user_id IS NOT NULL
ON CONFLICT (workspace_id, user_id) DO NOTHING;

INSERT INTO public.user_workspace_settings (user_id, active_workspace_id, locale, timezone, default_currency)
SELECT
    u.user_id,
    w.id,
    COALESCE(u.locale, 'ru'),
    COALESCE(w.timezone, 'Europe/Moscow'),
    COALESCE(u.currency, 'RUB')
FROM public.users u
JOIN public.workspaces w ON w.kind = 'personal' AND w.owner_user_id = u.user_id
ON CONFLICT (user_id) DO UPDATE
SET active_workspace_id = COALESCE(public.user_workspace_settings.active_workspace_id, EXCLUDED.active_workspace_id),
    locale = COALESCE(public.user_workspace_settings.locale, EXCLUDED.locale),
    timezone = COALESCE(public.user_workspace_settings.timezone, EXCLUDED.timezone),
    default_currency = COALESCE(public.user_workspace_settings.default_currency, EXCLUDED.default_currency),
    updated_at = now();

UPDATE public.operations o
SET workspace_id = w.id,
    actor_user_id = COALESCE(o.actor_user_id, o.user_id, o.chat_id)
FROM public.workspaces w
WHERE o.workspace_id IS NULL
  AND w.kind = 'personal'
  AND w.owner_user_id = COALESCE(o.user_id, o.chat_id);

UPDATE public.budgets b
SET workspace_id = w.id
FROM public.workspaces w
WHERE b.workspace_id IS NULL
  AND w.kind = 'personal'
  AND w.owner_user_id = b.user_id;

UPDATE public.category_limits cl
SET workspace_id = w.id
FROM public.workspaces w
WHERE cl.workspace_id IS NULL
  AND w.kind = 'personal'
  AND w.owner_user_id = cl.user_id;

UPDATE public.category_limit_state cls
SET workspace_id = w.id
FROM public.workspaces w
WHERE cls.workspace_id IS NULL
  AND w.kind = 'personal'
  AND w.owner_user_id = cls.user_id;

UPDATE public.user_aliases ua
SET workspace_id = w.id
FROM public.workspaces w
WHERE ua.workspace_id IS NULL
  AND w.kind = 'personal'
  AND w.owner_user_id = ua.user_id;

DO $$
BEGIN
    IF to_regclass('public.user_reminders') IS NOT NULL THEN
        UPDATE public.user_reminders ur
        SET workspace_id = w.id,
            actor_user_id = COALESCE(ur.actor_user_id, ur.user_id)
        FROM public.workspaces w
        WHERE ur.workspace_id IS NULL
          AND w.kind = 'personal'
          AND w.owner_user_id = ur.user_id;
    END IF;
END $$;

INSERT INTO public.achievement_definitions (key, title_ru, title_en, group_key, target)
VALUES
('first_operation', 'Первая операция', 'First operation', 'onboarding', 1),
('first_income', 'Первый доход', 'First income', 'onboarding', 1),
('first_voice_operation', 'Первая голосовая операция', 'First voice operation', 'onboarding', 1),
('first_ocr_operation', 'Первая операция из изображения', 'First OCR operation', 'onboarding', 1),
('first_custom_category', 'Первая своя категория', 'First custom category', 'onboarding', 1),
('first_budget', 'Первый бюджет', 'First budget', 'onboarding', 1),
('first_category_limit', 'Первый лимит категории', 'First category limit', 'onboarding', 1),
('first_reminder', 'Первое напоминание', 'First reminder', 'onboarding', 1),
('first_export', 'Первый экспорт', 'First export', 'onboarding', 1),
('first_shared_workspace', 'Первое общее пространство', 'First shared workspace', 'onboarding', 1),
('tracking_streak_3', '3 дня учета подряд', '3-day tracking streak', 'consistency', 3),
('tracking_streak_5', '5 дней учета подряд', '5-day tracking streak', 'consistency', 5),
('tracking_streak_7', '7 дней учета подряд', '7-day tracking streak', 'consistency', 7),
('tracking_streak_14', '14 дней учета подряд', '14-day tracking streak', 'consistency', 14),
('tracking_streak_30', '30 дней учета подряд', '30-day tracking streak', 'consistency', 30),
('tracking_streak_60', '60 дней учета подряд', '60-day tracking streak', 'consistency', 60),
('tracking_streak_100', '100 дней учета подряд', '100-day tracking streak', 'consistency', 100),
('full_week_tracked', 'Неделя без пропусков', 'Full week tracked', 'consistency', 7),
('regular_month_tracking', 'Регулярный месяц учета', 'Regular month tracking', 'consistency', 20),
('categorized_10', '10 операций с категориями', '10 categorized operations', 'data_quality', 10),
('categorized_50', '50 операций с категориями', '50 categorized operations', 'data_quality', 50),
('categorized_100', '100 операций с категориями', '100 categorized operations', 'data_quality', 100),
('first_correction', 'Первая правка операции', 'First corrected operation', 'data_quality', 1),
('clean_categories', 'Аккуратные категории', 'Clean category usage', 'data_quality', 1),
('budget_reviewed', 'Бюджет обновлен после расходов', 'Budget reviewed after spending', 'data_quality', 1),
('week_within_budget', 'Неделя в бюджете', 'Week within budget', 'budget', 1),
('month_within_budget', 'Месяц в бюджете', 'Month within budget', 'budget', 1),
('three_periods_within_budget', 'Три периода в бюджете', 'Three periods within budget', 'budget', 3),
('first_limit_respected', 'Первый лимит соблюден', 'First limit respected', 'budget', 1),
('threshold_recovered', 'Вернулись после предупреждения', 'Recovered after threshold', 'budget', 1),
('category_reduced', 'Категория снижена к прошлому периоду', 'Category reduced vs previous period', 'budget', 1),
('positive_month_cash_flow', 'Плюсовой месяц', 'Positive monthly cash flow', 'awareness', 1),
('reviewed_commitments', 'Повторные платежи просмотрены', 'Reviewed recurring commitments', 'awareness', 1),
('first_monthly_summary', 'Первый месячный итог', 'First monthly summary', 'awareness', 1),
('top_category_identified', 'Главная категория найдена', 'Top spending category identified', 'awareness', 1),
('first_shared_operation', 'Первая общая операция', 'First shared operation', 'shared', 1),
('shared_operations_10', '10 общих операций', '10 shared operations', 'shared', 10),
('first_shared_budget', 'Первый общий бюджет', 'First shared budget', 'shared', 1),
('first_shared_month', 'Первый общий месяц', 'First shared month completed', 'shared', 1)
ON CONFLICT (key) DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_operations_workspace_date
    ON public.operations (workspace_id, op_date DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_operations_workspace_type_category
    ON public.operations (workspace_id, type, category, op_date DESC);

CREATE INDEX IF NOT EXISTS idx_operations_actor_created
    ON public.operations (actor_user_id, created_at DESC);

COMMIT;

-- Rollback guidance:
-- 1. Leave added columns in place for forward compatibility if possible.
-- 2. To disable the feature, route services back to personal user_id/chat_id queries.
-- 3. Only after a verified backup, drop newly added tables in reverse dependency order:
--    user_achievements, achievement_definitions, notification_events,
--    notification_preferences, financial_activity_events, custom_categories,
--    operation_drafts, user_workspace_settings, workspace_invitations,
--    workspace_members, workspaces.
