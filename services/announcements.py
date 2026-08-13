from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from db.database import get_conn, pg_fetchall
from services.reports import completed_report_period


ANNOUNCEMENT_TTL_DAYS = 21
MAX_ANNOUNCEMENTS = 5
ACTION_TYPES = {
    "OPEN_HOME_SETTINGS",
    "OPEN_SHOPPING_LIST",
    "OPEN_PLANS",
    "OPEN_PROFILE",
    "OPEN_ANALYTICS",
    "OPEN_REPORTS",
    "OPEN_REPORT_WEEKLY",
    "OPEN_REPORT_MONTHLY",
    "OPEN_DETAIL",
}


@dataclass(frozen=True)
class Announcement:
    id: str
    family: str
    kind: str
    released_on: date
    title: str
    description: str
    action_type: str
    action_label: str
    detail: str | None = None

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "family": self.family,
            "kind": self.kind,
            "released_on": self.released_on,
            "title": self.title,
            "description": self.description,
            "detail": self.detail,
            "action": {"type": self.action_type, "label": self.action_label},
        }


ANNOUNCEMENTS = (
    Announcement("profile-controls-v1", "profile-controls", "feature", date(2026, 8, 13), "Больше контроля в Профиле", "Режим отпуска, настройки категорий и управление личными данными теперь собраны в понятных настройках.", "OPEN_PROFILE", "Открыть профиль"),
    Announcement("reports-2-0", "reports", "feature", date(2026, 8, 14), "Финансовые итоги стали подробнее", "Смотрите доходы, расходы, изменения по категориям и магазинам за неделю, месяц или выбранный период.", "OPEN_REPORTS", "Открыть отчёты"),
    Announcement("smart-planning-v1", "smart-planning", "feature", date(2026, 8, 13), "Планируйте суммы по своим данным", "КопиPaste анализирует прошлые расходы и помогает подобрать лимит, общий бюджет или темп для цели.", "OPEN_PLANS", "Открыть планы"),
    Announcement("custom-home-v1", "home", "feature", date(2026, 8, 11), "Настройте главную под себя", "Выберите нужные виджеты и расположите их в удобном порядке.", "OPEN_HOME_SETTINGS", "Настроить"),
    Announcement("shopping-list-v1", "shopping", "feature", date(2026, 8, 11), "Список покупок теперь под рукой", "Записывайте, что нужно купить, и отмечайте покупки прямо в КопиPaste.", "OPEN_SHOPPING_LIST", "Открыть список"),
    Announcement("plans-v2", "plans", "improvement", date(2026, 8, 10), "Планы стали удобнее", "Категории стали компактнее, а у целей появился полноценный архив.", "OPEN_PLANS", "Открыть планы"),
)

REPORT_READY_TEMPLATES = {
    "completed_week": ("report-ready-weekly", "report-ready-weekly", "Готов отчёт за неделю", "Посмотрите итоги последней завершённой недели.", "OPEN_REPORT_WEEKLY", "Открыть отчёт"),
    "completed_month": ("report-ready-monthly", "report-ready-monthly", "Готов отчёт за месяц", "Посмотрите итоги последнего завершённого месяца.", "OPEN_REPORT_MONTHLY", "Открыть отчёт"),
}


def report_ready_announcements(kinds: set[str], *, today: date) -> tuple[Announcement, ...]:
    candidates = []
    for kind in sorted(kinds):
        template = REPORT_READY_TEMPLATES.get(kind)
        if template:
            candidate_prefix, family, title, description, action_type, action_label = template
            _start, released_on, _period_key = completed_report_period(kind, today)
            candidate_id = f"{candidate_prefix}-{released_on.isoformat()}"
            candidates.append(Announcement(candidate_id, family, "report", released_on, title, description, action_type, action_label))
    return tuple(candidates)


def resolve_announcement_candidates(
    candidates: tuple[Announcement, ...] | list[Announcement],
    dismissed: set[str],
    *,
    today: date,
) -> list[dict]:
    newest_by_family: dict[str, Announcement] = {}
    for item in candidates:
        if item.action_type not in ACTION_TYPES:
            continue
        age = (today - item.released_on).days
        if age < 0 or age >= ANNOUNCEMENT_TTL_DAYS or item.id in dismissed:
            continue
        current = newest_by_family.get(item.family)
        if current is None or item.released_on > current.released_on:
            newest_by_family[item.family] = item
    items = sorted(newest_by_family.values(), key=lambda item: (item.released_on, item.id), reverse=True)
    return [item.as_dict() for item in items[:MAX_ANNOUNCEMENTS]]


def resolve_announcements(user_id: int, *, today: date | None = None, extra_candidates: tuple[Announcement, ...] = ()) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    rows = pg_fetchall("SELECT candidate_id FROM public.user_announcement_state WHERE user_id=%s", (int(user_id),))
    dismissed = {str(row[0]) for row in rows}
    return resolve_announcement_candidates((*ANNOUNCEMENTS, *extra_candidates), dismissed, today=today)


def announcement_candidate(candidate_id: str, today: date | None = None) -> Announcement | None:
    known = next((item for item in ANNOUNCEMENTS if item.id == candidate_id), None)
    if known is not None:
        return known
    for report_kind, (candidate_prefix, family, title, description, action_type, action_label) in REPORT_READY_TEMPLATES.items():
        prefix = f"{candidate_prefix}-"
        if not candidate_id.startswith(prefix):
            continue
        try:
            released_on = date.fromisoformat(candidate_id.removeprefix(prefix))
        except ValueError:
            return None
        age = ((today or datetime.now(timezone.utc).date()) - released_on).days
        valid_period_end = (
            released_on.weekday() == 6
            if report_kind == "completed_week"
            else (released_on + timedelta(days=1)).day == 1
        )
        if not valid_period_end or age < 0 or age >= ANNOUNCEMENT_TTL_DAYS:
            return None
        return Announcement(candidate_id, family, "report", released_on, title, description, action_type, action_label)
    return None


def dismiss_announcement(user_id: int, candidate_id: str, today: date | None = None) -> bool:
    known = announcement_candidate(candidate_id, today)
    if known is None:
        return False
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_announcement_state (user_id, candidate_id, dismissed_at)
                VALUES (%s, %s, now())
                ON CONFLICT (user_id, candidate_id) DO UPDATE SET dismissed_at=EXCLUDED.dismissed_at
                """,
                (int(user_id), candidate_id),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
