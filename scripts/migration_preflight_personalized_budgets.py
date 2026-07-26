from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import psycopg2

from db.database import pg_fetchall


REQUIRED_TABLES = [
    "general_spending_limits",
    "category_budget_groups",
    "category_budget_group_members",
    "limit_alert_deliveries",
    "subscription_patterns",
    "recurring_spend_patterns",
]


def table_exists(name: str) -> bool:
    rows = pg_fetchall(
        """
        SELECT to_regclass(%s)
        """,
        (f"public.{name}",),
    )
    return bool(rows and rows[0][0])


def main() -> int:
    try:
        existing = {name: table_exists(name) for name in REQUIRED_TABLES}
    except psycopg2.Error:
        print("preflight: database connection unavailable; no schema changes were applied")
        return 2
    for name, exists in existing.items():
        print(f"{name}: {'exists' if exists else 'missing'}")
    print("preflight: additive migration can be applied; existing tables will be preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
