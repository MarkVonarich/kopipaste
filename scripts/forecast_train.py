from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.forecast_models import PooledQuantileGBDTModel, load_model_artifact, save_model_artifact
from services.forecast_training import synthetic_observations


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a trusted local forecast artifact.")
    parser.add_argument("--synthetic", action="store_true", help="Required safety switch for repository smoke runs.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.synthetic:
        raise SystemExit("production training is not enabled by this release; use --synthetic for smoke validation")
    model = PooledQuantileGBDTModel().fit(synthetic_observations())
    checksum = save_model_artifact(model, args.output, {"source": "synthetic", "feature_schema": "forecast-features-v1"})
    _loaded, metadata = load_model_artifact(args.output, checksum)
    if metadata.get("feature_schema") != "forecast-features-v1":
        raise SystemExit("artifact metadata verification failed")
    print(f"artifact_sha256={checksum}")


if __name__ == "__main__":
    main()
