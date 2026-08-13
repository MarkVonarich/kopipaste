from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.forecast_training import finalize_forecast_outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize bounded forecast outcomes. Never trains a model.")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--execute", action="store_true", help="Use the configured safe database connection.")
    args = parser.parse_args()
    if not args.execute:
        print("forecast backfill dry-run: pass --execute with an explicitly safe DSN")
        return
    print(finalize_forecast_outcomes(args.limit))


if __name__ == "__main__":
    main()
