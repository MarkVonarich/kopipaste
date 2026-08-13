from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json

from services.forecast_models import (
    ForecastObservation,
    PooledQuantileGBDTModel,
    RobustRemainderModel,
    SeasonalRemainderModel,
    rolling_origin_backtest,
    select_champion,
)


log = logging.getLogger(__name__)
TRAINING_LOCK_ID = 742_024_001
FEATURE_SCHEMA_VERSION = "forecast-features-v1"
RISK_POLICY_VERSION = "downside-q80-v1"


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


def extract_training_observations(limit: int = 5000) -> list[ForecastObservation]:
    from db.database import pg_fetchall

    rows = pg_fetchall(
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
           AND s.actual_variable_expense IS NOT NULL
         ORDER BY s.as_of_date, s.id
         LIMIT %s
        """,
        (FEATURE_SCHEMA_VERSION, max(1, min(int(limit), 50000))),
    )
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
            realized_expense=Decimal(str(features.get("realized_expense") or 0)),
            recent_daily_pace=Decimal(str(features.get("recent_daily_pace") or 0)),
            weekday=as_of.weekday(),
            cycle_day=max(1, int(features.get("cycle_day") or 1)),
            operation_count=operation_count,
            coverage_ratio=min(Decimal("1"), Decimal(tracked_days) / Decimal(elapsed_days)),
            target_remainder=Decimal(str(target)),
        ))
    return observations


def register_model(
    *,
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
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if not acquire_training_lock(cur):
                conn.rollback()
                return False
            if status == "champion":
                cur.execute(
                    "UPDATE public.forecast_model_registry SET status='challenger', updated_at=now() WHERE status='champion'"
                )
            cur.execute(
                """
                INSERT INTO public.forecast_model_registry
                  (model_family, model_version, status, artifact_path, artifact_sha256,
                   feature_schema_version, risk_policy_version, training_cutoff, metrics, calibration)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (model_family, model_version) DO UPDATE
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
                (family, version, status, artifact_path, artifact_sha256, FEATURE_SCHEMA_VERSION, RISK_POLICY_VERSION, training_cutoff, Json(metrics), Json(calibration)),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finalize_forecast_outcomes(limit: int = 200) -> dict[str, int]:
    from db.database import get_conn

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH due AS (
                    SELECT id, user_id, workspace_id, currency, period_start, period_end, as_of_date
                      FROM public.forecast_snapshots
                     WHERE outcome_finalized_at IS NULL AND invalidated_at IS NULL
                       AND period_end < CURRENT_DATE
                     ORDER BY period_end, id
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                ), outcomes AS (
                    SELECT d.id,
                           COALESCE(SUM(o.amount) FILTER (
                               WHERE o.type='Расходы' AND o.op_date > d.as_of_date
                           ),0) AS variable_expense,
                           COALESCE(SUM(o.amount) FILTER (WHERE o.type='Доходы'),0)
                           - COALESCE(SUM(o.amount) FILTER (WHERE o.type='Расходы'),0) AS end_result
                      FROM due d
                      LEFT JOIN public.operations o
                        ON o.workspace_id IS NOT DISTINCT FROM d.workspace_id
                       AND (d.workspace_id IS NOT NULL OR o.user_id=d.user_id)
                       AND o.op_date BETWEEN d.period_start AND d.period_end
                       AND COALESCE(o.currency, d.currency)=d.currency
                       AND COALESCE(o.category,'')<>'Без операций'
                     GROUP BY d.id
                )
                UPDATE public.forecast_snapshots s
                   SET actual_variable_expense=o.variable_expense,
                       actual_end_result=o.end_result,
                       outcome_finalized_at=now(),
                       updated_at=now()
                  FROM outcomes o WHERE s.id=o.id
                """,
                (max(1, min(int(limit), 1000)),),
            )
            finalized = int(cur.rowcount)
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
