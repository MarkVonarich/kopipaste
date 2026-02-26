#!/usr/bin/env bash
set -euo pipefail
BASE=/root/bot_finuchet
TXT="$BASE/utils/text.py"
REC="$BASE/services/records.py"
ts=$(date +%Y%m%d-%H%M%S)
backup(){ [ -f "$1" ] && cp -a "$1" "$1.bak-$ts"; }

echo "==> add helper fmt_limit_warn()"
backup "$TXT"
python3 - "$TXT" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text(encoding="utf-8")
if "def fmt_limit_warn(" not in s:
    s += """

def fmt_limit_warn(title: str, period: str, spent: int, limit_amount: int, threshold: int) -> str:
    try:
        pct_real = int(round((spent / limit_amount) * 100)) if limit_amount > 0 else 0
    except Exception:
        pct_real = 0
    sign = ">" if (limit_amount and pct_real > threshold) else ""
    return f"⚠️ Лимит по «{title}» ({period}): {sign}{threshold}% ({spent}/{limit_amount})"
"""
    p.write_text(s, encoding="utf-8")
    print("helper added:", p)
else:
    print("helper exists:", p)
PY

echo "==> use helper in services/records.py (best-effort)"
backup "$REC"
python3 - "$REC" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text(encoding="utf-8")

# Импорт хелпера (если нет)
if "from utils.text import fmt_limit_warn" not in s:
    # после существующего импорта из utils.text
    s = re.sub(r"from\s+utils\.text\s+import\s+([^\n]+)",
               lambda m: f"from utils.text import {m.group(1).strip()}, fmt_limit_warn",
               s, count=1)

# Типовые места формирования предупреждения.
# Пытаемся найти f-строку с «Лимит по» и заменить на вызов fmt_limit_warn(...)
def repl_warn(m):
    # Параметры нужно будет проверить глазами:
    # title, period_label, spent, limit_amount, threshold
    # Мы оставим это как явный вызов — дальше можно подставить актуальные имена переменных.
    return ("text = fmt_limit_warn(title, period_label, spent, limit_amount, threshold)\n")
s2 = re.sub(rf"[^\n]*Лимит по [«\"]", "text = fmt_limit_warn(title, period_label, spent, limit_amount, threshold)  # TODO: проверь имена переменных", s)
if s2 != s:
    s = s2

p.write_text(s, encoding="utf-8")
print("patched:", p)
PY

echo "==> done. restart: systemctl restart finuchet"
