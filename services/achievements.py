from __future__ import annotations

from dataclasses import dataclass

from psycopg2 import errors

from db.database import get_conn, pg_fetchall


@dataclass(frozen=True)
class AchievementDefinition:
    key: str
    group: str
    target: int
    title_ru: str
    title_en: str


DEFAULT_ACHIEVEMENTS: tuple[AchievementDefinition, ...] = (
    AchievementDefinition("first_operation", "onboarding", 1, "Первая операция", "First operation"),
    AchievementDefinition("first_income", "onboarding", 1, "Первый доход", "First income"),
    AchievementDefinition("first_voice_operation", "onboarding", 1, "Первая голосовая операция", "First voice operation"),
    AchievementDefinition("first_ocr_operation", "onboarding", 1, "Первая операция из изображения", "First OCR operation"),
    AchievementDefinition("first_custom_category", "onboarding", 1, "Первая своя категория", "First custom category"),
    AchievementDefinition("first_budget", "onboarding", 1, "Первый бюджет", "First budget"),
    AchievementDefinition("first_category_limit", "onboarding", 1, "Первый лимит категории", "First category limit"),
    AchievementDefinition("first_reminder", "onboarding", 1, "Первое напоминание", "First reminder"),
    AchievementDefinition("first_export", "onboarding", 1, "Первый экспорт", "First export"),
    AchievementDefinition("first_shared_workspace", "onboarding", 1, "Первое общее пространство", "First shared workspace"),
    AchievementDefinition("tracking_streak_3", "consistency", 3, "3 дня учета подряд", "3-day tracking streak"),
    AchievementDefinition("tracking_streak_5", "consistency", 5, "5 дней учета подряд", "5-day tracking streak"),
    AchievementDefinition("tracking_streak_7", "consistency", 7, "7 дней учета подряд", "7-day tracking streak"),
    AchievementDefinition("tracking_streak_14", "consistency", 14, "14 дней учета подряд", "14-day tracking streak"),
    AchievementDefinition("tracking_streak_30", "consistency", 30, "30 дней учета подряд", "30-day tracking streak"),
    AchievementDefinition("tracking_streak_60", "consistency", 60, "60 дней учета подряд", "60-day tracking streak"),
    AchievementDefinition("tracking_streak_100", "consistency", 100, "100 дней учета подряд", "100-day tracking streak"),
    AchievementDefinition("full_week_tracked", "consistency", 7, "Неделя без пропусков", "Full week tracked"),
    AchievementDefinition("regular_month_tracking", "consistency", 20, "Регулярный месяц учета", "Regular month tracking"),
    AchievementDefinition("categorized_10", "data_quality", 10, "10 операций с категориями", "10 categorized operations"),
    AchievementDefinition("categorized_50", "data_quality", 50, "50 операций с категориями", "50 categorized operations"),
    AchievementDefinition("categorized_100", "data_quality", 100, "100 операций с категориями", "100 categorized operations"),
    AchievementDefinition("first_correction", "data_quality", 1, "Первая правка операции", "First corrected operation"),
    AchievementDefinition("clean_categories", "data_quality", 1, "Аккуратные категории", "Clean category usage"),
    AchievementDefinition("budget_reviewed", "data_quality", 1, "Бюджет обновлен после расходов", "Budget reviewed after spending"),
    AchievementDefinition("week_within_budget", "budget", 1, "Неделя в бюджете", "Week within budget"),
    AchievementDefinition("month_within_budget", "budget", 1, "Месяц в бюджете", "Month within budget"),
    AchievementDefinition("three_periods_within_budget", "budget", 3, "Три периода в бюджете", "Three periods within budget"),
    AchievementDefinition("first_limit_respected", "budget", 1, "Первый лимит соблюден", "First limit respected"),
    AchievementDefinition("threshold_recovered", "budget", 1, "Вернулись после предупреждения", "Recovered after threshold"),
    AchievementDefinition("category_reduced", "budget", 1, "Категория снижена к прошлому периоду", "Category reduced vs previous period"),
    AchievementDefinition("positive_month_cash_flow", "awareness", 1, "Плюсовой месяц", "Positive monthly cash flow"),
    AchievementDefinition("reviewed_commitments", "awareness", 1, "Повторные платежи просмотрены", "Reviewed recurring commitments"),
    AchievementDefinition("first_monthly_summary", "awareness", 1, "Первый месячный итог", "First monthly summary"),
    AchievementDefinition("top_category_identified", "awareness", 1, "Главная категория найдена", "Top spending category identified"),
    AchievementDefinition("first_shared_operation", "shared", 1, "Первая общая операция", "First shared operation"),
    AchievementDefinition("shared_operations_10", "shared", 10, "10 общих операций", "10 shared operations"),
    AchievementDefinition("first_shared_budget", "shared", 1, "Первый общий бюджет", "First shared budget"),
    AchievementDefinition("first_shared_month", "shared", 1, "Первый общий месяц", "First shared month completed"),
)


def grant_reward_hook(user_id: int, achievement_key: str) -> dict:
    return {"granted": False, "reason": "reward hook is intentionally a safe no-op until entitlements exist", "user_id": user_id, "achievement_key": achievement_key}


def award_achievement(user_id: int, achievement_key: str, workspace_id: int | None = None, progress: int = 1) -> dict:
    scope_id = int(workspace_id or 0)
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.user_achievements (user_id, workspace_id, achievement_key, progress, earned_at)
                VALUES (%s, %s, %s, %s, CASE WHEN %s >= 1 THEN now() ELSE NULL END)
                ON CONFLICT (user_id, workspace_id, achievement_key) DO UPDATE
                   SET progress=GREATEST(public.user_achievements.progress, EXCLUDED.progress),
                       earned_at=COALESCE(public.user_achievements.earned_at, EXCLUDED.earned_at),
                       updated_at=now()
                RETURNING earned_at IS NOT NULL
                """,
                (user_id, scope_id, achievement_key, progress, progress),
            )
            earned = bool(cur.fetchone()[0])
        conn.commit()
        return {"awarded": earned, "reward": grant_reward_hook(user_id, achievement_key) if earned else None}
    except errors.UndefinedTable:
        conn.rollback()
        return {"awarded": False, "reason": "achievement tables are not migrated yet"}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_user_achievements(user_id: int, locale: str = "ru") -> list[dict]:
    title_col = "title_en" if locale == "en" else "title_ru"
    try:
        rows = pg_fetchall(
            f"""
            SELECT d.key, d.group_key, d.target, d.{title_col}, COALESCE(ua.progress, 0), ua.earned_at
              FROM public.achievement_definitions d
              LEFT JOIN public.user_achievements ua
                ON ua.achievement_key=d.key AND ua.user_id=%s
             WHERE d.is_active=TRUE
             ORDER BY d.group_key, d.key
            """,
            (user_id,),
        )
    except errors.UndefinedTable:
        return []
    return [
        {
            "key": r[0],
            "group": r[1],
            "target": int(r[2]),
            "title": r[3],
            "progress": int(r[4] or 0),
            "earned_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]
