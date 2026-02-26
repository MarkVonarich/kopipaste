# settings.py — v2026.01.04-01
__version__ = "2026.01.04-01"

import os

def _required(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required env var: {name}")
    return v

# Обязательные
TELEGRAM_TOKEN = _required("TELEGRAM_TOKEN")
DATABASE_URL   = _required("DATABASE_URL")

# Опциональные
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "chiracredible")
CURRENCYBEACON_API_KEY = os.getenv("CURRENCYBEACON_API_KEY", "").strip()

# Бюджеты по умолчанию: НЕ навязываем числа в коде.
# Если нужно — задашь в .env, иначе будет 0 (то есть "не задано").
WEEK_DEFAULT  = int(os.getenv("WEEK_DEFAULT", "0") or "0")
MONTH_DEFAULT = int(os.getenv("MONTH_DEFAULT", "0") or "0")
