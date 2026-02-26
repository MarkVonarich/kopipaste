#!/usr/bin/env python3
import re
import shutil
from datetime import datetime
from pathlib import Path

BASE = Path("/root/bot_finuchet")

FILES = {
    "db_queries": BASE / "db" / "queries.py",
    "records": BASE / "services" / "records.py",
    "helpers": BASE / "routers" / "helpers.py",
    "messages": BASE / "routers" / "messages.py",
    "callbacks": BASE / "routers" / "callbacks.py",
}

TS = datetime.now().strftime("%Y%m%d-%H%M%S")


def backup(p: Path):
    b = p.with_suffix(p.suffix + f".bak.{TS}")
    shutil.copy2(p, b)
    return b


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, s: str):
    p.write_text(s, encoding="utf-8")


def ensure_import_json(text: str) -> str:
    if re.search(r"^import json$", text, flags=re.M):
        return text
    # вставляем после typing/import блока (мягко)
    m = re.search(r"^from typing import .*$", text, flags=re.M)
    if not m:
        return "import json\n" + text
    insert_at = m.end()
    return text[:insert_at] + "\nimport json" + text[insert_at:]


def patch_db_queries(text: str) -> str:
    # bump version header if present
    text = re.sub(r"^# db/queries\.py — v.*$", "# db/queries.py — v2026.01.25-02", text, flags=re.M)
    text = re.sub(r'^__version__\s*=\s*".*"$', '__version__ = "2026.01.25-02"', text, flags=re.M)

    text = ensure_import_json(text)

    if "action_tokens (pending_op)" in text:
        return text

    block = r'''
# ---------------------------
# action_tokens (pending_op)
# ---------------------------

def create_action_token(user_id: int, chat_id: int, payload: dict) -> int:
    """Create new pending token and return token_id."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO public.action_tokens (user_id, chat_id, payload, status)
                VALUES (%s, %s, %s::jsonb, 'pending')
                RETURNING id
                """,
                (user_id, chat_id, json.dumps(payload, ensure_ascii=False)),
            )
            token_id = cur.fetchone()[0]
        conn.commit()
        return int(token_id)
    finally:
        conn.close()


def get_action_token(token_id: int):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, user_id, chat_id, payload, status, created_at, used_at, op_id
                  FROM public.action_tokens
                 WHERE id=%s
                 LIMIT 1
                """,
                (token_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            payload = row[3] if isinstance(row[3], dict) else dict(row[3] or {})
            return {
                "id": int(row[0]),
                "user_id": int(row[1]),
                "chat_id": int(row[2]),
                "payload": payload,
                "status": row[4],
                "created_at": row[5],
                "used_at": row[6],
                "op_id": row[7],
            }
    finally:
        conn.close()


def merge_action_token_payload(token_id: int, patch: dict) -> bool:
    """Merge patch into payload (jsonb || patch). Returns True if updated."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.action_tokens
                   SET payload = payload || %s::jsonb
                 WHERE id=%s AND status='pending'
                """,
                (json.dumps(patch, ensure_ascii=False), token_id),
            )
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        conn.close()


def mark_action_token_used(token_id: int, op_id=None) -> dict:
    """
    Idempotently mark token as used.
    Returns: {"changed": bool, "status": str, "op_id": Optional[int]}
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.action_tokens
                   SET status='used',
                       used_at=now(),
                       op_id=COALESCE(%s, op_id)
                 WHERE id=%s AND status='pending'
                 RETURNING op_id
                """,
                (op_id, token_id),
            )
            row = cur.fetchone()
            if row:
                conn.commit()
                return {"changed": True, "status": "used", "op_id": row[0]}

            cur.execute("SELECT status, op_id FROM public.action_tokens WHERE id=%s", (token_id,))
            row2 = cur.fetchone()
            conn.commit()
            if not row2:
                return {"changed": False, "status": "missing", "op_id": None}
            return {"changed": False, "status": row2[0], "op_id": row2[1]}
    finally:
        conn.close()


def find_latest_pending_token(chat_id: int, stage: str, ttl_minutes: int = 10):
    """Find latest pending token for chat by payload.stage within TTL."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id
                  FROM public.action_tokens
                 WHERE chat_id=%s
                   AND status='pending'
                   AND created_at >= now() - (%s || ' minutes')::interval
                   AND payload->>'stage' = %s
                 ORDER BY id DESC
                 LIMIT 1
                """,
                (chat_id, ttl_minutes, stage),
            )
            row = cur.fetchone()
            return int(row[0]) if row else None
    finally:
        conn.close()


def cleanup_action_tokens(ttl_minutes: int = 10, hard_delete_days: int = 7) -> dict:
    """
    1) Mark pending tokens older than TTL as expired.
    2) Delete used/expired tokens older than hard_delete_days.
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE public.action_tokens
                   SET status='expired',
                       used_at=now()
                 WHERE status='pending'
                   AND created_at < now() - (%s || ' minutes')::interval
                """,
                (ttl_minutes,),
            )
            expired = cur.rowcount

            cur.execute(
                """
                DELETE FROM public.action_tokens
                 WHERE status IN ('used','expired')
                   AND created_at < now() - (%s || ' days')::interval
                """,
                (hard_delete_days,),
            )
            deleted = cur.rowcount

        conn.commit()
        return {"expired": int(expired), "deleted": int(deleted)}
    finally:
        conn.close()
'''
    return text.rstrip() + "\n\n" + block.strip() + "\n"


def patch_records(text: str) -> str:
    text = re.sub(r"^# services/records\.py — v.*$", "# services/records.py — v2026.01.25-02", text, flags=re.M)
    text = re.sub(r'^__version__\s*=\s*".*"$', '__version__ = "2026.01.25-02"', text, flags=re.M)

    # record_operation считаем последней функцией — заменяем хвост
    m = re.search(r"^async def record_operation\(", text, flags=re.M)
    if not m:
        raise RuntimeError("record_operation not found in services/records.py")

    prefix = text[:m.start()].rstrip()

    new_tail = r'''
async def record_operation(
    category: str,
    amount: int,
    dt,
    op_type: str,
    update,
    context,
    note=None,
    raw_text=None,
):
    """
    Финальная запись операции (после выбора типа/категории).
    Возвращает op_id (для идемпотентности через action_tokens).
    """
    try:
        from datetime import datetime, date as _date

        chat_id = update.effective_chat.id
        dt = dt or context.user_data.get("dt")

        if isinstance(dt, _date) and not isinstance(dt, datetime):
            dt = datetime.combine(dt, datetime.min.time())

        op_date = dt.date() if dt else datetime.now().date()
        comment = (note or "").strip() or "From Telegram"
        cat = (category or "").strip()

        op_id = insert_operation(chat_id, op_type, cat, int(amount), comment, op_date)

        # обновим raw_text (если колонка есть)
        try:
            rt = raw_text
            if rt is None:
                em = update.effective_message
                rt = getattr(em, "text", None) or getattr(em, "caption", None)
            if rt is not None:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute(
                    "UPDATE public.operations SET raw_text=%s WHERE id=%s AND chat_id=%s",
                    (rt, op_id, chat_id),
                )
                conn.commit()
                conn.close()
        except Exception:
            pass

        # bump popularity for global alias suggestions
        try:
            bump_global_popularity(cat)
        except Exception:
            pass

        try:
            text = f"✅ Записано: *{_md_escape(cat)}* — *{amount}*"
            if comment and comment != "From Telegram":
                text += f"\\n📝 {_md_escape(comment)}"
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
        except Exception:
            pass

        return int(op_id)

    except Exception as e:
        log.exception("record_operation failed: %s", e)
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Не смог записать. Попробуй ещё раз.")
        except Exception:
            pass
        return 0
'''
    return prefix + "\n\n" + new_tail.strip() + "\n"


def patch_helpers(text: str) -> str:
    # перезапишем helpers целиком (он небольшой) — надёжнее, чем regex по кускам
    return r'''# routers/helpers.py — v2026.01.25-02
__version__ = "2026.01.25-02"

from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def _kb(rows):
    return InlineKeyboardMarkup(rows)


def prompt_type_menu(chat_id, merch, amt, dt, update, context, token_id=None):
    """
    Меню выбора типа операции.
    callback_data:
      - legacy: type|Расходы
      - new:    type|<token_id>|Расходы
    """
    when = dt.strftime("%d.%m %H:%M") if isinstance(dt, datetime) else ""
    head = f"🧾 *{merch}* — *{amt}*"
    if when:
        head += f" ({when})"

    def cd(typ: str):
        return f"type|{token_id}|{typ}" if token_id else f"type|{typ}"

    rows = [
        [InlineKeyboardButton("Расходы", callback_data=cd("Расходы"))],
        [InlineKeyboardButton("Доходы", callback_data=cd("Доходы"))],
    ]
    return update.message.reply_text(head + "\nВыбери тип:", reply_markup=_kb(rows), parse_mode="Markdown")


def prompt_category_menu(chat_id, token_id, typ, merch, amt, dt, note, categories, update, context):
    """
    Меню выбора категории.
    callback_data:
      - legacy: use_cat|Категория  / add_cat
      - new:    use_cat|<token_id>|Категория  / add_cat|<token_id>
    """
    def cd_cat(cat: str):
        return f"use_cat|{token_id}|{cat}" if token_id else f"use_cat|{cat}"

    def cd_add():
        return f"add_cat|{token_id}" if token_id else "add_cat"

    rows = [[InlineKeyboardButton(c, callback_data=cd_cat(c))] for c in categories]
    rows.append([InlineKeyboardButton("➕ Новая категория", callback_data=cd_add())])

    head = f"{typ}: *{merch}* — *{amt}*"
    if note:
        head += f"\n📝 {note}"

    return update.callback_query.edit_message_text(head + "\nВыбери категорию:", reply_markup=_kb(rows), parse_mode="Markdown")
'''


def patch_messages(text: str) -> str:
    # 1) добавим импорты токенов, если нет
    if "create_action_token" not in text:
        text = re.sub(
            r"(from routers\.helpers import prompt_type_menu, prompt_category_menu\n)",
            r"\1from db.queries import create_action_token, find_latest_pending_token, get_action_token, merge_action_token_payload, mark_action_token_used\n",
            text,
            flags=re.M,
        )

    # 2) вставим обработку "await_new_category" токена в начале handle_text
    if "await_new_category" not in text:
        text = re.sub(
            r"(chat_id\s*=\s*update\.effective_chat\.id\s*\n)",
            r"\1"
            r"    # Week2: если ждём ввод новой категории (после кнопки), то следующий текст — это категория\n"
            r"    try:\n"
            r"        tok_id = find_latest_pending_token(chat_id, stage='await_new_category', ttl_minutes=10)\n"
            r"        if tok_id and not any(ch.isdigit() for ch in text):\n"
            r"            tok = get_action_token(tok_id)\n"
            r"            if tok and tok.get('status') == 'pending':\n"
            r"                p = tok.get('payload') or {}\n"
            r"                merge_action_token_payload(tok_id, {'category': text, 'stage': 'ready'})\n"
            r"                # финализируем запись\n"
            r"                from services.records import record_operation\n"
            r"                dt = context.user_data.get('dt')\n"
            r"                amt = int(p.get('amt', 0))\n"
            r"                typ = p.get('type')\n"
            r"                note = p.get('note')\n"
            r"                raw_text = p.get('raw_text')\n"
            r"                op_id = await record_operation(text, amt, dt, typ, update, context, note, raw_text=raw_text)\n"
            r"                mark_action_token_used(tok_id, op_id=op_id)\n"
            r"                return\n"
            r"    except Exception:\n"
            r"        pass\n",
            text,
            flags=re.M,
        )

    # 3) при создании pending — создаём token + прокидываем token_id в меню типа
    # ищем место где context.user_data['pending'] = p и сразу после вызывается prompt_type_menu(...)
    text = re.sub(
        r"(context\.user_data\['pending'\]\s*=\s*p\s*\n)(\s*await prompt_type_menu\(([^)]*)\)\s*\n)",
        r"\1"
        r"    # Week2: сохраняем pending_op в action_tokens (TTL + идемпотентность)\n"
        r"    token_payload = dict(p)\n"
        r"    token_payload.update({'stage': 'need_type', 'raw_text': update.message.text})\n"
        r"    token_id = create_action_token(update.effective_user.id, chat_id, token_payload)\n"
        r"    context.user_data['pending_token_id'] = token_id\n"
        r"\2".replace("prompt_type_menu(", "prompt_type_menu(").replace(")", ", token_id=token_id)"),
        text,
        flags=re.M,
    )

    # если regex выше не сработал (из-за форматирования) — второй проход: заменим конкретный вызов prompt_type_menu без token_id
    text = re.sub(
        r"await prompt_type_menu\((chat_id,\s*merch,\s*amt,\s*dt,\s*update,\s*context)\)",
        r"await prompt_type_menu(\1, token_id=context.user_data.get('pending_token_id'))",
        text,
        flags=re.M,
    )

    return text


def patch_callbacks(text: str) -> str:
    # Вставим импорты токенов рядом с record_operation импортом, если нет
    if "get_action_token" not in text:
        text = re.sub(
            r"(from services\.records import record_operation\n)",
            r"\1from db.queries import get_action_token, merge_action_token_payload, mark_action_token_used\n",
            text,
            flags=re.M,
        )

    # 1) type| блок
    start = "if data.startswith('type|'):"
    end = "elif data.startswith('use_cat|'):"
    a = text.find(start)
    b = text.find(end, a + 1)
    if a == -1 or b == -1:
        raise RuntimeError("Cannot find type| block markers in callbacks.py")

    new_type_block = r"""if data.startswith('type|'):
        parts = data.split('|')
        token_id = None
        if len(parts) >= 3 and parts[1].isdigit():
            token_id = int(parts[1])
            typ = parts[2]
        else:
            typ = parts[1] if len(parts) > 1 else None

        # старая логика оставляем как fallback
        p = context.user_data.get('pending') or {}

        if token_id:
            tok = get_action_token(token_id)
            if not tok or tok.get('status') != 'pending':
                await query.answer("Сессия устарела. Пришли запись заново.", show_alert=True)
                return
            merge_action_token_payload(token_id, {'type': typ, 'stage': 'need_category'})
            p = tok.get('payload') or {}
            p['type'] = typ
            context.user_data['pending_token_id'] = token_id

        p['type'] = typ
        context.user_data['pending'] = p

        categories = context.user_data.get('categories') or []
        # если categories не подгружены — используем дефолтный список из кэша (как было)
        if not categories:
            categories = context.user_data.get('cat_list') or context.user_data.get('CATEGORIES') or []

        await prompt_category_menu(
            chat_id=chat_id,
            token_id=token_id,
            typ=typ,
            merch=p.get('merch'),
            amt=p.get('amt'),
            dt=context.user_data.get('dt'),
            note=p.get('note'),
            categories=categories,
            update=update,
            context=context,
        )
        return
"""
    text = text[:a] + new_type_block + text[b:]

    # 2) use_cat| блок
    start = "elif data.startswith('use_cat|'):"
    end = "elif data == 'add_cat':"
    a = text.find(start)
    b = text.find(end, a + 1)
    if a == -1 or b == -1:
        raise RuntimeError("Cannot find use_cat| block markers in callbacks.py")

    new_use_cat_block = r"""elif data.startswith('use_cat|'):
        if cl_mode:
            # лимиты категорий — оставляем legacy формат
            cat = data.split('|', 1)[1]
            context.user_data['cl_mode'] = False
            context.user_data['cl_step'] = 'limit_amount'
            context.user_data['cl_category'] = cat
            await query.edit_message_text(f"Ок, категория: {cat}. Теперь введи лимит (число).")
            return

        parts = data.split('|')
        token_id = None
        if len(parts) >= 3 and parts[1].isdigit():
            token_id = int(parts[1])
            cat = parts[2]
        else:
            cat = parts[1] if len(parts) > 1 else None

        p = context.user_data.get('pending') or {}
        if token_id:
            tok = get_action_token(token_id)
            if not tok:
                await query.answer("Сессия устарела. Пришли запись заново.", show_alert=True)
                return
            if tok.get('status') != 'pending':
                # идемпотентность: повторный тап
                await query.answer("Уже записано ✅", show_alert=True)
                return
            p = tok.get('payload') or {}
            context.user_data['pending_token_id'] = token_id

        typ = p.get('type')
        if not typ:
            await query.answer("Сначала выбери тип.", show_alert=True)
            return

        amt = int(p.get('amt', 0))
        dt = context.user_data.get('dt')
        note = p.get('note')
        raw_text = p.get('raw_text')

        op_id = await record_operation(cat, amt, dt, typ, update, context, note, raw_text=raw_text)

        if token_id:
            mark_action_token_used(token_id, op_id=op_id)

        context.user_data.pop('pending', None)
        context.user_data.pop('pending_token_id', None)
        return
"""
    text = text[:a] + new_use_cat_block + text[b:]

    # 3) add_cat блок
    start = "elif data == 'add_cat':"
    end = "elif data.startswith('op_edit|'):"
    a = text.find(start)
    b = text.find(end, a + 1)
    if a == -1 or b == -1:
        # если структура другая — просто заменим условие на startswith и выходим
        text = text.replace("elif data == 'add_cat':", "elif data == 'add_cat' or data.startswith('add_cat|'):")
        return text

    new_add_cat_block = r"""elif data == 'add_cat' or data.startswith('add_cat|'):
        token_id = None
        if data.startswith('add_cat|'):
            parts = data.split('|')
            if len(parts) >= 2 and parts[1].isdigit():
                token_id = int(parts[1])

        if token_id:
            merge_action_token_payload(token_id, {'stage': 'await_new_category'})
            context.user_data['pending_token_id'] = token_id

        context.user_data['adding_category'] = True
        await query.edit_message_text("Введите новую категорию (только название, без суммы):")
        return
"""
    text = text[:a] + new_add_cat_block + text[b:]
    return text


def main():
    for k, p in FILES.items():
        if not p.exists():
            raise SystemExit(f"File not found: {p}")

    print(f"==> Backups TS={TS}")
    for k, p in FILES.items():
        b = backup(p)
        print(f"  {p} -> {b}")

    # db/queries.py
    q = read(FILES["db_queries"])
    q2 = patch_db_queries(q)
    write(FILES["db_queries"], q2)
    print("OK: db/queries.py patched")

    # services/records.py
    r = read(FILES["records"])
    r2 = patch_records(r)
    write(FILES["records"], r2)
    print("OK: services/records.py patched")

    # routers/helpers.py (перезапись)
    h = read(FILES["helpers"])
    h2 = patch_helpers(h)
    write(FILES["helpers"], h2)
    print("OK: routers/helpers.py overwritten")

    # routers/messages.py
    m = read(FILES["messages"])
    m2 = patch_messages(m)
    write(FILES["messages"], m2)
    print("OK: routers/messages.py patched")

    # routers/callbacks.py
    c = read(FILES["callbacks"])
    c2 = patch_callbacks(c)
    write(FILES["callbacks"], c2)
    print("OK: routers/callbacks.py patched")

    print("DONE. Restart finuchet service and test Week2 flow.")


if __name__ == "__main__":
    main()
