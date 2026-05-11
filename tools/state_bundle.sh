#!/usr/bin/env bash
# tools/state_bundle.sh — v2026.01.25-02
# State bundle = code + DB schema + metadata + samples (no secrets)

set -euo pipefail

BASE_DIR="/root/bot_finuchet"
OUT_ROOT="$BASE_DIR/_backups/state_bundles"

TS="$(date +%Y%m%d-%H%M%S)"
B="$OUT_ROOT/state_${TS}"

log(){ echo "[$(date +%H:%M:%S)] $*"; }

mkdir -p "$OUT_ROOT" "$B"

log "[1/7] Collect code snapshot (excluding .env, caches, backups)…"
rsync -a \
  --exclude='.env' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='_backups' --exclude='.state' \
  "$BASE_DIR/" "$B/code/"

log "[2/7] Collect systemd + python metadata…"
systemctl cat finuchet > "$B/systemd_finuchet.txt"
python3 -m pip freeze > "$B/pip_freeze.txt"

log "[3/7] Collect DB schema + tables list…"
cd /tmp
sudo -u postgres pg_dump -d finance_bot -s > "$B/db_schema.sql"
sudo -u postgres psql -d finance_bot -c "\dt+" > "$B/db_tables.txt"

log "[4/7] DB counts (quick sanity)…"
sudo -u postgres psql -d finance_bot -c "
select 'operations' as t, count(*) from public.operations
union all select 'global_aliases', count(*) from public.global_aliases
union all select 'user_aliases', count(*) from public.user_aliases
union all select 'category_limits', count(*) from public.category_limits
union all select 'users', count(*) from public.users
order by 1;" > "$B/db_counts.txt"

log "[5/7] Export data samples (operations + aliases + users)…"
OPS_CSV="$B/operations_ml_sample.csv"
OPS_JSONL="$B/operations_ml_sample.jsonl"
export OPS_CSV OPS_JSONL

# stdout -> пишем как root (иначе Permission denied от postgres)
sudo -u postgres psql -d finance_bot -c "\copy (
  select
    id,
    chat_id,
    created_at,
    op_date,
    type,
    category,
    amount,
    coalesce(comment,'')   as comment,
    coalesce(raw_text,'')  as raw_text,
    coalesce(user_id,0)    as user_id
  from public.operations
  order by created_at desc
  limit 50000
) to stdout csv header" > "$OPS_CSV"

sudo -u postgres psql -d finance_bot -c "\copy (select * from public.global_aliases) to stdout csv header" > "$B/global_aliases.csv"
sudo -u postgres psql -d finance_bot -c "\copy (select * from public.user_aliases)  to stdout csv header" > "$B/user_aliases.csv"

sudo -u postgres psql -d finance_bot -c "\copy (
  select
    user_id,
    coalesce(display_name,'') as display_name,
    coalesce(currency,'') as currency,
    coalesce(tz_offset_min,180) as tz_offset_min,
    coalesce(reminder_hour,20) as reminder_hour,
    created_at
  from public.users
  order by created_at desc
) to stdout csv header" > "$B/users_settings.csv"

log "[6/7] Build JSONL from operations sample (handy for ML)…"
python3 - << 'PY'
import os, csv, json
from datetime import datetime, date, timedelta

src = os.environ["OPS_CSV"]
dst = os.environ["OPS_JSONL"]

def parse_dt(s: str):
    if not s: return None
    s = s.replace("Z","+00:00")
    try: return datetime.fromisoformat(s)
    except Exception: return None

def parse_date(s: str):
    try: return date.fromisoformat(s)
    except Exception: return None

def week_range(d: date):
    start = d - timedelta(days=d.weekday())  # Monday
    end = start + timedelta(days=6)
    return f"{start.isoformat()}..{end.isoformat()}"

with open(src, "r", encoding="utf-8") as f, open(dst, "w", encoding="utf-8") as w:
    r = csv.DictReader(f)
    for row in r:
        od = parse_date(row.get("op_date",""))
        cd = parse_dt(row.get("created_at",""))
        if od:
            row["weekday"] = od.strftime("%a")
            row["week_range"] = week_range(od)
        if cd:
            row["created_at_iso"] = cd.isoformat()
        t = (row.get("type") or "").lower()
        row["is_income"] = int(("доход" in t) or (t == "income"))
        w.write(json.dumps(row, ensure_ascii=False) + "\n")

print("JSONL OK:", dst)
PY

log "[7/7] Pack bundle…"
tar -czf "$OUT_ROOT/state_${TS}.tgz" -C "$OUT_ROOT" "state_${TS}"

log "DONE: $OUT_ROOT/state_${TS}.tgz"
ls -lh "$OUT_ROOT/state_${TS}.tgz"
