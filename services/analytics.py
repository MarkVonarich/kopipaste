# services/analytics.py — v2025.08.18-01
__version__ = "2025.08.18-01"

from datetime import datetime, timedelta, date
from typing import Dict, Tuple
from db.queries import get_user_budgets
from db.database import pg_fetchall
from settings import WEEK_DEFAULT
from services.records import list_categories_for_type
from db.queries import get_user_currency

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

