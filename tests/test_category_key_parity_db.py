from __future__ import annotations

import pytest
from psycopg2 import OperationalError

from db.database import pg_fetchall
from services.categories import category_key_sql, normalized_category_key
from settings import DATABASE_URL


def test_python_and_postgres_category_key_parity_on_safe_test_db():
    if "finuchet_test" not in (DATABASE_URL or ""):
        pytest.skip("category key parity test only runs against the safe finuchet_test database")
    samples = [
        "Прочее",
        " ПРОЧЕЕ ",
        "Прочее   ",
        "Прочее\t\nдом",
        "Ёлка",
        "елка",
        "Coffee   Shops",
    ]
    sql = f"WITH sample(raw) AS (VALUES (%s)) SELECT {category_key_sql('raw')} FROM sample"
    try:
        for raw in samples:
            sql_key = pg_fetchall(sql, (raw,))[0][0]
            assert normalized_category_key(raw) == sql_key
    except OperationalError as exc:
        pytest.skip(f"safe test PostgreSQL is unavailable: {exc}")
