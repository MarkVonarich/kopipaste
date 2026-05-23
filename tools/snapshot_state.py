# tools/snapshot_state.py — v2025.08.18-02
__version__ = "2025.08.18-02"

import os, sys, re, hashlib, datetime, textwrap
from pathlib import Path

ROOT = Path("/root/bot_finuchet")
STATE = ROOT / "STATE.yml"
STATUS = ROOT / "STATUS.md"
ENV_FILE = Path("/root/.env")

def sha8_path(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:8]

def get_version_and_dunder(p: Path):
    ver_comment = "UNKNOWN"
    dunder = "UNKNOWN"
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            head = [next(f, "") for _ in range(5)]
        for line in head:
            m = re.search(r"#\s+.+?\s+—\s+v(\d{4}\.\d{2}\.\d{2}-\d{2})", line)
            if m:
                ver_comment = m.group(1)
                break
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "__version__" in line:
                    m2 = re.search(r'__version__\s*=\s*"([^"]+)"', line)
                    if m2:
                        dunder = m2.group(1)
                    break
    except Exception:
        pass
    return ver_comment, dunder

def read_env_keys():
    keys = []
    if ENV_FILE.exists():
        for raw in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k = line.split("=", 1)[0].strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                keys.append(k)
    return sorted(set(keys))

def env_checksum8():
    if not ENV_FILE.exists():
        return "missing"
    return sha8_path(ENV_FILE)

def scan_files():
    files = []
    for p in ROOT.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        ver, dunder = get_version_and_dunder(p)
        stat = p.stat()
        files.append({
            "path": rel,
            "version": ver,
            "dunder": dunder,
            "sha8": sha8_path(p),
            "size": stat.st_size,
            "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    files.sort(key=lambda x: x["path"])
    return files

def fetch_db_schema():
    """
    Возвращает dict: {table: [ {column, type, nullable, default}, ... ] }.
    Без падения, если нет соединения.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return {}
    try:
        import psycopg2  # noqa: F401
        import psycopg2.extras  # noqa: F401
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        cur.execute("""
            SELECT c.table_name,
                   c.column_name,
                   COALESCE(c.udt_name, c.data_type) AS col_type,
                   c.is_nullable,
                   c.column_default
              FROM information_schema.columns c
             WHERE c.table_schema = 'public'
             ORDER BY c.table_name, c.ordinal_position
        """)
        rows = cur.fetchall()
        cur.close(); conn.close()
    except Exception:
        return {}
    schema = {}
    for t, col, typ, nullable, default in rows:
        schema.setdefault(t, []).append({
            "column": col,
            "type": typ,
            "nullable": (nullable == "YES"),
            "default": default if default is not None else "",
        })
    return schema

def to_yaml(data) -> str:
    """Мини-YAML без внешних библиотек."""
    def esc(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        s = str(v)
        if re.search(r"[:#\-\[\]\{\},&*!|>'\"%@`]", s) or s.strip()!=s or "\n" in s:
            s = s.replace('"','\\"')
            return f'"{s}"'
        return s or '""'

    lines = []
    lines.append("# STATE.yml — generated snapshot")
    lines.append(f"generated_at: {esc(datetime.datetime.now().isoformat(timespec='seconds'))}")
    lines.append(f"tool_version: {esc(__version__)}")
    lines.append(f"env_checksum8: {esc(env_checksum8())}")
    lines.append("env_keys:")
    for k in read_env_keys():
        lines.append(f"  - {esc(k)}")
    files = data.get("files", [])
    lines.append("files:")
    for f in files:
        lines.append("  - path: " + esc(f["path"]))
        lines.append("    version: " + esc(f["version"]))
        lines.append("    dunder: " + esc(f["dunder"]))
        lines.append("    sha8: " + esc(f["sha8"]))
        lines.append("    size: " + esc(f["size"]))
        lines.append("    mtime: " + esc(f["mtime"]))
    schema = data.get("db_schema", {})
    lines.append("db_schema:")
    for table in sorted(schema.keys()):
        lines.append(f"  {table}:")
        for col in schema[table]:
            lines.append("    - column: " + esc(col["column"]))
            lines.append("      type: " + esc(col["type"]))
            lines.append("      nullable: " + esc(col["nullable"]))
            lines.append("      default: " + esc(col["default"]))
    return "\n".join(lines) + "\n"

def append_status(summary: str):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    with STATUS.open("a", encoding="utf-8") as f:
        f.write("\n## Snapshot {}\n".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        f.write(summary.strip() + "\n")

def main():
    files = scan_files()
    db_schema = fetch_db_schema()
    out = to_yaml({"files": files, "db_schema": db_schema})
    # Бэкапнем прежний STATE.yml, если был
    if STATE.exists():
        ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        STATE.rename(STATE.with_suffix(f".yml.bak-{ts}"))
    STATE.write_text(out, encoding="utf-8")
    summary = textwrap.dedent(f"""
      - files: {len(files)}
      - db_tables: {len(db_schema)}
      - .env checksum8: {env_checksum8()}
    """).strip()
    append_status(summary)
    print("OK: STATE.yml updated, STATUS.md appended.")
    print(summary)

if __name__ == "__main__":
    sys.exit(main())
