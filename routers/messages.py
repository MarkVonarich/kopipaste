
# routers/messages.py — v2025.08.26-batch-05 (effective_message everywhere)
__version__ = "2025.08.26-batch-05"

import re
from datetime import datetime
from telegram import ReplyKeyboardRemove
from telegram.ext import ContextTypes
from services.currency import detect_currency_token, convert_amount_if_needed
from services.records import get_user_alias, record_operation
from cache.global_dict import bump_global_popularity
from routers.helpers import prompt_type_menu
from ui.keyboards import ml_top2_kb
from utils.parsing import parse_user_input, split_wo_date, parse_day_list
from utils.text import norm_text
from db.queries import update_user_field, insert_ml_observation
from services.ml_prep import normalize_for_ml
from services.ml_suggest import get_top2_suggestions
import logging

log = logging.getLogger(__name__)
try:
    from timezonefinder import TimezoneFinder
except Exception:
    TimezoneFinder = None

BATCH_MAX = 25  # ограничение длины списка на один ввод


def _md_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")


async def _safe_reply(emsg, text_md: str, reply_markup=None):
    """Reply in markdown, fallback to plain text if telegram rejects entities."""
    try:
        return await emsg.reply_text(text_md, parse_mode='Markdown', reply_markup=reply_markup)
    except Exception as e:
        log.warning("reply_text markdown failed, fallback plain: %s", e)
        plain = (text_md or '').replace('*', '').replace('_', '').replace('`', '')
        return await emsg.reply_text(plain, reply_markup=reply_markup)


async def _process_free_text(update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    """
    Одна строка → тот же старый флоу.
    Используется и в одиночном режиме, и в батче (на текущем элементе).
    """
    text = input_text or ""
    cid  = update.effective_chat.id
    emsg = update.effective_message  # универсальный объект сообщения (и для callback'ов тоже)

    try:
        merch_display, amt_raw, dt, src_curr = parse_user_input(text)
    except ValueError as e:
        reason = str(e)
        if reason == "no_amount":
            base, dt = split_wo_date(text)
            base = base.strip() or "операция"
            context.user_data['pending'] = {'merch': norm_text(base), 'time': dt}
            context.user_data['await_amount'] = True
            return await emsg.reply_text(f"Введите сумму для «{base}» (например, 250):")
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=text,
                normalized_text=normalize_for_ml(text),
                detected_type='Расходы',
                action='parse_failed',
                meta={'source': 'telegram', 'error': reason},
            )
        except Exception:
            pass
        if reason == "bad_amount":
            return await emsg.reply_text("⚠️ Неверная сумма. Пример: «пицца 450 вчера».")
        return await emsg.reply_text("⚠️ Не понял сумму. Пример: «пицца 450 вчера». Нажмите «Примеры» в меню.")

    merch = norm_text(merch_display)

    # FX: детект валюты именно из текущего куска текста (важно для батча)
    src_curr = src_curr or detect_currency_token(text)
    amt_final, note = convert_amount_if_needed(cid, amt_raw, src_curr)

    alias = get_user_alias(cid, merch)
    if alias:
        typ, cat = alias
        # Чтобы raw_text в операции был ровно этот кусок
        context.user_data['batch_item_text'] = text
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=text,
                normalized_text=normalize_for_ml(text),
                detected_type=typ,
                action='fallback_direct_write',
                chosen_category=cat,
                chosen_type=typ,
                meta={'source': 'telegram', 'flow': 'alias_hit'},
            )
        except Exception:
            pass
        return await record_operation(cat, amt_final, dt, typ, update, context, note)

    op_type = 'Расходы'
    normalized = normalize_for_ml(text)
    top2, sugg_meta = get_top2_suggestions(cid, normalized, op_type)
    if len(top2) < 2:
        top2 = [{'cat': 'Продукты', 'score': 0.6}, {'cat': 'Другое', 'score': 0.4}]
    cat1, cat2 = top2[0]['cat'], top2[1]['cat']
    context.user_data['pending'] = {
        'merch': merch,
        'amt': amt_final,
        'time': dt,
        'type': op_type,
        'note': note,
        'ml_cat1': cat1,
        'ml_cat2': cat2,
        'ml_top2': top2,
    }
    try:
        insert_ml_observation(
            user_id=cid,
            chat_id=cid,
            raw_text=text,
            normalized_text=normalized,
            detected_type=op_type,
            action='suggest_shown',
            suggested_top2=top2,
            confidence_top1=top2[0].get('score'),
            meta={'source': 'baseline', 'stage': '2.2', 'merchant': merch, 'currency_detected': src_curr, 'suggest': sugg_meta},
        )
    except Exception:
        pass

    msg = await _safe_reply(
        emsg,
        f"Категория?\n➖ {amt_final} ₽ • {_md_escape(merch)}",
        reply_markup=ml_top2_kb(cat1, cat2),
    )
    context.user_data['suggest_msg_id'] = msg.message_id
    context.user_data['batch_item_text'] = text
    return


# ─────────────────────────────────────────────
# Батч-контроллер: последовательно, по одному
# ─────────────────────────────────────────────

async def _batch_start(update, context: ContextTypes.DEFAULT_TYPE, items: list[str]):
    context.user_data['batch_active'] = True
    context.user_data['batch_queue']  = list(items)  # копия
    context.user_data['batch_total']  = len(items)
    context.user_data['batch_done']   = 0
    await _batch_next(update, context)

async def _batch_next(update, context: ContextTypes.DEFAULT_TYPE):
    q = context.user_data.get('batch_queue') or []
    if not q:
        # Завершили батч
        context.user_data['batch_active'] = False
        context.user_data['batch_item_text'] = ""
        return
    item = q.pop(0)
    context.user_data['batch_queue'] = q
    context.user_data['batch_item_text'] = item
    await _process_free_text(update, context, item)

async def continue_batch_if_needed(update, context: ContextTypes.DEFAULT_TYPE):
    """Вызывается после успешной записи одной операции (из services.records)."""
    if not context.user_data.get('batch_active'):
        return
    context.user_data['batch_done'] = int(context.user_data.get('batch_done', 0)) + 1
    await _batch_next(update, context)


# ─────────────────────────────────────────────
# Основной хэндлер входящих сообщений
# ─────────────────────────────────────────────

async def handle_text(update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    cid  = update.effective_chat.id
    emsg = update.effective_message

    # ----- настройки и прочие ветки (без изменений) -----
    if context.user_data.pop('await_reminder_custom', False):
        m = re.search(r'\d{1,2}', text)
        if not m:
            return await emsg.reply_text("⚠️ Введите число от 0 до 23 (например, 20)")
        hour = int(m.group())
        if not (0 <= hour <= 23):
            return await emsg.reply_text("⚠️ Час должен быть 0–23")
        update_user_field(cid, 'reminder_hour', hour)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await emsg.reply_text(f"✅ Напоминание каждый день в {hour:02d}:00", reply_markup=kb)

    if context.user_data.pop('setting_week', False):
        if not re.fullmatch(r'\d+', text.strip()):
            context.user_data['setting_week'] = True
            return await emsg.reply_text("⚠️ Введите целое число")
        from db.queries import set_budget
        set_budget(cid, week=int(text.strip()))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await emsg.reply_text(f"✅ Недельный бюджет: {int(text)}", reply_markup=kb)

    if context.user_data.pop('setting_month', False):
        if not re.fullmatch(r'\d+', text.strip()):
            context.user_data['setting_month'] = True
            return await emsg.reply_text("⚠️ Введите целое число")
        from db.queries import set_budget
        set_budget(cid, month=int(text.strip()))
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await emsg.reply_text(f"✅ Месячный бюджет: {int(text)}", reply_markup=kb)

    if context.user_data.get('adding_category'):
        p = context.user_data.get('pending', {})
        new_cat = (text or "").strip()
        if not new_cat:
            return await emsg.reply_text("⚠️ Введите название категории")
        context.user_data.pop('adding_category', None)
        typ = p.get('type') or 'Расходы'
        merch = p.get('merch', 'операция')
        amt = p.get('amt', 0)
        dt  = p.get('time', datetime.now())
        note = p.get('note')
        from db.queries import upsert_user_alias
        upsert_user_alias(cid, merch, typ, new_cat)
        bump_global_popularity(merch, typ, new_cat, 1)
        context.user_data['batch_item_text'] = text
        return await record_operation(new_cat, amt, dt, typ, update, context, note)

    if context.user_data.pop('await_amount', False):
        src_curr = detect_currency_token(text or "")
        m = list(re.finditer(r'\d+(?:[ \.,]\d{3})*', text or ""))
        if not m:
            context.user_data['await_amount'] = True
            return await emsg.reply_text("⚠️ Введите сумму числом (например, 70 или 70 000)")
        amt_raw = int(re.sub(r'[ \.,]', '', m[-1].group(0)))
        p   = context.user_data.get('pending', {})
        merch = p.get('merch', 'операция'); dt = p.get('time', datetime.now())

        raw_text = text
        if not src_curr:
            src_curr = detect_currency_token(raw_text or "")
        amt_final, note = convert_amount_if_needed(cid, amt_raw, src_curr)

        alias = get_user_alias(cid, merch)
        if alias:
            typ, cat = alias
            context.user_data['batch_item_text'] = text
            try:
                insert_ml_observation(
                    user_id=cid,
                    chat_id=cid,
                    raw_text=text,
                    normalized_text=normalize_for_ml(text),
                    detected_type=typ,
                    action='fallback_direct_write',
                    chosen_category=cat,
                    chosen_type=typ,
                    meta={'source': 'telegram', 'flow': 'await_amount_alias_hit'},
                )
            except Exception:
                pass
            return await record_operation(cat, amt_final, dt, typ, update, context, note)

        op_type = 'Расходы'
        normalized = normalize_for_ml(text)
        top2, sugg_meta = get_top2_suggestions(cid, normalized, op_type)
        if len(top2) < 2:
            top2 = [{'cat': 'Продукты', 'score': 0.6}, {'cat': 'Другое', 'score': 0.4}]
        cat1, cat2 = top2[0]['cat'], top2[1]['cat']
        context.user_data['pending'] = {
            'merch': merch,
            'amt': amt_final,
            'time': dt,
            'type': op_type,
            'note': note,
            'ml_cat1': cat1,
            'ml_cat2': cat2,
            'ml_top2': top2,
        }
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=text,
                normalized_text=normalized,
                detected_type=op_type,
                action='suggest_shown',
                suggested_top2=top2,
                confidence_top1=top2[0].get('score'),
                meta={'source': 'baseline', 'stage': '2.2', 'merchant': merch, 'currency_detected': src_curr, 'flow': 'await_amount', 'suggest': sugg_meta},
            )
        except Exception:
            pass

        msg = await _safe_reply(
            emsg,
            f"Категория?\n➖ {amt_final} ₽ • {_md_escape(merch)}",
            reply_markup=ml_top2_kb(cat1, cat2),
        )
        context.user_data['suggest_msg_id'] = msg.message_id
        context.user_data['batch_item_text'] = text
        return

    try:
        # ─────────── батч (последовательно) ───────────
        items = parse_day_list(text)
        if items:
            if context.user_data.get('batch_active'):
                return await emsg.reply_text("⚠️ Введите новый список после завершения текущего.")
            if len(items) > BATCH_MAX:
                return await emsg.reply_text(
                    f"⚠️ Слишком длинный список: {len(items)} элементов. Разбейте на части (≤ {BATCH_MAX})."
                )
            return await _batch_start(update, context, items)

        # основной путь: одна строка
        return await _process_free_text(update, context, text)
    except Exception as e:
        log.exception("handle_text failed: user=%s text=%r err=%s", cid, text, e)
        try:
            insert_ml_observation(
                user_id=cid,
                chat_id=cid,
                raw_text=text,
                normalized_text=normalize_for_ml(text),
                detected_type='Расходы',
                action='parse_failed',
                meta={'source': 'telegram', 'error': str(e)[:200]},
            )
        except Exception:
            pass
        return await emsg.reply_text("Не получилось обработать сообщение. Попробуй ещё раз.")


async def handle_location(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    emsg = update.effective_message
    loc = update.message.location
    try:
        await emsg.reply_text("Спасибо! Обрабатываю…", reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass

    if not loc:
        return
    if not TimezoneFinder:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_tz')]])
        return await emsg.reply_text("⚠️ Автоопределение недоступно (нет timezonefinder). Выберите вручную.", reply_markup=kb)

    try:
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lng=loc.longitude, lat=loc.latitude)
        off = 180  # простой дефолт МСК
        update_user_field(cid, 'tz_offset_min', off)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_settings')]])
        return await emsg.reply_text(f"✅ Часовой пояс установлен (приблизительно {tz_name}, UTC{off//60:+d}). Можно поправить вручную.", reply_markup=kb)
    except Exception:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('◀️ Назад', callback_data='menu_tz')]])
        return await emsg.reply_text("⚠️ Не удалось определить. Выберите вручную.", reply_markup=kb)
