from __future__ import annotations

import logging
from urllib.parse import urlparse

from telegram import MenuButtonWebApp, WebAppInfo

from settings import MINIAPP_PUBLIC_URL

log = logging.getLogger(__name__)


def valid_miniapp_public_url(url: str | None = None) -> bool:
    value = str(url if url is not None else MINIAPP_PUBLIC_URL or "").strip()
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def normalize_miniapp_url(url: str | None) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    return value.rstrip("/")


async def register_miniapp_menu_button(bot, url: str | None = None) -> bool:
    value = str(url if url is not None else MINIAPP_PUBLIC_URL or "").strip()
    if not valid_miniapp_public_url(value):
        log.info("miniapp_menu_button_skipped reason=invalid_url")
        return False
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Открыть",
                web_app=WebAppInfo(url=value),
            )
        )
        log.info("miniapp_menu_button_registered")
        return True
    except Exception as exc:
        log.warning("miniapp_menu_button_registration_failed reason=%s", type(exc).__name__)
        return False
