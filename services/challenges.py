from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall
from services.analytics_privacy import safe_error_code, sanitize_properties
from services.notification_preferences import get_notification_preferences
from services.product_events import ProductEvent, track_product_event
from services.user_time import user_local_date
from settings import VOICE_INPUT_ENABLED

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChallengeDefinition:
    key: str
    title: str
    description: str
    period_type: str
    metric: str
    target: int
    group: str
    cta_label: str
    cta_callback: str
    completion_copy: str
    version: int = 1
    difficulty: str | None = None
    feature_flag: str | None = None


@dataclass(frozen=True)
class ChallengeCard:
    definition: ChallengeDefinition
    progress: int
    target: int
    completed: bool
    period_key: str
    period_end: date | None = None


@dataclass(frozen=True)
class AchievementDefinition:
    key: str
    title: str
    description: str


@dataclass(frozen=True)
class ChallengePrompt:
    card: ChallengeCard
    text: str
    purpose: str = "daily_progress_prompt"


ONBOARDING_CHALLENGES = [
    ChallengeDefinition("first_operation", "Первая операция", "Запишите первый расход или доход.", "once", "first_operation", 1, "onboarding", "Добавить операцию", "menu_examples", "Первая операция сохранена."),
    ChallengeDefinition("first_income", "Первый доход", "Сохраните первую операцию дохода.", "once", "first_income", 1, "onboarding", "Добавить доход", "income_status", "Первый доход сохранён."),
    ChallengeDefinition("open_history", "Открыть историю", "Посмотрите историю операций.", "once", "history_opened", 1, "onboarding", "Открыть историю", "menu_history", "История открыта."),
    ChallengeDefinition("edit_operation", "Исправить запись", "Отредактируйте сохранённую операцию.", "once", "operation_edited", 1, "onboarding", "Изменить операцию", "op_edit", "Операция исправлена."),
    ChallengeDefinition("custom_expense_category", "Категория расходов", "Создайте собственную категорию расходов.", "once", "category_created", 1, "onboarding", "Открыть категории", "cat_menu", "Категория расходов создана."),
    ChallengeDefinition("custom_income_category", "Категория доходов", "Создайте собственную категорию доходов.", "once", "category_created", 1, "onboarding", "Открыть категории", "cat_menu", "Категория доходов создана."),
    ChallengeDefinition("rename_category", "Переименовать категорию", "Переименуйте пользовательскую категорию.", "once", "category_renamed", 1, "onboarding", "Открыть категории", "cat_menu", "Категория переименована."),
    ChallengeDefinition("first_limit", "Первый лимит", "Установите лимит или бюджет категории.", "once", "limit_created", 1, "onboarding", "Настроить лимит", "lb_hub", "Лимит создан."),
    ChallengeDefinition("quiet_hours", "Тихие часы", "Настройте период без автоматических уведомлений.", "once", "quiet_hours_updated", 1, "onboarding", "Настроить тихие часы", "notif_quiet_hours", "Тихие часы настроены."),
    ChallengeDefinition("timezone", "Часовой пояс", "Проверьте часовой пояс для уведомлений.", "once", "timezone_updated", 1, "onboarding", "Изменить часовой пояс", "menu_tz", "Часовой пояс настроен."),
    ChallengeDefinition("first_reminder", "Первое напоминание", "Создайте напоминание о будущем событии.", "once", "reminder_created", 1, "onboarding", "Создать напоминание", "rem_add", "Напоминание создано."),
    ChallengeDefinition("first_financial_goal", "Первая финансовая цель", "Создайте первую финансовую цель.", "once", "goal_created", 1, "onboarding", "Открыть цели", "goal|home", "Финансовая цель создана."),
    ChallengeDefinition("recurring_reminder", "Повторяющееся напоминание", "Создайте напоминание с повтором.", "once", "recurring_reminder_created", 1, "onboarding", "Создать напоминание", "rem_add", "Повтор настроен."),
    ChallengeDefinition("first_export", "Первый экспорт", "Сформируйте экспорт истории.", "once", "export_completed", 1, "onboarding", "Сделать экспорт", "exp_menu", "Экспорт готов."),
    ChallengeDefinition("weekly_report", "Недельный отчёт", "Откройте недельный отчёт.", "once", "weekly_report_opened", 1, "onboarding", "Открыть отчёты", "menu_report", "Недельный отчёт открыт."),
    ChallengeDefinition("monthly_report", "Месячный отчёт", "Откройте месячный отчёт.", "once", "monthly_report_opened", 1, "onboarding", "Открыть отчёты", "menu_report", "Месячный отчёт открыт."),
    ChallengeDefinition("voice_entry", "Голосовой ввод", "Попробуйте голосовую запись.", "once", "voice_used", 1, "onboarding", "Открыть настройки", "menu_settings", "Голосовой ввод использован.", feature_flag="voice"),
    ChallengeDefinition("receipt_import", "Распознать чек", "Сохраните операции из фото чека.", "once", "receipt_imported", 1, "onboarding", "Открыть подсказки", "menu_examples", "Чек распознан."),
    ChallengeDefinition("privacy_review", "Приватность", "Откройте настройки приватности.", "once", "privacy_opened", 1, "onboarding", "Открыть приватность", "privacy_menu", "Приватность просмотрена."),
    ChallengeDefinition("category_management_review", "Порядок в категориях", "Откройте управление категориями.", "once", "category_management_opened", 1, "onboarding", "Открыть категории", "cat_menu", "Категории просмотрены."),
]

DAILY_CHALLENGES = [
    ChallengeDefinition("daily_two_operations", "Две записи за день", "Запишите две реальные операции за сегодня.", "day", "operation_count", 2, "daily", "Добавить операцию", "menu_examples", "Две записи за день готовы."),
    ChallengeDefinition("daily_two_categories", "Разные категории", "Используйте две категории в сегодняшних записях.", "day", "distinct_categories", 2, "daily", "Добавить операцию", "menu_examples", "Категории разнообразны."),
    ChallengeDefinition("daily_review_history", "Проверить историю", "Откройте историю сегодня.", "day", "history_opened", 1, "daily", "Открыть историю", "menu_history", "История проверена."),
]

WEEKLY_CHALLENGES = [
    ChallengeDefinition("weekly_five_days", "Вести учёт 5 дней", "Записывайте операции в пять разных дней недели.", "week", "distinct_active_days", 5, "weekly", "Добавить операцию", "menu_examples", "Пять активных дней есть."),
    ChallengeDefinition("weekly_seven_operations", "7 записей за неделю", "Сохраните семь реальных операций за неделю.", "week", "operation_count", 7, "weekly", "Добавить операцию", "menu_examples", "Семь записей готовы."),
    ChallengeDefinition("weekly_three_categories", "3 категории за неделю", "Используйте три разные категории.", "week", "distinct_categories", 3, "weekly", "Добавить операцию", "menu_examples", "Три категории использованы."),
    ChallengeDefinition("weekly_limit", "Лимит недели", "Создайте или поддерживайте один лимит.", "week", "limit_created", 1, "weekly", "Настроить лимит", "lb_hub", "Лимит есть."),
    ChallengeDefinition("weekly_reminder", "Напоминание недели", "Создайте одно полезное напоминание.", "week", "reminder_created", 1, "weekly", "Создать напоминание", "rem_add", "Напоминание создано."),
    ChallengeDefinition("weekly_report_open", "Открыть отчёт недели", "Посмотрите недельный отчёт.", "week", "weekly_report_opened", 1, "weekly", "Открыть отчёты", "menu_report", "Отчёт открыт."),
]

MONTHLY_CHALLENGES = [
    ChallengeDefinition("monthly_operations_base", "Базовый месяц", "Запишите 14 операций за месяц.", "month", "operation_count", 14, "monthly", "Добавить операцию", "menu_examples", "Базовый месяц выполнен.", difficulty="Базовый"),
    ChallengeDefinition("monthly_active_weeks", "Активные недели", "Ведите учёт минимум в 3 разных недели.", "month", "distinct_active_weeks", 3, "monthly", "Добавить операцию", "menu_examples", "Три активные недели есть."),
    ChallengeDefinition("monthly_report_open", "Месячный отчёт", "Откройте месячный отчёт.", "month", "monthly_report_opened", 1, "monthly", "Открыть отчёты", "menu_report", "Месячный отчёт открыт."),
    ChallengeDefinition("monthly_limits", "Два лимита", "Поддерживайте лимиты для двух категорий.", "month", "limit_count", 2, "monthly", "Настроить лимит", "lb_hub", "Лимиты настроены."),
]

ALL_CHALLENGES = {d.key: d for d in ONBOARDING_CHALLENGES + DAILY_CHALLENGES + WEEKLY_CHALLENGES + MONTHLY_CHALLENGES}

ACHIEVEMENTS = [
    AchievementDefinition("first_step", "🌱 Первый шаг", "Первая операция"),
    AchievementDefinition("first_income", "💰 Первый доход", "Первый доход"),
    AchievementDefinition("rhythm", "📅 В ритме", "Активность в 5 разные дней"),
    AchievementDefinition("under_control", "🛡 Под контролем", "Первый лимит"),
    AchievementDefinition("organized", "⏰ Организованность", "Первое напоминание"),
    AchievementDefinition("quiet_mode", "🌙 Спокойный режим", "Тихие часы настроены"),
    AchievementDefinition("archivist", "📦 Архивариус", "Первый экспорт"),
    AchievementDefinition("mindful", "🧠 Осознанность", "Недельный отчёт открыт"),
    AchievementDefinition("order", "🧹 Порядок", "Действие с категориями"),
    AchievementDefinition("new_input", "🎙 Новый способ", "Голосовой ввод"),
    AchievementDefinition("receipt_ok", "🧾 Чек принят", "Распознавание чека"),
]


def _period_bounds(local_today: date, period_type: str) -> tuple[date | None, date | None, str]:
    if period_type == "once":
        return None, None, "once"
    if period_type == "day":
        return local_today, local_today, local_today.isoformat()
    if period_type == "week":
        start = local_today - timedelta(days=local_today.weekday())
        return start, start + timedelta(days=6), start.isoformat()
    start = local_today.replace(day=1)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, nxt - timedelta(days=1), start.strftime("%Y-%m")


def user_local_today(user_id: int, *, now_utc: datetime | None = None) -> date:
    return user_local_date(user_id, now_utc=now_utc)


def _feature_enabled(defn: ChallengeDefinition) -> bool:
    if defn.feature_flag == "voice":
        return bool(VOICE_INPUT_ENABLED)
    return True


def _event_count(user_id: int, event_name: str, start: date | None, end: date | None, property_filter: tuple[str, str] | None = None) -> int:
    try:
        params: list[Any] = [user_id, event_name]
        filt = "user_id=%s AND event_name=%s AND deleted_at IS NULL"
        if start and end:
            filt += " AND occurred_at::date BETWEEN %s AND %s"
            params.extend([start, end])
        if property_filter:
            filt += " AND properties->>%s = %s"
            params.extend([property_filter[0], property_filter[1]])
        rows = pg_fetchall(f"SELECT COUNT(*)::int FROM analytics.product_events WHERE {filt}", tuple(params))
        return int(rows[0][0] or 0) if rows else 0
    except Exception:
        return 0


def _operation_metric(user_id: int, metric: str, start: date | None, end: date | None) -> int:
    try:
        params: list[Any] = [user_id]
        period = ""
        if start and end:
            period = "AND op_date BETWEEN %s AND %s"
            params.extend([start, end])
        if metric == "operation_count":
            rows = pg_fetchall(
                f"""
                SELECT COUNT(*)::int FROM public.operations
                 WHERE user_id=%s {period}
                   AND COALESCE(type,'') IN ('Расходы','Доходы')
                   AND COALESCE(category,'') <> 'Без операций'
                """,
                tuple(params),
            )
        elif metric == "distinct_categories":
            rows = pg_fetchall(
                f"""
                SELECT COUNT(DISTINCT category)::int FROM public.operations
                 WHERE user_id=%s {period}
                   AND COALESCE(type,'') IN ('Расходы','Доходы')
                   AND COALESCE(category,'') NOT IN ('', 'Без операций')
                """,
                tuple(params),
            )
        elif metric == "distinct_active_days":
            rows = pg_fetchall(
                f"""
                SELECT COUNT(DISTINCT op_date)::int FROM public.operations
                 WHERE user_id=%s {period}
                   AND COALESCE(type,'') IN ('Расходы','Доходы')
                   AND COALESCE(category,'') <> 'Без операций'
                """,
                tuple(params),
            )
        elif metric == "distinct_active_weeks":
            rows = pg_fetchall(
                f"""
                SELECT COUNT(DISTINCT date_trunc('week', op_date::timestamp)::date)::int FROM public.operations
                 WHERE user_id=%s {period}
                   AND COALESCE(type,'') IN ('Расходы','Доходы')
                   AND COALESCE(category,'') <> 'Без операций'
                """,
                tuple(params),
            )
        elif metric == "income_count":
            rows = pg_fetchall(
                f"""
                SELECT COUNT(*)::int FROM public.operations
                 WHERE user_id=%s {period}
                   AND COALESCE(type,'') = 'Доходы'
                   AND COALESCE(category,'') <> 'Без операций'
                """,
                tuple(params),
            )
        elif metric == "limit_count":
            rows = pg_fetchall(
                """
                SELECT COUNT(*)::int FROM public.category_limits
                 WHERE user_id=%s
                """,
                (user_id,),
            )
        else:
            return 0
        return int(rows[0][0] or 0) if rows else 0
    except Exception:
        return 0


def calculate_progress(user_id: int, defn: ChallengeDefinition, start: date | None, end: date | None) -> int:
    if defn.metric in {"operation_count", "distinct_categories", "distinct_active_days", "distinct_active_weeks", "income_count", "limit_count"}:
        return _operation_metric(user_id, defn.metric, start, end)
    if defn.metric == "first_operation":
        return min(_operation_metric(user_id, "operation_count", None, None), defn.target)
    if defn.metric == "first_income":
        return min(_operation_metric(user_id, "income_count", None, None), defn.target)
    if defn.metric == "recurring_reminder_created":
        return min(_event_count(user_id, "reminder_created", start, end, ("repeat_kind", "recurring")), defn.target)
    mapping = {
        "operation_edited": ("operation_edited", None),
        "export_completed": ("export_completed", None),
        "privacy_opened": ("privacy_opened", None),
        "quiet_hours_updated": ("quiet_hours_updated", None),
        "reminder_created": ("reminder_created", None),
        "goal_created": ("goal_created", None),
        "limit_created": ("limit_created", None),
        "weekly_report_opened": ("weekly_report_opened", None),
        "monthly_report_opened": ("monthly_report_opened", None),
        "category_created": ("category_created", None),
        "category_renamed": ("category_renamed", None),
        "category_management_opened": ("challenge_cta_opened", ("destination", "category_management")),
        "history_opened": ("challenge_cta_opened", ("destination", "history")),
        "timezone_updated": ("quiet_hours_updated", ("destination", "timezone")),
        "voice_used": ("operation_created", ("source", "voice")),
        "receipt_imported": ("operation_created", ("source", "receipt")),
    }
    event_name, marker = mapping.get(defn.metric, (None, None))
    if not event_name:
        return 0
    count = _event_count(user_id, event_name, start, end)
    if marker:
        count = _event_count(user_id, event_name, start, end, marker)
    if count:
        return min(count, defn.target)
    return min(count, defn.target)


def _existing_completed_once(user_id: int, defn: ChallengeDefinition) -> bool:
    return calculate_progress(user_id, defn, None, None) >= defn.target


def definitions_for_section(user_id: int, section: str) -> list[ChallengeDefinition]:
    if section == "onboarding":
        return [d for d in ONBOARDING_CHALLENGES if _feature_enabled(d) and not _existing_completed_once(user_id, d)]
    if section == "today":
        return DAILY_CHALLENGES[:2]
    if section == "week":
        return WEEKLY_CHALLENGES[:3]
    if section == "month":
        return MONTHLY_CHALLENGES[:3]
    return []


def upsert_assignments(user_id: int, section: str) -> list[ChallengeCard]:
    local_today = user_local_today(user_id)
    cards = []
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            for defn in definitions_for_section(user_id, section):
                start, end, period_key = _period_bounds(local_today, defn.period_type)
                progress = min(calculate_progress(user_id, defn, start, end), defn.target)
                status = "completed" if progress >= defn.target else "active"
                cur.execute(
                    """
                    INSERT INTO public.user_challenge_assignments
                        (user_id, challenge_key, period_type, period_key, target, progress, status, completed_at, version)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s='completed' THEN now() ELSE NULL END,%s)
                    ON CONFLICT (user_id, COALESCE(workspace_id, 0), challenge_key, period_type, period_key) DO UPDATE
                       SET progress=GREATEST(public.user_challenge_assignments.progress, EXCLUDED.progress),
                           status=CASE WHEN GREATEST(public.user_challenge_assignments.progress, EXCLUDED.progress) >= public.user_challenge_assignments.target THEN 'completed' ELSE public.user_challenge_assignments.status END,
                           completed_at=CASE
                               WHEN GREATEST(public.user_challenge_assignments.progress, EXCLUDED.progress) >= public.user_challenge_assignments.target
                               THEN COALESCE(public.user_challenge_assignments.completed_at, now())
                               ELSE public.user_challenge_assignments.completed_at
                           END,
                           updated_at=now()
                    RETURNING progress, status
                    """,
                    (user_id, defn.key, defn.period_type, period_key, defn.target, progress, status, status, defn.version),
                )
                row = cur.fetchone()
                cards.append(ChallengeCard(defn, int(row[0] or 0), defn.target, row[1] == "completed", period_key, end))
        conn.commit()
    except (errors.UndefinedTable, errors.UndefinedColumn, errors.InvalidSchemaName):
        if conn:
            conn.rollback()
        for defn in definitions_for_section(user_id, section):
            start, end, period_key = _period_bounds(local_today, defn.period_type)
            progress = min(calculate_progress(user_id, defn, start, end), defn.target)
            cards.append(ChallengeCard(defn, progress, defn.target, progress >= defn.target, period_key, end))
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()
    return cards


def grant_achievement(user_id: int, achievement_key: str) -> bool:
    conn = None
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_achievement_grants (user_id, achievement_key)
                VALUES (%s,%s)
                ON CONFLICT DO NOTHING
                """,
                (user_id, achievement_key),
            )
            created = int(cur.rowcount or 0) == 1
        conn.commit()
        return created
    except Exception:
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def achievements_for_user(user_id: int) -> list[tuple[AchievementDefinition, datetime | None]]:
    earned: dict[str, datetime] = {}
    try:
        rows = pg_fetchall("SELECT achievement_key, earned_at FROM public.user_achievement_grants WHERE user_id=%s", (user_id,))
        earned = {str(r[0]): r[1] for r in rows}
    except Exception:
        earned = {}
    return [(item, earned.get(item.key)) for item in ACHIEVEMENTS]


def _achievement_for_event(event_name: str, properties: dict[str, Any] | None) -> str | None:
    props = properties or {}
    if event_name == "operation_created":
        if props.get("source") == "voice":
            return "new_input"
        if props.get("source") == "receipt":
            return "receipt_ok"
        if props.get("op_type") == "Доходы":
            return "first_income"
        return "first_step"
    return {
        "limit_created": "under_control",
        "reminder_created": "organized",
        "quiet_hours_updated": "quiet_mode",
        "export_completed": "archivist",
        "weekly_report_opened": "mindful",
        "category_renamed": "order",
        "category_created": "order",
    }.get(event_name)


def process_product_event(ev) -> None:
    user_id = getattr(ev, "user_id", None)
    if user_id is None:
        return
    event_name = getattr(ev, "event_name", "")
    properties = sanitize_properties(getattr(ev, "properties", None) or {})
    properties.setdefault("source", getattr(ev, "source", None))
    if "op_type" not in properties and "operation_type" in properties:
        properties["op_type"] = properties.get("operation_type")
    achievement_key = _achievement_for_event(event_name, properties)
    if achievement_key:
        if grant_achievement(int(user_id), achievement_key):
            _queue_achievement_notification(int(user_id), achievement_key)
    try:
        for section in ("onboarding", "today", "week", "month"):
            cards = upsert_assignments(int(user_id), section)
            completed = [c for c in cards if c.completed]
            if completed:
                log.info("challenge_progress_updated completed=%s", len(completed))
                for card in completed:
                    _queue_challenge_completion(int(user_id), card)
    except Exception as exc:
        log.warning("challenge_progress_failed reason=%s", safe_error_code(exc))


def challenge_prompt_candidates() -> list[int]:
    return []


def challenge_notifications_enabled(user_id: int) -> bool:
    return False


def build_challenge_prompt(user_id: int) -> ChallengePrompt | None:
    try:
        cards = upsert_assignments(user_id, "today")
    except Exception as exc:
        log.warning("challenge_prompt_build_failed reason=%s", safe_error_code(exc))
        return None
    active = [c for c in cards if not c.completed]
    if not active:
        return None
    card = active[0]
    text = (
        "🏆 Челлендж дня\n\n"
        f"{card.definition.title}\n"
        f"{card.definition.description}\n\n"
        f"Прогресс: {min(card.progress, card.target)}/{card.target}"
    )
    return ChallengePrompt(card=card, text=text)


def _challenge_button_payload() -> list[list[dict[str, str]]]:
    return [[{"label": "🏆 Открыть челленджи", "callback_data": "chal|home"}]]


def _queue_challenge_completion(user_id: int, card: ChallengeCard) -> None:
    if not challenge_notifications_enabled(user_id):
        return
    from services.automatic_notifications import DeliveryPolicy, queue_automatic_notification

    text = (
        "🏆 Челлендж выполнен\n\n"
        f"{card.definition.title}\n"
        f"{card.definition.completion_copy}"
    )
    queue_automatic_notification(
        user_id=user_id,
        notification_type="challenge_completed",
        dedupe_key=f"challenge:{card.definition.key}:{card.period_key}:challenge_completed",
        policy=DeliveryPolicy.DEFER,
        template_key="challenge_completed",
        payload={"text": text, "buttons": _challenge_button_payload(), "challenge_key": card.definition.key, "period_key": card.period_key},
    )


def _queue_achievement_notification(user_id: int, achievement_key: str) -> None:
    if not challenge_notifications_enabled(user_id):
        return
    item = next((achievement for achievement in ACHIEVEMENTS if achievement.key == achievement_key), None)
    if item is None:
        return
    from services.automatic_notifications import DeliveryPolicy, queue_automatic_notification

    text = f"🏅 Достижение получено\n\n{item.title}\n{item.description}"
    queue_automatic_notification(
        user_id=user_id,
        notification_type="achievement_granted",
        dedupe_key=f"achievement:{achievement_key}:achievement_granted",
        policy=DeliveryPolicy.DEFER,
        template_key="achievement_granted",
        payload={"text": text, "buttons": _challenge_button_payload(), "achievement_key": achievement_key},
    )


async def challenge_daily_prompt_job(context) -> dict[str, int]:
    from services.automatic_notifications import DeliveryPolicy, dispatch_automatic_notification
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    counts = {"sent": 0, "deferred": 0, "skipped": 0}
    for user_id in challenge_prompt_candidates():
        if not challenge_notifications_enabled(user_id):
            continue
        local_today = user_local_today(user_id)
        prompt = build_challenge_prompt(user_id)
        if not prompt:
            continue
        result = await dispatch_automatic_notification(
            context,
            user_id=user_id,
            notification_type="challenge_prompt",
            dedupe_key=f"challenge:{local_today.isoformat()}:daily_progress_prompt",
            policy=DeliveryPolicy.SKIP,
            text=prompt.text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏆 Открыть челленджи", callback_data="chal|home")]]),
            template_key="challenge_prompt",
            payload={
                "text": prompt.text,
                "buttons": _challenge_button_payload(),
                "challenge_key": prompt.card.definition.key,
                "period_key": prompt.card.period_key,
                "purpose": prompt.purpose,
            },
            original_scheduled_at=datetime.now(timezone.utc),
        )
        if result.status in counts:
            counts[result.status] += 1
        if result.status == "sent":
            track_product_event(ProductEvent(
                event_name="challenge_notification_sent",
                user_id=user_id,
                status="sent",
                properties={"notification_type": "challenge_prompt"},
            ))
        elif result.status == "skipped":
            track_product_event(ProductEvent(
                event_name="challenge_notification_skipped_quiet_hours",
                user_id=user_id,
                status="skipped",
                properties={"notification_type": "challenge_prompt", "reason": result.reason or "unknown"},
            ))
    return counts
