from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.database import pg_fetchall
from services.product_events import failed_local_write_count


def analytics_status_counts() -> dict[str, object]:
    rows = pg_fetchall(
        """
        SELECT
          (SELECT COUNT(*) FROM analytics.product_events WHERE occurred_at >= now() - interval '24 hours') AS product_events_24h,
          (SELECT COUNT(*) FROM analytics.event_outbox WHERE status='pending') AS pending_outbox,
          (SELECT COUNT(*) FROM analytics.event_outbox WHERE status='retrying') AS retrying_outbox,
          (SELECT COUNT(*) FROM analytics.event_outbox WHERE status='dead_letter') AS dead_letter_outbox,
          (SELECT COUNT(*) FROM security.security_events WHERE occurred_at >= now() - interval '24 hours') AS security_events_24h,
          (SELECT COUNT(*) FROM analytics.api_usage_events WHERE occurred_at >= now() - interval '24 hours') AS api_usage_events_24h,
          (SELECT MAX(occurred_at) FROM analytics.product_events) AS last_event_timestamp
        """
    )
    r = rows[0]
    return {
        "product_events_24h": int(r[0] or 0),
        "failed_local_writes": failed_local_write_count(),
        "pending_outbox": int(r[1] or 0),
        "retrying_outbox": int(r[2] or 0),
        "dead_letter_outbox": int(r[3] or 0),
        "security_events_24h": int(r[4] or 0),
        "api_usage_events_24h": int(r[5] or 0),
        "last_event_timestamp": r[6].isoformat() if r[6] else "none",
    }


def render_status(counts: dict[str, object]) -> str:
    return "\n".join(f"{k}: {v}" for k, v in counts.items())


def main() -> int:
    try:
        print(render_status(analytics_status_counts()))
        return 0
    except Exception as exc:
        print(f"analytics_status unavailable: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
