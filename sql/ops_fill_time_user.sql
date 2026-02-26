-- Функция: перед вставкой/апдейтом дозаполняем user_id/week-поля
-- weekday convention: ISO 1..7 (Mon..Sun)

BEGIN;

CREATE OR REPLACE FUNCTION public.ops_fill_time_user()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  tz_min   integer := 0;
  local_ts timestamp;
BEGIN
  IF NEW.user_id IS NULL AND NEW.chat_id IS NOT NULL THEN
    NEW.user_id := NEW.chat_id;
  END IF;

  IF NEW.created_at IS NULL THEN
    NEW.created_at := now();
  END IF;

  SELECT COALESCE(u.tz_offset_min, 0)
    INTO tz_min
    FROM public.users u
   WHERE u.user_id = NEW.user_id
   LIMIT 1;

  local_ts := (NEW.created_at AT TIME ZONE 'UTC') + make_interval(mins => tz_min);

  IF NEW.weekday IS NULL THEN
    NEW.weekday := EXTRACT(ISODOW FROM local_ts)::smallint;
  END IF;

  IF NEW.week_start IS NULL THEN
    NEW.week_start := date_trunc('week', local_ts)::date;
  END IF;

  IF NEW.iso_year IS NULL THEN
    NEW.iso_year := EXTRACT(ISOYEAR FROM local_ts)::smallint;
  END IF;

  IF NEW.iso_week IS NULL THEN
    NEW.iso_week := EXTRACT(WEEK FROM local_ts)::smallint;
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_ops_fill_time_user_biu ON public.operations;
CREATE TRIGGER trg_ops_fill_time_user_biu
BEFORE INSERT OR UPDATE ON public.operations
FOR EACH ROW
EXECUTE FUNCTION public.ops_fill_time_user();

COMMIT;
