-- Функция: перед вставкой/апдейтом дозаполняем user_id/weekday/week_range
CREATE OR REPLACE FUNCTION public.ops_fill_time_user()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  tz_min   integer;
  local_ts timestamptz;
  wk_start date;
  wk_end   date;
BEGIN
  -- user_id: если пуст, пробуем взять из chat_id (в личке они совпадают)
  IF NEW.user_id IS NULL AND NEW.chat_id IS NOT NULL THEN
    NEW.user_id := NEW.chat_id;
  END IF;

  -- таймзона пользователя (минуты), по умолчанию 0
  SELECT COALESCE(u.tz_offset_min, 0)
  INTO tz_min
  FROM public.users u
  WHERE u.user_id = NEW.user_id;

  -- локальное время пользователя
  local_ts := (NEW.created_at AT TIME ZONE 'UTC') + make_interval(mins => tz_min);

  -- день недели (1..7 ISO, пн..вс)
  IF NEW.weekday IS NULL THEN
    NEW.weekday := EXTRACT(ISODOW FROM local_ts)::int;
  END IF;

  -- диапазон недели (понедельник..воскресенье) в текстовом виде
  IF NEW.week_range IS NULL THEN
    wk_start := date_trunc('week', local_ts)::date;         -- понедельник
    wk_end   := (wk_start + INTERVAL '6 day')::date;        -- воскресенье
    NEW.week_range := to_char(wk_start, 'YYYY-MM-DD') || '—' || to_char(wk_end, 'YYYY-MM-DD');
  END IF;

  RETURN NEW;
END
$$;

-- Триггер до вставки/обновления
DROP TRIGGER IF EXISTS trg_ops_fill_time_user_biu ON public.operations;
CREATE TRIGGER trg_ops_fill_time_user_biu
BEFORE INSERT OR UPDATE ON public.operations
FOR EACH ROW
EXECUTE FUNCTION public.ops_fill_time_user();

-- Разовый бэкап значений по уже существующим строкам
UPDATE public.operations o
SET
  user_id = COALESCE(o.user_id, o.chat_id)
WHERE o.user_id IS NULL;

WITH tz AS (
  SELECT u.user_id, COALESCE(u.tz_offset_min, 0) AS tz_min
  FROM public.users u
)
UPDATE public.operations o
SET
  weekday = EXTRACT(ISODOW FROM ((o.created_at AT TIME ZONE 'UTC') + make_interval(mins => COALESCE(tz.tz_min,0))))::int,
  week_range = to_char(date_trunc('week', ((o.created_at AT TIME ZONE 'UTC') + make_interval(mins => COALESCE(tz.tz_min,0))))::date, 'YYYY-MM-DD')
             || '—' ||
             to_char((date_trunc('week', ((o.created_at AT TIME ZONE 'UTC') + make_interval(mins => COALESCE(tz.tz_min,0))))::date + INTERVAL '6 day')::date, 'YYYY-MM-DD')
FROM tz
WHERE o.weekday IS NULL OR o.week_range IS NULL;
