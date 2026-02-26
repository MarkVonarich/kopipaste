# routers/helpers.py — v2025.08.26-02 (headers with item)
__version__ = "2025.08.26-02"

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from services.records import list_categories_for_type

def _md_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")

def _shorten(s: str, n: int = 40) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"

async def prompt_type_menu(update, context: ContextTypes.DEFAULT_TYPE):
    p = context.user_data.get('pending', {})
    merch = p.get('merch') or ""
    merch_disp = _shorten(merch)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('💸 Расходы',    callback_data='type|Расходы')],
        [InlineKeyboardButton('💰 Доходы',     callback_data='type|Доходы')],
        [InlineKeyboardButton('📈 Инвестиции', callback_data='type|Инвестиции')],
        [InlineKeyboardButton('💾 Сбережения', callback_data='type|Сбережения')],
        [InlineKeyboardButton('◀️ Назад',      callback_data='start_main')],
    ])

    title = 'Выберите тип операции:'
    if merch_disp:
        title = f"Выберите тип операции для *{_md_escape(merch_disp)}*"

    msg = await (update.message or update.callback_query.message).reply_text(
        title, reply_markup=kb, parse_mode='Markdown'
    )
    context.user_data['type_menu_id'] = msg.message_id

async def prompt_category_menu(update, context: ContextTypes.DEFAULT_TYPE):
    p = context.user_data.get('pending', {})
    typ = p.get('type') or 'Расходы'
    merch = p.get('merch') or ""
    merch_disp = _shorten(merch)

    cid = update.effective_chat.id if update.effective_chat else update.callback_query.message.chat.id
    cats = list_categories_for_type(cid, typ)

    rows = [[InlineKeyboardButton('➕ Добавить категорию', callback_data='add_cat')]]
    for c in cats:
        rows.append([InlineKeyboardButton(c, callback_data=f"use_cat|{c}")])
    rows.append([InlineKeyboardButton('◀️ Назад', callback_data='start_main')])

    title = 'Выберите категорию ⬇️'
    if merch_disp:
        title = f"Выберите категорию для *{_md_escape(merch_disp)}*"

    msg = await (update.message or update.callback_query.message).reply_text(
        title, reply_markup=InlineKeyboardMarkup(rows), parse_mode='Markdown'
    )
    context.user_data['cat_menu_id'] = msg.message_id
