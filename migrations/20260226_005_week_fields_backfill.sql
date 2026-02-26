-- Stage 1.2 / PR2: backfill week fields in batches (non-locking approach)

DO $$
DECLARE
  _updated integer;
BEGIN
  LOOP
    WITH chunk AS (
      SELECT o.ctid
      FROM public.operations o
      WHERE o.week_start IS NULL
         OR o.iso_year IS NULL
         OR o.iso_week IS NULL
         OR o.weekday IS NULL
      ORDER BY o.id
      LIMIT 5000
    )
    UPDATE public.operations o
    SET
      week_start = COALESCE(o.week_start, date_trunc('week', o.op_date::timestamp)::date),
      iso_year   = COALESCE(o.iso_year, EXTRACT(ISOYEAR FROM o.op_date::timestamp)::smallint),
      iso_week   = COALESCE(o.iso_week, EXTRACT(WEEK FROM o.op_date::timestamp)::smallint),
      weekday    = COALESCE(o.weekday, EXTRACT(ISODOW FROM o.op_date::timestamp)::smallint)
    FROM chunk
    WHERE o.ctid = chunk.ctid;

    GET DIAGNOSTICS _updated = ROW_COUNT;
    EXIT WHEN _updated = 0;

    PERFORM pg_sleep(0.03);
  END LOOP;
END $$;
