# tools/patch_fx_messages.py — v2025.08.19-01
import re, sys, time, pathlib
p = pathlib.Path("/root/bot_finuchet/routers/messages.py")
txt = p.read_text(encoding="utf-8")
backup = p.with_suffix(".py.bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
backup.write_text(txt, encoding="utf-8")

changed = 0

# 1) detect_currency_token(text or "")  ->  detect_currency_token((update.message.text ... ) or "")
detect_pat = r'src_curr\s*=\s*detect_currency_token\(\s*text\s*or\s*""\s*\)'
detect_repl = 'src_curr = detect_currency_token((update.message.text if hasattr(update, "message") and getattr(update, "message", None) and getattr(update.message, "text", None) else text) or "")'
new_txt, n = re.subn(detect_pat, detect_repl, txt)
txt = new_txt; changed += n

# 2) после строки "amt_final, note = convert_amount_if_needed(...)" вставить "amt_raw = amt_final"
lines = txt.splitlines(True)
out = []
i = 0
while i < len(lines):
    line = lines[i]
    out.append(line)
    if re.search(r'amt_final\s*,\s*note\s*=\s*convert_amount_if_needed\(', line):
        # если следующая строка уже содержит переопределение — пропускаем
        next_line = lines[i+1] if i+1 < len(lines) else ""
        if "amt_raw = amt_final" not in next_line:
            indent = re.match(r'\s*', line).group(0)
            out.append(f"{indent}amt_raw = amt_final\n")
            changed += 1
    i += 1

txt2 = "".join(out)
if txt2 != "".join(lines):
    p.write_text(txt2, encoding="utf-8")
print(f"PATCH FX messages.py: changes={changed}, backup={backup.name}")
