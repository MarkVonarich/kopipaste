# tools/patch_inline_edit_buttons_v2.py — safely add "✏️ Изменить" flow and fix "Доходы" button
import re, time, pathlib, sys

def backup(p: pathlib.Path):
    bak = p.with_suffix(p.suffix + ".bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
    bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    return bak.name

def patch_records_py(path):
    p = pathlib.Path(path)
    src = p.read_text(encoding="utf-8")
    bak = backup(p)
    changed = 0

    # A) Переименуем подпись у доходов
    new_src = src.replace("💵 Доходы (месяц)", "💵 Доходы")
    if new_src != src:
        src = new_src; changed += 1

    # B) Добавим третью кнопку "✏️ Изменить" в клавиатуру после записи
    pat = re.compile(
        r"kb\s*=\s*InlineKeyboardMarkup\(\s*\[\s*\[\s*InlineKeyboardButton\('🗑️\s*Удалить',\s*callback_data='del_last'\s*\)\s*,\s*second\s*\]\s*\]\s*\)",
        re.DOTALL
    )
    if "callback_data='op_edit'" not in src and pat.search(src):
        src = pat.sub(
            "third = InlineKeyboardButton('✏️ Изменить', callback_data='op_edit')\n"
            "    kb = InlineKeyboardMarkup([[\n"
            "        InlineKeyboardButton('🗑️ Удалить', callback_data='del_last'),\n"
            "        second,\n"
            "        third\n"
            "    ]])",
            src
        )
        changed += 1

    if changed:
        p.write_text(src, encoding="utf-8")
    return changed, bak

def patch_queries_py(path):
    p = pathlib.Path(path)
    src = p.read_text(encoding="utf-8")
    bak = backup(p)
    changed = 0

    if "def get_last_operation(" not in src:
        src += """

def get_last_operation(user_id: int):
    \"\"\"Return last operation for user as dict with keys: id, op_date, type, category, amount.\"\"\"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, op_date, type, category, amount FROM operations WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return {"id": row[0], "op_date": row[1], "type": row[2], "category": row[3], "amount": row[4]}

def update_last_operation_category(user_id: int, new_category: str) -> bool:
    \"\"\"Update category of the last operation for user. Returns True if updated.\"\"\"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM operations WHERE user_id=%s ORDER BY id DESC LIMIT 1",
                (user_id,)
            )
            row = cur.fetchone()
            if not row:
                return False
            op_id = row[0]
            cur.execute(
                "UPDATE operations SET category=%s WHERE id=%s",
                (new_category, op_id)
            )
            return cur.rowcount > 0
"""
        changed += 1

    if changed:
        p.write_text(src, encoding="utf-8")
    return changed, bak

def patch_callbacks_py(path):
    p = pathlib.Path(path)
    src = p.read_text(encoding="utf-8")
    bak = backup(p)
    changed = 0

    # A) гарантируем импорты
    if "from db.queries import" in src and "get_last_operation" not in src:
        src = re.sub(r"(from\s+db\.queries\s+import\s+)([^\n]+)",
                     lambda m: m.group(1) + m.group(2).strip() + ", get_last_operation, update_last_operation_category",
                     src, count=1)
        changed += 1
    if "from services.records import" in src and "list_categories_for_type" not in src:
        src = re.sub(r"(from\s+services\.records\s+import\s+)([^\n]+)",
                     lambda m: m.group(1) + m.group(2).strip() + ", list_categories_for_type",
                     src, count=1)
        changed += 1

    # B) ранний роутер внутри callback_handler
    m = re.search(r"async\s+def\s+callback_handler\s*\(.*?\):", src)
    if not m:
        return 0, bak
    start = m.end()
    m2 = re.search(r"\n\s*if\s+data\s*==", src[start:])
    insert_at = start + (m2.start() if m2 else 0)
    router_snippet = """
    # --- op_edit inline router (injected) ---
    try:
        data = update.callback_query.data  # type: ignore[attr-defined]
    except Exception:
        data = None
    if data and data.startswith('op_edit'):
        return await _op_edit_router(update, context)
    # --- end op_edit inline router ---
"""
    if "_op_edit_router" not in src:
        src = src[:insert_at] + router_snippet + src[insert_at:]
        changed += 1

    # C) реализация роутера в конец файла
    if "_op_edit_router" not in src:
        src += """

async def _op_edit_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    \"\"\"Inline 'Изменить' submenu и выбор категории для *последней* операции.\"\"\"
    q = update.callback_query
    cid = update.effective_chat.id
    data = q.data

    # helper: вернуть базовую клавиатуру из 3-х кнопок под записью
    def _base_kb(last):
        typ = last.get("type")
        cat = last.get("category")
        if typ == 'Расходы':
            second = InlineKeyboardButton('💰 Остаток', callback_data='status')
        elif typ == 'Доходы':
            second = InlineKeyboardButton('💵 Доходы', callback_data='income_status')
        elif typ == 'Инвестиции':
            second = InlineKeyboardButton('📊 Инвестиции (месяц)', callback_data='inv_status')
        else:
            second = InlineKeyboardButton('🎯 Прогресс цели', callback_data=f'goal_status|{cat}')
        third = InlineKeyboardButton('✏️ Изменить', callback_data='op_edit')
        return InlineKeyboardMarkup([[InlineKeyboardButton('🗑️ Удалить', callback_data='del_last'), second, third]])

    last = get_last_operation(cid) or {}
    if data == 'op_edit':
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('📂 Изменить категорию', callback_data='op_edit_cat')],
                                   [InlineKeyboardButton('◀️ Назад', callback_data='op_edit_back')]])
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            await context.bot.edit_message_reply_markup(chat_id=cid, message_id=q.message.message_id, reply_markup=kb)
        return

    if data == 'op_edit_back':
        kb = _base_kb(last)
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            await context.bot.edit_message_reply_markup(chat_id=cid, message_id=q.message.message_id, reply_markup=kb)
        return

    if data == 'op_edit_cat':
        # Показываем категории текущего типа операции
        typ = last.get("type") or 'Расходы'
        cats = list_categories_for_type(typ)[:24]
        rows, row = [], []
        for c in cats:
            row.append(InlineKeyboardButton(c, callback_data=f'op_edit_cat_pick|{c}'))
            if len(row) == 3:
                rows.append(row); row = []
        if row: rows.append(row)
        rows.append([InlineKeyboardButton('◀️ Назад', callback_data='op_edit')])
        kb = InlineKeyboardMarkup(rows)
        await q.edit_message_reply_markup(reply_markup=kb)
        return

    if data.startswith('op_edit_cat_pick|'):
        new_cat = data.split('|',1)[1]
        ok = update_last_operation_category(cid, new_cat)
        await q.answer('Категория обновлена' if ok else 'Не удалось обновить категорию', show_alert=False)
        # Вернём базовую клавиатуру
        last = get_last_operation(cid) or {}
        kb = _base_kb(last)
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            await context.bot.edit_message_reply_markup(chat_id=cid, message_id=q.message.message_id, reply_markup=kb)
        return
"""
        changed += 1

    if changed:
        p.write_text(src, encoding="utf-8")
    return changed, bak

def main():
    root = pathlib.Path("/root/bot_finuchet")
    total = 0
    ch, bak = patch_records_py(root / "services" / "records.py")
    print(f"[records.py] changes={ch}, backup={bak}"); total += ch
    ch, bak = patch_queries_py(root / "db" / "queries.py")
    print(f"[queries.py] changes={ch}, backup={bak}"); total += ch
    ch, bak = patch_callbacks_py(root / "routers" / "callbacks.py")
    print(f"[callbacks.py] changes={ch}, backup={bak}"); total += ch
    if total == 0:
        print("No changes applied (already patched).")

if __name__ == "__main__":
    main()
