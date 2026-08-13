from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.forecast_models import metrics_json
from services.forecast_training import backtest_candidates, backtest_from_snapshots, synthetic_observations


def main() -> None:
    parser = argparse.ArgumentParser(description="Run rolling-origin forecast backtests.")
    parser.add_argument("--from-snapshots", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-production", action="store_true")
    parser.add_argument("--limit", type=int, default=5000)
    args = parser.parse_args()
    if args.from_snapshots:
        if not args.execute:
            print("forecast snapshot backtest dry-run: add --execute with an explicitly safe DSN")
            return
        result = backtest_from_snapshots(
            limit=args.limit,
            database_url=os.getenv("DATABASE_URL", ""),
            allow_production=args.allow_production,
        )
        if result["status"] != "evaluated":
            print(f"status={result['status']}")
            return
    else:
        result = backtest_candidates(synthetic_observations())
    champion = result["champion"]
    print(f"champion={champion.family}:{champion.version} metrics={metrics_json(champion)}")


if __name__ == "__main__":
    main()
