import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from miniapp.auth import MiniAppAuthError, verify_telegram_init_data


BOT_TOKEN = "123456:TEST"


def _init_data(user: dict | None = None, *, auth_date: int = 1000, bot_token: str = BOT_TOKEN) -> str:
    data = {
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "auth_date": str(auth_date),
    }
    if user is not None:
        data["user"] = json.dumps(user, separators=(",", ":"), ensure_ascii=False)
    check = "\n".join(f"{key}={value}" for key, value in sorted(data.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode(data)


def test_verify_telegram_init_data_accepts_valid_payload():
    user = verify_telegram_init_data(
        _init_data({"id": 42, "username": "fin", "language_code": "ru"}, auth_date=1000),
        bot_token=BOT_TOKEN,
        now=1100,
        max_age_seconds=300,
    )

    assert user.user_id == 42
    assert user.username == "fin"
    assert user.language_code == "ru"


def test_verify_telegram_init_data_rejects_bad_hash():
    payload = _init_data({"id": 42}, auth_date=1000).replace("hash=", "hash=bad")

    with pytest.raises(MiniAppAuthError) as exc:
        verify_telegram_init_data(payload, bot_token=BOT_TOKEN, now=1100, max_age_seconds=300)

    assert exc.value.code == "invalid_hash"


def test_verify_telegram_init_data_rejects_expired_payload():
    with pytest.raises(MiniAppAuthError) as exc:
        verify_telegram_init_data(_init_data({"id": 42}, auth_date=1000), bot_token=BOT_TOKEN, now=2000, max_age_seconds=300)

    assert exc.value.code == "expired_init_data"


def test_verify_telegram_init_data_requires_user():
    with pytest.raises(MiniAppAuthError) as exc:
        verify_telegram_init_data(_init_data(None, auth_date=1000), bot_token=BOT_TOKEN, now=1100, max_age_seconds=300)

    assert exc.value.code == "missing_user"
