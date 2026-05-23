#!/usr/bin/env bash
set -euo pipefail

BASE="/root/bot_finuchet"
DST="$BASE/_backups"
KEEP=15

mkdir -p "$DST/state" "$DST/code"
shopt -s nullglob

# 1) переносим все бэкапы STATE.yml в _backups/state/
for f in "$BASE"/STATE.yml.bak-*; do
  mv -f "$f" "$DST/state/"
done

# 2) переносим все копии main.py (bak/panic/sos/broken и прочие суффиксы) в _backups/code/
for f in "$BASE"/main.py.*; do
  bn="$(basename "$f")"
  [[ "$bn" == "main.py" ]] && continue
  [[ "$bn" == *.pyc ]] && continue
  mv -f "$f" "$DST/code/"
done

# 3) ротация: оставляем только последние $KEEP
ls -1t "$DST/state"/STATE.yml.bak-* 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
ls -1t "$DST/code"/main.py.*       2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f

echo "Backups moved to $DST and pruned (keep=$KEEP)."
