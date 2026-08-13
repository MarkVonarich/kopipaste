from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.forecast_models import PooledQuantileGBDTModel, load_model_artifact, save_model_artifact
from services.forecast_training import synthetic_observations, train_from_snapshots


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a trusted local forecast artifact.")
    parser.add_argument("--synthetic", action="store_true", help="Required safety switch for repository smoke runs.")
    parser.add_argument("--from-snapshots", action="store_true", help="Use finalized aggregate snapshots.")
    parser.add_argument("--execute", action="store_true", help="Required before database-backed training.")
    parser.add_argument("--allow-production", action="store_true", help="Also requires FORECAST_PRODUCTION_TRAINING_ENABLED=true.")
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.from_snapshots:
        if not args.execute:
            print("forecast snapshot training dry-run: add --execute with an explicitly safe DSN")
            return
        model_dir = os.getenv("FORECAST_MODEL_DIR", "").strip()
        if not model_dir:
            raise SystemExit("FORECAST_MODEL_DIR is required")
        result = train_from_snapshots(
            limit=args.limit,
            model_directory=model_dir,
            database_url=os.getenv("DATABASE_URL", ""),
            allow_production=args.allow_production,
        )
        print(f"status={result['status']} family={result.get('family')} observations={result.get('observations')}")
        return
    if not args.synthetic or args.output is None:
        raise SystemExit("choose --synthetic --output PATH or --from-snapshots")
    model = PooledQuantileGBDTModel().fit(synthetic_observations())
    checksum = save_model_artifact(model, args.output, {"source": "synthetic", "feature_schema": "forecast-features-v1"})
    _loaded, metadata = load_model_artifact(args.output, checksum)
    if metadata.get("feature_schema") != "forecast-features-v1":
        raise SystemExit("artifact metadata verification failed")
    print(f"artifact_sha256={checksum}")


if __name__ == "__main__":
    main()
