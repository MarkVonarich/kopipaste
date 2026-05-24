
# routers/messages.py — v2025.08.26-batch-05 (effective_message everywhere)
__version__ = "2025.08.26-batch-05"

import re
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from telegram import ReplyKeyboardRemove
from telegram.ext import ContextTypes
from services.currency import detect_currency_token, convert_amount_if_needed
from services.records import get_user_alias, record_operation
from routers.helpers import prompt_type_menu
from ui.keyboards import ml_top2_kb
from utils.parsing import parse_user_input, split_wo_date, parse_day_list
from utils.text import norm_text
from utils.spoken_numbers import normalize_spoken_money_ru
from db.queries import update_user_field, insert_ml_observation, update_limit_amount, get_limit_by_key, record_category_confirmation
from db.queries import update_last_operation_fields, get_last_operation, reminder_insert, reminder_update
from services.ml_prep import normalize_for_ml, normalize_alias_text
from services.ml_suggest import get_top2_suggestions
from services.receipt_parser import parse_receipt_image
from settings import VOICE_INPUT_ENABLED, VOICE_TRANSCRIBE_PROVIDER, VOICE_TRANSCRIBE_MODEL, VOICE_MAX_SECONDS
import logging

log = logging.getLogger(__name__)
try:
    from timezonefinder import TimezoneFinder
except Exception:
    TimezoneFinder = None

BATCH_MAX = 25  # ограничение длины списка на один ввод


def _md_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`")




def _parse_amount_input(text: str) -> int | None:
    t = (text or '').strip().replace(' ', '').replace(',', '.')
    m = re.search(r"\d+(?:\.\d+)?", t)
    if not m:
        return None
    try:
        val = float(m.group(0))
    except Exception:
        return None
    return int(val)


def _parse_budget_amount(text: str) -> int | None:
    t = (text or '').strip().replace(' ', '').replace(',', '')
    if not t.isdigit():
        return None
    return int(t)


def _parse_reminder_title_amount(text: str) -> tuple[str, int] | None:
    src = (text or '').strip()
    if not src:
        return None
    m = list(re.finditer(r"(\d+(?:[ \t]\d{3})*(?:[.,]\d+)?)", src))
    if not m:
        return None
    last = m[-1]
    amt_raw = last.group(1).replace(' ', '').replace(',', '.')
    try:
        amt = int(float(amt_raw))
    except Exception:
        return None
    title = (src[:last.start()] + src[last.end():]).strip()
    if not title:
        return None
    if amt <= 0 or amt >= 1_000_000_000:
        return None
    return norm_text(title), amt


def _parse_flexible_date(text: str):
    t = (text or '').strip().lower()
    if t == 'сегодня':
        return datetime.now().date()
    if t == 'вчера':
        from datetime import timedelta
        return (datetime.now() - timedelta(days=1)).date()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', t):
            return datetime.strptime(t, '%Y-%m-%d').date()
        if re.fullmatch(r'\d{1,2}\.\d{1,2}\.\d{4}', t):
            return datetime.strptime(t, '%d.%m.%Y').date()
        if re.fullmatch(r'\d{1,2}\.\d{1,2}', t):
            return datetime.strptime(f'{t}.{datetime.now().year}', '%d.%m.%Y').date()
    except Exception:
        return None
    return None

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
        'ml_source': sugg_meta.get('source', 'baseline'),
        'ml_model_version': sugg_meta.get('model_version'),
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
            meta={'source': sugg_meta.get('source', 'baseline'), 'stage': '2.3', 'merchant': merch, 'currency_detected': src_curr, 'suggest': sugg_meta, 'model_version': sugg_meta.get('model_version'), 'trained_at': sugg_meta.get('trained_at')},
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


async def handle_photo(update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    emsg = update.effective_message
    try:
        file = None
        if update.message.photo:
            file = await update.message.photo[-1].get_file()
        elif update.message.document and (update.message.document.mime_type or '').startswith('image/'):
            file = await update.message.document.get_file()
        if not file:
            return await emsg.reply_text('Не удалось прочитать изображение. Попробуй отправить фото ещё раз.')

        image_bytes = await file.download_as_bytearray()
        result = parse_receipt_image(bytes(image_bytes), cid)
        if not result.configured:
            return await emsg.reply_text('Фото получил, но распознавание пока не настроено на сервере.')

        if result.warning in {'provider_error', 'image_too_large', 'openai_pkg_missing'}:
            return await emsg.reply_text('Не смог распознать фото. Попробуй ещё раз или пришли скрин крупнее.')

        if not result.candidates:
            return await emsg.reply_text('Не удалось уверенно извлечь операции с этого изображения. Попробуй фото покрупнее.')

        context.user_data['receipt_candidates'] = [
            {
                'amount': float(c.amount),
                'category': c.category,
                'type': c.op_type,
                'date': c.op_date.isoformat(),
                'merchant': c.merchant,
                'confidence': c.confidence,
                'raw_text': c.raw_text,
            }
            for c in result.candidates
        ]
        context.user_data['receipt_warning'] = result.warning
        lines = ['🧾 Нашёл операции:', '']
        for i, c in enumerate(result.candidates[:20], start=1):
            amount_i = int(round(float(c.amount)))
            major = c.category if c.op_type == 'Расходы' else 'Доходы'
            lines.append(f"{i}. {major} — {amount_i:,} ₽ — {c.merchant}".replace(',', ' '))
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('✅ Записать всё', callback_data='receipt_confirm_all')],
            [InlineKeyboardButton('✏️ Изменить', callback_data='receipt_review_one')],
            [InlineKeyboardButton('❌ Отмена', callback_data='receipt_cancel')],
        ])
        if result.warning:
            lines.append('\n⚠️ Я не уверен в части строк, лучше проверь перед записью.')
        await emsg.reply_text('\n'.join(lines), reply_markup=kb)
    except Exception as e:
        log.exception('receipt parse failed user=%s err=%s', cid, e)
        await emsg.reply_text('Не удалось обработать изображение. Попробуй ещё раз позже.')


async def handle_voice(update, context: ContextTypes.DEFAULT_TYPE):
    emsg = update.effective_message
    if not VOICE_INPUT_ENABLED:
        return await emsg.reply_text('Голосовой ввод сейчас выключен.')
    if VOICE_TRANSCRIBE_PROVIDER != 'openai':
        return await emsg.reply_text('Голос получил, но обработка аудио пока не настроена на сервере.')
    api_key = (os.getenv('OPENAI_API_KEY') or os.getenv('RECEIPT_OCR_API_KEY') or '').strip()
    if not api_key:
        return await emsg.reply_text('Голос получил, но обработка аудио пока не настроена на сервере.')
    msg = update.message
    media = msg.voice or msg.audio
    if not media:
        return await emsg.reply_text('Не удалось прочитать аудио. Попробуй ещё раз.')
    if (media.duration or 0) > VOICE_MAX_SECONDS:
        return await emsg.reply_text(f'Слишком длинное аудио. Максимум {VOICE_MAX_SECONDS} сек.')
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, timeout=30)
        with tempfile.TemporaryDirectory(prefix='fin_voice_') as td:
            src = os.path.join(td, 'in.oga')
            dst = os.path.join(td, 'out.mp3')
            f = await media.get_file()
            await f.download_to_drive(custom_path=src)
            audio_path = src
            if shutil.which('ffmpeg'):
                proc = subprocess.run(['ffmpeg', '-y', '-i', src, '-ac', '1', '-ar', '16000', dst], capture_output=True)
                if proc.returncode == 0:
                    audio_path = dst
                else:
                    log.warning('voice transcode failed user=%s', update.effective_chat.id)
            else:
                log.warning('voice: ffmpeg missing user=%s', update.effective_chat.id)
            with open(audio_path, 'rb') as af:
                tr = client.audio.transcriptions.create(model=VOICE_TRANSCRIBE_MODEL, file=af)
            text = (getattr(tr, 'text', None) or '').strip()
        if not text:
            return await emsg.reply_text('Не расслышал. Попробуй сказать короче: кофе 250')
        log.info('voice_transcribe: ok user=%s', update.effective_chat.id)
        await emsg.reply_text(f'Распознал: {text[:180]}')
        normalized_text, changed = normalize_spoken_money_ru(text)
        log.info('voice_normalized_text: changed=%s user=%s', changed, update.effective_chat.id)
        try:
            parse_user_input(normalized_text)
            parse_ok = True
        except ValueError:
            parse_ok = False
        if not parse_ok:
            log.info('voice_to_text_flow: failed user=%s', update.effective_chat.id)
            return await emsg.reply_text(f'Но не понял сумму. Попробуй так: {normalized_text[:120] or "такси 750"}')
        await _process_free_text(update, context, normalized_text)
        log.info('voice_to_text_flow: ok user=%s', update.effective_chat.id)
        return
    except Exception as e:
        log.warning('voice transcribe failed user=%s reason=%s', update.effective_chat.id, type(e).__name__)
        return await emsg.reply_text('Не смог распознать голос. Попробуй ещё раз или напиши текстом.')


# ─────────────────────────────────────────────
# Основной хэндлер входящих сообщений
# ─────────────────────────────────────────────

async def handle_text(update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    cid  = update.effective_chat.id
    emsg = update.effective_message

    if context.user_data.get('lim_edit_amount'):
        st = context.user_data.get('lim_edit_amount') or {}
        amount = _parse_amount_input(text)
        if amount is None:
            return await emsg.reply_text('⚠️ Введите сумму числом, например: 25000')
        if amount <= 0 or amount >= 1_000_000_000:
            return await emsg.reply_text('⚠️ Сумма должна быть больше 0 и меньше 1 000 000 000')
        period = st.get('period')
        category = st.get('category')
        try:
            row = update_limit_amount(cid, period, category, amount)
        except Exception as e:
            log.exception('edit_amount failed user=%s period=%s cat=%s err=%s', cid, period, category, e)
            return await emsg.reply_text('Не смог сохранить, попробуй ещё раз.')
        context.user_data.pop('lim_edit_amount', None)
        if not row:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton('📌 Мои лимиты', callback_data='lim_list')]])
            return await emsg.reply_text('Лимит не найден или уже изменён.', reply_markup=kb)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('⬅️ К карточке', callback_data='lim_list')]])
        return await emsg.reply_text(f"✅ Сумма обновлена: {row['amount']} {row['currency']}", reply_markup=kb)

    if context.user_data.get('await_rem_title_amount'):
        log.info('reminder_wizard_text state=await_rem_title_amount user=%s', cid)
        parsed = _parse_reminder_title_amount(text)
        if not parsed:
            log.info('reminder_wizard_title_amount_parsed ok=false user=%s', cid)
            context.user_data['await_rem_title_amount'] = True
            return await emsg.reply_text('Не понял сумму. Напиши так: ChatGPT 1990')
        log.info('reminder_wizard_title_amount_parsed ok=true user=%s', cid)
        context.user_data.pop('await_rem_title_amount', None)
        merch, amt = parsed
        d = context.user_data.setdefault('rem_draft', {})
        d['title'] = merch
        d['amount'] = int(amt)
        d['category'] = 'Прочее' if d.get('rem_type') != 'Доходы' else 'Переводы'
        log.info('reminder_wizard_next_step=category user=%s', cid)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('Заведения', callback_data='rem_cat_zav'), InlineKeyboardButton('Продукты', callback_data='rem_cat_prod')],
            [InlineKeyboardButton('Транспорт', callback_data='rem_cat_tr'), InlineKeyboardButton('Подписки', callback_data='rem_cat_sub')],
            [InlineKeyboardButton('Прочее', callback_data='rem_cat_other')],
            [InlineKeyboardButton('✏️ Другая', callback_data='rem_cat_custom')],
            [InlineKeyboardButton('⬅️ Назад', callback_data='rem_add')],
        ])
        return await emsg.reply_text('Выбери категорию:', reply_markup=kb)

    if context.user_data.get('await_rem_edit'):
        st = context.user_data.pop('await_rem_edit')
        rid, field = int(st['rid']), st['field']
        log.info('reminder_wizard_text state=%s user=%s', field, cid)
        try:
            if rid == -1:
                d = context.user_data.setdefault('rem_draft', {})
                if field == 'category_draft':
                    d['category'] = norm_text(text)[:64] or 'Прочее'
                elif field == 'date_draft':
                    dd = _parse_flexible_date(text)
                    if not dd:
                        raise ValueError('date')
                    d['event_date'] = dd
                elif field == 'repeat_draft':
                    n = int((text or '').strip())
                    if n < 1 or n > 3650:
                        raise ValueError('repeat')
                    d['repeat_rule'] = 'custom_days'; d['repeat_interval_days'] = n
                elif field == 'notify_draft':
                    n = int((text or '').strip())
                    if n < 0 or n > 30:
                        raise ValueError('notify')
                    d['notify_days_before'] = n
                return await emsg.reply_text('✅ Сохранено в черновике. Продолжи через кнопки.')
            if field == 'amount':
                a = _parse_amount_input(text)
                if a is None or a <= 0 or a >= 1_000_000_000:
                    raise ValueError('amount')
                reminder_update(cid, rid, amount=int(a))
            elif field == 'category':
                reminder_update(cid, rid, category=norm_text(text)[:64] or 'Прочее')
            elif field == 'date':
                d = _parse_flexible_date(text)
                if not d:
                    raise ValueError('date')
                reminder_update(cid, rid, event_date=d)
            elif field == 'repeat':
                t = (text or '').strip().lower()
                if t.startswith('custom_days:'):
                    n = int(t.split(':', 1)[1]); reminder_update(cid, rid, repeat_rule='custom_days', repeat_interval_days=n)
                else:
                    reminder_update(cid, rid, repeat_rule=t, repeat_interval_days=None)
            elif field == 'notify':
                n = int((text or '').strip())
                if n < 0 or n > 30:
                    raise ValueError('notify')
                reminder_update(cid, rid, notify_days_before=n)
            elif field == 'type':
                v = 'Доходы' if 'доход' in (text or '').lower() else 'Расходы'
                reminder_update(cid, rid, rem_type=v)
        except Exception:
            context.user_data['await_rem_edit'] = st
            return await emsg.reply_text('⚠️ Некорректное значение, попробуй ещё раз.')
        return await emsg.reply_text('✅ Напоминание обновлено. Открой карточку через /reminders')

    if context.user_data.pop('await_op_edit_amount', False):
        amount = _parse_amount_input(text)
        if amount is None or amount <= 0 or amount >= 1_000_000_000:
            context.user_data['await_op_edit_amount'] = True
            return await emsg.reply_text('⚠️ Неверная сумма. Введите число >0 и <1 000 000 000')
        row = update_last_operation_fields(cid, amount=int(amount))
        if not row:
            return await emsg.reply_text('Не нашёл операцию для изменения.')
        return await emsg.reply_text('✅ Сумма обновлена.')

    if context.user_data.pop('await_op_edit_date', False):
        d = _parse_flexible_date(text)
        if not d:
            context.user_data['await_op_edit_date'] = True
            return await emsg.reply_text('⚠️ Не понял дату. Пример: 24.05.2026')
        row = update_last_operation_fields(cid, op_date=d)
        if not row:
            return await emsg.reply_text('Не нашёл операцию для изменения.')
        return await emsg.reply_text('✅ Дата обновлена.')

    if context.user_data.pop('await_op_edit_comment', False):
        row = update_last_operation_fields(cid, comment=(text or '').strip()[:200])
        if not row:
            return await emsg.reply_text('Не нашёл операцию для изменения.')
        return await emsg.reply_text('✅ Комментарий обновлён.')

    if context.user_data.get('await_export_start') or context.user_data.get('await_export_end'):
        d = _parse_flexible_date(text)
        if not d:
            return await emsg.reply_text('⚠️ Не понял дату. Формат: DD.MM.YYYY / DD.MM / YYYY-MM-DD')
        st = context.user_data.setdefault('export_state', {})
        if context.user_data.get('await_export_start'):
            st['from'] = d.isoformat()
            context.user_data.pop('await_export_start', None)
            return await emsg.reply_text('Дата начала сохранена. Выбери конец периода через /export.')
        st['to'] = d.isoformat()
        context.user_data.pop('await_export_end', None)
        return await emsg.reply_text('Дата конца сохранена. Выбери «Скачать XLSX» через /export.')

    if context.user_data.pop('await_receipt_edit_text', False):
        idx = int(context.user_data.get('receipt_edit_idx') or -1)
        cands = context.user_data.get('receipt_candidates') or []
        if idx < 0 or idx >= len(cands):
            return await emsg.reply_text('Не нашёл операцию для редактирования.')
        field = context.user_data.pop('receipt_edit_field', 'full')
        try:
            if field == 'amount':
                v = _parse_amount_input(text)
                if v is None or v <= 0:
                    raise ValueError('amount')
                cands[idx]['amount'] = int(v)
            elif field == 'comment':
                cands[idx]['merchant'] = norm_text(text.strip())[:64]
            elif field == 'date':
                _m, _a, dt, _src_curr = parse_user_input(f'x 1 {text}')
                cands[idx]['date'] = dt.date().isoformat()
            elif field == 'category':
                cands[idx]['category'] = norm_text(text.strip())[:32] or 'Прочее'
            else:
                merch_display, amt_raw, dt, _src_curr = parse_user_input(text)
                cands[idx]['amount'] = int(amt_raw)
                cands[idx]['merchant'] = norm_text(merch_display)
                cands[idx]['date'] = dt.date().isoformat()
                if re.search(r'зарплат|доход|перевод|пополн', text.lower()):
                    cands[idx]['type'] = 'Доходы'
            context.user_data['receipt_candidates'] = cands
            context.user_data['receipt_review_idx'] = idx
            context.user_data.pop('receipt_edit_idx', None)
            return await emsg.reply_text('✅ Операция обновлена. Нажми «Проверить» или «Записать всё».')
        except Exception:
            context.user_data['await_receipt_edit_text'] = True
            return await emsg.reply_text('Не понял исправление. Пример: столовая 392')

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

    if context.user_data.get('budget_add_period'):
        period = context.user_data.get('budget_add_period')
        amount = _parse_budget_amount(text)
        if amount is None:
            return await emsg.reply_text('⚠️ Введите сумму числом, например: 60000')
        if amount <= 0:
            return await emsg.reply_text('⚠️ Бюджет не может быть меньше 1 ₽')
        if amount >= 1_000_000_000:
            return await emsg.reply_text('⚠️ Слишком большой бюджет')
        from db.queries import get_user_budgets, set_budget
        wl, ml = get_user_budgets(cid)
        cur = ml if period == 'month' else wl
        if cur and cur > 0:
            context.user_data['budget_pending_amount'] = amount
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton('✅ Заменить', callback_data=f'bud_replace_confirm|{period}')],
                [InlineKeyboardButton('✏️ Ввести другую сумму', callback_data='bud_add')],
                [InlineKeyboardButton('⬅️ Назад', callback_data='settings_budgets')],
            ])
            return await emsg.reply_text(f"Бюджет на {'месяц' if period=='month' else 'неделю'} уже есть: {cur} ₽\n\nЗаменить его?", reply_markup=kb)
        if period == 'month':
            set_budget(cid, month=amount)
        else:
            set_budget(cid, week=amount)
        context.user_data.pop('budget_add_period', None)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('💰 К бюджетам', callback_data='settings_budgets')],
            [InlineKeyboardButton('➕ Добавить ещё', callback_data='bud_add')],
            [InlineKeyboardButton('⬅️ В настройки', callback_data='menu_settings')],
        ])
        return await emsg.reply_text(f"✅ Бюджет добавлен\n\n{'Месяц' if period=='month' else 'Неделя'} — {amount} ₽", reply_markup=kb)

    if context.user_data.get('budget_manual_period'):
        period = context.user_data.get('budget_manual_period')
        amount = _parse_budget_amount(text)
        if amount is None:
            return await emsg.reply_text('⚠️ Введите сумму числом, например: 60000')
        if amount <= 0:
            return await emsg.reply_text('⚠️ Бюджет не может быть меньше 1 ₽')
        if amount >= 1_000_000_000:
            return await emsg.reply_text('⚠️ Слишком большой бюджет')
        from db.queries import set_budget
        if period == 'month':
            set_budget(cid, month=amount)
        else:
            set_budget(cid, week=amount)
        context.user_data.pop('budget_manual_period', None)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('💰 К карточке', callback_data=f'bud_card|{period}')]])
        return await emsg.reply_text(f"✅ Бюджет обновлён: {amount} ₽", reply_markup=kb)

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
        alias_norm = normalize_alias_text(context.user_data.get('batch_item_text') or merch)
        record_category_confirmation(cid, context.user_data.get('batch_item_text') or merch, alias_norm, new_cat, typ, 'accept')
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
                meta={'source': sugg_meta.get('source', 'baseline'), 'stage': '2.3', 'merchant': merch, 'currency_detected': src_curr, 'flow': 'await_amount', 'suggest': sugg_meta, 'model_version': sugg_meta.get('model_version'), 'trained_at': sugg_meta.get('trained_at')},
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
