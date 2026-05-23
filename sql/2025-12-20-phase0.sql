-- === Phase0: уведомления, ISO-недели, пользовательские категории, быстрые кнопки ===

BEGIN;

-- 1) Библиотека шаблонов уведомлений и лог доставок/реакций
CREATE TABLE IF NOT EXISTS public.notification_templates (
  id            BIGSERIAL PRIMARY KEY,
  key           TEXT UNIQUE NOT NULL,              -- например: no_records_today
  text          TEXT NOT NULL,                     -- текст с плейсхолдерами
  variables_json JSONB DEFAULT '{}'::jsonb,
  enabled       BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.notifications_log (
  id            BIGSERIAL PRIMARY KEY,
  user_id       BIGINT NOT NULL,
  template_id   BIGINT NOT NULL REFERENCES public.notification_templates(id) ON DELETE CASCADE,
  variables_json JSONB DEFAULT '{}'::jsonb,
  sent_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  delivered     BOOLEAN,
  clicked_at    TIMESTAMPTZ,
  recorded_at   TIMESTAMPTZ,
  result        TEXT                                  -- sent|skipped_no_need|skipped_inactive|error
);

CREATE INDEX IF NOT EXISTS idx_notif_log_user_sent ON public.notifications_log(user_id, sent_at DESC);

-- 2) Подтверждённые персональные категории пользователя (и эмодзи)
CREATE TABLE IF NOT EXISTS public.user_categories (
  user_id     BIGINT NOT NULL,
  category    TEXT   NOT NULL,
  emoji       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, category)
);

-- 3) Обогащение operations «правильными» неделями ISO (не ломая текущие колонки)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema='public' AND table_name='operations' AND column_name='week_start') THEN
    ALTER TABLE public.operations
      ADD COLUMN week_start DATE,
      ADD COLUMN iso_year   SMALLINT,
      ADD COLUMN iso_week   SMALLINT,
      ADD COLUMN weekday    SMALLINT;
  END IF;
END$$;

-- 4) Триггер: заполняем user_id (если пуст), week_start/iso_year/iso_week/weekday
CREATE OR REPLACE FUNCTION public.ops_fill_time_user()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  tz_min   integer := 0;
  local_ts timestamptz;
BEGIN
  IF NEW.user_id IS NULL AND NEW.chat_id IS NOT NULL THEN
    NEW.user_id := NEW.chat_id; -- для личных чатов эквивалентно
  END IF;

  SELECT COALESCE(u.tz_offset_min, 0) INTO tz_min
  FROM public.users u
  WHERE u.user_id = NEW.user_id;

  local_ts := (NEW.created_at AT TIME ZONE 'UTC') + make_interval(mins => tz_min);

  IF NEW.weekday IS NULL THEN
    NEW.weekday := EXTRACT(ISODOW FROM local_ts)::SMALLINT;    -- 1..7 (пн..вс)
  END IF;

  IF NEW.week_start IS NULL THEN
    NEW.week_start := date_trunc('week', local_ts)::date;      -- ISO monday
  END IF;

  IF NEW.iso_year IS NULL OR NEW.iso_week IS NULL THEN
    NEW.iso_year := EXTRACT(ISOYEAR FROM local_ts)::SMALLINT;
    NEW.iso_week := EXTRACT(WEEK    FROM local_ts)::SMALLINT;  -- ISO week number
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_ops_fill_time_user_biu ON public.operations;
CREATE TRIGGER trg_ops_fill_time_user_biu
BEFORE INSERT OR UPDATE ON public.operations
FOR EACH ROW
EXECUTE FUNCTION public.ops_fill_time_user();

-- 5) Бэкфилл старых строк
UPDATE public.operations o
SET user_id = COALESCE(o.user_id, o.chat_id)
WHERE o.user_id IS NULL;

WITH tz AS (
  SELECT u.user_id, COALESCE(u.tz_offset_min, 0) AS tz_min
  FROM public.users u
)
UPDATE public.operations o
SET
  weekday    = COALESCE(o.weekday,
                EXTRACT(ISODOW FROM ((o.created_at AT TIME ZONE 'UTC') + make_interval(mins => COALESCE(tz.tz_min,0))))::SMALLINT),
  week_start = COALESCE(o.week_start,
                date_trunc('week', ((o.created_at AT TIME ZONE 'UTC') + make_interval(mins => COALESCE(tz.tz_min,0))))::date),
  iso_year   = COALESCE(o.iso_year,
                EXTRACT(ISOYEAR FROM ((o.created_at AT TIME ZONE 'UTC') + make_interval(mins => COALESCE(tz.tz_min,0))))::SMALLINT),
  iso_week   = COALESCE(o.iso_week,
                EXTRACT(WEEK FROM ((o.created_at AT TIME ZONE 'UTC') + make_interval(mins => COALESCE(tz.tz_min,0))))::SMALLINT)
FROM tz
WHERE tz.user_id = o.user_id;

-- 6) Индексы под аналитику и быстрые кнопки
CREATE INDEX IF NOT EXISTS idx_ops_user_created ON public.operations(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ops_user_cat_amt ON public.operations(user_id, category, amount);

-- 7) Дефолтный шаблон для «нет записей сегодня»
INSERT INTO public.notification_templates(key, text, variables_json, enabled)
VALUES
  ('no_records_today',
   'Сегодня пока нет записей. Хочешь сохранить расход за день? Например: «кофе 150» ☕',
   '{}'::jsonb, TRUE)
ON CONFLICT (key) DO NOTHING;

COMMIT;
