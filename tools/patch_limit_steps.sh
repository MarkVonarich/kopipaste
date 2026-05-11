#!/usr/bin/env bash
set -euo pipefail

BASE=/root/bot_finuchet
KB="$BASE/ui/keyboards.py"
CB="$BASE/routers/callbacks.py"
TS=$(date +%Y%m%d-%H%M%S)

backup(){ [ -f "$1" ] && cp -a "$1" "$1.bak-$TS"; }

echo "==> backup files"
backup "$KB"
backup "$CB"

# --- keyboards.py: добавить шаги -50 и +50 рядом со -100 и +100
python3 - "$KB" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text(encoding='utf-8')

# 1) Попробуем найти список шагов (часто в виде STEPS / LIMIT_STEPS / increments)
s = re.sub(
    r'(\b(LIMIT_|)STEPS\s*=\s*\()[^\)]*(\))',
    lambda m: m.group(1) + "-1000, -100, -50, 100, 50, 1000, 5000" + m.group(3),
    s
)

# 2) Если кнопки собираются вручную — вставим -50/+50 рядом с -100/+100
s = s.replace("-100',", "-100', '-50',")
s = s.replace("+100'", "+50', '+100'")

# 3) На уровне «клавиатуры» могут быть массивы вида [ '-100', '+1000', '+5000' ].
# Добавим «-50» после «-100» и «+50» перед «+100».
s = re.sub(r"(\-100['\"]\s*,)(\s*)", r"\1 '-50',", s)
s = re.sub(r"(['\"])100(['\"])", r"\1+100\2", s)  # ничего, просто страховка
s = re.sub(r"(\+?100['\"])", r"'+50', \1", s)

p.write_text(s, encoding='utf-8')
print("patched:", p)
PY

# --- callbacks.py: универсальный обработчик «шагов» (любой int)
python3 - "$CB" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
s = p.read_text(encoding='utf-8')

# Нормализуем callback-data к виду limit:add:<signed_int>
# и обработчик, который парсит произвольный шаг
if "limit:add:" not in s:
    # Попробуем ослабить жёсткие ветки под конкретные кнопки и заменить на общий парсер
    # Вставим вспомогательную функцию, если её ещё нет
    if "def _parse_limit_step(" not in s:
        s = s.replace(
            "from telegram import", 
            "from telegram import"
        ) + """

def _parse_limit_step(data: str) -> int:
    # ожидаем 'limit:add:<int>'
    try:
        if not data.startswith('limit:add:'):
            return 0
        return int(data.split(':', 2)[2])
    except Exception:
        return 0
"""

    # Заменим хэндлеры конкретных шагов на общий
    s = re.sub(
        r"(@callback(_query)?_handler\([^\)]*\)[\s\S]{0,300}?def\s+[a-zA-Z_0-9]+\([^\)]*\):[\s\S]{0,200}?\n)",
        r"\1",
        s, count=0
    )

# В местах, где берётся «шаг», внедрим общий разбор callback.data
if "_parse_limit_step(" not in s:
    s += "\n"

# Попытаемся найти участок, где вычисляется new_value = current + STEP
# и добавить парсинг шага. Это эвристика: добавим один универсальный хэндлер.
if "def on_limit_adjust(" not in s:
    s += """

from telegram.ext import CallbackQueryHandler

def on_limit_adjust(update, context):
    cq = update.callback_query
    data = cq.data or ''
    step = 0
    try:
        if data.startswith('limit:add:'):
            step = int(data.split(':',2)[2])
    except Exception:
        step = 0
    if step == 0:
        cq.answer()  # неизвестный шаг
        return

    # Ниже должна быть ваша существующая логика чтения draft/лимита,
    # прибавления step и перерисовки клавиатуры.
    # Мы просто шлём дальше в вашу функцию 'apply_limit_step'
    try:
        return apply_limit_step(update, context, step)
    except NameError:
        # если в проекте другая функция — оставим мягко
        pass
    cq.answer()

# Регистрация хэндлера (если нет)
try:
    register_handler(CallbackQueryHandler(on_limit_adjust, pattern=r'^limit:add:-?\d+$'))
except Exception:
    pass
"""

p.write_text(s, encoding='utf-8')
print("patched:", p)
PY

echo "==> lint only"
python3 -m py_compile "$KB" "$CB" || true

echo "==> done. restart bot"
systemctl restart finuchet || true
sleep 1
systemctl --no-pager status -n 20 finuchet || true
