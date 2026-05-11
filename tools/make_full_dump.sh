#!/usr/bin/env bash
set -euo pipefail

BASE="/root/bot_finuchet"
OUT_DIR="$BASE/_backups/full_dumps"
INSPECT_DIR="$BASE/_backups/inspect"
TS="$(date +%Y%m%d-%H%M%S)"
DUMP_TXT="$OUT_DIR/code_db_dump-$TS.txt"
TREE_TXT="$OUT_DIR/tree-$TS.txt"
PIP_TXT="$OUT_DIR/pip-freeze-$TS.txt"
SYSUNIT_TXT="$OUT_DIR/systemd-unit-$TS.txt"
SYSPATH_TXT="$OUT_DIR/python-syspath-$TS.txt"
ENV_TXT="$OUT_DIR/dotenv-$TS.txt"
DB_OVERVIEW_TXT="$OUT_DIR/db-overview-$TS.txt"

mkdir -p "$OUT_DIR" "$INSPECT_DIR"

# ---------- 0) Заголовок дампа ----------
{
  echo "=== FINUCHET FULL DUMP ==="
  echo "timestamp: $TS"
  echo "host: $(hostname -f 2>/dev/null || hostname)"
  echo "kernel: $(uname -a)"
  echo "pwd: $(pwd)"
  echo
} > "$DUMP_TXT"

# ---------- 1) Python/pip/sys.path ----------
{
  echo "=== PYTHON ==="
  python3 -V || true
  echo
  echo "=== PIP ==="
  pip3 --version || true
  echo
} >> "$DUMP_TXT"

# freeze отдельным файлом + инлайн в общий дамп
(pip3 freeze || true) | tee "$PIP_TXT" >> "$DUMP_TXT"

# sys.path из того же окружения systemd (но мы исполняем под root/BASE)
python3 - <<'PY' | tee "$SYSPATH_TXT" >> "$DUMP_TXT"
import os, sys
print("=== PYTHON sys.path / CWD ===")
print("CWD:", os.getcwd())
print("PYTHONPATH:", os.environ.get("PYTHONPATH"))
for p in sys.path:
    print(" -", p)
PY

# ---------- 2) systemd unit & окружение ----------
{
  echo
  echo "=== SYSTEMD: finuchet (unit + drop-ins) ==="
  systemctl cat finuchet || true
  echo
  echo "=== SYSTEMD: show key props ==="
  systemctl show -p User -p WorkingDirectory -p ExecStart -p Environment -p EnvironmentFiles finuchet.service || true
  echo
} | tee "$SYSUNIT_TXT" >> "$DUMP_TXT"

# ---------- 3) .env (БЕЗ маскировки, как просил) ----------
{
  echo
  echo "=== .ENV (raw) ==="
  if [ -f "$BASE/.env" ]; then
    cat "$BASE/.env"
  else
    echo "no .env at $BASE/.env"
  fi
  echo
  echo "sha256(.env):"
  if [ -f "$BASE/.env" ]; then
    sha256sum "$BASE/.env" || true
  else
    echo "n/a"
  fi
  echo
} | tee "$ENV_TXT" >> "$DUMP_TXT"

# ---------- 4) Дерево проекта ----------
{
  echo
  echo "=== PROJECT TREE ($BASE) ==="
  # если есть tree — красиво; если нет — find
  if command -v tree >/dev/null 2>&1; then
    (cd "$BASE" && tree -a -I "_backups|.venv|__pycache__")
  else
    (cd "$BASE" && find . -mindepth 1 \( -path "./_backups" -o -path "./.venv" -o -path "*/__pycache__" \) -prune -o -print)
  fi
  echo
} | tee "$TREE_TXT" >> "$DUMP_TXT"

# ---------- 5) КОД: склеиваем все важные текстовые файлы ----------
# набор расширений — можно дополнять
readarray -t FILES < <(find "$BASE" \
  \( -path "$BASE/_backups" -o -path "$BASE/.venv" -o -path "*/__pycache__" \) -prune -o \
  -type f \( \
     -name "*.py" -o -name "*.pyi" -o \
     -name "*.sh" -o -name "*.service" -o \
     -name "*.yml" -o -name "*.yaml" -o \
     -name "*.sql" -o -name "*.ini" -o \
     -name "*.env.example" -o -name "requirements*.txt" -o \
     -name "Procfile" -o -name "Dockerfile" -o \
     -name "*.md" -o -name "*.txt" \
  \) -print | LC_ALL=C sort)

{
  echo
  echo "=== CODE CONCAT ==="
  for f in "${FILES[@]}"; do
    echo
    echo "-----8<----- [BEGIN FILE] $f -----"
    # безопасный вывод (если вдруг бинарник затесался — пропустим)
    if file -b --mime "$f" | grep -qi 'text'; then
      sed -n '1,200000p' "$f"
    else
      echo "(skipped non-text file)"
    fi
    echo "-----8<----- [END FILE] $f -----"
  done
  echo
} >> "$DUMP_TXT"

# ---------- 6) Срез БД (локальный postgres через sudo -u postgres) ----------
# Быстрые/лёгкие запросы, без тяжелых COUNT(*)
{
  echo
  echo "=== DATABASE SNAPSHOT (overview) ==="
  echo "-- version"
  sudo -u postgres psql -d finance_bot -Atc "SELECT version();" || true
  echo
  echo "-- tables (approx tuples/size)"
  sudo -u postgres psql -d finance_bot -X -q -v ON_ERROR_STOP=0 <<'SQL' || true
\pset pager off
\pset border 1
SELECT
  c.relname                                AS table,
  pg_size_pretty(pg_total_relation_size(c.oid)) AS total_size,
  COALESCE(s.n_live_tup,0)                AS est_rows
FROM pg_class c
JOIN pg_namespace n ON n.oid=c.relnamespace
LEFT JOIN pg_stat_user_tables s ON s.relname=c.relname
WHERE n.nspname='public' AND c.relkind='r'
ORDER BY c.relname;
SQL

  echo
  echo "-- columns"
  sudo -u postgres psql -d finance_bot -X -q -v ON_ERROR_STOP=0 <<'SQL' || true
\pset pager off
\pset border 1
SELECT c.table_name AS "table",
       c.ordinal_position AS "#",
       c.column_name AS "column",
       CASE
         WHEN c.data_type='numeric' THEN
           c.data_type||'('||c.numeric_precision||COALESCE(','||c.numeric_scale,'')||')'
         WHEN c.character_maximum_length IS NOT NULL THEN
           c.data_type||'('||c.character_maximum_length||')'
         ELSE c.data_type
       END AS "type",
       CASE WHEN c.is_nullable='YES' THEN 'NULL' ELSE 'NOT NULL' END AS "null",
       COALESCE(c.column_default,'') AS "default"
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema=c.table_schema AND t.table_name=c.table_name
WHERE c.table_schema='public' AND t.table_type='BASE TABLE'
ORDER BY c.table_name, c.ordinal_position;
SQL

  echo
  echo "-- indexes"
  sudo -u postgres psql -d finance_bot -X -q -v ON_ERROR_STOP=0 <<'SQL' || true
\pset pager off
\pset border 1
SELECT
  t.relname AS "table",
  i.relname AS "index",
  pg_get_indexdef(ix.indexrelid) AS "definition"
FROM pg_class t
JOIN pg_index ix       ON t.oid = ix.indrelid
JOIN pg_class i        ON i.oid = ix.indexrelid
JOIN pg_namespace n    ON n.oid = t.relnamespace
WHERE n.nspname='public'
ORDER BY t.relname, i.relname;
SQL

  echo
  echo "-- quick counts (safe small set)"
  sudo -u postgres psql -d finance_bot -X -q -v ON_ERROR_STOP=0 <<'SQL' || true
\pset pager off
\pset border 1
SELECT 'users'         AS table, COUNT(*) FROM public.users
UNION ALL
SELECT 'records'       AS table, COUNT(*) FROM public.records
UNION ALL
SELECT 'global_aliases' AS table, COUNT(*) FROM public.global_aliases
UNION ALL
SELECT 'user_aliases'   AS table, COUNT(*) FROM public.user_aliases
UNION ALL
SELECT 'reminders_log'  AS table, COUNT(*) FROM public.reminders_log
ORDER BY table;
SQL

  echo
  echo "-- recent records (10)"
  sudo -u postgres psql -d finance_bot -X -q -v ON_ERROR_STOP=0 <<'SQL' || true
\pset pager off
\pset border 1
SELECT id, user_id, chat_id, record_date, type, category, amount, currency, amount_rub, comment, created_at
FROM public.records
ORDER BY created_at DESC
LIMIT 10;
SQL
} | tee "$DB_OVERVIEW_TXT" >> "$DUMP_TXT"

# ---------- 7) Финал: краткая сводка путей ----------
{
  echo
  echo "=== FILES WRITTEN ==="
  echo "$DUMP_TXT     (главный слитный дамп)"
  echo "$TREE_TXT      (структура проекта)"
  echo "$PIP_TXT       (pip freeze)"
  echo "$SYSUNIT_TXT   (systemd unit)"
  echo "$SYSPATH_TXT   (python sys.path)"
  echo "$ENV_TXT       (.env raw)"
  echo "$DB_OVERVIEW_TXT (обзор БД)"
  echo
} >> "$DUMP_TXT"

echo "OK: dump saved to $DUMP_TXT"
