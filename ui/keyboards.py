# ui/keyboards.py — v2025.08.18-01
__version__ = "2025.08.18-01"

from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Добавить операцию', callback_data='menu_examples')],
        [InlineKeyboardButton('📊 Бюджеты и лимиты', callback_data='settings_budgets')],
        [InlineKeyboardButton('🔔 Напоминания', callback_data='rem_menu'),
         InlineKeyboardButton('📤 Экспорт', callback_data='exp_menu')],
        [InlineKeyboardButton('⚙️ Настройки', callback_data='menu_settings'),
         InlineKeyboardButton('❓ Помощь', callback_data='menu_help')],
    ]) 


def settings_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('💱 Валюта', callback_data='menu_currency'),
         InlineKeyboardButton('⏰ Время напоминаний', callback_data='menu_reminder')],
        [InlineKeyboardButton('🔔 Оповещения', callback_data='menu_notifications')],
        [InlineKeyboardButton('🧩 Пространства', callback_data='workspace_menu')],
        [InlineKeyboardButton('💰 Бюджеты', callback_data='settings_budgets'),
         InlineKeyboardButton('📂 Лимиты', callback_data='lim_list')],
        [InlineKeyboardButton('🕒 Часовой пояс', callback_data='menu_tz')],
        [InlineKeyboardButton('❓ Помощь', callback_data='menu_help')],
        [InlineKeyboardButton('◀️ В меню', callback_data='start_main')],
    ])


def help_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('➕ Добавить операцию', callback_data='menu_examples')],
        [InlineKeyboardButton('📊 Бюджеты и лимиты', callback_data='settings_budgets')],
        [InlineKeyboardButton('🔔 Напоминания', callback_data='rem_menu'),
         InlineKeyboardButton('📤 Экспорт', callback_data='exp_menu')],
        [InlineKeyboardButton('🆘 Поддержка', callback_data='menu_support')],
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
