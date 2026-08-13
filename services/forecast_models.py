from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from random import Random
from typing import Any, Iterable, Protocol


MONEY_STEP = Decimal("0.01")
QUANTILES = (Decimal("0.50"), Decimal("0.80"), Decimal("0.90"))


def money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def quantile(values: Iterable[Decimal], probability: Decimal) -> Decimal:
    ordered = sorted(money(value) for value in values)
    if not ordered:
        return Decimal("0.00")
    position = (Decimal(len(ordered) - 1) * probability)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - Decimal(lower)
    return money(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction)


@dataclass(frozen=True)
class ForecastObservation:
    snapshot_key: str
    as_of_ordinal: int
    horizon_days: int
    elapsed_ratio: Decimal
    realized_expense: Decimal
    recent_daily_pace: Decimal
    weekday: int
    cycle_day: int
    operation_count: int
    coverage_ratio: Decimal
    target_remainder: Decimal

    def features(self) -> tuple[float, ...]:
        return (
            float(self.horizon_days),
            float(self.elapsed_ratio),
            float(self.realized_expense),
            float(self.recent_daily_pace),
            float(self.weekday),
            float(self.cycle_day),
            float(self.operation_count),
            float(self.coverage_ratio),
        )


@dataclass(frozen=True)
class QuantilePrediction:
    q50: Decimal
    q80: Decimal
    q90: Decimal
    family: str
    version: str

    def __post_init__(self) -> None:
        if not self.q50 <= self.q80 <= self.q90:
            raise ValueError("non_monotonic_quantiles")

    def as_dict(self) -> dict[str, str]:
        return {
            "q50": str(money(self.q50)),
            "q80": str(money(self.q80)),
            "q90": str(money(self.q90)),
            "family": self.family,
            "version": self.version,
        }


class ForecastModel(Protocol):
    family: str
    version: str

    def fit(self, observations: list[ForecastObservation]) -> "ForecastModel": ...
    def predict(self, observation: ForecastObservation) -> QuantilePrediction: ...


def monotonic_prediction(values: Iterable[Any], family: str, version: str) -> QuantilePrediction:
    ordered = sorted(money(value) for value in values)
    while len(ordered) < 3:
        ordered.append(ordered[-1] if ordered else Decimal("0.00"))
    return QuantilePrediction(ordered[0], ordered[1], ordered[2], family, version)


class RobustRemainderModel:
    family = "robust_remainder"
    version = "robust-v1"

    def __init__(self) -> None:
        self._remainders: list[Decimal] = []

    def fit(self, observations: list[ForecastObservation]) -> "RobustRemainderModel":
        self._remainders = [money(item.target_remainder) for item in observations if item.coverage_ratio > 0]
        return self

    def predict(self, observation: ForecastObservation) -> QuantilePrediction:
        values = self._remainders or [money(observation.recent_daily_pace * observation.horizon_days)]
        return QuantilePrediction(
            quantile(values, QUANTILES[0]),
            quantile(values, QUANTILES[1]),
            quantile(values, QUANTILES[2]),
            self.family,
            self.version,
        )


class SeasonalRemainderModel:
    family = "seasonal_temporal"
    version = "seasonal-v1"

    def __init__(self) -> None:
        self._rows: list[ForecastObservation] = []

    def fit(self, observations: list[ForecastObservation]) -> "SeasonalRemainderModel":
        self._rows = list(observations)
        return self

    def predict(self, observation: ForecastObservation) -> QuantilePrediction:
        ranked = sorted(
            self._rows,
            key=lambda row: (
                abs(row.horizon_days - observation.horizon_days) * 3
                + abs(row.cycle_day - observation.cycle_day)
                + (0 if row.weekday == observation.weekday else 2)
            ),
        )[:8]
        values = [row.target_remainder for row in ranked]
        if not values:
            values = [money(observation.recent_daily_pace * observation.horizon_days)]
        return QuantilePrediction(
            quantile(values, QUANTILES[0]),
            quantile(values, QUANTILES[1]),
            quantile(values, QUANTILES[2]),
            self.family,
            self.version,
        )


class PooledQuantileGBDTModel:
    family = "pooled_quantile_gbdt"
    version = "gbdt-v1"

    def __init__(self) -> None:
        self._models: list[Any] = []
        self._fallback = RobustRemainderModel()

    def fit(self, observations: list[ForecastObservation]) -> "PooledQuantileGBDTModel":
        self._fallback.fit(observations)
        if len(observations) < 12:
            return self
        from sklearn.ensemble import HistGradientBoostingRegressor

        features = [item.features() for item in observations]
        targets = [float(item.target_remainder) for item in observations]
        self._models = [
            HistGradientBoostingRegressor(
                loss="quantile",
                quantile=float(level),
                max_iter=80,
                max_leaf_nodes=12,
                min_samples_leaf=5,
                random_state=19,
            ).fit(features, targets)
            for level in QUANTILES
        ]
        return self

    def predict(self, observation: ForecastObservation) -> QuantilePrediction:
        if not self._models:
            fallback = self._fallback.predict(observation)
            return QuantilePrediction(fallback.q50, fallback.q80, fallback.q90, self.family, f"{self.version}-fallback")
        values = [model.predict([observation.features()])[0] for model in self._models]
        return monotonic_prediction(values, self.family, self.version)


@dataclass(frozen=True)
class CalibrationResult:
    state: str
    offsets: tuple[Decimal, Decimal, Decimal]
    sample_count: int
    empirical_coverage_80: Decimal | None
    empirical_coverage_90: Decimal | None


def calibrate_quantiles(predictions: list[QuantilePrediction], actuals: list[Decimal], minimum_samples: int = 12) -> CalibrationResult:
    if len(predictions) != len(actuals) or len(actuals) < minimum_samples:
        return CalibrationResult("insufficient", (Decimal("0.00"),) * 3, len(actuals), None, None)
    residuals = [money(actual - prediction.q50) for prediction, actual in zip(predictions, actuals)]
    offsets = tuple(quantile(residuals, level) for level in QUANTILES)
    coverage80 = Decimal(sum(actual <= prediction.q80 for prediction, actual in zip(predictions, actuals))) / Decimal(len(actuals))
    coverage90 = Decimal(sum(actual <= prediction.q90 for prediction, actual in zip(predictions, actuals))) / Decimal(len(actuals))
    return CalibrationResult("calibrated", offsets, len(actuals), coverage80, coverage90)


def apply_calibration(prediction: QuantilePrediction, calibration: CalibrationResult) -> QuantilePrediction:
    if calibration.state != "calibrated":
        return prediction
    return monotonic_prediction(
        (prediction.q50 + calibration.offsets[0], prediction.q80 + calibration.offsets[1], prediction.q90 + calibration.offsets[2]),
        prediction.family,
        prediction.version,
    )


def deterministic_seed(fingerprint: str) -> int:
    return int.from_bytes(hashlib.sha256(fingerprint.encode("ascii", "ignore")).digest()[:8], "big")


def bootstrap_scenarios(values: Iterable[Decimal], *, fingerprint: str, count: int = 1000) -> list[Decimal]:
    samples = [money(value) for value in values]
    if not samples or count < 1:
        return []
    random = Random(deterministic_seed(fingerprint))
    return sorted(samples[random.randrange(len(samples))] for _ in range(count))


def pinball_loss(actual: Decimal, predicted: Decimal, level: Decimal) -> Decimal:
    error = money(actual - predicted)
    return money(max(level * error, (level - Decimal("1")) * error))


@dataclass(frozen=True)
class BacktestMetrics:
    folds: int
    mae: Decimal
    mase: Decimal | None
    pinball_q50: Decimal
    pinball_q80: Decimal
    pinball_q90: Decimal
    coverage_q80: Decimal
    coverage_q90: Decimal
    interval_width: Decimal
    breach_rate: Decimal


@dataclass(frozen=True)
class BacktestResult:
    family: str
    version: str
    metrics: BacktestMetrics
    predictions: tuple[QuantilePrediction, ...]
    actuals: tuple[Decimal, ...]


def rolling_origin_backtest(model_factory: Any, observations: list[ForecastObservation], *, minimum_train: int = 6) -> BacktestResult:
    ordered = sorted(observations, key=lambda item: item.as_of_ordinal)
    predictions: list[QuantilePrediction] = []
    actuals: list[Decimal] = []
    naive_errors: list[Decimal] = []
    for index in range(minimum_train, len(ordered)):
        training = ordered[:index]
        target = ordered[index]
        if max(item.as_of_ordinal for item in training) >= target.as_of_ordinal:
            raise ValueError("forecast_backtest_leakage")
        model: ForecastModel = model_factory().fit(training)
        predictions.append(model.predict(target))
        actuals.append(money(target.target_remainder))
        naive_errors.append(abs(money(target.target_remainder - training[-1].target_remainder)))
    if not predictions:
        raise ValueError("insufficient_backtest_history")
    errors = [abs(money(actual - prediction.q50)) for prediction, actual in zip(predictions, actuals)]
    mae = money(sum(errors) / Decimal(len(errors)))
    naive = money(sum(naive_errors) / Decimal(len(naive_errors))) if naive_errors else Decimal("0.00")
    metrics = BacktestMetrics(
        folds=len(predictions),
        mae=mae,
        mase=money(mae / naive) if naive > 0 else None,
        pinball_q50=money(sum(pinball_loss(a, p.q50, QUANTILES[0]) for p, a in zip(predictions, actuals)) / Decimal(len(actuals))),
        pinball_q80=money(sum(pinball_loss(a, p.q80, QUANTILES[1]) for p, a in zip(predictions, actuals)) / Decimal(len(actuals))),
        pinball_q90=money(sum(pinball_loss(a, p.q90, QUANTILES[2]) for p, a in zip(predictions, actuals)) / Decimal(len(actuals))),
        coverage_q80=Decimal(sum(a <= p.q80 for p, a in zip(predictions, actuals))) / Decimal(len(actuals)),
        coverage_q90=Decimal(sum(a <= p.q90 for p, a in zip(predictions, actuals))) / Decimal(len(actuals)),
        interval_width=money(sum(p.q90 - p.q50 for p in predictions) / Decimal(len(predictions))),
        breach_rate=Decimal(sum(a > p.q80 for p, a in zip(predictions, actuals))) / Decimal(len(actuals)),
    )
    sample = predictions[0]
    return BacktestResult(sample.family, sample.version, metrics, tuple(predictions), tuple(actuals))


def select_champion(results: list[BacktestResult], *, robust_family: str = "robust_remainder", max_breach_delta: Decimal = Decimal("0.03")) -> BacktestResult:
    if not results:
        raise ValueError("no_backtest_results")
    robust = next((item for item in results if item.family == robust_family), None)
    if robust is None:
        raise ValueError("robust_benchmark_required")
    eligible = [item for item in results if item.metrics.breach_rate <= robust.metrics.breach_rate + max_breach_delta]
    return min(eligible or [robust], key=lambda item: (item.metrics.pinball_q80, item.metrics.mae, item.family))


def save_model_artifact(model: Any, path: str | Path, metadata: dict[str, Any]) -> str:
    import joblib

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "metadata": metadata}, target)
    return hashlib.sha256(target.read_bytes()).hexdigest()


def load_model_artifact(path: str | Path, expected_sha256: str) -> tuple[Any, dict[str, Any]]:
    import joblib

    target = Path(path)
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if actual != expected_sha256:
        raise ValueError("model_artifact_checksum_mismatch")
    payload = joblib.load(target)
    if not isinstance(payload, dict) or "model" not in payload or not isinstance(payload.get("metadata"), dict):
        raise ValueError("invalid_model_artifact")
    return payload["model"], payload["metadata"]


def trusted_artifact_path(model_directory: str | Path, artifact_path: str | Path) -> Path:
    root = Path(model_directory).expanduser().resolve(strict=True)
    candidate = Path(artifact_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.expanduser().resolve(strict=True)
    if candidate == root or root not in candidate.parents:
        raise ValueError("model_artifact_outside_trusted_directory")
    return candidate


def load_trusted_model_artifact(
    model_directory: str | Path,
    artifact_path: str | Path,
    expected_sha256: str,
    *,
    expected_feature_schema: str,
) -> tuple[Any, dict[str, Any]]:
    model, metadata = load_model_artifact(trusted_artifact_path(model_directory, artifact_path), expected_sha256)
    if metadata.get("feature_schema") != expected_feature_schema:
        raise ValueError("model_artifact_feature_schema_mismatch")
    return model, metadata


def metrics_json(result: BacktestResult) -> str:
    payload = asdict(result.metrics)
    return json.dumps({key: str(value) if isinstance(value, Decimal) else value for key, value in payload.items()}, sort_keys=True)
