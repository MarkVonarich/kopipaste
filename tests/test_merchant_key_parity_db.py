from __future__ import annotations

import pytest
from psycopg2 import OperationalError

from db.database import pg_fetchall
from services.merchant_intelligence import merchant_key_sql, normalize_merchant_key
from settings import DATABASE_URL


def test_python_and_postgres_merchant_key_v1_parity_on_safe_test_db():
    if "finuchet_test" not in (DATABASE_URL or ""):
        pytest.skip("merchant key parity test only runs against the safe finuchet_test database")
    samples = [
        "Яндекс Лавка",
        " ЯНДЕКС   ЛАВКА",
        "Яндекс*Лавка",
        "Яндекс-Лавка",
        "Яндекс.Лавка",
        "Ёлки-Палки",
        "Coffee House",
        "A&B",
        "shop.ru",
        "Ｆｏｏ",
        "Café",
        "Café",
        "☕ Cafe",
        "магазин №7",
        "ООО \"Ромашка\"",
    ]
    sql = f"WITH sample(raw) AS (VALUES (%s)) SELECT {merchant_key_sql('raw')} FROM sample"
    try:
        for raw in samples:
            sql_key = pg_fetchall(sql, (raw,))[0][0]
            assert normalize_merchant_key(raw) == sql_key
    except OperationalError as exc:
        pytest.skip(f"safe test PostgreSQL is unavailable: {exc}")
