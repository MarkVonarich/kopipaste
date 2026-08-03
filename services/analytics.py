# services/analytics.py — v2025.08.18-01
__version__ = "2025.08.18-01"

from datetime import datetime, timedelta, date
from decimal import Decimal
from typing import Dict, Tuple
from db.queries import get_user_budgets
from db.database import pg_fetchall
from settings import WEEK_DEFAULT
from services.records import list_categories_for_type
from db.queries import get_user_currency
from utils.money import to_decimal_money


def _iso(d) -> str | None:
    return d.isoformat() if hasattr(d, "isoformat") else None


def dashboard_summary(workspace_id: int | None, user_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    rows = pg_fetchall(
        """
        SELECT
            COALESCE(currency, %s) AS currency,
            COALESCE(SUM(amount) FILTER (WHERE type='Расходы' AND op_date=%s), 0) AS expenses_today,
            COALESCE(SUM(amount) FILTER (WHERE type='Расходы' AND op_date BETWEEN %s AND %s), 0) AS expenses_week,
            COALESCE(SUM(amount) FILTER (WHERE type='Расходы' AND op_date BETWEEN %s AND %s), 0) AS expenses_month,
            COALESCE(SUM(amount) FILTER (WHERE type='Доходы' AND op_date BETWEEN %s AND %s), 0) AS income_month,
            COUNT(*) FILTER (WHERE op_date BETWEEN %s AND %s) AS operation_count
          FROM public.operations
         WHERE (%s::bigint IS NULL OR workspace_id=%s)
           AND (%s::bigint IS NULL OR user_id=%s)
           AND op_date BETWEEN %s AND %s
         GROUP BY COALESCE(currency, %s)
         ORDER BY COALESCE(currency, %s)
        """,
        (
            get_user_currency(user_id), today, week_start, today, month_start, today,
            month_start, today, month_start, today, workspace_id, workspace_id,
            None if workspace_id else user_id, None if workspace_id else user_id,
            month_start, today, get_user_currency(user_id), get_user_currency(user_id),
        ),
    )
    by_currency = []
    for currency, today_exp, week_exp, month_exp, month_inc, op_count in rows:
        today_exp_dec = to_decimal_money(today_exp or 0)
        week_exp_dec = to_decimal_money(week_exp or 0)
        month_exp_dec = to_decimal_money(month_exp or 0)
        month_inc_dec = to_decimal_money(month_inc or 0)
        by_currency.append({
            "currency": currency,
            "expenses_today": today_exp_dec,
            "expenses_week": week_exp_dec,
            "expenses_month": month_exp_dec,
            "income_month": month_inc_dec,
            "net_cash_flow_month": month_inc_dec - month_exp_dec,
            "operation_count": int(op_count or 0),
        })
    recent = pg_fetchall(
        """
        SELECT id, op_date, type, category, amount, COALESCE(currency, %s), comment, source, created_at
          FROM public.operations
         WHERE (%s::bigint IS NULL OR workspace_id=%s)
           AND (%s::bigint IS NULL OR user_id=%s)
         ORDER BY op_date DESC, id DESC
         LIMIT 10
        """,
        (get_user_currency(user_id), workspace_id, workspace_id, None if workspace_id else user_id, None if workspace_id else user_id),
    )
    return {
        "workspace_id": workspace_id,
        "period": {"today": today.isoformat(), "week_start": week_start.isoformat(), "month_start": month_start.isoformat()},
        "by_currency": by_currency,
        "recent_operations": [
            {
                "id": int(r[0]),
                "operation_date": _iso(r[1]),
                "type": r[2],
                "category": r[3],
                "amount": to_decimal_money(r[4] or 0),
                "currency": r[5],
                "comment": r[6],
                "source": r[7],
                "created_at": _iso(r[8]),
            }
            for r in recent
        ],
    }


def time_series(workspace_id: int | None, user_id: int, start: date, end: date, bucket: str = "day") -> dict:
    if bucket not in {"day", "week", "month"}:
        raise ValueError("bucket must be day, week or month")
    trunc = {"day": "day", "week": "week", "month": "month"}[bucket]
    rows = pg_fetchall(
        f"""
        SELECT date_trunc('{trunc}', op_date::timestamp)::date AS bucket_start,
               type,
               COALESCE(currency, %s) AS currency,
               COALESCE(SUM(amount), 0) AS total,
               COUNT(*) AS count
          FROM public.operations
         WHERE op_date BETWEEN %s AND %s
           AND (%s::bigint IS NULL OR workspace_id=%s)
           AND (%s::bigint IS NULL OR user_id=%s)
         GROUP BY bucket_start, type, COALESCE(currency, %s)
         ORDER BY bucket_start, type, COALESCE(currency, %s)
        """,
        (get_user_currency(user_id), start, end, workspace_id, workspace_id, None if workspace_id else user_id, None if workspace_id else user_id, get_user_currency(user_id), get_user_currency(user_id)),
    )
    return {
        "workspace_id": workspace_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "bucket": bucket,
        "points": [
            {"bucket_start": _iso(r[0]), "type": r[1], "currency": r[2], "total": int(r[3] or 0), "count": int(r[4] or 0)}
            for r in rows
        ],
    }


def category_analytics(workspace_id: int | None, user_id: int, start: date, end: date, op_type: str = "Расходы") -> dict:
    rows = pg_fetchall(
        """
        SELECT category,
               COALESCE(currency, %s) AS currency,
               COALESCE(SUM(amount), 0) AS total,
               COUNT(*) AS count
          FROM public.operations
         WHERE op_date BETWEEN %s AND %s
           AND type=%s
           AND (%s::bigint IS NULL OR workspace_id=%s)
           AND (%s::bigint IS NULL OR user_id=%s)
         GROUP BY category, COALESCE(currency, %s)
         ORDER BY COALESCE(SUM(amount), 0) DESC, category
        """,
        (get_user_currency(user_id), start, end, op_type, workspace_id, workspace_id, None if workspace_id else user_id, None if workspace_id else user_id, get_user_currency(user_id)),
    )
    totals_by_currency: dict[str, Decimal] = {}
    for _, currency, total, _ in rows:
        totals_by_currency[currency] = totals_by_currency.get(currency, Decimal("0.00")) + to_decimal_money(total or 0)
    return {
        "workspace_id": workspace_id,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "type": op_type,
        "categories": [
            {
                "category": r[0],
                "currency": r[1],
                "total": to_decimal_money(r[2] or 0),
                "count": int(r[3] or 0),
                "percentage": round(float(to_decimal_money(r[2] or 0) * Decimal("100") / (totals_by_currency.get(r[1]) or Decimal("1.00"))), 2),
            }
            for r in rows
        ],
    }

def get_week_range(dt: datetime) -> str:
    start = dt - timedelta(days=dt.weekday())
    end   = start + timedelta(days=6)
    return f"{start.strftime('%d.%m')}–{end.strftime('%d.%m')}"

async def build_report(period: str, chat_id: str) -> str:
    now = datetime.now()
    if period == 'today':
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == 'week':
        start = now - timedelta(days=now.weekday())
    elif period == '2weeks':
        start = now - timedelta(days=13)
    else:
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    rows = pg_fetchall("""
      SELECT type, category, amount
        FROM public.operations
       WHERE chat_id=%s
         AND op_date BETWEEN %s AND %s
    """, (chat_id, start.date(), now.date()))

    total_inc = sum(r[2] for r in rows if r[0]=='Доходы')
    total_exp = sum(r[2] for r in rows if r[0]=='Расходы')
    sums: Dict[str,int] = {}
    for typ,cat,amt in rows:
        if typ=='Расходы':
            sums[cat] = sums.get(cat,0)+amt

    top3 = sorted(sums.items(), key=lambda x: x[1], reverse=True)[:3]
    days = (now.date()-start.date()).days+1
    avg  = total_exp//days if days else total_exp

    wl, ml = get_user_budgets(int(chat_id))
    limits = {'today':None,'week':wl,'2weeks':(wl or 0)*2,'month':ml}
    limit = limits.get(period)
    over  = total_exp-(limit or 0)
    pct   = (total_exp*100/(limit or 1)) if limit else 0

    curcode = get_user_currency(int(chat_id))
    blue, white, yellow = '🟦','◻️','🟨'
    hdrs = {
        'today':  f'📅 Сегодня {now:%d.%m.%Y}',
        'week':   f'📆 Неделя {get_week_range(now)}',
        '2weeks': f'⌛ 2 недели {get_week_range(now)}',
        'month':  f'🗓️ Месяц {now:%B %Y}'
    }
    lines = [f"{blue} *{hdrs[period]}*",
             f"{white} Доходы: *{total_inc} {curcode}*",
             f"{white} Расходы: *{total_exp} {curcode}*"]
    if limit is None:
        lines.append(f"{white} Баланс: *{total_inc-total_exp} {curcode}*")
    else:
        lines.append(f"{white} Остаток: *{(limit or 0)-total_exp} {curcode}*")
    if period!='today':
        lines.append(f"{white} Ср/день: *{avg} {curcode}*")
    if period=='today':
        for c,v in top3:
            lines.append(f"{white} {c} — {v} {curcode}")
        lines.append(f"{white} Итого расходов: *{total_exp} {curcode}*")
    else:
        if top3:
            lines.append(f"{white} Топ-3 расходов:")
            for c,v in top3:
                lines.append(f"    {c} — {v} {curcode}")
        if limit:
            if over>0: lines.append(f"{yellow} Превышение: *{over} {curcode}*")
            lines.append(f"{yellow if pct>100 else white} % бюджета: *{pct:.0f}%*")
    return "\n".join(lines)
