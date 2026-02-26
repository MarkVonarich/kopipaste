# services/onboarding.py — v2025.08.18-01
__version__ = "2025.08.18-01"

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from ui.messages import send_msg
from ui.keyboards import main_menu_kb

async def onboarding_welcome(update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['onb'] = True
    text = (
        "Привет! Я *КопиPaste* — бот для учёта финансов.\n\n"
        "Что умею:\n"
        "• Быстрый ввод: «молоко 150», «пицца 450 вчера»\n"
        "• Подсказываю категории и запоминаю привычки\n"
        "• Бюджеты, напоминания, отчёты и простая аналитика\n\n"
        "Пробежимся по настройкам?"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('Вперёд →', callback_data='onb_curr')],
        [InlineKeyboardButton('Пропустить', callback_data='onb_finish')],
    ])
    await send_msg(update, context, text, reply_markup=kb, parse_mode='Markdown')

async def onboarding_budget(update, context, info: str|None=None):
    if info: await send_msg(update, context, info)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('Установить недельный бюджет', callback_data='set_week')],
        [InlineKeyboardButton('Установить месячный бюджет', callback_data='set_month')],
        [InlineKeyboardButton('Пропустить', callback_data='onb_finish')],
    ])
    await send_msg(update, context, "Настроим бюджеты или пропустим?", reply_markup=kb)

async def onboarding_budget_after_week(update, context):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('Установить месячный бюджет', callback_data='set_month')],
        [InlineKeyboardButton('Пропустить', callback_data='onb_finish')],
    ])
    await send_msg(update, context, "Ок, неделя есть. Зададим месячный бюджет?", reply_markup=kb)

async def onboarding_finish(update, context):
    context.user_data.pop('onb', None)
    txt = (
        "Готово! Можете сразу писать мне операции, например:\n"
        "• молоко 150\n• пицца 450 вчера\n• зарплата 50000\n\n"
        "Если что — /settings."
    )
    await send_msg(update, context, txt, reply_markup=main_menu_kb())

