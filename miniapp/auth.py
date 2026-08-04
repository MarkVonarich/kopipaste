from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl


class MiniAppAuthError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class MiniAppUser:
    user_id: int
    auth_date: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    language_code: str | None = None


def _parse_init_data(init_data: str) -> dict[str, str]:
    try:
        pairs = parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise MiniAppAuthError("invalid_init_data") from exc
    data = dict(pairs)
    if "hash" not in data:
        raise MiniAppAuthError("missing_hash")
    return data


def _data_check_string(data: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(data.items()) if key != "hash")


def verify_telegram_init_data(
    init_data: str,
    *,
    bot_token: str,
    max_age_seconds: int = 86400,
    now: int | None = None,
) -> MiniAppUser:
    if not bot_token:
        raise MiniAppAuthError("bot_token_missing")
    data = _parse_init_data(init_data)
    received_hash = data.get("hash") or ""
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, _data_check_string(data).encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash):
        raise MiniAppAuthError("invalid_hash")

    try:
        auth_date = int(data.get("auth_date") or "0")
    except ValueError as exc:
        raise MiniAppAuthError("invalid_auth_date") from exc
    current = int(now if now is not None else time.time())
    if auth_date <= 0 or current - auth_date > int(max_age_seconds):
        raise MiniAppAuthError("expired_init_data")
    if auth_date - current > 300:
        raise MiniAppAuthError("future_auth_date")

    raw_user = data.get("user")
    if not raw_user:
        raise MiniAppAuthError("missing_user")
    try:
        user: dict[str, Any] = json.loads(raw_user)
        user_id = int(user["id"])
    except Exception as exc:
        raise MiniAppAuthError("missing_user") from exc
    return MiniAppUser(
        user_id=user_id,
        auth_date=auth_date,
        username=user.get("username"),
        first_name=user.get("first_name"),
        last_name=user.get("last_name"),
        language_code=user.get("language_code"),
    )
