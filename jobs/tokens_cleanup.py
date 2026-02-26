# jobs/tokens_cleanup.py — v2026.01.25-01
__version__ = "2026.01.25-01"

import asyncio
import logging
from telegram.ext import ContextTypes

from db.queries import cleanup_action_tokens

log = logging.getLogger("finbot.tokens")


async def action_tokens_cleanup_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Чистим action_tokens:
    - pending старше TTL -> expired
    - used/expired старше N дней -> delete
    """
    try:
        res = await asyncio.to_thread(cleanup_action_tokens)
        log.info("action_tokens cleanup: expired=%s deleted=%s", res.get("expired"), res.get("deleted"))
    except Exception as e:
        log.exception("action_tokens cleanup failed: %s", e)
