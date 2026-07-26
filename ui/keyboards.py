# ui/keyboards.py — v2025.08.18-01
__version__ = "2025.08.18-01"

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from services.i18n import t


def main_menu_kb(locale: str | None = None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t('menu.add_operation', locale), callback_data='menu_examples')],
        [InlineKeyboardButton(t('menu.limits_budgets', locale), callback_data='lb_hub'),
         InlineKeyboardButton(t('menu.reminders', locale), callback_data='rem_menu')],
        [InlineKeyboardButton(t('menu.export', locale), callback_data='exp_menu'),
         InlineKeyboardButton(t('menu.settings', locale), callback_data='menu_settings')],
        [InlineKeyboardButton(t('menu.help', locale), callback_data='menu_help')],
    ])


def limits_budgets_hub_kb(locale: str | None = None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t('limits_budgets.category_limits', locale), callback_data='lim_list'),
         InlineKeyboardButton(t('limits_budgets.general_limit', locale), callback_data='gl_menu')],
        [InlineKeyboardButton(t('limits_budgets.category_budget', locale), callback_data='cbg_menu')],
        [InlineKeyboardButton(t('limits_budgets.spending_status', locale), callback_data='lb_status'),
         InlineKeyboardButton(t('limits_budgets.notifications', locale), callback_data='menu_notifications')],
        [InlineKeyboardButton(t('menu.back', locale), callback_data='start_main')],
    ])


def export_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('📅 Сегодня', callback_data='exp_today'),
         InlineKeyboardButton('🗓 7 дней', callback_data='exp_7')],
        [InlineKeyboardButton('🗓 14 дней', callback_data='exp_14'),
         InlineKeyboardButton('📅 Этот месяц', callback_data='exp_m')],
        [InlineKeyboardButton('↩️ Прошлый месяц', callback_data='exp_pm'),
         InlineKeyboardButton('📆 Этот год', callback_data='exp_y')],
        [InlineKeyboardButton('↩️ Прошлый год', callback_data='exp_py'),
         InlineKeyboardButton('⚙️ Свой период', callback_data='exp_custom')],
        [InlineKeyboardButton('⬅️ Назад', callback_data='start_main')],
    ])


def reminders_menu_kb(has_any: bool) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton('➕ Добавить', callback_data='rem_add'),
        InlineKeyboardButton('📋 Все', callback_data='rem_all'),
    ]]
    rows.append([InlineKeyboardButton('⚙️ Настройки уведомлений', callback_data='menu_notifications')])
    rows.append([InlineKeyboardButton('⬅️ Назад', callback_data='start_main')])
    return InlineKeyboardMarkup(rows)


def settings_menu_kb(locale: str | None = None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💱 Валюта', callback_data='menu_currency'),
         InlineKeyboardButton('⏰ Время напоминаний', callback_data='menu_reminder')],
        [InlineKeyboardButton('🔔 Оповещения', callback_data='menu_notifications'),
         InlineKeyboardButton('🧩 Пространства', callback_data='workspace_menu')],
        [InlineKeyboardButton(t('menu.limits_budgets', locale), callback_data='lb_hub')],
        [InlineKeyboardButton('🕒 Часовой пояс', callback_data='menu_tz'),
         InlineKeyboardButton('❓ Помощь', callback_data='menu_help')],
        [InlineKeyboardButton('◀️ В меню', callback_data='start_main')],
    ])


def help_menu_kb(locale: str | None = None):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t('menu.add_operation', locale), callback_data='menu_examples')],
        [InlineKeyboardButton(t('menu.limits_budgets', locale), callback_data='lb_hub'),
         InlineKeyboardButton(t('menu.reminders', locale), callback_data='rem_menu')],
        [InlineKeyboardButton(t('menu.export', locale), callback_data='exp_menu'),
         InlineKeyboardButton('🆘 Поддержка', callback_data='menu_support')],
        [InlineKeyboardButton('◀️ В меню', callback_data='start_main')],
    ])


def ml_top2_kb(cat1: str, cat2: str, toggle_label: str = '🔁 Доход/Расход'):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f'✅ {cat1}', callback_data=f'ml_pick|{cat1}'),
         InlineKeyboardButton(f'✅ {cat2}', callback_data=f'ml_pick|{cat2}')],
        [InlineKeyboardButton('➕ Новая категория', callback_data='ml_new_cat')],
        [InlineKeyboardButton('✍️ Другая категория', callback_data='ml_other'),
         InlineKeyboardButton(toggle_label, callback_data='ml_toggle_income')],
        [InlineKeyboardButton('❌ Отмена', callback_data='start_main')],
    ])
