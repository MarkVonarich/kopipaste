#!/usr/bin/env python3
import sys, re, io, os
from pathlib import Path

if len(sys.argv) < 3:
    print("Usage: extract_from_dump.py <dump.txt> <relative_path> [--out <dst>]", file=sys.stderr); sys.exit(2)

dump_path = Path(sys.argv[1])
rel_path  = sys.argv[2].lstrip("./")
dst_path  = None
if "--out" in sys.argv:
    i = sys.argv.index("--out")
    dst_path = Path(sys.argv[i+1])
else:
    dst_path = Path(rel_path)

if not dump_path.exists() or dump_path.stat().st_size == 0:
    print(f"❌ dump not found or empty: {dump_path}", file=sys.stderr); sys.exit(1)

# Поддерживаем 2 разметки:
# A) "----- BEGIN FILE: routers/messages.py -----" ... "----- END FILE: routers/messages.py -----"
# B) "===== BEGIN ./routers/messages.py =====" ... "===== END ./routers/messages.py ====="
# Будем матчить обе, нормализуя путь (с/без "./").
beg_a = re.compile(r"^[-=]{3,}\s*BEGIN FILE:\s*(.+?)\s*[-=]{3,}\s*$")
end_a = re.compile(r"^[-=]{3,}\s*END FILE:\s*(.+?)\s*[-=]{3,}\s*$")
beg_b = re.compile(r"^[-=]{3,}\s*BEGIN\s+\.?/?(.+?)\s*[-=]{3,}\s*$")
end_b = re.compile(r"^[-=]{3,}\s*END\s+\.?/?(.+?)\s*[-=]{3,}\s*$")

target_norm = rel_path.replace("\\","/")

buf = []
in_block = False
current = None

with dump_path.open("r", encoding="utf-8", errors="ignore") as f:
    for line in f:
        lb = line.rstrip("\r\n")

        m1 = beg_a.match(lb) or beg_b.match(lb)
        if m1:
            p = m1.group(1).lstrip("./")
            if p == target_norm:
                in_block = True
                current = p
            else:
                in_block = False
            continue

        m2 = end_a.match(lb) or end_b.match(lb)
        if m2:
            p = m2.group(1).lstrip("./")
            if in_block and p == target_norm:
                break
            else:
                in_block = False
            continue

        if in_block:
            buf.append(line)

if not buf:
    print(f"❌ Block not found in dump: {rel_path}", file=sys.stderr); sys.exit(3)

dst_path.parent.mkdir(parents=True, exist_ok=True)
with dst_path.open("w", encoding="utf-8") as out:
    out.write("".join(buf))

print(f"✅ Extracted {rel_path} -> {dst_path} ({len(buf)} lines)")
