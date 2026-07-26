from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2

from db.database import pg_fetchall


REQUIRED_OBJECTS = [
    ("schema", "analytics"),
    ("schema", "security"),
    ("table", "analytics.product_events"),
    ("table", "analytics.event_outbox"),
    ("table", "analytics.acquisition_attribution"),
    ("table", "analytics.api_usage_events"),
    ("table", "security.security_events"),
]


def _exists(kind: str, name: str) -> bool:
    if kind == "schema":
        rows = pg_fetchall("SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name=%s)", (name,))
        return bool(rows and rows[0][0])
    rows = pg_fetchall("SELECT to_regclass(%s)", (name,))
    return bool(rows and rows[0][0])


def main() -> int:
    try:
        for kind, name in REQUIRED_OBJECTS:
            print(f"{kind}:{name}: {'exists' if _exists(kind, name) else 'missing'}")
    except psycopg2.Error:
        print("preflight: database connection unavailable; no schema changes were applied")
        return 2
    print("preflight: additive analytics migration can be applied; legacy tables will be preserved")
    print("preflight: no secrets, raw messages, OCR text, voice transcripts or Telegram usernames are inspected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
