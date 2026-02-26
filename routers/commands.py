# routers/commands.py — v2026.02.26-01
__version__ = "2026.02.26-01"

from telegram import BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
import pandas as pd
from datetime import datetime

from db.database import get_conn
from db.queries import ensure_user, get_user_budgets, get_user_currency, get_ml_stats
from services.ml_train import train_model
from ui.keyboards import main_menu_kb
from services.onboarding import onboarding_welcome
from settings import ADMIN_USER_IDS


async def on_startup(app):
    from cache.global_dict import load_global_cache
    from services.currency import update_fx_rates

    load_global_cache()
    update_fx_rates()
    await app.bot.set_my_commands([
        BotCommand('start', 'Главное меню / онбординг'),
        BotCommand('settings', 'Настройки'),
        BotCommand('budget', 'Показать бюджеты'),
        BotCommand('limits', 'Мои лимиты'),
        BotCommand('export', 'Экспорт XLSX/CSV'),
        BotCommand('about', 'О боте и зачем он нужен'),
        BotCommand('mlstats', 'ML-статистика top1/top2'),
        BotCommand('mltrain', 'Обучить ML модель (admin)'),
    ])


async def cmd_start(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_new = ensure_user(uid)
    if context.args and any(a.lower() in ('onboarding', 'ob') for a in context.args):
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
    await update.message.reply_text(f"Ваши бюджеты:\n• Неделя: {wl or 0} {cur}\n• Месяц: {ml or 0} {cur}")


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
    df = pd.DataFrame(rows, columns=["id", "date", "type", "category", "amount", "comment"])
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"/tmp/export_{cid}_{ts}"
    sent = False
    try:
        xlsx_path = base + ".xlsx"
        df.to_excel(xlsx_path, index=False)
        await context.bot.send_document(chat_id=cid, document=open(xlsx_path, "rb"), filename=f"fin_{ts}.xlsx")
        sent = True
    except Exception:
        pass
    try:
        csv_path = base + ".csv"
        df.to_csv(csv_path, index=False)
        await context.bot.send_document(chat_id=cid, document=open(csv_path, "rb"), filename=f"fin_{ts}.csv")
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
        "Команды: /start /settings /budget /export /mlstats\n"
        "Поддержка: @" + SUPPORT_USERNAME.lstrip('@')
    )
    await update.message.reply_text(txt, parse_mode='Markdown', disable_web_page_preview=True)


async def cmd_mlstats(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    stats = get_ml_stats(cid, days=30)
    picks = stats.get('picks', 0)
    top1 = stats.get('top1_hit', 0)
    top2 = stats.get('top2_hit', 0)
    b_picks = stats.get('baseline_picks', 0)
    b_top1 = stats.get('baseline_top1_hit', 0)
    b_top2 = stats.get('baseline_top2_hit', 0)
    m_picks = stats.get('model_picks', 0)
    m_top1 = stats.get('model_top1_hit', 0)
    m_top2 = stats.get('model_top2_hit', 0)

    def pct(v, n):
        return (v * 100.0 / n) if n else 0.0

    await update.message.reply_text(
        "ML stats (30д):\n"
        f"• overall picks: {picks}\n"
        f"• overall top1/top2: {top1} ({pct(top1, picks):.1f}%) / {top2} ({pct(top2, picks):.1f}%)\n"
        f"• baseline picks: {b_picks}; top1/top2: {b_top1} ({pct(b_top1, b_picks):.1f}%) / {b_top2} ({pct(b_top2, b_picks):.1f}%)\n"
        f"• model picks: {m_picks}; top1/top2: {m_top1} ({pct(m_top1, m_picks):.1f}%) / {m_top2} ({pct(m_top2, m_picks):.1f}%)"
    )


async def cmd_mltrain(update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else 0
    if uid not in ADMIN_USER_IDS:
        return await update.message.reply_text('⛔ Команда только для администратора.')

    await update.message.reply_text('🧠 Запускаю обучение модели...')
    try:
        report = train_model(days=180, op_type='Расходы', limit=20000)
    except Exception as e:
        return await update.message.reply_text(f'❌ Ошибка обучения: {e}')

    if not report.get('ok'):
        return await update.message.reply_text(
            f"⚠️ Обучение не выполнено: {report.get('error')} (samples={report.get('samples', 0)})"
        )

    await update.message.reply_text(
        "✅ ML model trained\n"
        f"• version: {report.get('model_version')}\n"
        f"• trained_at: {report.get('trained_at')}\n"
        f"• samples: {report.get('samples_total')}\n"
        f"• classes: {len(report.get('classes', []))}\n"
        f"• holdout top1/top2: {report.get('holdout_top1')} / {report.get('holdout_top2')}\n"
        f"• train_sec: {report.get('train_sec')}"
    )


async def cmd_limits(update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton('📌 Мои лимиты', callback_data='lim_list')],
    ])
    await update.message.reply_text('📌 Управление лимитами', reply_markup=kb)
