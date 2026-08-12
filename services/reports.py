from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from db.database import pg_fetchall
from utils.money import to_decimal_money


REPORT_KINDS = {"selected", "completed_week", "completed_month"}


@dataclass(frozen=True)
class ReportBuildRequest:
    report_kind: str
    workspace_scope: int | str | None
    workspace_name: str
    workspace_type: str
    read_only: bool
    selected_currency: str | None
    fallback_currency: str


def completed_report_period(report_kind: str, today: date) -> tuple[date, date, str]:
    if report_kind == "completed_week":
        current_week = today - timedelta(days=today.weekday())
        end = current_week - timedelta(days=1)
        return end - timedelta(days=6), end, "completed_week"
    if report_kind == "completed_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, "completed_month"
    raise ValueError("bad_report_kind")


def comparable_period(start: date, end: date, period_key: str) -> tuple[date, date, str]:
    if period_key in {"current_month", "completed_month"}:
        prev_end = start - timedelta(days=1)
        prev_start = prev_end.replace(day=1)
        if period_key == "completed_month":
            return prev_start, prev_end, "month_before_report"
        if (end + timedelta(days=1)).day == 1:
            return prev_start, prev_end, "previous_month"
        current_length = (end - start).days + 1
        return prev_start, min(prev_start + timedelta(days=current_length - 1), prev_end), "previous_month_to_date"
    if period_key == "previous_month":
        prev_end = start - timedelta(days=1)
        return prev_end.replace(day=1), prev_end, "month_before_previous"
    length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    return prev_end - timedelta(days=length - 1), prev_end, "previous_equal_period"


def _scope(
    period: dict[str, Any],
    filters: dict[str, Any],
    currency: str,
    *,
    workspace_scope: int | str | None,
    operation_type: str,
    category_key: str | None = None,
    merchant_key: str | None = None,
) -> dict[str, Any]:
    return {
        "workspace_id": workspace_scope,
        "period": period["key"],
        "start_date": period["start_date"],
        "end_date": period["end_date"],
        "operation_type": operation_type,
        "currency": currency,
        "category": "all" if category_key else filters.get("category") or "all",
        "category_key": category_key,
        "merchant_key": merchant_key,
        "scope_category": filters.get("category") if category_key and filters.get("category") not in {None, "all"} else None,
    }


def _dimension_items(
    items: list[dict[str, Any]],
    *,
    dimension: str,
    currency: str,
    period: dict[str, Any],
    filters: dict[str, Any],
    workspace_scope: int | str | None,
    operation_type: str,
) -> list[dict[str, Any]]:
    result = []
    for raw in items:
        item = dict(raw)
        key = str(item.get("key") or "")
        drillable = bool(item.get("drillable", True) and not item.get("synthetic") and not item.get("fallback") and key)
        item["drillable"] = drillable
        if dimension == "merchant":
            count = int(item.get("count") or 0)
            item["average_check"] = (
                (to_decimal_money(item.get("total") or 0) / Decimal(count)).quantize(Decimal("0.01"))
                if count > 0
                else None
            )
        item["operation_scope"] = (
            _scope(
                period,
                filters,
                currency,
                workspace_scope=workspace_scope,
                operation_type=operation_type,
                category_key=key if dimension == "category" else None,
                merchant_key=key if dimension == "merchant" else None,
            )
            if drillable
            else None
        )
        result.append(item)
    return result


def _observations(
    metrics: dict[str, Any],
    contribution_items: list[dict[str, Any]],
    merchant_items: list[dict[str, Any]],
    currency: str,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    result_metric = metrics.get("result") or {}
    if result_metric.get("state") == "sign_change":
        observations.append({
            "kind": "result_sign_change",
            "title": "Финансовый результат сменил знак",
            "description": "Текущий и сопоставимый периоды находятся по разные стороны от нуля.",
            "delta": result_metric.get("delta"),
            "currency": currency,
            "drilldown": None,
        })
    expense_metric = metrics.get("expense") or {}
    expense_delta = to_decimal_money(expense_metric.get("delta") or 0)
    if expense_delta != 0 and len(observations) < 3:
        observations.append({
            "kind": "expense_change",
            "title": "Расходы изменились",
            "description": "Сравнение выполнено с сопоставимым предыдущим периодом.",
            "delta": expense_delta,
            "currency": currency,
            "comparison_state": expense_metric.get("state"),
            "drilldown": None,
        })
    top_category = next((item for item in contribution_items if item.get("drillable") and to_decimal_money(item.get("delta") or 0) != 0), None)
    if top_category and len(observations) < 3:
        observations.append({
            "kind": "category_change",
            "title": "Заметное изменение по категории",
            "description": str(top_category.get("category") or "Категория"),
            "delta": top_category.get("delta"),
            "currency": currency,
            "drilldown": top_category.get("operation_scope"),
        })
    top_merchant = next((item for item in merchant_items if item.get("drillable") and to_decimal_money(item.get("delta") or 0) != 0), None)
    if top_merchant and len(observations) < 3:
        observations.append({
            "kind": "merchant_change",
            "title": "Заметное изменение по магазину",
            "description": str(top_merchant.get("merchant") or "Магазин"),
            "delta": top_merchant.get("delta"),
            "currency": currency,
            "drilldown": top_merchant.get("operation_scope"),
        })
    return observations


def build_report(analytics: dict[str, Any], request: ReportBuildRequest) -> dict[str, Any]:
    if request.report_kind not in REPORT_KINDS:
        raise ValueError("bad_report_kind")
    available = [str(value) for value in analytics.get("available_currencies") or ()]
    currency = request.selected_currency if request.selected_currency in available else (available[0] if available else request.selected_currency or request.fallback_currency)
    summary = (analytics.get("summary") or {}).get("currency_groups", {}).get(currency)
    metrics = (analytics.get("overview_metrics") or {}).get(currency) or {}
    operation_count = int((summary or {}).get("count") or 0)
    income = to_decimal_money((summary or {}).get("income") or 0)
    expense = to_decimal_money((summary or {}).get("expense") or 0)
    if operation_count == 0:
        data_state = "no_data"
    elif income > 0 and expense == 0:
        data_state = "income_only"
    elif expense > 0 and income == 0:
        data_state = "expense_only"
    else:
        data_state = "complete"
    structure_type = str((analytics.get("category_structure") or {}).get("type") or "expense")
    operation_type = "income" if structure_type == "income" else "expense"
    category_group = (analytics.get("category_structure") or {}).get("currency_groups", {}).get(currency) or {"items": []}
    merchant_group = (analytics.get("merchant_structure") or {}).get("currency_groups", {}).get(currency) or {"items": []}
    contribution_group = (analytics.get("change_contribution") or {}).get("currency_groups", {}).get(currency) or {"items": []}
    categories = _dimension_items(
        list(category_group.get("items") or []),
        dimension="category",
        currency=currency,
        period=analytics["period"],
        filters=analytics["filters"],
        workspace_scope=request.workspace_scope,
        operation_type=operation_type,
    )
    merchants = _dimension_items(
        list(merchant_group.get("items") or []),
        dimension="merchant",
        currency=currency,
        period=analytics["period"],
        filters=analytics["filters"],
        workspace_scope=request.workspace_scope,
        operation_type=operation_type,
    )
    contributions = _dimension_items(
        list(contribution_group.get("items") or []),
        dimension="category",
        currency=currency,
        period=analytics["period"],
        filters=analytics["filters"],
        workspace_scope=request.workspace_scope,
        operation_type=operation_type,
    )
    return {
        "kind": request.report_kind,
        "period": analytics["period"],
        "comparison_period": analytics["previous_period"],
        "workspace": {
            "scope": request.workspace_scope,
            "name": request.workspace_name,
            "type": request.workspace_type,
            "read_only": request.read_only,
        },
        "filters": analytics["filters"],
        "available_currencies": available,
        "selected_currency": currency,
        "data_state": data_state,
        "summary": None if summary is None else {
            "currency": currency,
            "income": income,
            "expense": expense,
            "result": income - expense,
            "operation_count": operation_count,
        },
        "comparison": metrics or None,
        "structure_type": structure_type,
        "categories": categories,
        "merchants": merchants,
        "observations": _observations(metrics, contributions, merchants, currency),
        "export_available": request.report_kind == "selected" and len(available) <= 1,
        "export_reason": (
            None
            if request.report_kind == "selected" and len(available) <= 1
            else "Для отчёта с несколькими валютами экспорт недоступен."
            if request.report_kind == "selected"
            else "Экспорт доступен для отчёта за выбранный период."
        ),
    }


def report_ready_kinds(workspace_where_sql: str, workspace_params: tuple[Any, ...], *, today: date) -> set[str]:
    week_start, week_end, _ = completed_report_period("completed_week", today)
    month_start, month_end, _ = completed_report_period("completed_month", today)
    rows = pg_fetchall(
        f"""
        SELECT COUNT(*) FILTER (WHERE op_date BETWEEN %s AND %s),
               COUNT(*) FILTER (WHERE op_date BETWEEN %s AND %s)
          FROM public.operations
         WHERE {workspace_where_sql}
           AND COALESCE(type,'') <> 'noop'
           AND COALESCE(category,'') <> 'Без операций'
        """,
        (week_start, week_end, month_start, month_end, *workspace_params),
    )
    week_count, month_count = rows[0] if rows else (0, 0)
    result = set()
    if int(week_count or 0) > 0:
        result.add("completed_week")
    if int(month_count or 0) > 0:
        result.add("completed_month")
    return result
