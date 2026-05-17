# tools/patch_fx_hook_messages_v4.py — v2025.08.19-04
import re, time, pathlib

p = pathlib.Path("/root/bot_finuchet/routers/messages.py")
src = p.read_text(encoding="utf-8")
bak = p.with_suffix(".py.bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
bak.write_text(src, encoding="utf-8")

changed = 0

def ensure_imports(s: str) -> str:
    global changed
    if "import logging" not in s:
        s = re.sub(r"(\n)(from .*?import .*?\n|import .*?\n)+",
                   lambda m: m.group(0) + "import logging\n", s, count=1)
        changed += 1
    if "getLogger(__name__)" not in s:
        s = re.sub(r"(import logging\s*\n)", r"\1\nlog = logging.getLogger(__name__)\n", s, count=1)
        changed += 1
    if "from services.currency import" in s and "detect_currency_token" not in s:
        s = re.sub(r"(from\s+services\.currency\s+import\s+)([^\n]+)",
                   lambda m: m.group(1) + m.group(2).strip() + ", detect_currency_token",
                   s, count=1)
        changed += 1
    return s

src = ensure_imports(src)

HOOK = """{indent}# FX hook (universal): повторная детекция валюты по сырому тексту (до конвертации)
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

    if "convert_amount_if_needed(" in line:
        # уже есть хук прямо над вызовом?
        prev_block = "".join(out[-8:])  # смотрим последние ~8 строк, которые уже в out
        if "FX HOOK:" not in prev_block:
            indent = re.match(r"\s*", line).group(0)
            out.append(HOOK.format(indent=indent))
            changed += 1

        # теперь сама строка с вызовом
        out.append(line)

        # после вызова выравниваем amt_raw = <lhs_first>, если можно распарсить
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\s*,\s*[A-Za-z_][A-Za-z0-9_]*)?)\s*=\s*convert_amount_if_needed\(", line)
        if m:
            first_field = m.group(1).split(",")[0].strip()
            indent = re.match(r"\s*", line).group(0)
            # избегаем дублирования, если следующая строка уже присваивает amt_raw =
            nxt = lines[i+1] if i+1 < len(lines) else ""
            if "amt_raw =" not in nxt:
                out.append(f"{indent}amt_raw = {first_field}\n")
                changed += 1
        i += 1
        continue

    out.append(line)
    i += 1

new = "".join(out)
if new != src:
    p.write_text(new, encoding="utf-8")

print(f"PATCH FX hook v4 messages.py: changes={changed}, backup={bak.name}")
