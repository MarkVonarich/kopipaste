from pathlib import Path

p = Path('routers/callbacks.py')
s = p.read_text(encoding='utf-8')

def ensure_top_import(line: str):
    global s
    if line in s:
        return
    # Вставим САМЫМ ВЕРХОМ, без отступов, отдельной строкой
    s = line + "\n" + s

# --- гарантируем импорты отдельными строками (не правим существующие) ---
ensure_top_import('from datetime import datetime, timedelta')
ensure_top_import('from db.database import get_conn')
ensure_top_import('from telegram import InlineKeyboardButton, InlineKeyboardMarkup')

# --- вставка блоков в callback_handler после "data = q.data" ---
if "def callback_handler" not in s:
    raise SystemExit("❌ Не нашёл def callback_handler в routers/callbacks.py")

marker = 'data = q.data'
pos = s.find(marker)
if pos == -1:
    raise SystemExit("❌ Не нашёл строку 'data = q.data' в routers/callbacks.py")

if "noop_today" in s and "noop_delete" in s and "noop_back" in s:
    print("ℹ️ Блоки noop_* уже есть — пропускаю")
else:
    insert_pos = pos + len(marker)
    ins = """
    # === NOOP (\"Без операций сегодня\") ===
    if data == 'noop_today':
        cid = update.effective_chat.id
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT COALESCE(tz_offset_min,180) FROM public.users WHERE user_id=%s", (cid,))
        row = cur.fetchone(); tz = int(row[0]) if row and row[0] is not None else 0
        local_today = (datetime.utcnow() + timedelta(minutes=tz)).date()
        try:
            cur.execute(
                "INSERT INTO public.operations (chat_id, user_id, op_date, type, category, amount, comment, raw_text) "
                "VALUES (%s,%s,%s,'noop','Без операций',0,'no-op day','noop_button') "
                "ON CONFLICT DO NOTHING",
                (cid, cid, local_today)
            )
            conn.commit()
        finally:
            cur.close(); conn.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Удалить', callback_data='noop_delete'),
                                    InlineKeyboardButton('Назад',   callback_data='noop_back')]])
        await q.edit_message_text('Отметил: *без операций сегодня*.', parse_mode='Markdown', reply_markup=kb)
        return

    if data == 'noop_delete':
        cid = update.effective_chat.id
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT COALESCE(tz_offset_min,180) FROM public.users WHERE user_id=%s", (cid,))
        row = cur.fetchone(); tz = int(row[0]) if row and row[0] is not None else 0
        local_today = (datetime.utcnow() + timedelta(minutes=tz)).date()
        cur.execute("DELETE FROM public.operations WHERE chat_id=%s AND op_date=%s AND type='noop'", (cid, local_today))
        conn.commit(); cur.close(); conn.close()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Без операций сегодня', callback_data='noop_today')]])
        await q.edit_message_text('Отметку удалил. Если передумаешь — нажми ниже.', reply_markup=kb)
        return

    if data == 'noop_back':
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('Без операций сегодня', callback_data='noop_today')]])
        await q.edit_message_text('Ок! Можешь отметить отсутствие операций позже.', reply_markup=kb)
        return
"""
    s = s[:insert_pos] + ins + s[insert_pos:]

p.write_text(s, encoding='utf-8')
print("✅ Patched routers/callbacks.py")
