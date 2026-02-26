#!/usr/bin/env python3
# tools/make_snapshot_v1.py — snapshot selected files into STATE.yml and archive
import hashlib, os, tarfile, sys
from pathlib import Path
from datetime import datetime

ROOT = Path("/root/bot_finuchet")
STATE = ROOT / "STATE.yml"
SNAPDIR = ROOT / "_snapshots"

DEFAULT_FILES = [
    "routers/callbacks.py",
    "services/records.py",
    "db/queries.py",
    "routers/messages.py",
    "ui/messages.py",
    "utils/text.py",
    "services/parsing.py",     # если нет — пропустим
]

def sha8(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:8]

def read_versions_map(state_path: Path):
    """Вернём {path: [versions...]} наивным парсером по строкам."""
    versions = {}
    if not state_path.exists():
        return versions
    cur_path = None
    for line in state_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.rstrip()
        if line.strip().startswith("- path:"):
            # пример: "- path: services/records.py"
            cur_path = line.split(":",1)[1].strip().strip('"').strip("'")
            versions.setdefault(cur_path, [])
        elif cur_path and line.strip().startswith("version:"):
            v = line.split(":",1)[1].strip().strip('"').strip("'")
            versions[cur_path].append(v)
        elif not line.strip():
            cur_path = None
    return versions

def next_version_for(path_str: str, existing: dict) -> str:
    today = datetime.now().strftime("%Y.%m.%d")
    seq = 0
    for v in existing.get(path_str, []):
        if v.startswith(today + "-"):
            try:
                n = int(v.split("-",1)[1])
                if n > seq:
                    seq = n
            except Exception:
                pass
    return f"{today}-{seq+1:02d}"

def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)

def main():
    file_args = sys.argv[1:]
    paths = file_args if file_args else DEFAULT_FILES
    files: list[Path] = []
    for rel in paths:
        p = ROOT / rel
        if p.exists() and p.is_file():
            files.append(p)
        else:
            print(f"[skip] {rel} (not found)")
    if not files:
        print("No files to snapshot, exiting.")
        return

    ensure_dir(SNAPDIR)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snap_root = SNAPDIR / stamp
    ensure_dir(snap_root)

    versions_map = read_versions_map(STATE)

    lines_to_append = []
    summary = []

    for p in files:
        rel = str(p.relative_to(ROOT))
        ver = next_version_for(rel, versions_map)
        sh = sha8(p)
        size = p.stat().st_size
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")

        # копия в каталог снапшота
        dst = snap_root / rel
        ensure_dir(dst.parent)
        dst.write_bytes(p.read_bytes())

        # подготовим блок для STATE.yml
        block = (
            f"  - path: {rel}\n"
            f"    version: \"{ver}\"\n"
            f"    sha8: {sh}\n"
            f"    size: {size}\n"
            f"    mtime: \"{mtime}\"\n"
        )
        lines_to_append.append(block)
        summary.append((rel, ver, sh, size))

    # запишем в STATE.yml
    if not STATE.exists():
        STATE.write_text("files:\n", encoding="utf-8")
    with STATE.open("a", encoding="utf-8") as fp:
        for block in lines_to_append:
            fp.write(block)

    # архив снапшота
    tar_path = SNAPDIR / f"snap-{stamp}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(snap_root, arcname=snap_root.name)

    # отчёт
    print("== snapshot created ==")
    print("folder:", snap_root)
    print("archive:", tar_path)
    for rel, ver, sh, size in summary:
        print(f"- {rel}  ver={ver}  sha8={sh}  size={size}")

if __name__ == "__main__":
    main()
