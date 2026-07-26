from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.posthog_exporter import dry_run_event_counts, export_once, load_posthog_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely preview or run one PostHog outbox export batch.")
    parser.add_argument("--send", action="store_true", help="Send to PostHog. Requires POSTHOG_EXPORT_ENABLED=true.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--event-uuid", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_posthog_config()
    if not args.send:
        try:
            counts = dry_run_event_counts(limit=args.limit, event_uuid=args.event_uuid)
        except Exception as exc:
            print(f"dry_run unavailable: {type(exc).__name__}")
            return 2
        total = sum(counts.values())
        print(f"dry_run: would_claim={total}")
        for event_name, count in sorted(counts.items()):
            print(f"{event_name}: {count}")
        return 0

    if not config.can_send:
        print(f"send disabled: {config.error_code}")
        return 2
    summary = export_once(limit=args.limit, event_uuid=args.event_uuid, dry_run=False)
    print(
        "send: "
        f"claimed={summary.claimed} sent={summary.sent} retried={summary.retried} "
        f"dead_letter={summary.dead_letter} skipped={summary.skipped} "
        f"duration_ms={summary.duration_ms} error_code={summary.error_code or 'none'}"
    )
    return 0 if summary.error_code in {None, "http_400", "http_401", "http_403", "http_413"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
