from datetime import datetime, timedelta, timezone
from db.database import pg_query

UTC = timezone.utc

THRESHOLD_REPEATS = 4       # твой порог
WINDOW_DAYS = 60            # окно анализа
MAX_BUTTONS = 3

def get_quick_buttons(user_id: int, chat_id: int | None = None) -> list[tuple[str, str]]:
    """
    Возвращает до трёх быстрых кнопок в формате [(label, payload), ...]
    Логика: считаем повторы по (category, amount) за последние WINDOW_DAYS.
    Если chat_id задан — считаем только по этому чату, иначе по пользователю в целом.
    """
    since = datetime.now(tz=UTC) - timedelta(days=WINDOW_DAYS)
    if chat_id:
        rows = pg_query("""
            SELECT category, amount, COUNT(*) AS c
            FROM public.operations
            WHERE user_id = %s AND chat_id = %s AND created_at >= %s
              AND category IS NOT NULL AND amount IS NOT NULL
            GROUP BY category, amount
            HAVING COUNT(*) >= %s
            ORDER BY c DESC, MAX(created_at) DESC
            LIMIT %s
        """, (user_id, chat_id, since, THRESHOLD_REPEATS, MAX_BUTTONS))
    else:
        rows = pg_query("""
            SELECT category, amount, COUNT(*) AS c
            FROM public.operations
            WHERE user_id = %s AND created_at >= %s
              AND category IS NOT NULL AND amount IS NOT NULL
            GROUP BY category, amount
            HAVING COUNT(*) >= %s
            ORDER BY c DESC, MAX(created_at) DESC
            LIMIT %s
        """, (user_id, since, THRESHOLD_REPEATS, MAX_BUTTONS))

    buttons: list[tuple[str, str]] = []
    for r in rows:
        label = f"{r['category']} {int(r['amount']) if float(r['amount']).is_integer() else r['amount']}"
        payload = f"quick::{r['category']}::{r['amount']}"
        buttons.append((label, payload))
    return buttons
