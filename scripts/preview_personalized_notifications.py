#!/usr/bin/env python
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.notification_preview import build_preview, render_admin_preview


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run personalized notification preview. Does not send Telegram messages.")
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--kind", choices=["auto", "subscription", "recurring-spend", "limit"], default="auto")
    args = parser.parse_args()
    preview = build_preview(args.user_id, args.kind)
    print(render_admin_preview(preview))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
