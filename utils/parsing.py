# utils/parsing.py — v2025.08.26-01
__version__ = "2025.08.26-01"

import re
from datetime import datetime, timedelta
import dateparser

DATE_TOKENS = {"вчера", "сегодня"}

def _clean_currency_tokens(text: str) -> str:
    """
    Аккуратно вычищает токены валюты (руб, rub, знак ₽) как отдельные слова
    или рядом с числом. Не удаляет букву 'р' внутри слов типа 'вчера'.
    """
    text = re.sub(r"(?i)\b(?:rub|руб(?:\.|ля|лей)?|р)\b\.?", "", text)
    text = re.sub(r"\s*₽", "", text)
    return text

def _extract_trailing_date(tokens):
    """
    Если последний токен похож на дату — убираем его и возвращаем дату.
    Иначе дату считаем текущей.
    """
    if not tokens:
        return tokens, datetime.now()

    last = tokens[-1].lower()

    # 4+ цифр в конце — точно не дата (скорее сумма/код)
    if re.fullmatch(r"\d{4,}", last):
        return tokens, datetime.now()

    if last in DATE_TOKENS:
        dt = datetime.now() - timedelta(days=1) if last == "вчера" else datetime.now()
        return tokens[:-1], dt

    # dd.mm, dd.mm.yy, dd.mm.yyyy
    m = re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$", last)
    if m:
        d, mth, y = m.group(1), m.group(2), m.group(3)
        if y is None:
            dt = datetime.strptime(f"{d}.{mth}", "%d.%m").replace(year=datetime.now().year)
        else:
            if len(y) == 2:
                dt = datetime.strptime(f"{d}.{mth}.{y}", "%d.%m.%y")  # 25 -> 2025
            else:
                dt = datetime.strptime(f"{d}.{mth}.{y}", "%d.%m.%Y")
        return tokens[:-1], dt

    # чисто числовой 1–3 — не дата (чтобы 'кола 20' не путать)
    if re.fullmatch(r"\d{1,3}", last):
        return tokens, datetime.now()

    # свободные словесные даты (вт/пт/завтра)
    if re.search(r"[A-Za-zА-Яа-я]", last):
        dp = dateparser.parse(last, languages=["ru"])
        if dp:
            return tokens[:-1], dp

    return tokens, datetime.now()

def split_wo_date(text: str):
    clean = _clean_currency_tokens(text)
    tokens = [t for t in clean.strip().split() if t]
    tokens, dt = _extract_trailing_date(tokens)
    return (" ".join(tokens), dt)

def parse_user_input(text: str):
    """
    Возвращает (описание, сумма, дата, src_currency|None)
    """
    if not text or not text.strip():
        raise ValueError("empty")

    no_date, dt = split_wo_date(text)
    if not no_date:
        raise ValueError("empty")

    src_curr = None

    # число: целое/десятичное, с пробелами/точками как разделителями тысяч
    matches = list(re.finditer(
        r"(?<!\d)(\d+(?:[.,]\d+)?|\d{1,3}(?:[ \.,]\d{3})+(?:[.,]\d+)?)",
        no_date
    ))
    if not matches:
        raise ValueError("no_amount")

    m = matches[-1]
    raw = re.sub(r"[ \.,]", "", m.group(0))
    try:
        amt = int(raw)
    except ValueError:
        raise ValueError("bad_amount")
    if amt <= 0:
        raise ValueError("bad_amount")

    merch = (no_date[:m.start()] + no_date[m.end():]).strip()
    merch = re.sub(r"\s+", " ", merch)
    if not merch:
        merch = "операция"

    return merch, amt, dt, src_curr


# ───────────────────────────────────────────────────────────────────────────────
# Batch: "Списком за день"
# ───────────────────────────────────────────────────────────────────────────────

_DATE_TAIL_RE = re.compile(r"(?:\bвчера\b|\bсегодня\b|\d{1,2}\.\d{1,2}(?:\.\d{2,4})?)\s*$", re.IGNORECASE)
_SPLIT_RE = re.compile(r"[;\n]+")

def _normalize_header_date_token(s: str) -> str | None:
    s = (s or "").strip().lower()
    if not s:
        return None
    if s in DATE_TOKENS:
        return s
    m = re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$", s)
    if not m:
        return None
    d, mth, y = m.group(1), m.group(2), m.group(3)
    try:
        if y is None:
            dt = datetime.strptime(f"{d}.{mth}", "%d.%m").replace(year=datetime.now().year)
        elif len(y) == 2:
            dt = datetime.strptime(f"{d}.{mth}.{y}", "%d.%m.%y")
        else:
            dt = datetime.strptime(f"{d}.{mth}.{y}", "%d.%m.%Y")
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return None

def parse_day_list(raw_text: str) -> list[str]:
    """
    Режет ввод по ';' и переводам строк. Возвращает список элементов.
    Поддерживает глобальную дату в первом элементе:
      "вчера; кофе 150; метро 69"  → глобальная дата 'вчера' для всех, где даты нет.
    Фильтрует мусор (элемент валиден, если есть цифры и хотя бы один пробел).
    Если валидных < 2 — не считаем списком (вернём []).
    """
    if not raw_text or not raw_text.strip():
        return []

    parts = [p.strip() for p in _SPLIT_RE.split(raw_text) if p.strip()]
    if len(parts) < 2:
        return []

    # Глобальная дата в "шапке"
    header = _normalize_header_date_token(parts[0])
    if header:
        parts = parts[1:]
    if not parts:
        return []

    out: list[str] = []
    for p in parts:
        if header and not _DATE_TAIL_RE.search(p):
            # если у элемента нет своей даты — добавим глобальную
            p = f"{p} {header}"
        # простая валидация "название 123"
        if any(ch.isdigit() for ch in p) and " " in p:
            out.append(p)

    return out if len(out) >= 2 else []
