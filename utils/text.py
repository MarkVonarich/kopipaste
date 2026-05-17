# utils/text.py — v2025.08.19-02 (small-amount fix inside)
from __future__ import annotations
from datetime import date
import re

__all__ = ["norm_text", "format_date_ru_with_weekday"]

# --- Базовые утилиты ---------------------------------------------------------

def norm_text(s: str) -> str:
    """Нормализуем пользовательский текст для сравнения/поиска."""
    if not isinstance(s, str):
        return ""
    s = s.strip()
    # унификация пробелов
    s = re.sub(r"\s+", " ", s)
    # унификация регистра и ё/е
    s = s.lower().replace("ё", "е")
    return s

# Месяцы/дни недели по-русски
_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
]

def format_date_ru_with_weekday(d: date) -> str:
    """Пр: 19 августа 2025, вторник"""
    if not isinstance(d, date):
        return ""
    try:
        day = d.day
        month = _MONTHS_RU[d.month - 1]
        wd = _WEEKDAYS_RU[d.weekday()]
        return f"{day} {month} {d.year}, {wd}"
    except Exception:
        # На всякий случай не валим поток из-за локалей/индексов
        return d.strftime("%d.%m.%Y")

# --- Small-amount fallback patch ---------------------------------------------
# Идея: если parse_user_input не нашёл сумму, пытаемся извлечь
# последнюю «целую» 1–4 цифры, которые не выглядят как дата/время.
#
# Ничего не меняем, если исходный парсер уже вернул сумму.

def _extract_last_plain_int(text: str) -> int | None:
    if not isinstance(text, str):
        return None
    # берём последнее число, НЕ соседящее с . / : (чтобы не путать с 14.08, 20:00, 01/02)
    last = None
    for m in re.finditer(r"(?<![\d\./])(\d{1,4})(?![\d/.:])", text):
        last = m.group(1)
    if last is None:
        return None
    try:
        return int(last)
    except Exception:
        return None

def _install_two_digit_amount_patch():
    try:
        # Пытаемся импортировать исходный парсер
        import utils.parsing as P  # type: ignore
        orig = getattr(P, "parse_user_input", None)
        if not callable(orig):
            return

        def wrapped(*args, **kwargs):
            res = orig(*args, **kwargs)
            # Попробуем достать исходный текст из аргументов
            text_arg = None
            if args:
                text_arg = args[0]
            if text_arg is None:
                text_arg = kwargs.get("text")

            # Достаём «amount» из результата. Поддержим самые частые формы:
            # 1) dict с ключом amount/amt
            # 2) tuple/list, где по опыту сумма на позиции 2 (kind, cat, amt, ...)
            amt = None
            try:
                if isinstance(res, dict):
                    amt = res.get("amount") or res.get("amt")
                elif isinstance(res, (tuple, list)) and len(res) >= 3:
                    amt = res[2]
            except Exception:
                pass

            # Если суммы нет — пробуем достать из текста
            if (amt is None or amt == 0) and isinstance(text_arg, str):
                fallback_amt = _extract_last_plain_int(text_arg)
                if isinstance(fallback_amt, int):
                    # Вписываем сумму обратно, сохраняя тип результата
                    if isinstance(res, dict):
                        if "amount" in res:
                            res["amount"] = fallback_amt
                        else:
                            res["amt"] = fallback_amt
                        return res
                    elif isinstance(res, list):
                        if len(res) >= 3:
                            res[2] = fallback_amt
                        return res
                    elif isinstance(res, tuple):
                        L = list(res)
                        if len(L) >= 3:
                            L[2] = fallback_amt
                        try:
                            return type(res)(L)  # вернуть тот же тип tuple-наследника
                        except Exception:
                            return tuple(L)
                    # Если формат неизвестен — просто возвращаем как есть
            return res

        # Ставим патч один раз
        if getattr(P.parse_user_input, "__name__", "") != "wrapped":
            P.parse_user_input = wrapped  # type: ignore[attr-defined]
    except Exception:
        # Не мешаем боту работать, если что-то пойдёт не так
        pass

# устанавливаем патч на импорт utils.text
_install_two_digit_amount_patch()


def fmt_limit_warn(title: str, period: str, spent: int, limit_amount: int, threshold: int) -> str:
    try:
        pct_real = int(round((spent / limit_amount) * 100)) if limit_amount > 0 else 0
    except Exception:
        pct_real = 0
    sign = ">" if limit_amount and pct_real > threshold else ""
    return f"⚠️ Лимит по «{title}» ({period}): {sign}{threshold}% ({spent}/{limit_amount})"
