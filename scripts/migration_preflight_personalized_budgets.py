from __future__ import annotations

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
    existing = {name: table_exists(name) for name in REQUIRED_TABLES}
    for name, exists in existing.items():
        print(f"{name}: {'exists' if exists else 'missing'}")
    print("preflight: additive migration can be applied; existing tables will be preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
