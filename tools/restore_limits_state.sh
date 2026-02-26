#!/usr/bin/env bash
set -euo pipefail

DUMP_OLD="${1:-/root/bot_finuchet/_backups/code_dump-20250831-155108.txt}"
ROOT="/root/bot_finuchet"

echo "==> Using dump: $DUMP_OLD"
test -s "$DUMP_OLD" || { echo "❌ dump not found: $DUMP_OLD"; exit 1; }

# 1) Бэкапим текущие файлы
cp -f "$ROOT/routers/messages.py" "$ROOT/routers/messages.py.bak-$(date +%Y%m%d-%H%M%S)" || true

# 2) Достаём из дампа правильный routers/messages.py
awk '
  /BEGIN/ && /routers\/messages\.py/ {in=1; next}
  /END/   && /routers\/messages\.py/ {in=0}
  in {print}
' "$DUMP_OLD" > "$ROOT/routers/messages.py"

# 3) Быстрая синтаксическая проверка
python3 -m py_compile "$ROOT/routers/messages.py"

echo "✅ routers/messages.py restored from dump."
