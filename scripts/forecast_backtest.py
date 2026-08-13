from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.forecast_models import metrics_json
from services.forecast_training import backtest_candidates, synthetic_observations


def main() -> None:
    result = backtest_candidates(synthetic_observations())
    champion = result["champion"]
    print(f"champion={champion.family}:{champion.version} metrics={metrics_json(champion)}")


if __name__ == "__main__":
    main()
