-- Stage 1.2 / PR1: week fields schema + auto-fill trigger + report indexes

BEGIN;

ALTER TABLE public.operations
  ADD COLUMN IF NOT EXISTS week_start DATE,
  ADD COLUMN IF NOT EXISTS iso_year SMALLINT,
  ADD COLUMN IF NOT EXISTS iso_week SMALLINT,
  ADD COLUMN IF NOT EXISTS weekday SMALLINT;

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
    NEW.weekday := EXTRACT(ISODOW FROM local_ts)::SMALLINT; -- 1..7 (Mon..Sun)
  END IF;

  IF NEW.week_start IS NULL THEN
    NEW.week_start := date_trunc('week', local_ts)::date; -- ISO Monday
  END IF;

  IF NEW.iso_year IS NULL THEN
    NEW.iso_year := EXTRACT(ISOYEAR FROM local_ts)::SMALLINT;
  END IF;

  IF NEW.iso_week IS NULL THEN
    NEW.iso_week := EXTRACT(WEEK FROM local_ts)::SMALLINT;
  END IF;

  RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_ops_fill_time_user_biu ON public.operations;
CREATE TRIGGER trg_ops_fill_time_user_biu
BEFORE INSERT OR UPDATE ON public.operations
FOR EACH ROW
EXECUTE FUNCTION public.ops_fill_time_user();

CREATE INDEX IF NOT EXISTS idx_operations_user_week_start
  ON public.operations(user_id, week_start);

CREATE INDEX IF NOT EXISTS idx_operations_user_iso_week
  ON public.operations(user_id, iso_year, iso_week);

COMMIT;
