# tools/patch_op_edit_router_v3.py — add ✏️ Изменить flow & make "Доходы" work
import re, time, pathlib

def backup(path):
    p = pathlib.Path(path)
    bak = p.with_suffix(p.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
    bak.write_text(p.read_text(encoding='utf-8'), encoding='utf-8')
    return bak.name

def patch_callbacks(path):
    p = pathlib.Path(path)
    s = p.read_text(encoding='utf-8')
    bak = backup(path)
    changed = 0

    # 1) add _op_edit_router() if missing
    if "_op_edit_router(" not in s:
        fn = r"""
async def _op_edit_router(update, context: ContextTypes.DEFAULT_TYPE):
    \"\"\"Inline submenu for editing the *last* recorded operation.

    States:
      - 'op_edit':     show submenu [Изменить категорию] [Назад]
      - 'op_edit_cat': open category picker (prompt_category_menu) in *edit mode*
      - 'op_edit_back': restore original tri-buttons
    \"\"\"
    q = update.callback_query
    data = getattr(q, 'data', None)
    cid  = getattr(getattr(q, 'message', None), 'chat', None).id if getattr(q, 'message', None) else None

    last_op = context.user_data.get('last_op', None)

    # 1) Entry: show the small submenu
    if data == 'op_edit':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton('📂 Изменить категорию', callback_data='op_edit_cat')],
            [InlineKeyboardButton('◀️ Назад', callback_data='op_edit_back')],
        ])
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            await q.message.reply_text('Что изменить?', reply_markup=kb)
        return

    # 2) Back: restore original tri-buttons
    if data == 'op_edit_back':
        typ = (last_op or {}).get('typ', 'Расходы')
        if typ == 'Расходы':
            second = InlineKeyboardButton('💰 Остаток', callback_data='status')
        elif typ == 'Доходы':
            second = InlineKeyboardButton('💵 Доходы', callback_data='income_status')
        elif typ == 'Инвестиции':
            second = InlineKeyboardButton('📊 Инвестиции (месяц)', callback_data='inv_status')
        else:
            cat = (last_op or {}).get('cat', 'Цель')
            second = InlineKeyboardButton('🎯 Прогресс цели', callback_data=f'goal_status|{cat}')
        third = InlineKeyboardButton('✏️ Изменить', callback_data='op_edit')
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('🗑️ Удалить', callback_data='del_last'), second, third]])
        try:
            await q.edit_message_reply_markup(reply_markup=kb)
        except Exception:
            await q.message.reply_text('Готово.', reply_markup=kb)
        return

    # 3) Change category: turn on edit_mode and reuse prompt_category_menu
    if data == 'op_edit_cat':
        if last_op:
            context.user_data['pending'] = {
                'type': last_op.get('typ', 'Расходы'),
                'merch': last_op.get('cat', 'операция'),
                'amt': last_op.get('amt', 0),
                'time': last_op.get('dt'),
                'note': last_op.get('note'),
            }
        context.user_data['edit_mode'] = True
        return await prompt_category_menu(update, context)
"""
        s = s.rstrip() + "\n\n" + fn
        changed += 1

    # 2) extend the 'use_cat|' branch to support edit_mode
    pat = re.compile(r"(?ms)^(\s*)if\s+data\.startswith\('use_cat\|'\):\s*\n(.*?)(?=^\s*if\s+data|\Z)")
    m = pat.search(s)
    if m and "edit_mode" not in m.group(0):
        indent = m.group(1)
        body   = m.group(2)
        body = re.sub(
            r"(\n\s*cat\s*=.*?\n\s*p\s*=\s*context\.user_data\.pop\('pending'.*?\)\n)",
            r"\1" + indent + "    # EDIT MODE: change last record in-place\n"
                 + indent + "    if context.user_data.pop('edit_mode', False):\n"
                 + indent + "        last_op = context.user_data.get('last_op', {})\n"
                 + indent + "        from db.queries import delete_last_operation\n"
                 + indent + "        try:\n"
                 + indent + "            delete_last_operation(cid)\n"
                 + indent + "        except Exception:\n"
                 + indent + "            pass\n"
                 + indent + "        amt = last_op.get('amt', p.get('amt', 0))\n"
                 + indent + "        dt  = last_op.get('dt',  None) or datetime.now()\n"
                 + indent + "        typ = last_op.get('typ', p.get('type') or 'Расходы')\n"
                 + indent + "        note= last_op.get('note', p.get('note'))\n"
                 + indent + "        from services.records import record_operation\n"
                 + indent + "        return await record_operation(cat, amt, dt, typ, update, context, note)\n"
            ,
            body, count=1, flags=re.S
        )
        s = s[:m.start(2)] + body + s[m.end(2):]
        changed += 1

    if changed:
        p.write_text(s, encoding='utf-8')
    return changed, bak

def patch_records(path):
    p = pathlib.Path(path)
    s = p.read_text(encoding='utf-8')
    bak = backup(path)
    changed = 0

    # rename income label (idempotent)
    if "💵 Доходы (месяц)" in s:
        s = s.replace("💵 Доходы (месяц)", "💵 Доходы")
        changed += 1

    # ensure we store last_op in record_operation()
    rec_pat = re.compile(r"(?ms)^async\s+def\s+record_operation\([^\)]*\):\s*\n(.*?)(?=^\S)", re.M)
    m = rec_pat.search(s)
    if m and "context.user_data['last_op']" not in m.group(1):
        body = m.group(1)
        body = re.sub(
            r"(insert_operation\([^\n]+\)\s*\n)",
            r"\1\n        # remember last operation for edit flow\n"
            r"        context.user_data['last_op'] = {\n"
            r"            'typ': typ, 'cat': cat, 'amt': amt, 'dt': dt, 'note': note,\n"
            r"        }\n",
            body, count=1
        )
        s = s[:m.start(1)] + body + s[m.end(1):]
        changed += 1

    if changed:
        p.write_text(s, encoding='utf-8')
    return changed, bak

if __name__ == "__main__":
    cb_path = "/root/bot_finuchet/routers/callbacks.py"
    rec_path= "/root/bot_finuchet/services/records.py"
    ch1, b1 = patch_callbacks(cb_path)
    ch2, b2 = patch_records(rec_path)
    print(f"[callbacks.py] changes={ch1}, backup={b1}")
    print(f"[records.py]   changes={ch2}, backup={b2}")
