from datetime import datetime, timedelta, timezone
from db.database import pg_fetchall
from utils.money import format_money as format_money_value

UTC = timezone.utc

THRESHOLD_REPEATS = 4       # твой порог
WINDOW_DAYS = 60            # окно анализа
MAX_BUTTONS = 2

def get_quick_buttons(user_id: int, chat_id: int | None = None) -> list[tuple[str, str]]:
    """
    Возвращает до трёх быстрых кнопок в формате [(label, payload), ...]
    Логика: считаем повторы по (category, amount) за последние WINDOW_DAYS.
    Если chat_id задан — считаем только по этому чату, иначе по пользователю в целом.
    """
    since = datetime.now(tz=UTC) - timedelta(days=WINDOW_DAYS)
    if chat_id:
        rows = pg_fetchall("""
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
        rows = pg_fetchall("""
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
    for category, amount, _c in rows:
        amount_fmt = format_money_value(amount, "RUB").replace(" ₽", "")
        label = f"{category} {amount_fmt}"
        payload = f"quick::{category}::{amount_fmt}"
        buttons.append((label, payload))
    return buttons
