# tools/patch_fx_hook_messages.py — v2025.08.19-02
import re, time, pathlib

p = pathlib.Path("/root/bot_finuchet/routers/messages.py")
src = p.read_text(encoding="utf-8")
bak = p.with_suffix(".py.bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
bak.write_text(src, encoding="utf-8")

changed = 0

# A. Убедимся, что есть import logging и log = logging.getLogger(__name__)
if "import logging" not in src:
    # вставим после блока импортов
    src = re.sub(r"(\n)(from .*?import .*?\n|import .*?\n)+", lambda m: m.group(0) + "import logging\n", src, count=1)
    changed += 1
if "getLogger(__name__)" not in src:
    # добавим после импортов
    src = re.sub(r"(import logging\s*\n)", r"\1\nlog = logging.getLogger(__name__)\n", src, count=1)
    changed += 1

# B. Перед каждой строкой с convert_amount_if_needed(...) вставим FX-хук
hook_tpl = """{indent}# FX hook: повторная детекция валюты по сырому тексту
{indent}try:
{indent}    raw_text = (getattr(getattr(update, 'effective_message', None), 'text', None)
{indent}                or getattr(getattr(update, 'message', None), 'text', None)
{indent}                or text)
{indent}except Exception:
{indent}    raw_text = text
{indent}if not src_curr:
{indent}    src_curr = detect_currency_token(raw_text or "")
{indent}    log.info("FX HOOK: src_curr=%s raw=%r", src_curr, (raw_text or "")[:160])
"""

lines = src.splitlines(True)
out = []
i = 0
while i < len(lines):
    line = lines[i]
    out.append(line)
    if re.search(r'amt_final\s*,\s*note\s*=\s*convert_amount_if_needed\(', line):
        # если хук уже рядом — не дублируем
        neighborhood = "".join(lines[max(0, i-5):i+1])
        if "FX HOOK:" not in neighborhood:
            indent = re.match(r"\s*", line).group(0)
            out.append(hook_tpl.format(indent=indent))
            changed += 1
        # также гарантируем, что следом переопределяем amt_raw (чтоб ниже везде использовался конверт)
        nxt = lines[i+1] if i+1 < len(lines) else ""
        if "amt_raw = amt_final" not in nxt:
            out.append(f"{indent}amt_raw = amt_final\n")
            changed += 1
    i += 1

new = "".join(out)
if new != src:
    p.write_text(new, encoding="utf-8")

print(f"PATCH FX hook messages.py: changes={changed}, backup={bak.name}")
