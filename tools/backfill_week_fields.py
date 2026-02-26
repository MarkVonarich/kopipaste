#!/usr/bin/env python3
"""Stage 1.2 helper: backfill operations.week_* fields in controllable batches."""

import argparse
from db.database import get_conn


def run(batch_size: int, sleep_ms: int) -> int:
    total = 0
    conn = get_conn()
    try:
        while True:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    WITH chunk AS (
                      SELECT ctid
                      FROM public.operations
                      WHERE week_start IS NULL
                         OR iso_year IS NULL
                         OR iso_week IS NULL
                         OR weekday IS NULL
                      ORDER BY id
                      LIMIT %s
                    )
                    UPDATE public.operations o
                    SET
                      week_start = COALESCE(o.week_start, date_trunc('week', o.op_date::timestamp)::date),
                      iso_year   = COALESCE(o.iso_year, EXTRACT(ISOYEAR FROM o.op_date::timestamp)::smallint),
                      iso_week   = COALESCE(o.iso_week, EXTRACT(WEEK FROM o.op_date::timestamp)::smallint),
                      weekday    = COALESCE(o.weekday, EXTRACT(ISODOW FROM o.op_date::timestamp)::smallint)
                    FROM chunk
                    WHERE o.ctid = chunk.ctid
                    """,
                    (batch_size,),
                )
                updated = cur.rowcount
            conn.commit()
            if updated == 0:
                break
            total += updated
            if sleep_ms > 0:
                with conn.cursor() as cur:
                    cur.execute("SELECT pg_sleep(%s)", (sleep_ms / 1000.0,))
        return total
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--sleep-ms", type=int, default=30)
    args = parser.parse_args()
    total = run(args.batch_size, args.sleep_ms)
    print(f"backfill complete: updated={total}")


if __name__ == "__main__":
    main()
