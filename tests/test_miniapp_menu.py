from __future__ import annotations

import logging
import asyncio

from telegram import MenuButtonWebApp

from services.miniapp_menu import register_miniapp_menu_button, valid_miniapp_public_url


class FakeBot:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def set_chat_menu_button(self, **kwargs):
        if self.fail:
            raise RuntimeError("temporary")
        self.calls.append(kwargs)


def test_valid_miniapp_public_url_requires_https():
    assert valid_miniapp_public_url("https://example.com/app") is True
    assert valid_miniapp_public_url("http://example.com/app") is False
    assert valid_miniapp_public_url("") is False


def test_register_miniapp_menu_button_sets_webapp_button():
    bot = FakeBot()

    ok = asyncio.run(register_miniapp_menu_button(bot, "https://example.com/app"))

    assert ok is True
    button = bot.calls[0]["menu_button"]
    assert isinstance(button, MenuButtonWebApp)
    assert button.text == "Открыть приложение"
    assert button.web_app.url == "https://example.com/app"


def test_register_miniapp_menu_button_skips_invalid_and_tolerates_errors(caplog):
    bot = FakeBot()
    assert asyncio.run(register_miniapp_menu_button(bot, "http://example.com/app")) is False
    assert bot.calls == []

    caplog.set_level(logging.WARNING)
    failing = FakeBot(fail=True)
    assert asyncio.run(register_miniapp_menu_button(failing, "https://example.com/app")) is False
    assert "RuntimeError" in caplog.text
    assert "https://example.com/app" not in caplog.text
