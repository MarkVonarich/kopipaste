from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Protocol

from psycopg2 import errors

from db.database import get_conn
from services.categories import normalized_category_key
from services.merchant_intelligence import (
    EMPTY_MERCHANT_KEY,
    fold_merchant_rows,
    merchant_features,
    normalize_merchant_key,
)
from utils.money import format_money, to_decimal_money


log = logging.getLogger(__name__)

MAX_VISIBLE_INSIGHTS = 3
MIN_RELATIVE_DELTA = Decimal("0.15")
MIN_CATEGORY_RELATIVE_DELTA = Decimal("0.20")
MIN_CATEGORY_CONTRIBUTION_SHARE = Decimal("0.35")
MIN_OPERATION_COUNT = 3
MIN_FREQUENCY_CURRENT_COUNT = 5
MIN_FREQUENCY_PREVIOUS_COUNT = 3
MIN_FREQUENCY_DELTA = 3
MIN_FREQUENCY_RELATIVE_DELTA = Decimal("0.30")
MIN_AVERAGE_CHECK_RELATIVE_DELTA = Decimal("0.20")
MIN_LIMIT_USED_PERCENT = 70
MIN_LIMIT_PACE_LEAD_PERCENT = 15
MIN_LIMIT_PERIOD_PROGRESS_PERCENT = 20
NEGATIVE_FEEDBACK_SUPPRESSION_DAYS = 30
REPEAT_WINDOW_HOURS = 24
MAX_IMPRESSIONS_IN_REPEAT_WINDOW = 3
FORECAST_INSIGHT_FAMILIES = {
    "forecast_end_result",
    "spendable_change",
    "spendable_risk",
    "future_expense_acceleration",
    "general_budget_breach_risk",
    "category_limit_breach_risk",
    "grouped_budget_breach_risk",
    "goal_affordability",
    "upcoming_commitment_pressure",
    "recurring_pressure",
    "category_projection",
    "category_mix_shift",
    "merchant_driver",
    "frequency_shift",
    "average_check_shift",
    "persistent_spending_trend",
    "unusual_spend_anomaly",
}

# These are significance floors, not exchange rates. Candidates are never converted
# or compared by monetary value across currencies.
MIN_ABSOLUTE_DELTA_BY_CURRENCY: dict[str, Decimal] = {
    "RUB": Decimal("500"),
    "KZT": Decimal("2500"),
    "UZS": Decimal("50000"),
    "TMT": Decimal("20"),
    "USD": Decimal("10"),
    "EUR": Decimal("10"),
    "GBP": Decimal("10"),
    "UAH": Decimal("200"),
    "TRY": Decimal("300"),
    "CNY": Decimal("75"),
    "BYN": Decimal("25"),
    "GEL": Decimal("25"),
    "RSD": Decimal("1000"),
    "AED": Decimal("40"),
    "THB": Decimal("350"),
    "VND": Decimal("250000"),
    "KRW": Decimal("15000"),
    "AMD": Decimal("4000"),
    "AZN": Decimal("20"),
    "EGP": Decimal("500"),
}

ACTION_TYPES = {
    "OPEN_ANALYTICS",
    "OPEN_CATEGORY",
    "OPEN_MERCHANT",
    "OPEN_OPERATIONS",
    "OPEN_LIMIT",
    "CREATE_LIMIT",
}
FEEDBACK_TYPES = {"useful", "not_useful"}


@dataclass(frozen=True)
class PeriodRef:
    key: str
    start: date
    end: date


@dataclass(frozen=True)
class CategoryAggregate:
    key: str
    name: str
    current_total: Decimal
    previous_total: Decimal
    current_count: int
    previous_count: int


@dataclass(frozen=True)
class MerchantAggregate:
    key: str
    name: str
    category_key: str
    category_name: str
    current_total: Decimal
    previous_total: Decimal
    current_count: int
    previous_count: int


@dataclass(frozen=True)
class LimitAggregate:
    identifier: str
    title: str
    category: str | None
    amount: Decimal
    spent: Decimal
    currency: str
    period: str
    used_percent: int
    enabled: bool = True
    kind: str = "category_limit"


@dataclass(frozen=True)
class InsightSnapshot:
    user_id: int
    workspace_id: int | None
    workspace_kind: str
    currency: str
    period: PeriodRef
    comparison_period: PeriodRef
    current_total: Decimal
    previous_total: Decimal
    current_count: int
    previous_count: int
    categories: tuple[CategoryAggregate, ...]
    merchants: tuple[MerchantAggregate, ...]
    limits: tuple[LimitAggregate, ...] = ()
    scope_category: str | None = None
    scope_category_key: str | None = None
    operation_type: str = "expense"
    can_write: bool = True
    forecast: dict[str, Any] | None = None


@dataclass(frozen=True)
class InsightAction:
    type: str
    label: str
    params: dict[str, str | int | None]

    def __post_init__(self) -> None:
        if self.type not in ACTION_TYPES:
            raise ValueError("unsupported insight action")


@dataclass
class InsightCandidate:
    detector_type: str
    family: str
    entity_type: str
    entity_key: str
    currency: str
    current_value: Decimal
    baseline_value: Decimal
    absolute_delta: Decimal
    relative_delta: Decimal | None
    impact: Decimal
    confidence: str
    severity: str
    title_key: str
    content_data: dict[str, Any]
    evidence: list[dict[str, Any]]
    actions: list[InsightAction]
    group_key: str
    period: PeriodRef
    comparison_period: PeriodRef
    operation_type: str = "expense"
    actionability: int = 0
    active_control: bool = False
    score: Decimal = Decimal("0")
    fingerprint: str = ""
    valid_until: datetime | None = None


@dataclass(frozen=True)
class InsightState:
    fingerprint: str
    detector_type: str
    show_count: int = 0
    first_shown_at: datetime | None = None
    last_shown_at: datetime | None = None
    feedback_type: str | None = None
    suppression_until: datetime | None = None


class InsightStateStore(Protocol):
    def load(self, user_id: int, workspace_id: int | None) -> list[InsightState]: ...

    def ensure(self, user_id: int, workspace_id: int | None, candidates: Iterable[InsightCandidate]) -> None: ...

    def record_impression(self, user_id: int, workspace_id: int | None, fingerprint: str) -> InsightState | None: ...

    def record_feedback(self, user_id: int, workspace_id: int | None, fingerprint: str, feedback_type: str) -> InsightState | None: ...


class PostgresInsightStateStore:
    def load(self, user_id: int, workspace_id: int | None) -> list[InsightState]:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT fingerprint, detector_type, show_count, first_shown_at,
                           last_shown_at, feedback_type, suppression_until
                     FROM public.insight_states
                     WHERE user_id=%s
                       AND workspace_scope_key=COALESCE(%s::bigint, 0)
                       AND (valid_until > now() OR suppression_until > now())
                       AND updated_at > now() - interval '120 days'
                    """,
                    (int(user_id), workspace_id),
                )
                rows = cur.fetchall()
            conn.rollback()
        except errors.UndefinedTable:
            conn.rollback()
            return []
        except Exception as exc:
            conn.rollback()
            log.info("insight_state_load_failed reason=%s", type(exc).__name__)
            return []
        finally:
            conn.close()
        return [
            InsightState(
                fingerprint=str(row[0]),
                detector_type=str(row[1]),
                show_count=int(row[2] or 0),
                first_shown_at=row[3],
                last_shown_at=row[4],
                feedback_type=str(row[5]) if row[5] else None,
                suppression_until=row[6],
            )
            for row in rows
        ]

    def ensure(self, user_id: int, workspace_id: int | None, candidates: Iterable[InsightCandidate]) -> None:
        values = list(candidates)
        if not values:
            return
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                for candidate in values:
                    cur.execute(
                        """
                        INSERT INTO public.insight_states
                          (user_id, workspace_id, fingerprint, detector_type,
                           entity_type, currency, period_start, period_end,
                           comparison_start, comparison_end, valid_until)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (user_id, workspace_scope_key, fingerprint)
                        DO UPDATE SET valid_until=GREATEST(public.insight_states.valid_until, EXCLUDED.valid_until),
                                      updated_at=now()
                        """,
                        (
                            int(user_id),
                            workspace_id,
                            candidate.fingerprint,
                            candidate.detector_type,
                            candidate.entity_type,
                            candidate.currency,
                            candidate.period.start,
                            candidate.period.end,
                            candidate.comparison_period.start,
                            candidate.comparison_period.end,
                            candidate.valid_until,
                        ),
                    )
            conn.commit()
        except errors.UndefinedTable:
            conn.rollback()
        except Exception as exc:
            conn.rollback()
            log.info("insight_state_ensure_failed reason=%s", type(exc).__name__)
        finally:
            conn.close()

    def record_impression(self, user_id: int, workspace_id: int | None, fingerprint: str) -> InsightState | None:
        return self._update_state(user_id, workspace_id, fingerprint, feedback_type=None)

    def record_feedback(self, user_id: int, workspace_id: int | None, fingerprint: str, feedback_type: str) -> InsightState | None:
        if feedback_type not in FEEDBACK_TYPES:
            raise ValueError("invalid insight feedback")
        return self._update_state(user_id, workspace_id, fingerprint, feedback_type=feedback_type)

    def _update_state(self, user_id: int, workspace_id: int | None, fingerprint: str, *, feedback_type: str | None) -> InsightState | None:
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                if feedback_type is None:
                    cur.execute(
                        """
                        UPDATE public.insight_states
                           SET first_shown_at=CASE
                                 WHEN first_shown_at IS NULL
                                   OR first_shown_at <= now() - interval '24 hours'
                                 THEN now()
                                 ELSE first_shown_at
                               END,
                               last_shown_at=now(),
                               show_count=CASE
                                 WHEN first_shown_at IS NULL
                                   OR first_shown_at <= now() - interval '24 hours'
                                 THEN 1
                                 ELSE show_count + 1
                               END,
                               updated_at=now()
                         WHERE user_id=%s
                           AND workspace_scope_key=COALESCE(%s::bigint, 0)
                           AND fingerprint=%s
                           AND valid_until > now()
                        RETURNING fingerprint, detector_type, show_count, first_shown_at,
                                  last_shown_at, feedback_type, suppression_until
                        """,
                        (int(user_id), workspace_id, fingerprint),
                    )
                else:
                    suppression = (
                        datetime.now(timezone.utc) + timedelta(days=NEGATIVE_FEEDBACK_SUPPRESSION_DAYS)
                        if feedback_type == "not_useful"
                        else None
                    )
                    cur.execute(
                        """
                        UPDATE public.insight_states
                           SET feedback_type=%s, feedback_at=now(), suppression_until=%s,
                               updated_at=now()
                         WHERE user_id=%s
                           AND workspace_scope_key=COALESCE(%s::bigint, 0)
                           AND fingerprint=%s
                           AND valid_until > now()
                        RETURNING fingerprint, detector_type, show_count, first_shown_at,
                                  last_shown_at, feedback_type, suppression_until
                        """,
                        (feedback_type, suppression, int(user_id), workspace_id, fingerprint),
                    )
                row = cur.fetchone()
            conn.commit()
        except errors.UndefinedTable:
            conn.rollback()
            return None
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        if not row:
            return None
        return InsightState(
            fingerprint=str(row[0]),
            detector_type=str(row[1]),
            show_count=int(row[2] or 0),
            first_shown_at=row[3],
            last_shown_at=row[4],
            feedback_type=str(row[5]) if row[5] else None,
            suppression_until=row[6],
        )


def minimum_absolute_delta(currency: str) -> Decimal:
    return MIN_ABSOLUTE_DELTA_BY_CURRENCY.get(currency.upper(), Decimal("10"))


def _ratio(delta: Decimal, baseline: Decimal) -> Decimal | None:
    if baseline <= 0:
        return None
    return (delta / abs(baseline)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _scope_params(snapshot: InsightSnapshot) -> dict[str, str | int | None]:
    return {
        "workspace_id": snapshot.workspace_id,
        "period": snapshot.period.key,
        "start_date": snapshot.period.start.isoformat(),
        "end_date": snapshot.period.end.isoformat(),
        "operation_type": snapshot.operation_type,
        "category": snapshot.scope_category or "all",
        "scope_category": snapshot.scope_category,
        "currency": snapshot.currency,
    }


def _action(action_type: str, label: str, params: dict[str, str | int | None]) -> InsightAction:
    allowed = {
        "workspace_id", "period", "start_date", "end_date", "operation_type",
        "category", "scope_category", "category_key", "target_category",
        "merchant_key", "currency", "limit_id",
    }
    return InsightAction(action_type, label, {key: value for key, value in params.items() if key in allowed})


def _candidate(
    snapshot: InsightSnapshot,
    *,
    detector_type: str,
    entity_type: str,
    entity_key: str,
    current_value: Decimal,
    baseline_value: Decimal,
    absolute_delta: Decimal,
    relative_delta: Decimal | None,
    confidence: str,
    severity: str,
    title_key: str,
    content_data: dict[str, Any],
    evidence: list[dict[str, Any]],
    actions: list[InsightAction],
    group_key: str,
    actionability: int,
    active_control: bool = False,
    impact_amount: Decimal | None = None,
    family: str | None = None,
) -> InsightCandidate:
    threshold = minimum_absolute_delta(snapshot.currency)
    family_by_detector = {
        "spending_change": "persistent_spending_trend",
        "category_contribution": "category_mix_shift",
        "merchant_contribution": "merchant_driver",
        "merchant_frequency": "frequency_shift",
        "average_check_change": "average_check_shift",
        "limit_pace": "category_limit_breach_risk",
        "spendable_risk": "spendable_risk",
    }
    return InsightCandidate(
        detector_type=detector_type,
        family=family or family_by_detector.get(detector_type, detector_type),
        entity_type=entity_type,
        entity_key=entity_key,
        currency=snapshot.currency,
        current_value=current_value,
        baseline_value=baseline_value,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        impact=abs(impact_amount if impact_amount is not None else absolute_delta) / max(threshold, Decimal("0.01")),
        confidence=confidence,
        severity=severity,
        title_key=title_key,
        content_data=content_data,
        evidence=evidence,
        actions=actions,
        group_key=group_key,
        period=snapshot.period,
        comparison_period=snapshot.comparison_period,
        actionability=actionability,
        active_control=active_control,
    )


def detect_spending_change(snapshot: InsightSnapshot) -> list[InsightCandidate]:
    delta = snapshot.current_total - snapshot.previous_total
    relative = _ratio(delta, snapshot.previous_total)
    if (
        snapshot.previous_total <= 0
        or snapshot.current_count < MIN_OPERATION_COUNT
        or snapshot.previous_count < MIN_OPERATION_COUNT
        or abs(delta) < minimum_absolute_delta(snapshot.currency)
        or relative is None
        or abs(relative) < MIN_RELATIVE_DELTA
    ):
        return []
    direction = "up" if delta > 0 else "down"
    severity = "high" if delta > 0 and relative >= Decimal("0.50") else "medium"
    scope = _scope_params(snapshot)
    scope_category = snapshot.scope_category
    scope_category_key = snapshot.scope_category_key
    return [_candidate(
        snapshot,
        detector_type="spending_change",
        entity_type="category" if scope_category_key else "scope",
        entity_key=scope_category_key or "expenses",
        current_value=snapshot.current_total,
        baseline_value=snapshot.previous_total,
        absolute_delta=delta,
        relative_delta=relative,
        confidence="high",
        severity=severity,
        title_key=f"spending_{direction}",
        content_data={
            "direction": direction,
            "scope_category": scope_category,
            "scope_category_key": scope_category_key,
        },
        evidence=[{
            "kind": "amount_comparison",
            "label": scope_category or "Расходы",
            "current_amount": snapshot.current_total,
            "previous_amount": snapshot.previous_total,
            "delta_amount": delta,
            "currency": snapshot.currency,
        }],
        actions=[_action("OPEN_ANALYTICS", "Посмотреть аналитику", scope)],
        group_key=(
            f"category:{snapshot.currency}:{scope_category_key}"
            if scope_category_key else f"spending:{snapshot.currency}"
        ),
        actionability=6,
    )]


def _relevant_limit_period(snapshot: InsightSnapshot) -> str | None:
    return {
        "current_week": "week",
        "current_month": "month",
    }.get(snapshot.period.key)


def _matching_category_limit(snapshot: InsightSnapshot, category_key: str) -> LimitAggregate | None:
    expected_period = _relevant_limit_period(snapshot)
    if not expected_period:
        return None
    for limit in snapshot.limits:
        if (
            not limit.enabled
            or not limit.category
            or limit.currency != snapshot.currency
            or limit.period != expected_period
        ):
            continue
        try:
            limit_category_key = normalized_category_key(limit.category)
        except ValueError:
            continue
        if limit_category_key == category_key:
            return limit
    return None


def detect_category_contribution(snapshot: InsightSnapshot) -> list[InsightCandidate]:
    if snapshot.scope_category_key:
        return []
    total_delta = snapshot.current_total - snapshot.previous_total
    if total_delta <= 0:
        return []
    threshold = minimum_absolute_delta(snapshot.currency)
    scope = _scope_params(snapshot)
    candidates: list[InsightCandidate] = []
    for category in snapshot.categories:
        delta = category.current_total - category.previous_total
        relative = _ratio(delta, category.previous_total)
        contribution_share = delta / total_delta if total_delta > 0 else Decimal("0")
        if (
            category.previous_total <= 0
            or category.current_count < MIN_OPERATION_COUNT
            or category.previous_count < MIN_OPERATION_COUNT
            or delta < threshold
            or relative is None
            or relative < MIN_CATEGORY_RELATIVE_DELTA
            or contribution_share < MIN_CATEGORY_CONTRIBUTION_SHARE
        ):
            continue
        params = {**scope, "category_key": category.key, "target_category": category.name}
        actions = [
            _action("OPEN_CATEGORY", "Посмотреть категорию", params),
            _action("OPEN_OPERATIONS", "Посмотреть операции", params),
        ]
        matching_limit = _matching_category_limit(snapshot, category.key)
        if snapshot.can_write:
            if matching_limit:
                actions.append(_action(
                    "OPEN_LIMIT",
                    "Открыть лимит",
                    {**params, "limit_id": matching_limit.identifier},
                ))
            else:
                actions.append(_action("CREATE_LIMIT", "Установить лимит", params))
        candidates.append(_candidate(
            snapshot,
            detector_type="category_contribution",
            entity_type="category",
            entity_key=category.key,
            current_value=category.current_total,
            baseline_value=category.previous_total,
            absolute_delta=delta,
            relative_delta=relative,
            confidence="high",
            severity="high" if contribution_share >= Decimal("0.60") else "medium",
            title_key="category_growth",
            content_data={"category": category.name, "contribution_share": contribution_share},
            evidence=[{
                "kind": "amount_comparison",
                "label": category.name,
                "current_amount": category.current_total,
                "previous_amount": category.previous_total,
                "delta_amount": delta,
                "currency": snapshot.currency,
            }, {
                "kind": "contribution_share",
                "label": "Вклад в общий рост",
                "share_pct": int((contribution_share * Decimal("100")).to_integral_value()),
            }],
            actions=actions,
            group_key=f"category:{snapshot.currency}:{category.key}",
            actionability=12 if matching_limit else 10,
            active_control=matching_limit is not None,
        ))
    return candidates


def detect_merchant_contribution(snapshot: InsightSnapshot) -> list[InsightCandidate]:
    category_by_key = {category.key: category for category in snapshot.categories}
    threshold = minimum_absolute_delta(snapshot.currency)
    scope = _scope_params(snapshot)
    candidates: list[InsightCandidate] = []
    for merchant in snapshot.merchants:
        if merchant.key == EMPTY_MERCHANT_KEY:
            continue
        category = category_by_key.get(merchant.category_key)
        if not category:
            continue
        category_delta = category.current_total - category.previous_total
        merchant_delta = merchant.current_total - merchant.previous_total
        share = merchant_delta / category_delta if category_delta > 0 else Decimal("0")
        if (
            category_delta <= 0
            or merchant_delta < threshold
            or merchant.current_count < MIN_OPERATION_COUNT
            or share < MIN_CATEGORY_CONTRIBUTION_SHARE
        ):
            continue
        params = {
            **scope,
            "category_key": merchant.category_key,
            "target_category": merchant.category_name,
            "merchant_key": merchant.key,
        }
        candidates.append(_candidate(
            snapshot,
            detector_type="merchant_contribution",
            entity_type="merchant",
            entity_key=merchant.key,
            current_value=merchant.current_total,
            baseline_value=merchant.previous_total,
            absolute_delta=merchant_delta,
            relative_delta=_ratio(merchant_delta, merchant.previous_total),
            confidence="high" if merchant.previous_count >= MIN_OPERATION_COUNT else "medium",
            severity="medium",
            title_key="merchant_contribution",
            content_data={
                "merchant": merchant.name,
                "category": merchant.category_name,
                "category_key": merchant.category_key,
                "share": share,
                "current_count": merchant.current_count,
                "previous_count": merchant.previous_count,
            },
            evidence=[{
                "kind": "merchant_contribution",
                "label": merchant.name,
                "delta_amount": merchant_delta,
                "currency": snapshot.currency,
                "share_pct": int((share * Decimal("100")).to_integral_value()),
                "current_count": merchant.current_count,
                "previous_count": merchant.previous_count,
                "merchant_key": merchant.key,
            }],
            actions=[
                _action("OPEN_MERCHANT", "Посмотреть магазин", params),
                _action("OPEN_OPERATIONS", "Посмотреть операции", params),
            ],
            group_key=f"category:{snapshot.currency}:{merchant.category_key}",
            actionability=9,
        ))
    return candidates


def detect_frequency_change(snapshot: InsightSnapshot) -> list[InsightCandidate]:
    scope = _scope_params(snapshot)
    candidates: list[InsightCandidate] = []
    for merchant in snapshot.merchants:
        count_delta = merchant.current_count - merchant.previous_count
        relative = Decimal(count_delta) / Decimal(merchant.previous_count) if merchant.previous_count > 0 else None
        if (
            merchant.key == EMPTY_MERCHANT_KEY
            or merchant.current_count < MIN_FREQUENCY_CURRENT_COUNT
            or merchant.previous_count < MIN_FREQUENCY_PREVIOUS_COUNT
            or count_delta < MIN_FREQUENCY_DELTA
            or relative is None
            or relative < MIN_FREQUENCY_RELATIVE_DELTA
        ):
            continue
        params = {
            **scope,
            "category_key": merchant.category_key,
            "target_category": merchant.category_name,
            "merchant_key": merchant.key,
        }
        candidates.append(_candidate(
            snapshot,
            detector_type="merchant_frequency",
            entity_type="merchant",
            entity_key=merchant.key,
            current_value=Decimal(merchant.current_count),
            baseline_value=Decimal(merchant.previous_count),
            absolute_delta=Decimal(count_delta),
            relative_delta=relative,
            confidence="high",
            severity="medium",
            title_key="merchant_frequency_up",
            content_data={
                "merchant": merchant.name,
                "category": merchant.category_name,
                "category_key": merchant.category_key,
                "current_count": merchant.current_count,
                "previous_count": merchant.previous_count,
            },
            evidence=[{
                "kind": "count_comparison",
                "label": "Покупок",
                "current_count": merchant.current_count,
                "previous_count": merchant.previous_count,
            }],
            actions=[
                _action("OPEN_MERCHANT", "Посмотреть магазин", params),
                _action("OPEN_OPERATIONS", "Посмотреть операции", params),
            ],
            group_key=f"merchant:{snapshot.currency}:{merchant.key}",
            actionability=8,
        ))
    return candidates


def detect_average_check_change(snapshot: InsightSnapshot) -> list[InsightCandidate]:
    scope = _scope_params(snapshot)
    candidates: list[InsightCandidate] = []
    minimum_average_delta = minimum_absolute_delta(snapshot.currency) * Decimal("0.20")
    for merchant in snapshot.merchants:
        if (
            merchant.key == EMPTY_MERCHANT_KEY
            or merchant.current_count < MIN_OPERATION_COUNT
            or merchant.previous_count < MIN_OPERATION_COUNT
        ):
            continue
        features = merchant_features(
            current_total=merchant.current_total,
            current_count=merchant.current_count,
            previous_total=merchant.previous_total,
            previous_count=merchant.previous_count,
        )
        current_average = to_decimal_money(features["average_check"])
        previous_average = to_decimal_money(features["previous_average_check"])
        delta = to_decimal_money(features["average_check_delta"])
        relative = _ratio(delta, previous_average)
        if delta < minimum_average_delta or relative is None or relative < MIN_AVERAGE_CHECK_RELATIVE_DELTA:
            continue
        params = {
            **scope,
            "category_key": merchant.category_key,
            "target_category": merchant.category_name,
            "merchant_key": merchant.key,
        }
        candidates.append(_candidate(
            snapshot,
            detector_type="average_check_change",
            entity_type="merchant",
            entity_key=merchant.key,
            current_value=current_average,
            baseline_value=previous_average,
            absolute_delta=delta,
            relative_delta=relative,
            confidence="high",
            severity="medium",
            title_key="average_check_up",
            content_data={
                "merchant": merchant.name,
                "category": merchant.category_name,
                "category_key": merchant.category_key,
            },
            evidence=[{
                "kind": "average_check",
                "label": "Средний чек",
                "current_amount": current_average,
                "previous_amount": previous_average,
                "delta_amount": delta,
                "currency": snapshot.currency,
            }],
            actions=[
                _action("OPEN_MERCHANT", "Посмотреть магазин", params),
                _action("OPEN_OPERATIONS", "Посмотреть операции", params),
            ],
            group_key=f"merchant:{snapshot.currency}:{merchant.key}",
            actionability=8,
        ))
    return candidates


def _period_progress(today: date, period: str) -> int:
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    else:
        start = today.replace(day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    elapsed = (today - start).days + 1
    total = (end - start).days + 1
    return int((Decimal(elapsed) / Decimal(total) * Decimal("100")).to_integral_value())


def detect_limit_pace(snapshot: InsightSnapshot, *, today: date) -> list[InsightCandidate]:
    if snapshot.period.key not in {"current_month", "current_week"}:
        return []
    expected_period = "week" if snapshot.period.key == "current_week" else "month"
    scope = _scope_params(snapshot)
    candidates: list[InsightCandidate] = []
    for limit in snapshot.limits:
        if not limit.enabled or limit.currency != snapshot.currency or limit.period != expected_period or limit.amount <= 0:
            continue
        if snapshot.scope_category_key:
            if not limit.category:
                continue
            try:
                if normalized_category_key(limit.category) != snapshot.scope_category_key:
                    continue
            except ValueError:
                continue
        period_progress = _period_progress(today, limit.period)
        pace_lead = limit.used_percent - period_progress
        if (
            limit.used_percent < MIN_LIMIT_USED_PERCENT
            or period_progress < MIN_LIMIT_PERIOD_PROGRESS_PERCENT
            or pace_lead < MIN_LIMIT_PACE_LEAD_PERCENT
        ):
            continue
        category_key = normalized_category_key(limit.category) if limit.category else "all_expenses"
        expected_spend = limit.amount * Decimal(period_progress) / Decimal("100")
        pace_excess = max(limit.spent - expected_spend, Decimal("0"))
        params = {
            **scope,
            "category_key": category_key if limit.category else None,
            "target_category": limit.category,
            "limit_id": limit.identifier,
        }
        candidates.append(_candidate(
            snapshot,
            detector_type="limit_pace",
            entity_type="limit",
            entity_key=limit.identifier,
            current_value=limit.spent,
            baseline_value=limit.amount,
            absolute_delta=pace_excess,
            relative_delta=Decimal(pace_lead) / Decimal("100"),
            confidence="high",
            severity="critical" if limit.used_percent >= 100 else "high" if limit.used_percent >= 90 else "medium",
            title_key="limit_pace",
            content_data={
                "title": limit.title,
                "category": limit.category,
                "category_key": category_key,
                "used_percent": limit.used_percent,
                "period_progress": period_progress,
                "pace_excess": pace_excess,
            },
            evidence=[{
                "kind": "limit_pace",
                "label": limit.title,
                "spent_amount": limit.spent,
                "limit_amount": limit.amount,
                "currency": snapshot.currency,
                "used_percent": limit.used_percent,
                "period_progress": period_progress,
            }],
            actions=[
                _action("OPEN_LIMIT", "Открыть лимит", params),
                _action("OPEN_OPERATIONS", "Посмотреть операции", params),
            ],
            group_key=f"limit:{snapshot.currency}:{category_key}",
            actionability=12,
            active_control=True,
            impact_amount=pace_excess,
            family={
                "general_limit": "general_budget_breach_risk",
                "category_budget": "grouped_budget_breach_risk",
            }.get(limit.kind, "category_limit_breach_risk"),
        ))
    return candidates


def detect_projection_risks(snapshot: InsightSnapshot, *, today: date) -> list[InsightCandidate]:
    if not (snapshot.period.start <= today <= snapshot.period.end):
        return []
    elapsed_days = max(1, (today - snapshot.period.start).days + 1)
    period_days = max(1, (snapshot.period.end - snapshot.period.start).days + 1)
    if elapsed_days >= period_days or snapshot.current_count < MIN_OPERATION_COUNT:
        return []
    scale = Decimal(period_days) / Decimal(elapsed_days)
    threshold = minimum_absolute_delta(snapshot.currency)
    scope = _scope_params(snapshot)
    candidates: list[InsightCandidate] = []
    projected_total = to_decimal_money(snapshot.current_total * scale)
    projected_delta = projected_total - snapshot.previous_total
    projected_relative = _ratio(projected_delta, snapshot.previous_total)
    if (
        snapshot.previous_total > 0
        and snapshot.previous_count >= MIN_OPERATION_COUNT
        and projected_delta >= threshold
        and projected_relative is not None
        and projected_relative >= MIN_RELATIVE_DELTA
    ):
        candidates.append(_candidate(
            snapshot,
            detector_type="future_expense_acceleration",
            family="future_expense_acceleration",
            entity_type="scope",
            entity_key="expenses",
            current_value=projected_total,
            baseline_value=snapshot.previous_total,
            absolute_delta=projected_delta,
            relative_delta=projected_relative,
            confidence="high" if elapsed_days >= max(7, period_days // 3) else "medium",
            severity="high" if projected_relative >= Decimal("0.50") else "medium",
            title_key="future_expense_acceleration",
            content_data={"projected_total": projected_total},
            evidence=[{
                "kind": "projected_amount",
                "label": "Прогноз расходов к концу периода",
                "expected_amount": projected_total,
                "previous_amount": snapshot.previous_total,
                "currency": snapshot.currency,
                "history_used": 1,
            }],
            actions=[_action("OPEN_ANALYTICS", "Посмотреть причины", scope)],
            group_key=f"forecast:{snapshot.currency}:expense_pace",
            actionability=15,
            impact_amount=projected_delta,
        ))

    for category in snapshot.categories:
        if category.current_count < MIN_OPERATION_COUNT or category.previous_count < MIN_OPERATION_COUNT or category.previous_total <= 0:
            continue
        projected = to_decimal_money(category.current_total * scale)
        delta = projected - category.previous_total
        relative = _ratio(delta, category.previous_total)
        if delta < threshold or relative is None or relative < MIN_CATEGORY_RELATIVE_DELTA:
            continue
        params = {**scope, "category_key": category.key, "target_category": category.name}
        candidates.append(_candidate(
            snapshot,
            detector_type="category_projection",
            family="category_projection",
            entity_type="category",
            entity_key=category.key,
            current_value=projected,
            baseline_value=category.previous_total,
            absolute_delta=delta,
            relative_delta=relative,
            confidence="high" if elapsed_days >= max(7, period_days // 3) else "medium",
            severity="high" if relative >= Decimal("0.50") else "medium",
            title_key="category_projection",
            content_data={"category": category.name, "projected_total": projected},
            evidence=[{
                "kind": "category_projection",
                "label": category.name,
                "expected_amount": projected,
                "previous_amount": category.previous_total,
                "currency": snapshot.currency,
            }],
            actions=[
                _action("OPEN_CATEGORY", "Посмотреть категорию", params),
                _action("OPEN_OPERATIONS", "Посмотреть операции", params),
            ],
            group_key=f"category-projection:{snapshot.currency}:{category.key}",
            actionability=14,
            impact_amount=delta,
        ))
        if relative >= Decimal("1.00") and category.current_count >= MIN_OPERATION_COUNT + 1:
            candidates.append(_candidate(
                snapshot,
                detector_type="unusual_spend_anomaly",
                family="unusual_spend_anomaly",
                entity_type="category",
                entity_key=category.key,
                current_value=category.current_total,
                baseline_value=category.previous_total,
                absolute_delta=category.current_total - category.previous_total,
                relative_delta=_ratio(category.current_total - category.previous_total, category.previous_total),
                confidence="high",
                severity="high",
                title_key="unusual_spend_anomaly",
                content_data={"category": category.name},
                evidence=[{
                    "kind": "unusual_category_spend",
                    "label": category.name,
                    "current_amount": category.current_total,
                    "previous_amount": category.previous_total,
                    "currency": snapshot.currency,
                }],
                actions=[_action("OPEN_CATEGORY", "Проверить категорию", params)],
                group_key=f"anomaly:{snapshot.currency}:{category.key}",
                actionability=15,
                impact_amount=category.current_total - category.previous_total,
            ))
    return candidates


def detect_candidates(snapshot: InsightSnapshot, *, today: date) -> list[InsightCandidate]:
    if snapshot.operation_type != "expense":
        return []
    candidates: list[InsightCandidate] = []
    for detector in (
        detect_spending_change,
        detect_category_contribution,
        detect_merchant_contribution,
        detect_frequency_change,
        detect_average_check_change,
    ):
        candidates.extend(detector(snapshot))
    candidates.extend(detect_limit_pace(snapshot, today=today))
    candidates.extend(detect_projection_risks(snapshot, today=today))
    candidates.extend(detect_forecast_candidates(snapshot))
    return candidates


def detect_forecast_risk(snapshot: InsightSnapshot) -> list[InsightCandidate]:
    forecast = snapshot.forecast or {}
    if not forecast.get("available"):
        return []
    spendable = to_decimal_money(forecast.get("amount") or 0)
    risk_state = str(forecast.get("risk_state") or "normal")
    if risk_state == "normal" and spendable > minimum_absolute_delta(snapshot.currency) * 2:
        return []
    params = _scope_params(snapshot)
    severity = "critical" if spendable <= 0 else "high" if risk_state == "attention" else "medium"
    return [_candidate(
        snapshot,
        detector_type="spendable_risk",
        entity_type="forecast",
        entity_key="spendable",
        current_value=spendable,
        baseline_value=Decimal("0"),
        absolute_delta=spendable,
        relative_delta=None,
        confidence=str(forecast.get("quality_tier") or "limited"),
        severity=severity,
        title_key="spendable_risk",
        content_data={"amount": spendable, "quality_label": forecast.get("quality_label")},
        evidence=[{
            "kind": "amount_comparison",
            "label": "Свободно до конца периода",
            "current_amount": spendable,
            "previous_amount": Decimal("0"),
            "currency": snapshot.currency,
        }],
        actions=[_action("OPEN_ANALYTICS", "Посмотреть причины", params)],
        group_key=f"forecast:{snapshot.currency}:spendable",
        actionability=20,
        active_control=True,
        impact_amount=max(minimum_absolute_delta(snapshot.currency), abs(spendable)),
    )]


def detect_forecast_candidates(snapshot: InsightSnapshot) -> list[InsightCandidate]:
    forecast = snapshot.forecast or {}
    if not forecast.get("available"):
        return []
    candidates = detect_forecast_risk(snapshot)
    threshold = minimum_absolute_delta(snapshot.currency)
    params = _scope_params(snapshot)
    current_result = to_decimal_money(forecast.get("current_result") or 0)
    commitments = to_decimal_money(forecast.get("known_commitments") or 0)
    goal_reserve = to_decimal_money(forecast.get("goal_reserve") or 0)
    expected_end = to_decimal_money(forecast.get("expected_end_result") or 0)
    spendable = to_decimal_money(forecast.get("amount") or 0)
    quality = str(forecast.get("quality_tier") or "limited")
    reason_codes = set(forecast.get("reason_codes") or ())
    general_budget_remaining = forecast.get("general_budget_remaining")
    if "general_budget_binding" in reason_codes and general_budget_remaining is not None:
        remaining = to_decimal_money(general_budget_remaining)
        candidates.append(_candidate(
            snapshot,
            detector_type="general_budget_breach_risk",
            family="general_budget_breach_risk",
            entity_type="budget",
            entity_key="general",
            current_value=remaining,
            baseline_value=current_result,
            absolute_delta=max(Decimal("0"), current_result - remaining),
            relative_delta=None,
            confidence="high",
            severity="high" if remaining <= threshold else "medium",
            title_key="general_budget_breach_risk",
            content_data={"remaining": remaining},
            evidence=[{"kind": "general_budget", "label": "Остаток общего бюджета", "amount": remaining, "currency": snapshot.currency}],
            actions=[_action("OPEN_ANALYTICS", "Посмотреть бюджет", params)],
            group_key=f"budget:{snapshot.currency}:general",
            actionability=18,
            active_control=True,
            impact_amount=max(threshold, current_result - remaining),
        ))
    recurring_total = to_decimal_money(forecast.get("recurring_commitments") or 0)
    recurring_count = int(forecast.get("recurring_commitment_count") or 0)
    if recurring_count and recurring_total >= threshold:
        candidates.append(_candidate(
            snapshot,
            detector_type="recurring_pressure",
            family="recurring_pressure",
            entity_type="forecast",
            entity_key="recurring",
            current_value=recurring_total,
            baseline_value=current_result,
            absolute_delta=recurring_total,
            relative_delta=_ratio(recurring_total, current_result),
            confidence="high",
            severity="high" if recurring_total >= max(current_result, threshold) * Decimal("0.50") else "medium",
            title_key="recurring_pressure",
            content_data={"commitments": recurring_total, "count": recurring_count},
            evidence=[{"kind": "recurring_commitments", "label": "Регулярные платежи до конца периода", "amount": recurring_total, "count": recurring_count, "currency": snapshot.currency}],
            actions=[_action("OPEN_ANALYTICS", "Посмотреть платежи", params)],
            group_key=f"forecast:{snapshot.currency}:recurring",
            actionability=16,
            impact_amount=recurring_total,
        ))
    if expected_end < 0:
        candidates.append(_candidate(
            snapshot,
            detector_type="forecast_end_result",
            family="forecast_end_result",
            entity_type="forecast",
            entity_key="period_end",
            current_value=expected_end,
            baseline_value=Decimal("0"),
            absolute_delta=expected_end,
            relative_delta=None,
            confidence=quality,
            severity="critical",
            title_key="forecast_end_result",
            content_data={"expected_end_result": expected_end},
            evidence=[{"kind": "forecast", "label": "Ожидаемый итог периода", "expected_amount": expected_end, "currency": snapshot.currency}],
            actions=[_action("OPEN_ANALYTICS", "Посмотреть прогноз", params)],
            group_key=f"forecast:{snapshot.currency}:period_end",
            actionability=20,
            active_control=True,
            impact_amount=abs(expected_end),
        ))
    if commitments >= threshold and (current_result <= 0 or commitments >= max(current_result, threshold) * Decimal("0.25")):
        candidates.append(_candidate(
            snapshot,
            detector_type="upcoming_commitment_pressure",
            family="upcoming_commitment_pressure",
            entity_type="forecast",
            entity_key="commitments",
            current_value=commitments,
            baseline_value=current_result,
            absolute_delta=commitments,
            relative_delta=_ratio(commitments, current_result),
            confidence="high",
            severity="high" if commitments >= max(current_result, threshold) * Decimal("0.50") else "medium",
            title_key="upcoming_commitment_pressure",
            content_data={"commitments": commitments, "count": int(forecast.get("known_commitment_count") or 0)},
            evidence=[{"kind": "future_commitments", "label": "Будущие обязательные платежи", "amount": commitments, "count": int(forecast.get("known_commitment_count") or 0), "currency": snapshot.currency}],
            actions=[_action("OPEN_ANALYTICS", "Посмотреть платежи", params)],
            group_key=f"forecast:{snapshot.currency}:commitments",
            actionability=16,
            impact_amount=commitments,
        ))
    if goal_reserve >= threshold and spendable <= goal_reserve:
        candidates.append(_candidate(
            snapshot,
            detector_type="goal_affordability",
            family="goal_affordability",
            entity_type="forecast",
            entity_key="goals",
            current_value=goal_reserve,
            baseline_value=spendable,
            absolute_delta=goal_reserve,
            relative_delta=None,
            confidence="high",
            severity="medium",
            title_key="goal_affordability",
            content_data={"goal_reserve": goal_reserve, "spendable": spendable},
            evidence=[{"kind": "goal_reserve", "label": "Защищено на цели", "amount": goal_reserve, "currency": snapshot.currency}],
            actions=[_action("OPEN_ANALYTICS", "Посмотреть расчёт", params)],
            group_key=f"forecast:{snapshot.currency}:goals",
            actionability=14,
            impact_amount=goal_reserve,
        ))
    change = forecast.get("change") if isinstance(forecast.get("change"), dict) else None
    if change and abs(to_decimal_money(change.get("delta") or 0)) >= threshold:
        delta = to_decimal_money(change.get("delta") or 0)
        candidates.append(_candidate(
            snapshot,
            detector_type="spendable_change",
            family="spendable_change",
            entity_type="forecast",
            entity_key="spendable_change",
            current_value=spendable,
            baseline_value=to_decimal_money(change.get("previous_amount") or 0),
            absolute_delta=delta,
            relative_delta=_ratio(delta, to_decimal_money(change.get("previous_amount") or 0)),
            confidence=quality,
            severity="high" if delta < 0 else "medium",
            title_key="spendable_change",
            content_data={"delta": delta, "reason_codes": list(change.get("reason_codes") or [])},
            evidence=[{"kind": "forecast_change", "label": "Изменение свободной суммы", "delta_amount": delta, "currency": snapshot.currency, "reason_codes": list(change.get("reason_codes") or [])}],
            actions=[_action("OPEN_ANALYTICS", "Посмотреть причины", params)],
            group_key=f"forecast:{snapshot.currency}:spendable_change",
            actionability=18,
            impact_amount=abs(delta),
        ))
    return candidates


def _merge_evidence(target: InsightCandidate, source: InsightCandidate) -> None:
    existing = {(item.get("kind"), item.get("label")) for item in target.evidence}
    for item in source.evidence:
        key = (item.get("kind"), item.get("label"))
        if key not in existing:
            target.evidence.append(item)
            existing.add(key)
    action_types = {action.type for action in target.actions}
    for action in source.actions:
        if action.type not in action_types and len(target.actions) < 3:
            target.actions.append(action)
            action_types.add(action.type)


def group_candidates(candidates: Iterable[InsightCandidate]) -> list[InsightCandidate]:
    values = list(candidates)
    suppressed: set[int] = set()
    category_candidates = [item for item in values if item.detector_type == "category_contribution"]
    merchant_candidates = [item for item in values if item.detector_type == "merchant_contribution"]
    overall_candidates = [item for item in values if item.detector_type == "spending_change" and item.absolute_delta > 0]
    limit_candidates = [item for item in values if item.detector_type == "limit_pace"]

    for category in category_candidates:
        related_merchants = [
            merchant for merchant in merchant_candidates
            if merchant.currency == category.currency
            and merchant.content_data.get("category_key") == category.entity_key
        ]
        if related_merchants:
            merchant = max(related_merchants, key=lambda item: (item.absolute_delta, item.entity_key))
            _merge_evidence(category, merchant)
            merchant_action = next((action for action in merchant.actions if action.type == "OPEN_MERCHANT"), None)
            if merchant_action:
                category.actions = [merchant_action] + [
                    action for action in category.actions
                    if action.type not in {"OPEN_CATEGORY", "OPEN_MERCHANT"}
                ][:2]
            category.content_data["merchant"] = merchant.content_data.get("merchant")
            category.content_data["merchant_key"] = merchant.entity_key
            suppressed.add(id(merchant))
            for behavior in values:
                if behavior.detector_type in {"merchant_frequency", "average_check_change"} and behavior.entity_key == merchant.entity_key and behavior.currency == merchant.currency:
                    _merge_evidence(category, behavior)
                    suppressed.add(id(behavior))
        for overall in overall_candidates:
            if overall.currency == category.currency:
                _merge_evidence(category, overall)
                suppressed.add(id(overall))

    for overall in overall_candidates:
        scope_category_key = overall.content_data.get("scope_category_key")
        if not scope_category_key or id(overall) in suppressed:
            continue
        related_merchants = [
            merchant for merchant in merchant_candidates
            if id(merchant) not in suppressed
            and merchant.currency == overall.currency
            and merchant.content_data.get("category_key") == scope_category_key
        ]
        if not related_merchants:
            continue
        merchant = max(related_merchants, key=lambda item: (item.absolute_delta, item.entity_key))
        _merge_evidence(overall, merchant)
        merchant_action = next((action for action in merchant.actions if action.type == "OPEN_MERCHANT"), None)
        if merchant_action:
            overall.actions = [merchant_action] + [
                action for action in overall.actions
                if action.type != "OPEN_MERCHANT"
            ][:2]
        overall.content_data["merchant"] = merchant.content_data.get("merchant")
        overall.content_data["merchant_key"] = merchant.entity_key
        suppressed.add(id(merchant))
        for behavior in values:
            if (
                behavior.detector_type in {"merchant_frequency", "average_check_change"}
                and behavior.entity_key == merchant.entity_key
                and behavior.currency == merchant.currency
            ):
                _merge_evidence(overall, behavior)
                suppressed.add(id(behavior))

    for limit in limit_candidates:
        category_key = limit.content_data.get("category_key")
        if category_key == "all_expenses":
            related_categories = [
                category for category in category_candidates
                if id(category) not in suppressed and category.currency == limit.currency
            ]
            if related_categories:
                category = max(related_categories, key=lambda item: (item.absolute_delta, item.entity_key))
                _merge_evidence(limit, category)
                suppressed.add(id(category))
            for overall in overall_candidates:
                if overall.currency == limit.currency:
                    _merge_evidence(limit, overall)
                    suppressed.add(id(overall))
            continue
        for category in category_candidates:
            if id(category) in suppressed:
                continue
            if category.currency == limit.currency and category.entity_key == category_key:
                _merge_evidence(limit, category)
                suppressed.add(id(category))
        for overall in overall_candidates:
            if (
                id(overall) not in suppressed
                and overall.currency == limit.currency
                and overall.content_data.get("scope_category_key") == category_key
            ):
                _merge_evidence(limit, overall)
                suppressed.add(id(overall))

    behavior_by_merchant: dict[tuple[str, str], list[InsightCandidate]] = {}
    for item in values:
        if item.detector_type in {"merchant_frequency", "average_check_change"} and id(item) not in suppressed:
            behavior_by_merchant.setdefault((item.currency, item.entity_key), []).append(item)
    for related in behavior_by_merchant.values():
        if len(related) < 2:
            continue
        primary = max(related, key=lambda item: (item.impact, item.detector_type))
        for secondary in related:
            if secondary is primary:
                continue
            _merge_evidence(primary, secondary)
            suppressed.add(id(secondary))

    return [item for item in values if id(item) not in suppressed]


def _material_bucket(value: Decimal, currency: str) -> int:
    step = max(minimum_absolute_delta(currency), Decimal("0.01"))
    return int((value / step).to_integral_value(rounding=ROUND_HALF_UP))


def assign_fingerprint(candidate: InsightCandidate, *, generated_at: datetime) -> InsightCandidate:
    if candidate.detector_type == "merchant_frequency":
        current_bucket = int(candidate.current_value)
        baseline_bucket = int(candidate.baseline_value)
    else:
        current_bucket = _material_bucket(candidate.current_value, candidate.currency)
        baseline_bucket = _material_bucket(candidate.baseline_value, candidate.currency)
    payload = {
        "detector": candidate.detector_type,
        "entity_type": candidate.entity_type,
        "entity_key": candidate.entity_key,
        "currency": candidate.currency,
        "period": [candidate.period.start.isoformat(), candidate.period.end.isoformat()],
        "comparison": [candidate.comparison_period.start.isoformat(), candidate.comparison_period.end.isoformat()],
        "current_bucket": current_bucket,
        "baseline_bucket": baseline_bucket,
    }
    candidate.fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    candidate.valid_until = generated_at + timedelta(days=7)
    return candidate


def _window_show_count(state: InsightState | None, *, now: datetime) -> int:
    if (
        not state
        or not state.first_shown_at
        or state.first_shown_at <= now - timedelta(hours=REPEAT_WINDOW_HOURS)
    ):
        return 0
    return state.show_count


def candidate_score(
    candidate: InsightCandidate,
    state: InsightState | None = None,
    *,
    now: datetime | None = None,
) -> Decimal:
    impact_score = min(Decimal("40"), candidate.impact * Decimal("8"))
    relative_score = min(Decimal("22"), abs(candidate.relative_delta or Decimal("0")) * Decimal("30"))
    severity_score = {
        "normal": Decimal("0"),
        "medium": Decimal("8"),
        "high": Decimal("18"),
        "critical": Decimal("30"),
    }.get(candidate.severity, Decimal("0"))
    confidence_score = Decimal("10") if candidate.confidence == "high" else Decimal("5")
    action_score = Decimal(candidate.actionability)
    control_score = Decimal("20") if candidate.active_control else Decimal("0")
    window_count = _window_show_count(state, now=now or datetime.now(timezone.utc))
    repeat_penalty = Decimal(min(window_count * 8, 24))
    return impact_score + relative_score + severity_score + confidence_score + action_score + control_score - repeat_penalty


def rank_candidates(
    candidates: Iterable[InsightCandidate],
    states: Iterable[InsightState] = (),
    *,
    now: datetime,
    limit: int = MAX_VISIBLE_INSIGHTS,
) -> list[InsightCandidate]:
    state_by_fingerprint = {state.fingerprint: state for state in states}
    suppressed_detectors = {
        state.detector_type
        for state in states
        if state.feedback_type == "not_useful" and state.suppression_until and state.suppression_until > now
    }
    eligible: list[InsightCandidate] = []
    for candidate in candidates:
        if candidate.detector_type in suppressed_detectors:
            continue
        state = state_by_fingerprint.get(candidate.fingerprint)
        if (
            state
            and state.show_count >= MAX_IMPRESSIONS_IN_REPEAT_WINDOW
            and state.first_shown_at
            and state.first_shown_at > now - timedelta(hours=REPEAT_WINDOW_HOURS)
        ):
            continue
        candidate.score = candidate_score(candidate, state, now=now)
        eligible.append(candidate)
    eligible.sort(key=lambda item: (-item.score, item.detector_type, item.entity_type, item.entity_key, item.fingerprint))
    return eligible[: max(0, min(limit, MAX_VISIBLE_INSIGHTS))]


def _percent_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    pct = int((value * Decimal("100")).to_integral_value())
    return f"{pct:+d}%"


def present_candidate(candidate: InsightCandidate, feedback: str | None = None) -> dict[str, Any]:
    data = candidate.content_data
    delta_text = format_money(abs(candidate.absolute_delta), candidate.currency)
    relative_text = _percent_text(candidate.relative_delta)
    if candidate.title_key == "spending_up":
        category = data.get("scope_category")
        title = f"Расходы на {category} выросли на {delta_text}" if category else f"Расходы выросли на {delta_text}"
        summary = f"{relative_text} к сопоставимому периоду"
        if data.get("merchant"):
            summary += f" · основная причина — {data['merchant']}"
        tone = "warning"
    elif candidate.title_key == "spending_down":
        category = data.get("scope_category")
        title = f"Расходы на {category} снизились на {delta_text}" if category else f"Расходы снизились на {delta_text}"
        summary = f"{relative_text} к сопоставимому периоду"
        tone = "positive"
    elif candidate.title_key == "category_growth":
        title = f"Расходы на {data['category']} выросли на {delta_text}"
        summary = f"{relative_text} к сопоставимому периоду"
        if data.get("merchant"):
            summary += f" · основная причина — {data['merchant']}"
        tone = "warning"
    elif candidate.title_key == "merchant_contribution":
        title = f"Главный рост — {data['merchant']}"
        summary = f"+{delta_text} в категории {data['category']}"
        tone = "warning"
    elif candidate.title_key == "merchant_frequency_up":
        title = f"В {data['merchant']} стало больше покупок"
        summary = f"{data['current_count']} вместо {data['previous_count']}"
        tone = "neutral"
    elif candidate.title_key == "average_check_up":
        title = f"Средний чек в {data['merchant']} вырос"
        summary = f"{relative_text} к сопоставимому периоду"
        tone = "neutral"
    elif candidate.title_key == "limit_pace":
        title = f"{data['title']} близок к лимиту" if data["used_percent"] < 100 else f"{data['title']}: лимит использован"
        summary = f"Использовано {data['used_percent']}% · прошло {data['period_progress']}% периода"
        tone = "warning"
    elif candidate.title_key == "spendable_risk":
        title = "Свободная сумма требует внимания" if candidate.current_value > 0 else "Свободной суммы на период не остаётся"
        summary = f"По текущему прогнозу останется около {format_money(candidate.current_value, candidate.currency)}. Откройте расчёт, чтобы увидеть причины."
        tone = "warning"
    elif candidate.title_key == "forecast_end_result":
        title = "К концу периода итог может стать отрицательным"
        summary = f"Консервативный прогноз: {format_money(candidate.current_value, candidate.currency)}. Проверьте будущие расходы."
        tone = "warning"
    elif candidate.title_key == "upcoming_commitment_pressure":
        title = "Будущие платежи заметно влияют на период"
        summary = f"Учтено {data['count']} платежей на {format_money(data['commitments'], candidate.currency)}."
        tone = "warning"
    elif candidate.title_key == "goal_affordability":
        title = "Взносы на цели сокращают свободную сумму"
        summary = f"На цели защищено {format_money(data['goal_reserve'], candidate.currency)}."
        tone = "neutral"
    elif candidate.title_key == "spendable_change":
        direction = "снизилась" if data["delta"] < 0 else "выросла"
        title = f"Свободная сумма {direction}"
        summary = f"Изменение около {format_money(abs(data['delta']), candidate.currency)} по новому прогнозу."
        tone = "warning" if data["delta"] < 0 else "positive"
    elif candidate.title_key == "future_expense_acceleration":
        title = "Текущий темп расходов выше обычного"
        summary = f"К концу периода расходы могут быть выше примерно на {delta_text}."
        tone = "warning"
    elif candidate.title_key == "category_projection":
        title = f"{data['category']}: расходы могут вырасти"
        summary = f"Прогноз к концу периода выше обычного примерно на {delta_text}."
        tone = "warning"
    elif candidate.title_key == "unusual_spend_anomaly":
        title = f"Необычный рост в категории {data['category']}"
        summary = f"Расходы заметно отличаются от сопоставимого периода. Проверьте операции."
        tone = "warning"
    elif candidate.title_key == "general_budget_breach_risk":
        title = "Общий бюджет ограничивает свободную сумму"
        summary = f"В бюджете осталось {format_money(data['remaining'], candidate.currency)}."
        tone = "warning"
    elif candidate.title_key == "recurring_pressure":
        title = "Регулярные платежи влияют на прогноз"
        summary = f"До конца периода учтено {data['count']} платежей на {format_money(data['commitments'], candidate.currency)}."
        tone = "warning"
    else:
        title = "Изменение расходов"
        summary = "Сравнение с сопоставимым периодом"
        tone = "neutral"
    return {
        "id": candidate.fingerprint,
        "type": candidate.detector_type,
        "detector": candidate.detector_type,
        "family": candidate.family,
        "tone": tone,
        "severity": candidate.severity,
        "title": title,
        "summary": summary,
        "currency": candidate.currency,
        "period": {
            "key": candidate.period.key,
            "start_date": candidate.period.start,
            "end_date": candidate.period.end,
        },
        "comparison_period": {
            "key": candidate.comparison_period.key,
            "start_date": candidate.comparison_period.start,
            "end_date": candidate.comparison_period.end,
        },
        "evidence": candidate.evidence,
        "actions": [
            {"type": action.type, "label": action.label, "params": action.params}
            for action in candidate.actions
        ],
        "feedback": feedback if feedback in FEEDBACK_TYPES else None,
    }


class InsightEngine:
    def __init__(self, store: InsightStateStore | None = None) -> None:
        self.store = store or PostgresInsightStateStore()

    def generate(self, snapshot: InsightSnapshot, *, today: date, now: datetime | None = None) -> list[dict[str, Any]]:
        generated_at = now or datetime.now(timezone.utc)
        candidates = group_candidates(detect_candidates(snapshot, today=today))
        for candidate in candidates:
            assign_fingerprint(candidate, generated_at=generated_at)
        states = self.store.load(snapshot.user_id, snapshot.workspace_id)
        selected = rank_candidates(candidates, states, now=generated_at)
        self.store.ensure(snapshot.user_id, snapshot.workspace_id, selected)
        state_by_fingerprint = {state.fingerprint: state for state in states}
        presented = []
        for candidate in selected:
            state = state_by_fingerprint.get(candidate.fingerprint)
            presented.append(present_candidate(candidate, state.feedback_type if state else None))
        return presented

    def impression(self, user_id: int, workspace_id: int | None, fingerprint: str) -> InsightState | None:
        return self.store.record_impression(user_id, workspace_id, fingerprint)

    def feedback(self, user_id: int, workspace_id: int | None, fingerprint: str, feedback_type: str) -> InsightState | None:
        return self.store.record_feedback(user_id, workspace_id, fingerprint, feedback_type)


def _category_name(variants: dict[str, tuple[int, Decimal]]) -> str:
    # Analytics folds rows ordered by amount, then name; preserve that display rule.
    return sorted(variants, key=lambda name: (-variants[name][1], name.casefold(), name))[0]


def build_snapshot(
    *,
    user_id: int,
    workspace_id: int | None,
    workspace_kind: str,
    currency: str,
    period: PeriodRef,
    comparison_period: PeriodRef,
    current_rows: Iterable[tuple[Any, Any, Any, Any, Any]],
    previous_rows: Iterable[tuple[Any, Any, Any, Any, Any]],
    limits: Iterable[dict[str, Any]] = (),
    scope_category: str | None = None,
    can_write: bool = True,
    forecast: dict[str, Any] | None = None,
) -> InsightSnapshot:
    selected_currency = currency.upper()
    selected_scope_category = str(scope_category or "").strip() or None
    selected_scope_category_key = (
        normalized_category_key(selected_scope_category)
        if selected_scope_category else None
    )
    current_values = [row for row in current_rows if str(row[2]).upper() == selected_currency]
    previous_values = [row for row in previous_rows if str(row[2]).upper() == selected_currency]
    category_totals: dict[str, dict[str, Any]] = {}
    merchant_totals: dict[tuple[str, str], dict[str, Any]] = {}
    current_merchant_identity_rows: list[tuple[str, str, Decimal, int]] = []
    previous_merchant_identity_rows: list[tuple[str, str, Decimal, int]] = []

    def consume(rows: Iterable[tuple[Any, Any, Any, Any, Any]], side: str) -> None:
        for raw_category, raw_merchant, row_currency, raw_total, raw_count in rows:
            category_name = str(raw_category or "Прочее").strip() or "Прочее"
            try:
                category_key = normalized_category_key(category_name)
            except ValueError:
                category_name = "Прочее"
                category_key = normalized_category_key(category_name)
            total = to_decimal_money(raw_total or 0)
            count = int(raw_count or 0)
            category = category_totals.setdefault(category_key, {
                "current_variants": {}, "previous_variants": {},
                "current_total": Decimal("0"), "previous_total": Decimal("0"),
                "current_count": 0, "previous_count": 0,
            })
            variants = category[f"{side}_variants"]
            variant_count, variant_total = variants.get(category_name, (0, Decimal("0")))
            variants[category_name] = (variant_count + count, variant_total + total)
            category[f"{side}_total"] += total
            category[f"{side}_count"] += count
            merchant_key = normalize_merchant_key(str(raw_merchant or ""))
            if not merchant_key or merchant_key == EMPTY_MERCHANT_KEY:
                continue
            merchant = merchant_totals.setdefault((category_key, merchant_key), {
                "current_total": Decimal("0"), "previous_total": Decimal("0"),
                "current_count": 0, "previous_count": 0,
            })
            merchant[f"{side}_total"] += total
            merchant[f"{side}_count"] += count
            identity_row = (str(raw_merchant or ""), str(row_currency), total, count)
            if side == "current":
                current_merchant_identity_rows.append(identity_row)
            else:
                previous_merchant_identity_rows.append(identity_row)

    consume(current_values, "current")
    consume(previous_values, "previous")
    current_folded_merchants = fold_merchant_rows(current_merchant_identity_rows).get(selected_currency, {})
    previous_folded_merchants = fold_merchant_rows(previous_merchant_identity_rows).get(selected_currency, {})
    categories = tuple(
        CategoryAggregate(
            key=key,
            name=_category_name(value["current_variants"] or value["previous_variants"]),
            current_total=value["current_total"],
            previous_total=value["previous_total"],
            current_count=value["current_count"],
            previous_count=value["previous_count"],
        )
        for key, value in sorted(category_totals.items())
    )
    category_names = {item.key: item.name for item in categories}
    merchants = tuple(
        MerchantAggregate(
            key=merchant_key,
            name=str(
                current_folded_merchants.get(merchant_key, {}).get("name")
                or previous_folded_merchants.get(merchant_key, {}).get("name")
                or merchant_key
            ),
            category_key=category_key,
            category_name=category_names.get(category_key, category_key),
            current_total=value["current_total"],
            previous_total=value["previous_total"],
            current_count=value["current_count"],
            previous_count=value["previous_count"],
        )
        for (category_key, merchant_key), value in sorted(merchant_totals.items())
    )
    limit_values = tuple(
        LimitAggregate(
            identifier=str(item.get("id") or ""),
            title=str(item.get("title") or "Лимит"),
            category=str(item["category"]) if item.get("category") else None,
            amount=to_decimal_money(item.get("amount") or 0),
            spent=to_decimal_money(item.get("spent") or 0),
            currency=str(item.get("currency") or selected_currency).upper(),
            period=str(item.get("period") or "month"),
            used_percent=int(item.get("percent") or 0),
            enabled=bool(item.get("enabled", True)),
            kind=str(item.get("budget_kind") or item.get("kind") or "category_limit"),
        )
        for item in limits
        if item.get("id") and item.get("amount") is not None
    )
    current_total = sum((item.current_total for item in categories), Decimal("0"))
    previous_total = sum((item.previous_total for item in categories), Decimal("0"))
    return InsightSnapshot(
        user_id=int(user_id),
        workspace_id=workspace_id,
        workspace_kind=workspace_kind,
        currency=selected_currency,
        period=period,
        comparison_period=comparison_period,
        current_total=current_total,
        previous_total=previous_total,
        current_count=sum(item.current_count for item in categories),
        previous_count=sum(item.previous_count for item in categories),
        categories=categories,
        merchants=merchants,
        limits=limit_values,
        scope_category=selected_scope_category,
        scope_category_key=selected_scope_category_key,
        can_write=can_write,
        forecast=forecast,
    )


insight_engine = InsightEngine()
