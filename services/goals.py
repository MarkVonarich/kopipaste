from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from psycopg2 import errors
from psycopg2.extras import Json

from db.database import get_conn, pg_fetchall
from db.queries import get_user_currency
from services.automatic_notifications import DeliveryPolicy, queue_automatic_notification
from services.analytics_privacy import safe_error_code
from services.goal_planning import (
    FREQUENCY_MONTHLY,
    FREQUENCY_NONE,
    FREQUENCY_SALARY_MONTHLY,
    FREQUENCY_SALARY_TWICE_MONTHLY,
    FREQUENCY_TWICE_MONTHLY,
    FREQUENCY_WEEKLY,
    STRATEGY_CONTRIBUTION,
    STRATEGY_DEADLINE,
    STRATEGY_NONE,
    PlanCalculation,
    ScheduleConfig,
    calculate_contribution_first,
    calculate_deadline_first,
    ceil_money,
    next_occurrence,
    progress_percent,
    remaining_amount,
    status_for_goal,
)
from services.product_events import ProductEvent, track_product_event
from services.user_time import local_date_time_to_utc, user_local_date

log = logging.getLogger(__name__)

GOAL_STATUSES = {"active", "achieved", "paused", "archived", "deleted"}
MOVEMENT_TYPES = {"initial", "contribution", "withdrawal", "adjustment"}
MAX_GOAL_NAME = 80


@dataclass(frozen=True)
class GoalError(Exception):
    code: str

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class Goal:
    id: int
    owner_user_id: int
    workspace_id: int | None
    display_name: str
    normalized_name: str
    currency: str
    target_amount: Decimal
    current_balance: Decimal
    deadline: date | None
    strategy: str
    frequency: str
    comfortable_amount: Decimal | None
    planned_contribution_amount: Decimal | None
    schedule_config: dict[str, Any]
    status: str
    reminders_enabled: bool
    salary_categories: list[str]
    projected_completion_date: date | None
    next_contribution_date: date | None
    achieved_at: datetime | None = None
    archived_at: datetime | None = None
    movement_count: int = 0


@dataclass(frozen=True)
class GoalMovement:
    id: int
    goal_id: int
    movement_type: str
    amount: Decimal
    balance_after: Decimal
    occurred_at: datetime
    source: str
    linked_operation_id: int | None = None


def normalize_goal_name(value: str) -> str:
    name = re.sub(r"\s+", " ", (value or "").strip())
    if not name:
        raise GoalError("empty_name")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", name):
        raise GoalError("control_characters")
    if len(name) > MAX_GOAL_NAME:
        raise GoalError("name_too_long")
    return name


def normalized_goal_key(value: str) -> str:
    return normalize_goal_name(value).casefold()


def parse_money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise GoalError("invalid_amount")
    if amount <= 0:
        raise GoalError("invalid_amount")
    return amount.quantize(Decimal("0.01"))


def parse_nonnegative_money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value).replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise GoalError("invalid_amount")
    if amount < 0:
        raise GoalError("invalid_amount")
    return amount.quantize(Decimal("0.01"))


def _today_for_user(user_id: int, now_utc: datetime | None = None) -> date:
    return user_local_date(user_id, now_utc=now_utc)


def _schedule_from_row(row: Goal) -> ScheduleConfig:
    cfg = row.schedule_config or {}
    days = tuple(int(day) for day in (cfg.get("days") or []) if str(day).isdigit())
    return ScheduleConfig(
        frequency=row.frequency or FREQUENCY_NONE,
        day=int(cfg.get("day") or 1) if cfg.get("day") is not None else None,
        days=days,
        weekday=int(cfg.get("weekday")) if cfg.get("weekday") is not None else None,
        salary_payments_per_month=cfg.get("salary_payments_per_month"),
    )


def _row_to_goal(r) -> Goal:
    return Goal(
        id=int(r[0]),
        owner_user_id=int(r[1]),
        workspace_id=int(r[2]) if r[2] is not None else None,
        display_name=r[3],
        normalized_name=r[4],
        currency=r[5],
        target_amount=Decimal(str(r[6])),
        current_balance=Decimal(str(r[7])),
        deadline=r[8],
        strategy=r[9] or STRATEGY_NONE,
        frequency=r[10] or FREQUENCY_NONE,
        comfortable_amount=Decimal(str(r[11])) if r[11] is not None else None,
        planned_contribution_amount=Decimal(str(r[12])) if r[12] is not None else None,
        schedule_config=r[13] or {},
        status=r[14],
        reminders_enabled=bool(r[15]),
        salary_categories=list(r[16] or []),
        projected_completion_date=r[17],
        next_contribution_date=r[18],
        achieved_at=r[19],
        archived_at=r[20],
        movement_count=int(r[21] or 0),
    )


def _goal_select() -> str:
    return """
        SELECT g.id, g.owner_user_id, g.workspace_id, g.display_name, g.normalized_name,
               g.currency, g.target_amount, g.current_balance, g.deadline,
               g.strategy, g.frequency, g.comfortable_amount, g.planned_contribution_amount,
               g.schedule_config, g.status, g.reminders_enabled, g.salary_categories,
               g.projected_completion_date, g.next_contribution_date,
               g.achieved_at, g.archived_at,
               (SELECT COUNT(*) FROM public.goal_movements m WHERE m.goal_id=g.id) AS movement_count
          FROM public.financial_goals g
    """


def _safe_track(event_name: str, *, user_id: int, workspace_id: int | None, status: str = "success", currency: str | None = None, properties: dict[str, Any] | None = None) -> None:
    try:
        track_product_event(ProductEvent(
            event_name=event_name,
            user_id=user_id,
            workspace_id=workspace_id,
            status=status,
            currency=currency,
            entity_type="goal",
            properties=properties or {},
        ))
    except Exception as exc:
        log.info("goal_product_event_failed event=%s reason=%s", event_name, safe_error_code(exc))


def _progress_bucket(goal: Goal) -> str:
    pct = progress_percent(goal.target_amount, goal.current_balance)
    if pct == 0:
        return "0"
    if pct < 25:
        return "1-24"
    if pct < 50:
        return "25-49"
    if pct < 75:
        return "50-74"
    if pct < 100:
        return "75-99"
    return "100+"


def _calculate_plan(goal: Goal, today: date | None = None) -> PlanCalculation:
    today = today or _today_for_user(goal.owner_user_id)
    schedule = _schedule_from_row(goal)
    if goal.strategy == STRATEGY_DEADLINE:
        return calculate_deadline_first(
            target_amount=goal.target_amount,
            current_balance=goal.current_balance,
            deadline=goal.deadline,
            schedule=schedule,
            today=today,
        )
    if goal.strategy == STRATEGY_CONTRIBUTION and goal.comfortable_amount is not None:
        return calculate_contribution_first(
            target_amount=goal.target_amount,
            current_balance=goal.current_balance,
            comfortable_amount=goal.comfortable_amount,
            schedule=schedule,
            today=today,
        )
    return PlanCalculation(STRATEGY_NONE, goal.frequency or FREQUENCY_NONE, remaining_amount(goal.target_amount, goal.current_balance), 0, next_occurrence=next_occurrence(today, schedule))


def _status_after(goal: Goal, today: date | None = None) -> str:
    if goal.status in {"paused", "archived", "deleted"}:
        return goal.status
    plan = _calculate_plan(goal, today)
    calculated = status_for_goal(
        status=goal.status,
        target_amount=goal.target_amount,
        current_balance=goal.current_balance,
        deadline=goal.deadline,
        plan=plan,
        today=today or _today_for_user(goal.owner_user_id),
    )
    return "achieved" if calculated == "achieved" else "active"


def _update_plan_columns(cur, goal: Goal, *, today: date | None = None) -> Goal:
    today = today or _today_for_user(goal.owner_user_id)
    plan = _calculate_plan(goal, today)
    status = _status_after(goal, today)
    planned = plan.recommended_amount if plan.strategy == STRATEGY_DEADLINE and plan.feasible else goal.planned_contribution_amount
    projected = plan.projected_completion_date
    next_date = plan.next_occurrence
    cur.execute(
        """
        UPDATE public.financial_goals
           SET planned_contribution_amount=%s,
               projected_completion_date=%s,
               next_contribution_date=%s,
               status=%s,
               achieved_at=CASE WHEN %s='achieved' THEN COALESCE(achieved_at, now()) ELSE CASE WHEN %s='active' THEN NULL ELSE achieved_at END END,
               updated_at=now(),
               version=version + 1
         WHERE id=%s
        """,
        (planned, projected, next_date, status, status, status, goal.id),
    )
    return get_goal_cur(cur, goal.id, goal.owner_user_id, goal.workspace_id)


def get_goal_cur(cur, goal_id: int, owner_user_id: int, workspace_id: int | None, *, lock: bool = False) -> Goal:
    cur.execute(
        _goal_select()
        + """
         WHERE g.id=%s
           AND g.owner_user_id=%s
           AND g.workspace_id IS NOT DISTINCT FROM %s
        """
        + (" FOR UPDATE OF g" if lock else ""),
        (int(goal_id), int(owner_user_id), workspace_id),
    )
    row = cur.fetchone()
    if not row:
        raise GoalError("goal_not_found")
    return _row_to_goal(row)


def get_goal(goal_id: int, owner_user_id: int, workspace_id: int | None) -> Goal | None:
    try:
        rows = pg_fetchall(
            _goal_select()
            + """
             WHERE g.id=%s
               AND g.owner_user_id=%s
               AND g.workspace_id IS NOT DISTINCT FROM %s
            """,
            (int(goal_id), int(owner_user_id), workspace_id),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        return None
    return _row_to_goal(rows[0]) if rows else None


def list_goals(owner_user_id: int, workspace_id: int | None, *, status_group: str = "active", limit: int = 20, offset: int = 0) -> list[Goal]:
    if status_group == "completed":
        statuses = ("achieved",)
    elif status_group == "archive":
        statuses = ("archived",)
    else:
        statuses = ("active", "paused")
    try:
        rows = pg_fetchall(
            _goal_select()
            + """
             WHERE g.owner_user_id=%s
               AND g.workspace_id IS NOT DISTINCT FROM %s
               AND g.status=ANY(%s)
             ORDER BY g.updated_at DESC, g.id DESC
             LIMIT %s OFFSET %s
            """,
            (int(owner_user_id), workspace_id, list(statuses), int(limit), int(offset)),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        return []
    return [_row_to_goal(row) for row in rows]


def create_goal(
    *,
    owner_user_id: int,
    workspace_id: int | None,
    display_name: str,
    target_amount: Decimal | int | str,
    currency: str | None = None,
    deadline: date | None = None,
    initial_amount: Decimal | int | str = Decimal("0"),
) -> Goal:
    name = normalize_goal_name(display_name)
    key = normalized_goal_key(name)
    target = parse_money(target_amount)
    initial = parse_nonnegative_money(initial_amount)
    goal_currency = (currency or get_user_currency(owner_user_id) or "RUB")[:8]
    today = _today_for_user(owner_user_id)
    if deadline and deadline < today:
        raise GoalError("past_deadline")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            status = "achieved" if initial >= target else "active"
            cur.execute(
                """
                INSERT INTO public.financial_goals
                    (owner_user_id, workspace_id, display_name, normalized_name, currency,
                     target_amount, current_balance, deadline, status, achieved_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s='achieved' THEN now() ELSE NULL END)
                RETURNING id
                """,
                (owner_user_id, workspace_id, name, key, goal_currency, target, initial, deadline, status, status),
            )
            goal_id = int(cur.fetchone()[0])
            if initial > 0:
                cur.execute(
                    """
                    INSERT INTO public.goal_movements
                        (goal_id, actor_user_id, movement_type, amount, balance_after, source, idempotency_key)
                    VALUES (%s,%s,'initial',%s,%s,'manual',%s)
                    """,
                    (goal_id, owner_user_id, initial, initial, f"goal:{goal_id}:initial"),
                )
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id)
            goal = _update_plan_columns(cur, goal, today=today)
        conn.commit()
        _safe_track("goal_created", user_id=owner_user_id, workspace_id=workspace_id, currency=goal_currency, properties={"status": goal.status, "has_deadline": bool(deadline), "progress_bucket": _progress_bucket(goal)})
        return goal
    except errors.UniqueViolation:
        conn.rollback()
        raise GoalError("duplicate_name")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_goal_plan(
    *,
    goal_id: int,
    owner_user_id: int,
    workspace_id: int | None,
    strategy: str,
    frequency: str = FREQUENCY_NONE,
    deadline: date | None = None,
    comfortable_amount: Decimal | int | str | None = None,
    schedule_config: dict[str, Any] | None = None,
    reminders_enabled: bool | None = None,
    salary_categories: list[str] | None = None,
) -> Goal:
    if strategy not in {STRATEGY_NONE, STRATEGY_DEADLINE, STRATEGY_CONTRIBUTION}:
        raise GoalError("invalid_strategy")
    if frequency not in {FREQUENCY_NONE, FREQUENCY_MONTHLY, FREQUENCY_TWICE_MONTHLY, FREQUENCY_WEEKLY, FREQUENCY_SALARY_MONTHLY, FREQUENCY_SALARY_TWICE_MONTHLY}:
        raise GoalError("invalid_frequency")
    comfortable = parse_money(comfortable_amount) if comfortable_amount is not None else None
    today = _today_for_user(owner_user_id)
    if deadline and deadline < today:
        raise GoalError("past_deadline")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            if goal.status in {"archived", "deleted"}:
                raise GoalError("goal_not_active")
            cur.execute(
                """
                UPDATE public.financial_goals
                   SET strategy=%s,
                       frequency=%s,
                       deadline=%s,
                       comfortable_amount=%s,
                       schedule_config=%s,
                       reminders_enabled=COALESCE(%s, reminders_enabled),
                       salary_categories=COALESCE(%s, salary_categories),
                       updated_at=now(),
                       version=version + 1
                 WHERE id=%s
                """,
                (strategy, frequency, deadline, comfortable, Json(schedule_config or {}), reminders_enabled, salary_categories, goal.id),
            )
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            goal = _update_plan_columns(cur, goal, today=today)
        conn.commit()
        _safe_track("goal_plan_created" if goal.movement_count <= 1 else "goal_plan_updated", user_id=owner_user_id, workspace_id=workspace_id, currency=goal.currency, properties={"strategy": goal.strategy, "frequency": goal.frequency, "has_deadline": bool(goal.deadline), "reminder_enabled": bool(goal.reminders_enabled), "progress_bucket": _progress_bucket(goal)})
        return goal
    except errors.UniqueViolation:
        conn.rollback()
        raise GoalError("duplicate_name")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_goal_details(
    *,
    goal_id: int,
    owner_user_id: int,
    workspace_id: int | None,
    display_name: str | None = None,
    target_amount: Decimal | int | str | None = None,
    deadline: date | None | object = ...,
) -> Goal:
    today = _today_for_user(owner_user_id)
    name = normalize_goal_name(display_name) if display_name is not None else None
    key = normalized_goal_key(name) if name is not None else None
    target = parse_money(target_amount) if target_amount is not None else None
    if deadline is not ... and deadline is not None and deadline < today:
        raise GoalError("past_deadline")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            if goal.status in {"archived", "deleted"}:
                raise GoalError("goal_not_active")
            new_target = target if target is not None else goal.target_amount
            new_status = "achieved" if goal.current_balance >= new_target else ("active" if goal.status == "achieved" else goal.status)
            cur.execute(
                """
                UPDATE public.financial_goals
                   SET display_name=COALESCE(%s, display_name),
                       normalized_name=COALESCE(%s, normalized_name),
                       target_amount=COALESCE(%s, target_amount),
                       deadline=CASE WHEN %s THEN %s ELSE deadline END,
                       status=%s,
                       achieved_at=CASE WHEN %s='achieved' THEN COALESCE(achieved_at, now()) ELSE NULL END,
                       updated_at=now(),
                       version=version + 1
                 WHERE id=%s
                """,
                (name, key, target, deadline is not ..., None if deadline is ... else deadline, new_status, new_status, goal.id),
            )
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            goal = _update_plan_columns(cur, goal, today=today)
        conn.commit()
        _safe_track("goal_plan_updated", user_id=owner_user_id, workspace_id=workspace_id, currency=goal.currency, properties={"strategy": goal.strategy, "frequency": goal.frequency, "status": goal.status, "has_deadline": bool(goal.deadline), "progress_bucket": _progress_bucket(goal)})
        return goal
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _movement_delta(movement_type: str, amount: Decimal, current_balance: Decimal, new_balance: Decimal | None = None) -> Decimal:
    if movement_type in {"initial", "contribution"}:
        return amount
    if movement_type == "withdrawal":
        if amount > current_balance:
            raise GoalError("insufficient_balance")
        return -amount
    if movement_type == "adjustment":
        if new_balance is None:
            raise GoalError("invalid_amount")
        return new_balance - current_balance
    raise GoalError("invalid_movement")


def add_goal_movement(
    *,
    goal_id: int,
    owner_user_id: int,
    workspace_id: int | None,
    actor_user_id: int,
    movement_type: str,
    amount: Decimal | int | str | None = None,
    new_balance: Decimal | int | str | None = None,
    source: str = "manual",
    linked_operation_id: int | None = None,
    idempotency_key: str | None = None,
) -> tuple[Goal, GoalMovement | None, bool]:
    if int(actor_user_id) != int(owner_user_id):
        raise GoalError("wrong_actor")
    if movement_type not in MOVEMENT_TYPES:
        raise GoalError("invalid_movement")
    parsed_amount = parse_money(amount or 0) if movement_type != "adjustment" else Decimal("0.00")
    target_balance = parse_nonnegative_money(new_balance) if movement_type == "adjustment" else None
    idem = (idempotency_key or f"goal:{goal_id}:{movement_type}:{source}:{datetime.now(timezone.utc).isoformat()}")[:180]
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            if goal.status in {"archived", "deleted"}:
                raise GoalError("goal_not_active")
            delta = _movement_delta(movement_type, parsed_amount, goal.current_balance, target_balance)
            save_amount = abs(delta) if movement_type == "adjustment" else parsed_amount
            if save_amount <= 0:
                raise GoalError("invalid_amount")
            balance_after = goal.current_balance + delta
            if balance_after < 0:
                raise GoalError("insufficient_balance")
            cur.execute(
                """
                INSERT INTO public.goal_movements
                    (goal_id, actor_user_id, movement_type, amount, balance_after, source, linked_operation_id, idempotency_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO NOTHING
                RETURNING id, occurred_at
                """,
                (goal.id, actor_user_id, movement_type, save_amount, balance_after, source, linked_operation_id, idem),
            )
            row = cur.fetchone()
            if not row:
                existing = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
                conn.commit()
                return existing, None, False
            movement_id, occurred_at = int(row[0]), row[1]
            cur.execute(
                """
                UPDATE public.financial_goals
                   SET current_balance=%s,
                       status=CASE
                           WHEN %s >= target_amount THEN 'achieved'
                           WHEN status='achieved' THEN 'active'
                           ELSE status
                       END,
                       achieved_at=CASE WHEN %s >= target_amount THEN COALESCE(achieved_at, now()) ELSE NULL END,
                       updated_at=now(),
                       version=version + 1
                 WHERE id=%s
                """,
                (balance_after, balance_after, balance_after, goal.id),
            )
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            goal = _update_plan_columns(cur, goal)
        conn.commit()
        movement = GoalMovement(movement_id, goal.id, movement_type, save_amount, balance_after, occurred_at, source, linked_operation_id)
        event_name = {
            "contribution": "goal_contribution_added",
            "withdrawal": "goal_withdrawal_added",
            "adjustment": "goal_progress_adjusted",
            "initial": "goal_created",
        }.get(movement_type)
        if event_name and movement_type != "initial":
            _safe_track(event_name, user_id=owner_user_id, workspace_id=workspace_id, currency=goal.currency, properties={"strategy": goal.strategy, "frequency": goal.frequency, "status": goal.status, "source": source, "progress_bucket": _progress_bucket(goal)})
        if goal.status == "achieved":
            _safe_track("goal_achieved", user_id=owner_user_id, workspace_id=workspace_id, currency=goal.currency, properties={"strategy": goal.strategy, "frequency": goal.frequency, "progress_bucket": "100+"})
            queue_goal_achieved_notification(goal)
        return goal, movement, True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_movements(goal_id: int, owner_user_id: int, workspace_id: int | None, *, limit: int = 10, offset: int = 0) -> list[GoalMovement]:
    goal = get_goal(goal_id, owner_user_id, workspace_id)
    if not goal:
        return []
    rows = pg_fetchall(
        """
        SELECT id, goal_id, movement_type, amount, balance_after, occurred_at, source, linked_operation_id
          FROM public.goal_movements
         WHERE goal_id=%s
         ORDER BY occurred_at DESC, id DESC
         LIMIT %s OFFSET %s
        """,
        (goal.id, int(limit), int(offset)),
    )
    return [GoalMovement(int(r[0]), int(r[1]), r[2], Decimal(str(r[3])), Decimal(str(r[4])), r[5], r[6], r[7]) for r in rows]


def set_goal_status(goal_id: int, owner_user_id: int, workspace_id: int | None, status: str) -> Goal:
    if status not in GOAL_STATUSES - {"deleted"}:
        raise GoalError("invalid_status")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            cur.execute(
                """
                UPDATE public.financial_goals
                   SET status=%s,
                       archived_at=CASE WHEN %s='archived' THEN COALESCE(archived_at, now()) ELSE NULL END,
                       updated_at=now(),
                       version=version + 1
                 WHERE id=%s
                """,
                (status, status, goal.id),
            )
            if status in {"archived", "paused", "achieved"}:
                suppress_pending_goal_notifications_cur(cur, goal.id, reason=f"goal_{status}")
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
        conn.commit()
        event = {"paused": "goal_paused", "active": "goal_resumed", "archived": "goal_archived", "achieved": "goal_achieved"}.get(status)
        if event:
            _safe_track(event, user_id=owner_user_id, workspace_id=workspace_id, currency=goal.currency, properties={"status": status, "progress_bucket": _progress_bucket(goal)})
        return goal
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_goal_permanently(goal_id: int, owner_user_id: int, workspace_id: int | None) -> int:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            cur.execute("SELECT COUNT(*) FROM public.goal_movements WHERE goal_id=%s", (goal.id,))
            movement_count = int(cur.fetchone()[0] or 0)
            suppress_pending_goal_notifications_cur(cur, goal.id, reason="goal_deleted")
            cur.execute("DELETE FROM public.goal_movements WHERE goal_id=%s", (goal.id,))
            cur.execute("DELETE FROM public.goal_drafts WHERE owner_user_id=%s AND workspace_id IS NOT DISTINCT FROM %s", (owner_user_id, workspace_id))
            cur.execute("DELETE FROM public.financial_goals WHERE id=%s", (goal.id,))
        conn.commit()
        _safe_track("goal_deleted", user_id=owner_user_id, workspace_id=workspace_id, currency=goal.currency, properties={"status": "deleted"})
        return movement_count
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_goal_reminders(goal_id: int, owner_user_id: int, workspace_id: int | None, enabled: bool) -> Goal:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
            cur.execute("UPDATE public.financial_goals SET reminders_enabled=%s, updated_at=now(), version=version + 1 WHERE id=%s", (bool(enabled), goal.id))
            if not enabled:
                suppress_pending_goal_notifications_cur(cur, goal.id, reason="goal_reminders_disabled")
            goal = get_goal_cur(cur, goal_id, owner_user_id, workspace_id, lock=True)
        conn.commit()
        _safe_track("goal_reminders_enabled" if enabled else "goal_reminders_disabled", user_id=owner_user_id, workspace_id=workspace_id, currency=goal.currency, properties={"reminder_enabled": bool(enabled), "status": goal.status})
        return goal
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def suppress_pending_goal_notifications_cur(cur, goal_id: int, *, reason: str) -> int:
    cur.execute(
        """
        UPDATE public.automatic_notifications
           SET status='skipped',
               skip_reason=%s,
               locked_at=NULL,
               locked_by=NULL,
               updated_at=now()
         WHERE notification_type LIKE 'goal_%'
           AND status IN ('pending','claimed')
           AND payload->>'goal_id'=%s
        """,
        (reason, str(int(goal_id))),
    )
    return int(cur.rowcount or 0)


def goal_notifications_enabled(user_id: int) -> bool:
    try:
        rows = pg_fetchall(
            "SELECT COALESCE(goal_notifications_enabled, false) FROM public.notification_preferences WHERE user_id=%s LIMIT 1",
            (user_id,),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        return False
    return bool(rows and rows[0][0])


def queue_goal_reminder(goal: Goal, occurrence: date) -> str:
    if not goal_notifications_enabled(goal.owner_user_id) or not goal.reminders_enabled or goal.status != "active":
        return "blocked"
    amount = goal.planned_contribution_amount or goal.comfortable_amount or Decimal("0")
    if amount <= 0:
        return "blocked"
    text = (
        f"🎯 Плановое пополнение\n\n"
        f"{goal.display_name}\n"
        f"Рекомендуемый взнос: {format_money(amount, goal.currency)}\n"
        f"Дата: {format_date_ru(occurrence)}"
    )
    result = queue_automatic_notification(
        user_id=goal.owner_user_id,
        workspace_id=goal.workspace_id,
        notification_type="goal_planned_contribution",
        dedupe_key=f"goal:{goal.id}:planned:{occurrence.isoformat()}",
        policy=DeliveryPolicy.DEFER,
        template_key="goal_planned_contribution",
        payload={
            "text": text,
            "goal_id": goal.id,
            "occurrence": occurrence.isoformat(),
            "buttons": [[{"label": "🎯 Открыть цель", "callback_data": f"goal|o|{goal.id}"}]],
        },
        original_scheduled_at=local_date_time_to_utc(goal.owner_user_id, occurrence, datetime.min.time(), goal.workspace_id),
    )
    if result.status in {"queued", "deferred"}:
        _safe_track("goal_reminder_sent", user_id=goal.owner_user_id, workspace_id=goal.workspace_id, currency=goal.currency, properties={"status": result.status, "frequency": goal.frequency, "source": "planned"})
    return result.status


def queue_goal_achieved_notification(goal: Goal) -> None:
    if not goal_notifications_enabled(goal.owner_user_id) or not goal.reminders_enabled:
        return
    text = f"🎉 Цель достигнута!\n\n{goal.display_name}\n{format_money(goal.current_balance, goal.currency)} из {format_money(goal.target_amount, goal.currency)}"
    queue_automatic_notification(
        user_id=goal.owner_user_id,
        workspace_id=goal.workspace_id,
        notification_type="goal_achieved",
        dedupe_key=f"goal:{goal.id}:achieved",
        policy=DeliveryPolicy.DEFER,
        template_key="goal_achieved",
        payload={"text": text, "goal_id": goal.id, "buttons": [[{"label": "🎯 Открыть цель", "callback_data": f"goal|o|{goal.id}"}]]},
    )


def enqueue_due_goal_reminders(*, today: date | None = None, limit: int = 500) -> dict[str, int]:
    utc_today = today or datetime.now(timezone.utc).date()
    try:
        rows = pg_fetchall(
            """
            SELECT g.id, g.owner_user_id, g.workspace_id
              FROM public.financial_goals g
              JOIN public.notification_preferences np ON np.user_id=g.owner_user_id
             WHERE COALESCE(np.goal_notifications_enabled, false)
               AND g.reminders_enabled
               AND g.status='active'
               AND g.next_contribution_date IS NOT NULL
               AND g.next_contribution_date <= %s
             ORDER BY g.next_contribution_date, g.id
             LIMIT %s
            """,
            (utc_today + timedelta(days=1), int(limit)),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        return {"queued": 0, "deferred": 0, "duplicate": 0, "blocked": 0}
    counts = {"queued": 0, "deferred": 0, "duplicate": 0, "blocked": 0}
    for goal_id, user_id, workspace_id in rows:
        goal = get_goal(int(goal_id), int(user_id), int(workspace_id) if workspace_id is not None else None)
        if not goal or not goal.next_contribution_date:
            counts["blocked"] += 1
            continue
        effective_today = today or user_local_date(int(user_id), int(workspace_id) if workspace_id is not None else None)
        if goal.next_contribution_date > effective_today:
            continue
        status = queue_goal_reminder(goal, goal.next_contribution_date)
        counts[status if status in counts else "blocked"] += 1
    return counts


def salary_suggestion_goals(*, owner_user_id: int, workspace_id: int | None, category: str, currency: str, limit: int = 5) -> list[Goal]:
    normalized = normalized_goal_key(category)
    try:
        rows = pg_fetchall(
            _goal_select()
            + """
             WHERE g.owner_user_id=%s
               AND g.workspace_id IS NOT DISTINCT FROM %s
               AND g.status='active'
               AND g.currency=%s
               AND g.frequency IN ('salary_monthly','salary_twice_monthly')
               AND EXISTS (
                    SELECT 1 FROM unnest(g.salary_categories) c
                     WHERE lower(c)=lower(%s) OR lower(c)=lower(%s)
               )
             ORDER BY g.next_contribution_date NULLS LAST, g.id
             LIMIT %s
            """,
            (owner_user_id, workspace_id, currency, category, normalized, int(limit)),
        )
    except (errors.UndefinedTable, errors.UndefinedColumn):
        return []
    return [_row_to_goal(row) for row in rows]


def build_salary_suggestion_text(goals: list[Goal]) -> str:
    if not goals:
        return ""
    if len(goals) == 1:
        goal = goals[0]
        amount = goal.planned_contribution_amount or goal.comfortable_amount or ceil_money(remaining_amount(goal.target_amount, goal.current_balance))
        return f"🎯 По вашему плану сегодня можно направить {format_money(amount, goal.currency)} в цель «{goal.display_name}»."
    return "🎯 Есть цели, привязанные к этой зарплатной категории.\n\nВыберите, куда направить часть дохода:"


def format_money(value: Decimal | int | str, currency: str = "RUB") -> str:
    amount = Decimal(str(value))
    major = int(amount) if amount == amount.to_integral_value() else amount.quantize(Decimal("0.01"))
    symbol = {"RUB": "₽", "USD": "$", "EUR": "€"}.get((currency or "RUB").upper(), currency or "")
    return f"{major:,}".replace(",", " ") + (f" {symbol}" if symbol else "")


def format_date_ru(value: date | None) -> str:
    if not value:
        return "без срока"
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
    return f"{value.day} {months[value.month - 1]} {value.year}"


def goal_status_label(goal: Goal) -> str:
    calc = status_for_goal(
        status=goal.status,
        target_amount=goal.target_amount,
        current_balance=goal.current_balance,
        deadline=goal.deadline,
        plan=_calculate_plan(goal),
        today=_today_for_user(goal.owner_user_id),
    )
    return {
        "no_plan": "План: без расписания",
        "ahead": "🚀 Вы опережаете график.",
        "on_track": "✅ Вы идёте по плану.",
        "behind": "⚠️ Вы отстаёте от плана.",
        "overdue": "Срок прошёл, но цель ещё не достигнута.",
        "achieved": "🎉 Цель достигнута!",
        "paused": "⏸ Цель на паузе.",
        "archived": "🗄 Цель в архиве.",
    }.get(calc, "План: без расписания")


def render_goal_card_text(goal: Goal) -> str:
    pct = progress_percent(goal.target_amount, goal.current_balance)
    filled = max(0, min(12, pct * 12 // 100))
    bar = "█" * filled + "░" * (12 - filled)
    plan = _calculate_plan(goal)
    plan_line = "без расписания"
    if goal.strategy == STRATEGY_DEADLINE and plan.recommended_amount is not None and plan.feasible:
        plan_line = f"{format_money(plan.recommended_amount, goal.currency)} за одно пополнение"
    elif goal.strategy == STRATEGY_CONTRIBUTION and goal.comfortable_amount is not None:
        plan_line = f"{format_money(goal.comfortable_amount, goal.currency)} за одно пополнение"
    next_line = "пополнить вручную"
    if plan.next_occurrence and (plan.recommended_amount or goal.comfortable_amount):
        amount = plan.recommended_amount or goal.comfortable_amount
        next_line = f"{format_date_ru(plan.next_occurrence)} — {format_money(amount, goal.currency)}"
    excess = goal.current_balance - goal.target_amount
    excess_line = f"\nСверх цели: {format_money(excess, goal.currency)}" if excess > 0 else ""
    return (
        f"{goal.display_name if goal.display_name else '🎯 Цель'}\n\n"
        f"{format_money(goal.current_balance, goal.currency)} из {format_money(goal.target_amount, goal.currency)}\n"
        f"{bar} {pct}%{excess_line}\n\n"
        f"План:\n{plan_line}\n\n"
        f"Следующее пополнение:\n{next_line}\n\n"
        f"Статус:\n{goal_status_label(goal)}\n\n"
        f"Следующий шаг:\n{'Пополнить цель.' if goal.status != 'achieved' else 'Выберите, что сделать с достигнутой целью.'}"
    )
