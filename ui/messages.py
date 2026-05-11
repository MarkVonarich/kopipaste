# ui/messages.py — v2025.08.19-gram-01
from __future__ import annotations

from typing import Optional, Union
from datetime import date, datetime

from telegram import Update
from telegram.ext import ContextTypes

from utils.text import format_date_ru_with_weekday


# === Общая отправка сообщения (как было) ======================================
async def send_msg(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup=None,
    parse_mode: Optional[str] = None,
    disable_preview: bool = False,
):
    if getattr(update, "message", None):
        return await update.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_preview,
        )
    if getattr(update, "callback_query", None):
        return await update.callback_query.message.reply_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_preview,
        )


# === Рендер итогового ответа ===================================================
def _fmt_amount_groups(n: Union[int, float]) -> str:
    """10000 -> '10 000' (без указания знака, т.к. тип операции задаёт смысл)."""
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    return f"{n:,}".replace(",", " ")


def _md_escape(s: str) -> str:
    """Минимальные экранирования под Telegram Markdown (не MarkdownV2)."""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("`", "\\`")
    )


def render_final_reply(
    name: str,
    amount: Union[int, float],
    currency: str,
    category: str,
    op_dt: Union[date, datetime],
    original: Optional[str] = None,
    op_kind: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    """
    Формирует текст:
      <Имя> <глагол по типу> *<сумма CUR>* <предлог/разделитель> *<Категория> [ (note) ]*
      <дата, день недели>
      _<оригинал пользователя>_
    """
    # 1) Подготовка полей
    cat_bold = f"*{_md_escape(category)}*"
    amt_bold = f"*{_fmt_amount_groups(amount)} {currency}*"
    nm = _md_escape(name or "")
    d = op_dt.date() if isinstance(op_dt, datetime) else op_dt
    date_str = format_date_ru_with_weekday(d)

    note_sfx = ""
    if note:
        note_sfx = f" ({_md_escape(note)})"

    # 2) Грамматика по типам
    kind = (op_kind or "").strip().lower()
    if kind == "расходы":
        line1 = f"{nm} потратил(а) {amt_bold} на {cat_bold}{note_sfx}"
    elif kind == "доходы":
        line1 = f"{nm} получил(а) {amt_bold} — {cat_bold}{note_sfx}"
    elif kind == "инвестиции":
        line1 = f"{nm} вложил(а) {amt_bold} в {cat_bold}{note_sfx}"
    elif kind == "цели":
        line1 = f"{nm} отложил(а) {amt_bold} на {cat_bold}{note_sfx}"
    else:
        # запасной вариант
        line1 = f"{nm} записал(а) {amt_bold} — {cat_bold}{note_sfx}"

    # 3) Оригинальный ввод — всегда курсивом, если передан
    orig_line = f"_{_md_escape(original)}_" if (original and original.strip()) else None

    if orig_line:
        return f"{line1}\n{date_str}\n{orig_line}"
    return f"{line1}\n{date_str}"
