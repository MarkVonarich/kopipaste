# ui/keyboards.py — v2025.08.18-01
__version__ = "2025.08.18-01"

from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('🧾 Отчёт',     callback_data='menu_report'),
         InlineKeyboardButton('📈 Аналитика', callback_data='menu_analytics')],
        [InlineKeyboardButton('⚙️ Настройки', callback_data='menu_settings')],
        [InlineKeyboardButton('📌 Примеры',   callback_data='menu_examples'),
         InlineKeyboardButton('🆘 Поддержка', callback_data='menu_support')],
    ]) 
