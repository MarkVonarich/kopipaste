from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json

from services.forecast_models import (
    ForecastObservation,
    PooledQuantileGBDTModel,
    RobustRemainderModel,
    SeasonalRemainderModel,
    calibrate_quantiles,
    load_trusted_model_artifact,
    money,
    rolling_origin_backtest,
    save_model_artifact,
    select_champion,
)
from services.forecasting import (
    FEATURE_SCHEMA_VERSION,
    RISK_POLICY_VERSION,
    TARGET_VALIDITY_POLICY_VERSION,
    evaluate_target_validity,
    operation_is_deterministic,
)


log = logging.getLogger(__name__)
TRAINING_LOCK_ID = 742_024_001
MIN_TRAINING_OBSERVATIONS = 18


def normalize_training_currency(currency: str) -> str:
    normalized = str(currency or "").strip().upper()
    if not normalized or len(normalized) > 12 or not normalized.isalnum():
        raise ValueError("invalid_training_currency")
    return normalized


def training_fingerprint(
    currency: str,
    family: str,
    observations: list[ForecastObservation],
) -> str:
    payload = {
        "currency": normalize_training_currency(currency),
        "family": family,
        "feature_schema": FEATURE_SCHEMA_VERSION,
        "risk_policy": RISK_POLICY_VERSION,
        "training_cutoff": max((item.as_of_ordinal for item in observations), default=None),
        "observations": [
            {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in asdict(item).items()
            }
            for item in sorted(observations, key=lambda value: (value.as_of_ordinal, value.snapshot_key))
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def publish_immutable_artifact(temporary_path: Path, final_path: Path, checksum: str) -> None:
    try:
        os.link(temporary_path, final_path)
    except FileExistsError:
        existing_checksum = hashlib.sha256(final_path.read_bytes()).hexdigest()
        if existing_checksum != checksum:
            raise ValueError("model_artifact_immutable_collision")
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def synthetic_observations(count: int = 30) -> list[ForecastObservation]:
    return [
        ForecastObservation(
            snapshot_key=f"synthetic-{index}",
            as_of_ordinal=738900 + index,
            horizon_days=5 + index % 18,
            elapsed_ratio=Decimal("0.35") + Decimal(index % 8) / Decimal("20"),
            realized_expense=Decimal(8000 + index * 190),
            recent_daily_pace=Decimal(500 + index % 7 * 45),
            weekday=index % 7,
            cycle_day=1 + index % 28,
            operation_count=5 + index % 15,
            coverage_ratio=Decimal("0.70") + Decimal(index % 4) / Decimal("10"),
            target_remainder=Decimal(5200 + (index % 6) * 650 + index * 85),
        )
        for index in range(count)
    ]


def backtest_candidates(observations: list[ForecastObservation]) -> dict[str, Any]:
    factories = (RobustRemainderModel, SeasonalRemainderModel, PooledQuantileGBDTModel)
    results = [rolling_origin_backtest(factory, observations) for factory in factories]
    champion = select_champion(results)
    return {"champion": champion, "challengers": tuple(item for item in results if item is not champion)}


def _training_rows(cur: Any, limit: int, currency: str) -> list[tuple[Any, ...]]:
    cur.execute(
        """
        SELECT s.source_fingerprint,
               s.as_of_date,
               s.horizon_days,
               s.features,
               s.actual_variable_expense
          FROM public.forecast_snapshots s
         WHERE s.outcome_finalized_at IS NOT NULL
           AND s.invalidated_at IS NULL
           AND s.feature_schema_version=%s
           AND s.currency=%s
           AND s.actual_variable_expense IS NOT NULL
           AND s.target_valid=TRUE
         ORDER BY s.as_of_date, s.id
         LIMIT %s
        """,
        (FEATURE_SCHEMA_VERSION, currency, max(1, min(int(limit), 50000))),
    )
    return list(cur.fetchall())


def observations_from_rows(rows: list[tuple[Any, ...]]) -> list[ForecastObservation]:
    observations: list[ForecastObservation] = []
    for fingerprint, as_of, horizon, raw_features, target in rows:
        features = raw_features if isinstance(raw_features, dict) else {}
        tracked_days = max(0, int(features.get("tracked_days") or 0))
        operation_count = max(0, int(features.get("operation_count") or 0))
        elapsed_days = max(1, int(features.get("elapsed_days") or tracked_days or 1))
        observations.append(ForecastObservation(
            snapshot_key=str(fingerprint),
            as_of_ordinal=as_of.toordinal(),
            horizon_days=max(0, int(horizon or 0)),
            elapsed_ratio=Decimal(str(features.get("elapsed_ratio") or 0)),
            realized_expense=Decimal(str(features.get("realized_variable_expense") or features.get("realized_expense") or 0)),
            recent_daily_pace=Decimal(str(features.get("recent_daily_pace") or 0)),
            weekday=as_of.weekday(),
            cycle_day=max(1, int(features.get("cycle_day") or 1)),
            operation_count=operation_count,
            coverage_ratio=min(Decimal("1"), Decimal(tracked_days) / Decimal(elapsed_days)),
            target_remainder=Decimal(str(target)),
        ))
    return observations


def extract_training_observations(currency: str, limit: int = 5000, *, cur: Any | None = None) -> list[ForecastObservation]:
    exact_currency = normalize_training_currency(currency)
    if cur is not None:
        return observations_from_rows(_training_rows(cur, limit, exact_currency))
    from db.database import pg_fetchall

    rows = pg_fetchall(
        """
        SELECT s.source_fingerprint, s.as_of_date, s.horizon_days,
               s.features, s.actual_variable_expense
          FROM public.forecast_snapshots s
         WHERE s.outcome_finalized_at IS NOT NULL
           AND s.invalidated_at IS NULL
           AND s.feature_schema_version=%s
           AND s.currency=%s
           AND s.actual_variable_expense IS NOT NULL
           AND s.target_valid=TRUE
         ORDER BY s.as_of_date, s.id LIMIT %s
        """,
        (FEATURE_SCHEMA_VERSION, exact_currency, max(1, min(int(limit), 50000))),
    )
    return observations_from_rows(list(rows))


def register_model(
    *,
    currency: str,
    family: str,
    version: str,
    status: str,
    artifact_path: str,
    artifact_sha256: str,
    training_cutoff: date,
    metrics: dict[str, Any],
    calibration: dict[str, Any],
) -> bool:
    from db.database import get_conn

    if status not in {"candidate", "challenger", "champion"}:
        raise ValueError("invalid_model_status")
    exact_currency = normalize_training_currency(currency)
    if status == "champion":
        from settings import FORECAST_MODEL_DIR

        if not FORECAST_MODEL_DIR:
            raise ValueError("forecast_model_directory_required")
        _model, metadata = load_trusted_model_artifact(
            FORECAST_MODEL_DIR,
            artifact_path,
            artifact_sha256,
            expected_feature_schema=FEATURE_SCHEMA_VERSION,
        )
        if (
            metadata.get("risk_policy") != RISK_POLICY_VERSION
            or metadata.get("model_family") != family
            or metadata.get("model_version") != version
            or str(metadata.get("currency") or "").upper() != exact_currency
        ):
            raise ValueError("model_artifact_metadata_mismatch")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if not acquire_training_lock(cur):
                conn.rollback()
                return False
            if status == "champion":
                cur.execute(
                    "UPDATE public.forecast_model_registry SET status='challenger', updated_at=now() WHERE currency=%s AND status='champion'",
                    (exact_currency,),
                )
            cur.execute(
                """
                INSERT INTO public.forecast_model_registry
                  (currency, model_family, model_version, status, artifact_path, artifact_sha256,
                   feature_schema_version, risk_policy_version, training_cutoff, metrics, calibration)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (currency, model_family, model_version) DO UPDATE
                   SET status=EXCLUDED.status,
                       artifact_path=EXCLUDED.artifact_path,
                       artifact_sha256=EXCLUDED.artifact_sha256,
                       feature_schema_version=EXCLUDED.feature_schema_version,
                       risk_policy_version=EXCLUDED.risk_policy_version,
                       training_cutoff=EXCLUDED.training_cutoff,
                       metrics=EXCLUDED.metrics,
                       calibration=EXCLUDED.calibration,
                       updated_at=now()
                """,
                (exact_currency, family, version, status, artifact_path, artifact_sha256, FEATURE_SCHEMA_VERSION, RISK_POLICY_VERSION, training_cutoff, Json(metrics), Json(calibration)),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def calibration_metadata(result: Any) -> dict[str, Any]:
    calibrated = calibrate_quantiles(list(result.predictions), list(result.actuals), minimum_samples=12)
    return {
        "state": calibrated.state,
        "offsets": [str(value) for value in calibrated.offsets],
        "sample_count": calibrated.sample_count,
        "empirical_coverage_80": str(calibrated.empirical_coverage_80) if calibrated.empirical_coverage_80 is not None else None,
        "empirical_coverage_90": str(calibrated.empirical_coverage_90) if calibrated.empirical_coverage_90 is not None else None,
    }


def _metric_payload(result: Any, *, eligible: bool) -> dict[str, Any]:
    payload = {
        key: str(value) if isinstance(value, Decimal) else value
        for key, value in asdict(result.metrics).items()
    }
    payload["guardrail_eligible"] = bool(eligible)
    return payload


def ensure_safe_training_dsn(database_url: str, *, allow_production: bool = False) -> None:
    is_test = "finuchet_test" in str(database_url or "")
    production_enabled = os.getenv("FORECAST_PRODUCTION_TRAINING_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if not is_test and not (allow_production and production_enabled):
        raise RuntimeError("unsafe_forecast_training_dsn")


def train_from_snapshots(
    *,
    currency: str,
    limit: int,
    model_directory: str | Path,
    database_url: str,
    allow_production: bool = False,
) -> dict[str, Any]:
    ensure_safe_training_dsn(database_url, allow_production=allow_production)
    exact_currency = normalize_training_currency(currency)
    from db.database import get_conn

    root = Path(model_directory).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    artifact_path: Path | None = None
    temporary_artifact: Path | None = None
    try:
        with conn.cursor() as cur:
            if not acquire_training_lock(cur):
                conn.rollback()
                return {"status": "locked"}
            observations = extract_training_observations(exact_currency, limit, cur=cur)
            if len(observations) < MIN_TRAINING_OBSERVATIONS:
                raise ValueError("insufficient_training_dataset")
            evaluated = backtest_candidates(observations)
            champion_result = evaluated["champion"]
            factories = {
                RobustRemainderModel.family: RobustRemainderModel,
                SeasonalRemainderModel.family: SeasonalRemainderModel,
                PooledQuantileGBDTModel.family: PooledQuantileGBDTModel,
            }
            model = factories[champion_result.family]().fit(observations)
            dataset_fingerprint = training_fingerprint(exact_currency, champion_result.family, observations)
            cutoff = date.fromordinal(max(item.as_of_ordinal for item in observations))
            version = (
                f"{champion_result.family}-{exact_currency.lower()}-"
                f"{cutoff.strftime('%Y%m%d')}-{dataset_fingerprint[:12]}"
            )
            artifact_path = root / f"{version}.joblib"
            temporary_artifact = root / f".{version}.{os.getpid()}.tmp"
            checksum = save_model_artifact(model, temporary_artifact, {
                "feature_schema": FEATURE_SCHEMA_VERSION,
                "risk_policy": RISK_POLICY_VERSION,
                "model_family": champion_result.family,
                "model_version": version,
                "currency": exact_currency,
                "training_cutoff": cutoff.isoformat(),
                "training_fingerprint": dataset_fingerprint,
            })
            _temporary_model, temporary_metadata = load_trusted_model_artifact(
                root, temporary_artifact, checksum, expected_feature_schema=FEATURE_SCHEMA_VERSION,
            )
            if (
                str(temporary_metadata.get("currency") or "").upper() != exact_currency
                or temporary_metadata.get("training_fingerprint") != dataset_fingerprint
            ):
                raise ValueError("model_artifact_currency_mismatch")
            publish_immutable_artifact(temporary_artifact, artifact_path, checksum)
            _loaded_model, persisted_metadata = load_trusted_model_artifact(
                root, artifact_path, checksum, expected_feature_schema=FEATURE_SCHEMA_VERSION,
            )
            if persisted_metadata != temporary_metadata:
                raise ValueError("model_artifact_metadata_mismatch")
            robust = next(item for item in (champion_result, *evaluated["challengers"]) if item.family == RobustRemainderModel.family)
            all_results = (champion_result, *evaluated["challengers"])
            for result in all_results:
                eligible = result.metrics.breach_rate <= robust.metrics.breach_rate + Decimal("0.03")
                status = "champion" if result is champion_result else "challenger"
                result_fingerprint = training_fingerprint(exact_currency, result.family, observations)
                result_version = (
                    version if result is champion_result else
                    f"{result.family}-{exact_currency.lower()}-{cutoff.strftime('%Y%m%d')}-{result_fingerprint[:12]}"
                )
                if status == "champion":
                    cur.execute(
                        "UPDATE public.forecast_model_registry SET status='challenger', updated_at=now() WHERE currency=%s AND status='champion'",
                        (exact_currency,),
                    )
                cur.execute(
                    """
                    INSERT INTO public.forecast_model_registry
                      (currency, model_family, model_version, status, artifact_path, artifact_sha256,
                       feature_schema_version, risk_policy_version, training_cutoff, metrics, calibration)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (currency, model_family, model_version) DO UPDATE
                       SET status=EXCLUDED.status, artifact_path=EXCLUDED.artifact_path,
                           artifact_sha256=EXCLUDED.artifact_sha256, metrics=EXCLUDED.metrics,
                           calibration=EXCLUDED.calibration, updated_at=now()
                    """,
                    (
                        exact_currency,
                        result.family,
                        result_version,
                        status,
                        str(artifact_path) if result is champion_result else None,
                        checksum if result is champion_result else None,
                        FEATURE_SCHEMA_VERSION,
                        RISK_POLICY_VERSION,
                        cutoff,
                        Json(_metric_payload(result, eligible=eligible)),
                        Json(calibration_metadata(result)),
                    ),
                )
        conn.commit()
        return {
            "status": "trained",
            "family": champion_result.family,
            "version": version,
            "currency": exact_currency,
            "observations": len(observations),
            "artifact_sha256": checksum,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
        if temporary_artifact is not None and temporary_artifact.exists():
            temporary_artifact.unlink()


def backtest_from_snapshots(*, currency: str, limit: int, database_url: str, allow_production: bool = False) -> dict[str, Any]:
    ensure_safe_training_dsn(database_url, allow_production=allow_production)
    exact_currency = normalize_training_currency(currency)
    from db.database import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if not acquire_training_lock(cur):
                conn.rollback()
                return {"status": "locked"}
            observations = extract_training_observations(exact_currency, limit, cur=cur)
            if len(observations) < MIN_TRAINING_OBSERVATIONS:
                raise ValueError("insufficient_training_dataset")
            result = backtest_candidates(observations)
        conn.rollback()
        return {"status": "evaluated", "currency": exact_currency, "observations": len(observations), **result}
    finally:
        conn.close()


def finalize_forecast_outcomes(limit: int = 200, *, now_utc: datetime | None = None) -> dict[str, int]:
    from db.database import get_conn
    from services.user_time import user_local_date

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, workspace_id, currency, legacy_default_currency,
                       period_start, period_end, as_of_date,
                       known_commitment_facts, goal_reserve_facts
                  FROM public.forecast_snapshots
                 WHERE outcome_finalized_at IS NULL AND invalidated_at IS NULL
                   AND period_end <= CURRENT_DATE
                 ORDER BY period_end, id
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED
                """,
                (max(1, min(int(limit), 1000)),),
            )
            candidates = list(cur.fetchall())
            finalized = 0
            current_utc = now_utc or datetime.now(timezone.utc)
            for snapshot_id, user_id, workspace_id, currency, default_currency, period_start, period_end, as_of_date, known_facts, goal_facts in candidates:
                if period_end >= user_local_date(int(user_id), workspace_id, now_utc=current_utc):
                    continue
                goal_ids = {int(item["goal_id"]) for item in (goal_facts or []) if item.get("goal_id") is not None}
                cur.execute(
                    """
                    SELECT o.id, o.op_date, o.type, o.amount,
                           COALESCE(o.currency, %s), COALESCE(o.source,''),
                           COALESCE(o.comment,''), COALESCE(o.category,''),
                           EXISTS (
                               SELECT 1 FROM public.goal_movements gm
                                WHERE gm.linked_operation_id=o.id
                                  AND gm.goal_id=ANY(%s)
                                  AND gm.movement_type IN ('initial','contribution')
                           ) AS goal_linked
                      FROM public.operations o
                     WHERE o.workspace_id IS NOT DISTINCT FROM %s
                       AND (%s IS NOT NULL OR o.user_id=%s)
                       AND o.op_date BETWEEN %s AND %s
                       AND (o.currency=%s OR (o.currency IS NULL AND %s=%s))
                     ORDER BY o.op_date, o.id
                    """,
                    (default_currency, list(goal_ids) or [0], workspace_id, workspace_id, int(user_id), period_start, period_end, currency, default_currency, currency),
                )
                operations = [
                    {
                        "id": row[0], "op_date": row[1], "type": row[2], "amount": money(row[3]),
                        "currency": row[4], "source": row[5], "comment": row[6], "category": row[7],
                        "goal_linked": bool(row[8]),
                    }
                    for row in cur.fetchall()
                ]
                variable = sum((
                    item["amount"] for item in operations
                    if item["type"] == "Расходы"
                    and item["category"] != "Без операций"
                    and item["op_date"] > as_of_date
                    and not operation_is_deterministic(item, known_facts or [], allow_source_markers=False)
                ), Decimal("0.00"))
                income = sum((item["amount"] for item in operations if item["type"] == "Доходы" and item["category"] != "Без операций"), Decimal("0.00"))
                expense = sum((item["amount"] for item in operations if item["type"] == "Расходы" and item["category"] != "Без операций"), Decimal("0.00"))
                target_rows = [item for item in operations if item["op_date"] > as_of_date]
                target_operation_count = sum(item["category"] != "Без операций" and item["type"] in {"Доходы", "Расходы"} for item in target_rows)
                target_expense_count = sum(
                    item["type"] == "Расходы" and item["category"] != "Без операций"
                    for item in target_rows
                )
                target_expense_days = {
                    item["op_date"] for item in target_rows
                    if item["type"] == "Расходы" and item["category"] != "Без операций"
                }
                target_marker_days = {
                    item["op_date"] for item in target_rows
                    if item["type"] == "noop" and item["category"] == "Без операций"
                    and item["op_date"] not in target_expense_days
                }
                target_tracked_days = len(target_expense_days | target_marker_days)
                target_valid, target_reason, target_coverage = evaluate_target_validity(
                    horizon_days=(period_end - as_of_date).days,
                    expense_count=target_expense_count,
                    expense_tracked_days=len(target_expense_days),
                    no_operation_marker_days=len(target_marker_days),
                    variable_expense=money(variable),
                )
                cur.execute(
                    """
                    UPDATE public.forecast_snapshots
                       SET actual_variable_expense=%s, actual_end_result=%s,
                           target_operation_count=%s, target_tracked_days=%s,
                           target_expense_count=%s, target_expense_tracked_days=%s,
                           target_no_operation_marker_days=%s,
                           target_coverage_ratio=%s, target_valid=%s,
                           target_validity_reason=%s, target_validity_policy_version=%s,
                           outcome_finalized_at=now(), updated_at=now()
                     WHERE id=%s AND outcome_finalized_at IS NULL
                    """,
                    (
                        money(variable), money(income - expense), target_operation_count,
                        target_tracked_days, target_expense_count, len(target_expense_days),
                        len(target_marker_days), target_coverage, target_valid, target_reason,
                        TARGET_VALIDITY_POLICY_VERSION, snapshot_id,
                    ),
                )
                finalized += int(cur.rowcount)
        conn.commit()
        return {"finalized": finalized}
    except errors.UndefinedTable:
        conn.rollback()
        return {"finalized": 0}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def acquire_training_lock(cur) -> bool:
    cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (TRAINING_LOCK_ID,))
    return bool(cur.fetchone()[0])
