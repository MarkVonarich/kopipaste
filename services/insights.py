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
    operation_type: str = "expense"
    can_write: bool = True


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
                    SELECT fingerprint, detector_type, show_count, last_shown_at,
                           feedback_type, suppression_until
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
                last_shown_at=row[3],
                feedback_type=str(row[4]) if row[4] else None,
                suppression_until=row[5],
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
                           SET first_shown_at=COALESCE(first_shown_at, now()),
                               last_shown_at=now(), show_count=show_count + 1,
                               updated_at=now()
                         WHERE user_id=%s
                           AND workspace_scope_key=COALESCE(%s::bigint, 0)
                           AND fingerprint=%s
                           AND valid_until > now()
                        RETURNING fingerprint, detector_type, show_count, last_shown_at,
                                  feedback_type, suppression_until
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
                        RETURNING fingerprint, detector_type, show_count, last_shown_at,
                                  feedback_type, suppression_until
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
            last_shown_at=row[3],
            feedback_type=str(row[4]) if row[4] else None,
            suppression_until=row[5],
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
        "category": "all",
        "currency": snapshot.currency,
    }


def _action(action_type: str, label: str, params: dict[str, str | int | None]) -> InsightAction:
    allowed = {
        "workspace_id", "period", "start_date", "end_date", "operation_type",
        "category", "category_key", "merchant_key", "currency", "limit_id",
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
) -> InsightCandidate:
    threshold = minimum_absolute_delta(snapshot.currency)
    return InsightCandidate(
        detector_type=detector_type,
        entity_type=entity_type,
        entity_key=entity_key,
        currency=snapshot.currency,
        current_value=current_value,
        baseline_value=baseline_value,
        absolute_delta=absolute_delta,
        relative_delta=relative_delta,
        impact=abs(absolute_delta) / max(threshold, Decimal("0.01")),
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
    return [_candidate(
        snapshot,
        detector_type="spending_change",
        entity_type="scope",
        entity_key="expenses",
        current_value=snapshot.current_total,
        baseline_value=snapshot.previous_total,
        absolute_delta=delta,
        relative_delta=relative,
        confidence="high",
        severity=severity,
        title_key=f"spending_{direction}",
        content_data={"direction": direction},
        evidence=[{
            "kind": "amount_comparison",
            "label": "Расходы",
            "current_amount": snapshot.current_total,
            "previous_amount": snapshot.previous_total,
            "delta_amount": delta,
            "currency": snapshot.currency,
        }],
        actions=[_action("OPEN_ANALYTICS", "Посмотреть аналитику", scope)],
        group_key=f"spending:{snapshot.currency}",
        actionability=6,
    )]


def detect_category_contribution(snapshot: InsightSnapshot) -> list[InsightCandidate]:
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
        params = {**scope, "category_key": category.key, "category": category.name}
        actions = [
            _action("OPEN_CATEGORY", "Посмотреть категорию", params),
            _action("OPEN_OPERATIONS", "Посмотреть операции", params),
        ]
        if snapshot.can_write:
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
            actionability=10,
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
            "category": merchant.category_name,
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
            "category": merchant.category_name,
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
            "category": merchant.category_name,
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
        period_progress = _period_progress(today, limit.period)
        pace_lead = limit.used_percent - period_progress
        if (
            limit.used_percent < MIN_LIMIT_USED_PERCENT
            or period_progress < MIN_LIMIT_PERIOD_PROGRESS_PERCENT
            or pace_lead < MIN_LIMIT_PACE_LEAD_PERCENT
        ):
            continue
        category_key = normalized_category_key(limit.category) if limit.category else "all_expenses"
        params = {
            **scope,
            "category": limit.category or "all",
            "category_key": category_key if limit.category else None,
            "limit_id": limit.identifier,
        }
        candidates.append(_candidate(
            snapshot,
            detector_type="limit_pace",
            entity_type="limit",
            entity_key=limit.identifier,
            current_value=limit.spent,
            baseline_value=limit.amount,
            absolute_delta=limit.spent - limit.amount,
            relative_delta=Decimal(limit.used_percent) / Decimal("100"),
            confidence="high",
            severity="critical" if limit.used_percent >= 100 else "high" if limit.used_percent >= 90 else "medium",
            title_key="limit_pace",
            content_data={
                "title": limit.title,
                "category": limit.category,
                "category_key": category_key,
                "used_percent": limit.used_percent,
                "period_progress": period_progress,
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


def candidate_score(candidate: InsightCandidate, state: InsightState | None = None) -> Decimal:
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
    repeat_penalty = Decimal(min((state.show_count if state else 0) * 8, 24))
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
            and state.last_shown_at
            and state.last_shown_at > now - timedelta(hours=REPEAT_WINDOW_HOURS)
        ):
            continue
        candidate.score = candidate_score(candidate, state)
        eligible.append(candidate)
    eligible.sort(key=lambda item: (-item.score, item.detector_type, item.entity_type, item.entity_key, item.fingerprint))
    return eligible[: max(0, min(limit, MAX_VISIBLE_INSIGHTS))]


def _percent_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    pct = int((value * Decimal("100")).to_integral_value())
    return f"{pct:+d}%"


def present_candidate(candidate: InsightCandidate) -> dict[str, Any]:
    data = candidate.content_data
    delta_text = format_money(abs(candidate.absolute_delta), candidate.currency)
    relative_text = _percent_text(candidate.relative_delta)
    if candidate.title_key == "spending_up":
        title = f"Расходы выросли на {delta_text}"
        summary = f"{relative_text} к сопоставимому периоду"
        tone = "warning"
    elif candidate.title_key == "spending_down":
        title = f"Расходы снизились на {delta_text}"
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
    else:
        title = "Изменение расходов"
        summary = "Сравнение с сопоставимым периодом"
        tone = "neutral"
    return {
        "id": candidate.fingerprint,
        "type": candidate.detector_type,
        "detector": candidate.detector_type,
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
        "feedback": None,
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
        return [present_candidate(candidate) for candidate in selected]

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
    can_write: bool = True,
) -> InsightSnapshot:
    selected_currency = currency.upper()
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
        can_write=can_write,
    )


insight_engine = InsightEngine()
