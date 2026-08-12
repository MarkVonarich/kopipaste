from __future__ import annotations

from dataclasses import dataclass

from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall


@dataclass(frozen=True)
class HomeWidget:
    key: str
    title: str
    description: str
    layout: str
    default_enabled: bool = True


HOME_WIDGETS = (
    HomeWidget("financial_result", "Финансовый результат", "Итог за выбранный период", "wide"),
    HomeWidget("activity", "Активность", "Дни с записанными операциями", "compact"),
    HomeWidget("income_expense", "Доходы и расходы", "Суммы и быстрые действия", "wide"),
    HomeWidget("whats_new", "Новое в КопиPaste", "Свежие возможности продукта", "wide"),
    HomeWidget("challenges", "Челленджи", "Текущие финансовые задания", "compact"),
    HomeWidget("goals", "Цели", "Главные активные цели", "compact"),
    HomeWidget("limits", "Лимиты", "Риски и прогресс лимитов", "compact"),
    HomeWidget("reminders", "Напоминания", "Ближайшие запланированные события", "compact"),
    HomeWidget("insights", "Инсайты", "Важные изменения периода", "compact"),
    HomeWidget("shopping_list", "Список покупок", "Покупки выбранного пространства", "compact"),
    HomeWidget("recent_operations", "Последние операции", "Свежие записи периода", "wide"),
)
HOME_WIDGET_KEYS = tuple(widget.key for widget in HOME_WIDGETS)


def reconcile_home_preferences(order: object, enabled: object) -> dict:
    known = set(HOME_WIDGET_KEYS)
    raw_order = order if isinstance(order, list) else []
    raw_enabled = enabled if isinstance(enabled, list) else []
    clean_order = []
    for value in raw_order:
        key = str(value)
        if key in known and key not in clean_order:
            clean_order.append(key)
    saved_keys = set(clean_order)
    clean_order.extend(key for key in HOME_WIDGET_KEYS if key not in clean_order)
    if not isinstance(enabled, list):
        clean_enabled = list(HOME_WIDGET_KEYS)
    else:
        explicit_enabled = {str(value) for value in raw_enabled}
        defaults_for_new = {widget.key for widget in HOME_WIDGETS if widget.default_enabled and widget.key not in saved_keys}
        clean_enabled = [key for key in clean_order if key in explicit_enabled or key in defaults_for_new]
    return {"order": clean_order, "enabled": clean_enabled}


def home_widget_registry() -> list[dict]:
    return [
        {
            "key": widget.key,
            "title": widget.title,
            "description": widget.description,
            "layout": widget.layout,
            "default_enabled": widget.default_enabled,
            "default_order": index,
        }
        for index, widget in enumerate(HOME_WIDGETS)
    ]


def validate_home_preferences(order: object, enabled: object) -> None:
    if not isinstance(order, list) or not isinstance(enabled, list):
        raise ValueError("bad_home_preferences")
    if len(order) > len(HOME_WIDGET_KEYS) or len(enabled) > len(HOME_WIDGET_KEYS):
        raise ValueError("bad_home_preferences")
    if any(not isinstance(value, str) or len(value) > 64 for value in [*order, *enabled]):
        raise ValueError("bad_home_preferences")
    if len(set(order)) != len(order) or len(set(enabled)) != len(enabled):
        raise ValueError("bad_home_preferences")
    known = set(HOME_WIDGET_KEYS)
    if set(order) != known or not set(enabled) <= known:
        raise ValueError("bad_home_preferences")


def get_home_preferences(user_id: int) -> dict:
    rows = pg_fetchall(
        "SELECT widget_order, enabled_widgets FROM public.user_home_preferences WHERE user_id=%s LIMIT 1",
        (int(user_id),),
    )
    if not rows:
        return reconcile_home_preferences(None, None)
    return reconcile_home_preferences(rows[0][0], rows[0][1])


def save_home_preferences(user_id: int, order: object, enabled: object) -> dict:
    validate_home_preferences(order, enabled)
    prefs = reconcile_home_preferences(order, enabled)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_home_preferences (user_id, widget_order, enabled_widgets, updated_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (user_id) DO UPDATE
                   SET widget_order=EXCLUDED.widget_order,
                       enabled_widgets=EXCLUDED.enabled_widgets,
                       updated_at=now()
                """,
                (int(user_id), Json(prefs["order"]), Json(prefs["enabled"])),
            )
        conn.commit()
        return prefs
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
