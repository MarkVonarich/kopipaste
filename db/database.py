# db/database.py — v2026.02.26-01 (pool + application_name)
__version__ = "2026.02.26-01"

import threading
import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from settings import DATABASE_URL

_POOL = None
_POOL_LOCK = threading.Lock()


class _PooledConnection:
    def __init__(self, conn, pool):
        self._conn = conn
        self._pool = pool
        self._returned = False

    def __getattr__(self, item):
        return getattr(self._conn, item)

    def close(self):
        if not self._returned:
            self._pool.putconn(self._conn)
            self._returned = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def _get_pool():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=10,
                    dsn=DATABASE_URL,
                    application_name="finuchet",
                )
    return _POOL


def get_conn():
    pool = _get_pool()
    conn = pool.getconn()
    conn.autocommit = False
    return _PooledConnection(conn, pool)


def pg_fetchall(sql: str, params=()):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def pg_exec(sql: str, params=(), commit=True):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        if commit:
            conn.commit()
    finally:
        conn.close()
