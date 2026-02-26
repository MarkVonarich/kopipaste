#!/usr/bin/env bash
set -euo pipefail

BASE="/root/bot_finuchet"
OUT="${BASE}/_backups/state_bundles"
KEEP="${KEEP:-15}"
OPS_LIMIT="${OPS_LIMIT:-50000}"
TTL_SECONDS="${TTL_SECONDS:-600}"

ts="$(date +%Y%m%d-%H%M%S)"
B="${OUT}/state_${ts}"
mkdir -p "${B}/code" "${OUT}"

echo "[1/7] Collect code snapshot (excluding .env, caches, backups)…"
rsync -a \
  --exclude='.env' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='_backups' --exclude='.state' --exclude='.git' \
  "${BASE}/" "${B}/code/"

echo "[2/7] Collect systemd + python metadata…"
systemctl cat finuchet > "${B}/systemd_finuchet.txt" || true
systemctl show finuchet -p ExecStart -p Environment -p EnvironmentFiles -p WorkingDirectory --no-pager > "${B}/systemd_show.txt" || true
python3 -V > "${B}/python_version.txt" 2>&1 || true
python3 -m pip freeze > "${B}/pip_freeze.txt" 2>&1 || true
python3 - << 'PY' > "${B}/python_sys_path.txt" 2>&1 || true
import sys
print("\n".join(sys.path))
PY

echo "[3/7] Collect DB schema + tables list…"
cd /tmp
sudo -u postgres pg_dump -d finance_bot -s > "${B}/db_schema.sql"
sudo -u postgres psql -d finance_bot -c "\dt+" > "${B}/db_tables.txt"

echo "[4/7] DB counts (quick sanity)…"
sudo -u postgres psql -d finance_bot -c "
select 'operations' as t, count(*) from public.operations
union all select 'global_aliases', count(*) from public.global_aliases
union all select 'user_aliases', count(*) from public.user_aliases
union all select 'category_limits', count(*) from public.category_limits
union all select 'users', count(*) from public.users
union all select 'action_tokens', count(*) from public.action_tokens
order by 1;" > "${B}/db_counts.txt"

echo "[5/7] Export data samples (operations + aliases + users)…"

# --- operations export: adapt to real columns
HAS_COL() {
  local tbl="$1" col="$2"
  sudo -u postgres psql -d finance_bot -tAc \
    "select 1 from information_schema.columns where table_schema='public' and table_name='${tbl}' and column_name='${col}' limit 1" \
    | grep -q 1
}

# choose comment column name
COMMENT_COL="comment"
if ! HAS_COL "operations" "comment"; then
  if HAS_COL "operations" "note"; then COMMENT_COL="note"; else COMMENT_COL="comment"; fi
fi

RAW_COL="raw_text"
if ! HAS_COL "operations" "raw_text"; then RAW_COL="raw_text"; fi

# currency might not exist — fallback to empty
CURRENCY_SELECT="'' as currency"
if HAS_COL "operations" "currency"; then CURRENCY_SELECT="currency"; fi

sudo -u postgres psql -d finance_bot -c "\copy (
  select
    id,
    chat_id,
    created_at,
    type,
    category,
    amount,
    ${CURRENCY_SELECT},
    coalesce(${COMMENT_COL}, '') as comment,
    coalesce(${RAW_COL}, '') as raw_text
  from public.operations
  order by created_at desc
  limit ${OPS_LIMIT}
) to stdout csv header" > "${B}/operations_ml_sample.csv"

sudo -u postgres psql -d finance_bot -c "\copy (select * from public.global_aliases) to stdout csv header" > "${B}/global_aliases.csv"
sudo -u postgres psql -d finance_bot -c "\copy (select * from public.user_aliases)  to stdout csv header" > "${B}/user_aliases.csv"

# users export: only columns that exist
# (username might be missing if миграция не сделана)
SELECT_USERS="select user_id"
for c in username display_name locale currency tz_offset_min reminder_hour plan ml_consent onboarding_done created_at updated_at; do
  if HAS_COL "users" "${c}"; then
    SELECT_USERS="${SELECT_USERS}, ${c}"
  fi
done
SELECT_USERS="${SELECT_USERS} from public.users order by user_id"

sudo -u postgres psql -d finance_bot -c "\copy (${SELECT_USERS}) to stdout csv header" > "${B}/users.csv"

echo "[6/7] Pack…"
tar -czf "${OUT}/state_${ts}.tgz" -C "${OUT}" "state_${ts}"

echo "[7/7] Cleanup old bundles (keep=${KEEP})…"
ls -1dt "${OUT}"/state_* 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -rf || true

ls -lh "${OUT}/state_${ts}.tgz"
echo "DONE"
