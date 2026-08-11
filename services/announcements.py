from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from db.database import get_conn, pg_fetchall


ANNOUNCEMENT_TTL_DAYS = 21
MAX_ANNOUNCEMENTS = 5
ACTION_TYPES = {
    "OPEN_HOME_SETTINGS",
    "OPEN_SHOPPING_LIST",
    "OPEN_PLANS",
    "OPEN_PROFILE",
    "OPEN_ANALYTICS",
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

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "family": self.family,
            "kind": self.kind,
            "released_on": self.released_on,
            "title": self.title,
            "description": self.description,
            "action": {"type": self.action_type, "label": self.action_label},
        }


ANNOUNCEMENTS = (
    Announcement("custom-home-v1", "home", "feature", date(2026, 8, 11), "Настройте главную под себя", "Выберите нужные виджеты и расположите их в удобном порядке.", "OPEN_HOME_SETTINGS", "Настроить"),
    Announcement("shopping-list-v1", "shopping", "feature", date(2026, 8, 11), "Список покупок теперь под рукой", "Записывайте, что нужно купить, и отмечайте покупки прямо в КопиPaste.", "OPEN_SHOPPING_LIST", "Открыть список"),
    Announcement("plans-v2", "plans", "improvement", date(2026, 8, 10), "Планы стали удобнее", "Категории стали компактнее, а у целей появился полноценный архив.", "OPEN_PLANS", "Открыть планы"),
)


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


def resolve_announcements(user_id: int, *, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    rows = pg_fetchall("SELECT candidate_id FROM public.user_announcement_state WHERE user_id=%s", (int(user_id),))
    dismissed = {str(row[0]) for row in rows}
    # Future report-ready candidates can be concatenated here before applying the same policy.
    return resolve_announcement_candidates(ANNOUNCEMENTS, dismissed, today=today)


def dismiss_announcement(user_id: int, candidate_id: str) -> bool:
    known = next((item for item in ANNOUNCEMENTS if item.id == candidate_id), None)
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
