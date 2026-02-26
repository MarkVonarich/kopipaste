# db/database.py — v2025.08.18-01 
__version__ = "2025.08.18-01"

import psycopg2
from settings import DATABASE_URL

def get_conn():
    return psycopg2.connect(DATABASE_URL)

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
