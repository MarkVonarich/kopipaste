from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.posthog_exporter import export_status_counts


def render_status(counts: dict) -> str:
    keys = [
        "enabled",
        "config_error",
        "host",
        "pending",
        "retrying",
        "processing",
        "sent",
        "dead_letter",
        "oldest_pending_timestamp",
        "last_sent_timestamp",
    ]
    return "\n".join(f"{key}: {counts.get(key)}" for key in keys)


def main() -> int:
    try:
        print(render_status(export_status_counts()))
        return 0
    except Exception as exc:
        print(f"posthog_export_status unavailable: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
