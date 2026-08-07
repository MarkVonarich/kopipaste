from __future__ import annotations

import json
import os
from typing import Callable
from urllib.parse import parse_qs
from uuid import uuid4

from settings import TELEGRAM_TOKEN

from .api import MiniAppAPI, MiniAppError, error_envelope, serialize
from .auth import MiniAppAuthError, verify_telegram_init_data

api = MiniAppAPI()

SAFE_HEADERS = [
    ("Content-Type", "application/json; charset=utf-8"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'"),
]


def _json_response(start_response: Callable, status: int, payload: dict):
    body = json.dumps(serialize(payload), ensure_ascii=False).encode("utf-8")
    start_response(f"{status} {'OK' if status < 400 else 'ERROR'}", [*SAFE_HEADERS, ("Content-Length", str(len(body)))])
    return [body]


def _read_body(environ) -> dict:
    length = int(environ.get("CONTENT_LENGTH") or 0)
    if length <= 0:
        return {}
    raw = environ["wsgi.input"].read(min(length, 64 * 1024))
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _params(environ) -> dict:
    out = {}
    for key, values in parse_qs(environ.get("QUERY_STRING") or "", keep_blank_values=True).items():
        out[key] = values[-1] if values else ""
    return out


def _init_data(environ) -> str:
    auth = environ.get("HTTP_AUTHORIZATION") or ""
    if auth.lower().startswith("tma "):
        return auth[4:]
    return environ.get("HTTP_X_TELEGRAM_INIT_DATA") or ""


def _request(environ, request_id: str):
    max_age = int(os.getenv("MINIAPP_INITDATA_MAX_AGE_SECONDS", "86400") or "86400")
    user = verify_telegram_init_data(_init_data(environ), bot_token=TELEGRAM_TOKEN, max_age_seconds=max_age)
    return api.request(
        user.user_id,
        request_id=request_id,
        locale=user.language_code,
        telegram_first_name=user.first_name,
        telegram_last_name=user.last_name,
        telegram_username=user.username,
    )


def application(environ, start_response):
    request_id = environ.get("HTTP_X_REQUEST_ID") or str(uuid4())
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    try:
        if method == "OPTIONS":
            return _json_response(start_response, 204, {})
        if path == "/miniapp/health":
            return _json_response(start_response, 200, {"ok": True, "request_id": request_id, "data": {"status": "ok"}})
        req = _request(environ, request_id)
        params = _params(environ)
        body = _read_body(environ) if method in {"POST", "PATCH", "DELETE"} else {}
        if method == "GET" and path == "/miniapp/api/bootstrap":
            return _json_response(start_response, 200, api.bootstrap(req, params))
        if method == "GET" and path == "/miniapp/api/workspaces":
            return _json_response(start_response, 200, api.workspaces(req))
        if path.startswith("/miniapp/api/workspaces/"):
            workspace_id = int(path.rsplit("/", 1)[-1])
            if method == "PATCH":
                return _json_response(start_response, 200, api.update_workspace(req, workspace_id, body))
        if method == "GET" and path == "/miniapp/api/categories":
            return _json_response(start_response, 200, api.categories(req, params))
        if method == "GET" and path == "/miniapp/api/overview":
            return _json_response(start_response, 200, api.overview(req, params))
        if method == "GET" and path == "/miniapp/api/operations":
            return _json_response(start_response, 200, api.operations(req, params))
        if method == "POST" and path == "/miniapp/api/operations":
            return _json_response(start_response, 200, api.create_operation(req, body))
        if path.startswith("/miniapp/api/operations/"):
            op_id = int(path.rsplit("/", 1)[-1])
            if method == "GET":
                return _json_response(start_response, 200, api.operation_detail(req, op_id))
            if method == "PATCH":
                return _json_response(start_response, 200, api.update_operation(req, op_id, body))
            if method == "DELETE":
                return _json_response(start_response, 200, api.delete_operation(req, op_id, body))
        if method == "GET" and path == "/miniapp/api/analytics":
            return _json_response(start_response, 200, api.analytics(req, params))
        if method == "GET" and path == "/miniapp/api/analytics/category-structure":
            return _json_response(start_response, 200, api.analytics(req, params))
        if method == "GET" and path == "/miniapp/api/analytics/time-dynamics":
            return _json_response(start_response, 200, api.analytics(req, params))
        if method == "GET" and path == "/miniapp/api/analytics/radar":
            return _json_response(start_response, 200, api.analytics(req, params))
        if method == "GET" and path == "/miniapp/api/plans":
            return _json_response(start_response, 200, api.plans(req, params))
        if method == "GET" and path == "/miniapp/api/reminders":
            return _json_response(start_response, 200, api.reminders(req, params))
        if method == "POST" and path == "/miniapp/api/reminders":
            return _json_response(start_response, 200, api.create_reminder(req, body))
        if path.startswith("/miniapp/api/reminders/"):
            parts = path.strip("/").split("/")
            reminder_id = int(parts[3])
            tail = parts[4] if len(parts) > 4 else ""
            if method == "GET" and not tail:
                return _json_response(start_response, 200, api.reminder_detail(req, reminder_id))
            if method == "PATCH" and not tail:
                return _json_response(start_response, 200, api.update_reminder(req, reminder_id, body))
            if method == "DELETE" and not tail:
                return _json_response(start_response, 200, api.delete_reminder(req, reminder_id))
            if method == "POST" and tail == "record":
                return _json_response(start_response, 200, api.record_reminder(req, reminder_id, body))
            if method == "POST" and tail == "snooze":
                return _json_response(start_response, 200, api.snooze_reminder(req, reminder_id, body))
            if method == "POST" and tail == "toggle":
                return _json_response(start_response, 200, api.toggle_reminder(req, reminder_id, body))
        if method == "POST" and path == "/miniapp/api/category-budgets":
            return _json_response(start_response, 200, api.create_category_budget(req, body))
        if path.startswith("/miniapp/api/category-budgets/"):
            budget_id = int(path.rsplit("/", 1)[-1])
            if method == "PATCH":
                return _json_response(start_response, 200, api.update_category_budget(req, budget_id, body))
            if method == "DELETE":
                return _json_response(start_response, 200, api.delete_category_budget(req, budget_id, body))
        if method == "GET" and path == "/miniapp/api/goals":
            return _json_response(start_response, 200, api.goals(req, params))
        if method == "POST" and path == "/miniapp/api/goals":
            return _json_response(start_response, 200, api.create_goal(req, body))
        if method == "POST" and path == "/miniapp/api/goals/plan-preview":
            return _json_response(start_response, 200, api.goal_plan_preview(req, body))
        if path.startswith("/miniapp/api/goals/"):
            parts = path.strip("/").split("/")
            goal_id = int(parts[3])
            tail = parts[4] if len(parts) > 4 else ""
            if method == "GET" and not tail:
                return _json_response(start_response, 200, api.goal_detail(req, goal_id, params))
            if method == "PATCH" and not tail:
                return _json_response(start_response, 200, api.update_goal(req, goal_id, body))
            if method == "POST" and tail == "contributions":
                return _json_response(start_response, 200, api.goal_contribution(req, goal_id, body))
            if method == "POST" and tail == "plan-preview":
                return _json_response(start_response, 200, api.goal_plan_preview(req, body, goal_id))
            if method == "POST" and tail == "reminders":
                return _json_response(start_response, 200, api.goal_reminders(req, goal_id, body))
            if method == "POST" and tail == "status":
                return _json_response(start_response, 200, api.goal_status(req, goal_id, body))
        if method == "GET" and path == "/miniapp/api/limits":
            return _json_response(start_response, 200, api.limits(req, params))
        if method == "POST" and path == "/miniapp/api/limits":
            return _json_response(start_response, 200, api.create_limit(req, body))
        if path.startswith("/miniapp/api/limits/"):
            limit_id = path.rsplit("/", 1)[-1]
            if method == "PATCH":
                return _json_response(start_response, 200, api.update_limit(req, limit_id, body))
            if method == "DELETE":
                return _json_response(start_response, 200, api.delete_limit(req, limit_id, body))
        if method == "GET" and path == "/miniapp/api/profile":
            return _json_response(start_response, 200, api.profile(req))
        if method == "GET" and path == "/miniapp/api/profile/categories":
            return _json_response(start_response, 200, api.profile_categories(req, params))
        if method == "GET" and path == "/miniapp/api/profile/notifications":
            return _json_response(start_response, 200, api.notification_preferences(req))
        if method == "POST" and path == "/miniapp/api/profile/notifications":
            return _json_response(start_response, 200, api.update_notification_preferences(req, body))
        if method == "POST" and path == "/miniapp/api/profile/preferred-name":
            return _json_response(start_response, 200, api.set_profile_preferred_name(req, body))
        if method == "POST" and path == "/miniapp/api/profile/currency":
            return _json_response(start_response, 200, api.set_profile_currency(req, body))
        if method == "POST" and path == "/miniapp/api/profile/timezone":
            return _json_response(start_response, 200, api.set_profile_timezone(req, body))
        if method == "POST" and path == "/miniapp/api/profile/active-workspace":
            return _json_response(start_response, 200, api.set_profile_active_workspace(req, body))
        if method == "GET" and path == "/miniapp/api/profile/premium":
            return _json_response(start_response, 200, api.premium(req))
        if method == "GET" and path == "/miniapp/api/profile/export":
            return _json_response(start_response, 200, api.export_entry(req))
        if method == "POST" and path == "/miniapp/api/profile/export":
            return _json_response(start_response, 200, api.export_entry(req, body))
        if method == "POST" and path == "/miniapp/api/profile/theme":
            return _json_response(start_response, 200, api.set_theme(req, body))
        if method == "POST" and path == "/miniapp/api/analytics/event":
            return _json_response(start_response, 200, api.track_ui_event(req, body))
        raise MiniAppError(404, "not_found", "Endpoint was not found.")
    except MiniAppAuthError:
        exc = MiniAppError(401, "unauthorized", "Telegram authorization failed.")
        return _json_response(start_response, exc.status, error_envelope(exc, request_id=request_id))
    except MiniAppError as exc:
        return _json_response(start_response, exc.status, error_envelope(exc, request_id=request_id))
    except Exception:
        exc = MiniAppError(500, "internal_error", "Request failed.")
        return _json_response(start_response, exc.status, error_envelope(exc, request_id=request_id))
