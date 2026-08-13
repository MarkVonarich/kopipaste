# settings.py — v2026.01.04-01
__version__ = "2026.01.04-01"

import os

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

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


def _parse_int_list(v: str) -> list[int]:
    out = []
    for part in (v or '').split(','):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            pass
    return out

ADMIN_USER_IDS = _parse_int_list(os.getenv('ADMIN_USER_IDS', ''))


def _parse_bool(name: str, default: bool) -> bool:
    val = (os.getenv(name, str(default)).strip().lower())
    return val in {"1", "true", "yes", "on"}


ENABLE_DAY_NUDGE = _parse_bool("ENABLE_DAY_NUDGE", False)
ENABLE_EVENING_REMINDER = _parse_bool("ENABLE_EVENING_REMINDER", True)
ENABLE_SMART_MORNING_LIMITS = _parse_bool("ENABLE_SMART_MORNING_LIMITS", True)

VOICE_INPUT_ENABLED = _parse_bool("VOICE_INPUT_ENABLED", True)
VOICE_TRANSCRIBE_PROVIDER = os.getenv("VOICE_TRANSCRIBE_PROVIDER", "openai").strip().lower() or "openai"
VOICE_TRANSCRIBE_MODEL = os.getenv("VOICE_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe").strip() or "gpt-4o-mini-transcribe"
VOICE_MAX_SECONDS = int(os.getenv("VOICE_MAX_SECONDS", "60") or "60")

# Optional analytics foundation settings. Missing HMAC secret must not break the bot.
ANALYTICS_HMAC_SECRET = os.getenv("ANALYTICS_HMAC_SECRET", "").strip()
ANALYTICS_OUTBOX_MAX_ATTEMPTS = int(os.getenv("ANALYTICS_OUTBOX_MAX_ATTEMPTS", "8") or "8")

# Optional PostHog outbox exporter settings. Export remains disabled by default.
POSTHOG_PROJECT_TOKEN = os.getenv("POSTHOG_PROJECT_TOKEN", "").strip()
POSTHOG_HOST = os.getenv("POSTHOG_HOST", "").strip()
POSTHOG_EXPORT_ENABLED = _parse_bool("POSTHOG_EXPORT_ENABLED", False)
POSTHOG_EXPORT_BATCH_SIZE = int(os.getenv("POSTHOG_EXPORT_BATCH_SIZE", "50") or "50")
POSTHOG_EXPORT_INTERVAL_SECONDS = int(os.getenv("POSTHOG_EXPORT_INTERVAL_SECONDS", "60") or "60")
POSTHOG_EXPORT_TIMEOUT_SECONDS = int(os.getenv("POSTHOG_EXPORT_TIMEOUT_SECONDS", "10") or "10")
POSTHOG_EXPORT_MAX_ATTEMPTS = int(os.getenv("POSTHOG_EXPORT_MAX_ATTEMPTS", "8") or "8")
POSTHOG_EXPORT_MAX_EVENT_AGE_DAYS = int(os.getenv("POSTHOG_EXPORT_MAX_EVENT_AGE_DAYS", "30") or "30")

# Optional Telegram Mini App public entry point.
MINIAPP_PUBLIC_URL = os.getenv("MINIAPP_PUBLIC_URL", "").strip()

# Trusted local forecast artifacts. An empty directory disables registered-model inference.
FORECAST_MODEL_DIR = os.getenv("FORECAST_MODEL_DIR", "").strip()
FORECAST_PRODUCTION_TRAINING_ENABLED = _parse_bool("FORECAST_PRODUCTION_TRAINING_ENABLED", False)
