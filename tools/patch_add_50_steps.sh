#!/usr/bin/env bash
set -euo pipefail
BASE=/root/bot_finuchet
KB="$BASE/ui/keyboards.py"
ts=$(date +%Y%m%d-%H%M%S)
backup(){ [ -f "$1" ] && cp -a "$1" "$1.bak-$ts"; }

echo "==> patch: add -50/+50 to limit keyboard"
backup "$KB"

python3 - "$KB" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text(encoding="utf-8")

changed = False

# 1) Если есть явный список шагов типа LIMIT_STEPS / STEPS
def add_steps_block(m):
    body = m.group(2)
    steps = [x.strip() for x in re.split(r"[,\s]+", body) if x.strip()]
    # Приведём к строкам для сравнения
    norm = [x.strip("'\"") for x in steps]
    if "-50" not in norm:
        steps.insert(steps.index("-100")+1 if "-100" in norm else 0, "'-50'")
    if "+50" not in norm and ("+100" in norm):
        steps.insert(steps.index("+100"), "'+50'")
    new = ", ".join(steps)
    return f"{m.group(1)}{new}{m.group(3)}"

pat_steps = re.compile(r"(\b(?:LIMIT_)?STEPS\s*=\s*\()[^)]+(\))", re.S)
s2 = pat_steps.sub(lambda m: add_steps_block((lambda s=m.group(0): re.match(r"(\b(?:LIMIT_)?STEPS\s*=\s*\()([^)]+)(\))", s))[0]), s)
if s2 != s:
    s = s2
    changed = True

# 2) Если кнопки собраны в явных массивах внутри функции клавиатуры
#   добавим '-50' сразу после '-100'
s2 = re.sub(r"(['\"]-100['\"]\s*,)", r"\1 '-50',", s)
if s2 != s:
    s = s2
    changed = True
#   добавим '+50' сразу перед '+100'
s2 = re.sub(r"(?<!\+)('|\")\+?100(\1)", r"'+50', '+100'", s)
if s2 != s:
    s = s2
    changed = True

if changed:
    p.write_text(s, encoding="utf-8")
    print("patched:", p)
else:
    print("no change needed:", p)
PY

echo "==> done. restart: systemctl restart finuchet"
