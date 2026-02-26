# routers/commands.py — v2025.08.18-01
__version__ = "2025.08.18-01"

from telegram import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import pandas as pd
from datetime import datetime
from db.database import get_conn
from db.queries import ensure_user, get_user_budgets, get_user_currency, get_ml_stats
from ui.keyboards import main_menu_kb
from services.onboarding import onboarding_welcome

async def on_startup(app):
    # предзагрузка кэша и курсов
    from cache.global_dict import load_global_cache
    from services.currency import update_fx_rates
    load_global_cache()
    update_fx_rates()
    await app.bot.set_my_commands([
        BotCommand('start','Главное меню / онбординг'),
        BotCommand('settings','Настройки'),
        BotCommand('budget','Показать бюджеты'),
        BotCommand('export','Экспорт XLSX/CSV'),
        BotCommand('about','О боте и зачем он нужен'),
        BotCommand('mlstats','ML-статистика top1/top2'),
    ])

async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_new = ensure_user(uid)
    if context.args and any(a.lower() in ('onboarding','ob') for a in context.args):
        is_new = True
    if is_new:
        return await onboarding_welcome(update, context)
    await update.message.reply_text('🔷 Главное меню:', reply_markup=main_menu_kb())

async def cmd_settings(update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('💱 Валюта', callback_data='menu_currency'),
         InlineKeyboardButton('⏰ Напоминание', callback_data='menu_reminder')],
        [InlineKeyboardButton('🕒 Часовой пояс', callback_data='menu_tz')],
        [InlineKeyboardButton('1️⃣ Установить бюджет', callback_data='menu_set_budget')],
        [InlineKeyboardButton('◀️ В меню', callback_data='start_main')],
    ])
    await update.message.reply_text('⚙️ Настройки:', reply_markup=kb)

async def cmd_budget(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    wl, ml = get_user_budgets(cid)
    cur = get_user_currency(cid)
    await update.message.reply_text(
        f"Ваши бюджеты:\n• Неделя: {wl or 0} {cur}\n• Месяц: {ml or 0} {cur}"
    )

async def cmd_export(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT id, op_date, type, category, amount, comment
          FROM public.operations
         WHERE chat_id=%s
         ORDER BY op_date, id
    """, (cid,))
    rows = cur.fetchall(); cur.close(); conn.close()
    if not rows:
        return await update.message.reply_text("Нет данных для экспорта.")
    df = pd.DataFrame(rows, columns=["id","date","type","category","amount","comment"])
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"/tmp/export_{cid}_{ts}"
    sent = False
    try:
        xlsx_path = base + ".xlsx"
        df.to_excel(xlsx_path, index=False)
        await context.bot.send_document(chat_id=cid, document=open(xlsx_path,"rb"), filename=f"fin_{ts}.xlsx")
        sent = True
    except Exception:
        pass
    try:
        csv_path = base + ".csv"
        df.to_csv(csv_path, index=False)
        await context.bot.send_document(chat_id=cid, document=open(csv_path,"rb"), filename=f"fin_{ts}.csv")
        sent = True
    except Exception:
        pass
    if not sent:
        await update.message.reply_text("⚠️ Не удалось сформировать файл экспорта (XLSX/CSV).")

async def cmd_about(update, context: ContextTypes.DEFAULT_TYPE):
    from settings import SUPPORT_USERNAME
    txt = (
        "Я *КопиPaste* — делаю учёт денег простым и быстрым.\n\n"
        "⚙️ Как пользоваться:\n"
        "• Пишите коротко: «молоко 150», «пицца 450 вчера», «зарплата 70 000».\n"
        "• Если я знаю вашу привычную категорию — запишу сразу.\n"
        "• Если нет — подскажу и запомню ваш выбор.\n\n"
        "🎯 Зачем это всё: регулярный учёт помогает увидеть, куда утекают деньги, и снижает лишние траты.\n\n"
        "Команды: /start /settings /budget /export\n"
        "Поддержка: @" + SUPPORT_USERNAME.lstrip('@')
    )
    await update.message.reply_text(txt, parse_mode='Markdown', disable_web_page_preview=True)


async def cmd_mlstats(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    stats = get_ml_stats(cid, days=30)
    picks = stats.get('picks', 0)
    top1 = stats.get('top1_hit', 0)
    top2 = stats.get('top2_hit', 0)
    top1_pct = (top1 * 100.0 / picks) if picks else 0.0
    top2_pct = (top2 * 100.0 / picks) if picks else 0.0
    await update.message.reply_text(
        f"ML stats (30д):\n"
        f"• picks: {picks}\n"
        f"• top1 hit: {top1} ({top1_pct:.1f}%)\n"
        f"• top2 hit: {top2} ({top2_pct:.1f}%)"
    )
