from __future__ import annotations
from collections import defaultdict
from datetime import date, timedelta
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

SOURCE_LABELS = {
    "ru": {
        "text": "Текст",
        "voice": "Голос",
        "ocr": "Фото / OCR",
        "reminder": "Напоминание",
        "import": "Импорт",
        "miniapp": "Telegram Mini App",
        "api": "API",
        "telegram": "Текст",
    },
    "en": {
        "text": "Text",
        "voice": "Voice",
        "ocr": "Photo / OCR",
        "reminder": "Reminder",
        "import": "Import",
        "miniapp": "Telegram Mini App",
        "api": "API",
        "telegram": "Text",
    },
}

FALLBACK_COMMENTS = {"from telegram", "telegram", "from group", "from image"}


def export_comment(value: str | None) -> str:
    text = (value or "").strip()
    return "" if text.casefold() in FALLBACK_COMMENTS else text


def export_source_label(source: str | None, locale: str = "ru") -> str:
    lang = "en" if (locale or "").startswith("en") else "ru"
    raw = (source or "text").strip().lower()
    return SOURCE_LABELS[lang].get(raw, raw or SOURCE_LABELS[lang]["text"])


def _auto_width(ws):
    for col in ws.columns:
        m = 0
        letter = col[0].column_letter
        for c in col:
            m = max(m, len(str(c.value or "")))
        ws.column_dimensions[letter].width = min(max(10, m + 2), 40)


def build_export_xlsx(path: str, rows: list[dict], dfrom: date, dto: date, locale: str = "ru"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Итоги"
    exp = [r for r in rows if r["type"] == "Расходы"]
    inc = [r for r in rows if r["type"] == "Доходы"]
    exp_sum = sum(int(r["amount"]) for r in exp)
    inc_sum = sum(int(r["amount"]) for r in inc)
    ws["A1"] = "КопиPaste — экспорт операций"
    ws["A2"] = f"Период: {dfrom.strftime('%d.%m.%Y')} — {dto.strftime('%d.%m.%Y')}"
    ws["A4"] = "Всего операций"; ws["B4"] = len(rows)
    ws["A5"] = "Расходы"; ws["B5"] = exp_sum
    ws["A6"] = "Доходы"; ws["B6"] = inc_sum
    ws["A7"] = "Баланс"; ws["B7"] = inc_sum - exp_sum
    ws["A9"] = "Топ категорий расходов:"
    grp_exp = defaultdict(int)
    for r in exp: grp_exp[r["category"]] += int(r["amount"])
    for i, (cat, sm) in enumerate(sorted(grp_exp.items(), key=lambda x: -x[1])[:10], start=10):
        ws[f"A{i}"] = cat; ws[f"B{i}"] = sm
    ws["D9"] = "Топ категорий доходов:"
    grp_inc = defaultdict(int)
    for r in inc: grp_inc[r["category"]] += int(r["amount"])
    for i, (cat, sm) in enumerate(sorted(grp_inc.items(), key=lambda x: -x[1])[:10], start=10):
        ws[f"D{i}"] = cat; ws[f"E{i}"] = sm

    ws2 = wb.create_sheet("Операции")
    hdr = ["Дата", "Тип", "Категория", "Сумма", "Комментарий", "Источник", "ID"]
    ws2.append(hdr)
    for r in sorted(rows, key=lambda x: (x["op_date"], x["id"])):
        ws2.append([r["op_date"], r["type"], r["category"], int(r["amount"]), export_comment(r.get("comment")), export_source_label(r.get("source"), locale), r["id"]])
    for c in ws2[1]:
        c.font = Font(bold=True); c.fill = PatternFill("solid", fgColor="D9E1F2")
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = f"A1:G{max(1, ws2.max_row)}"
    for i in range(2, ws2.max_row + 1): ws2[f"D{i}"].number_format = '# ##0 "₽"'

    ws3 = wb.create_sheet("По категориям")
    ws3.append(["Тип", "Категория", "Сумма", "Кол-во операций", "Доля от типа, %"])
    grp = defaultdict(lambda: {"sum": 0, "count": 0})
    type_totals = defaultdict(int)
    for r in rows:
        k = (r["type"], r["category"])
        grp[k]["sum"] += int(r["amount"]); grp[k]["count"] += 1
        type_totals[r["type"]] += int(r["amount"])
    for (tp, cat), st in sorted(grp.items(), key=lambda x: (x[0][0], -x[1]["sum"])):
        total = type_totals[tp] or 1
        ws3.append([tp, cat, st["sum"], st["count"], round(st["sum"] * 100.0 / total, 2)])

    ws4 = wb.create_sheet("По месяцам")
    ws4.append(["Месяц", "Расходы", "Доходы", "Баланс", "Кол-во операций"])
    mgrp = defaultdict(lambda: {"e": 0, "i": 0, "c": 0})
    for r in rows:
        m = r["op_date"].strftime("%Y-%m")
        if r["type"] == "Расходы": mgrp[m]["e"] += int(r["amount"])
        if r["type"] == "Доходы": mgrp[m]["i"] += int(r["amount"])
        mgrp[m]["c"] += 1
    for m, st in sorted(mgrp.items()):
        ws4.append([m, st["e"], st["i"], st["i"] - st["e"], st["c"]])

    ws5 = wb.create_sheet("По неделям")
    ws5.append(["Неделя", "Дата начала", "Дата конца", "Расходы", "Доходы", "Баланс", "Кол-во операций"])
    wgrp = defaultdict(lambda: {"e": 0, "i": 0, "c": 0, "s": None, "e_date": None})
    for r in rows:
        d = r["op_date"]
        start = d - timedelta(days=d.weekday())
        end = start + timedelta(days=6)
        k = start.isoformat()
        wgrp[k]["s"] = start; wgrp[k]["e_date"] = end; wgrp[k]["c"] += 1
        if r["type"] == "Расходы": wgrp[k]["e"] += int(r["amount"])
        if r["type"] == "Доходы": wgrp[k]["i"] += int(r["amount"])
    for k, st in sorted(wgrp.items()):
        ws5.append([k, st["s"], st["e_date"], st["e"], st["i"], st["i"] - st["e"], st["c"]])

    for s in [ws, ws2, ws3, ws4, ws5]:
        _auto_width(s)
    wb.save(path)
