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


def ml_top2_kb(cat1: str, cat2: str, toggle_label: str = '🔁 Доход/Расход'):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f'✅ {cat1}', callback_data=f'ml_pick|{cat1}'),
         InlineKeyboardButton(f'✅ {cat2}', callback_data=f'ml_pick|{cat2}')],
        [InlineKeyboardButton('✍️ Другая категория', callback_data='ml_other'),
         InlineKeyboardButton(toggle_label, callback_data='ml_toggle_income')],
    ])
