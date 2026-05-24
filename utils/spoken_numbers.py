from __future__ import annotations

import re

_UNITS = {
    'ноль': 0, 'один': 1, 'одна': 1, 'два': 2, 'две': 2, 'три': 3, 'четыре': 4,
    'пять': 5, 'шесть': 6, 'семь': 7, 'восемь': 8, 'девять': 9,
}
_TEENS = {
    'десять': 10, 'одиннадцать': 11, 'двенадцать': 12, 'тринадцать': 13, 'четырнадцать': 14,
    'пятнадцать': 15, 'шестнадцать': 16, 'семнадцать': 17, 'восемнадцать': 18, 'девятнадцать': 19,
}
_TENS = {
    'двадцать': 20, 'тридцать': 30, 'сорок': 40, 'пятьдесят': 50, 'шестьдесят': 60,
    'семьдесят': 70, 'восемьдесят': 80, 'девяносто': 90,
}
_HUNDREDS = {
    'сто': 100, 'двести': 200, 'триста': 300, 'четыреста': 400, 'пятьсот': 500,
    'шестьсот': 600, 'семьсот': 700, 'восемьсот': 800, 'девятьсот': 900,
}
_THOUSANDS = {'тысяча', 'тысячи', 'тысяч'}
_CURRENCY = {'рубль', 'рубля', 'рублей', 'руб', 'р', '₽'}
_NUM_WORDS = set(_UNITS) | set(_TEENS) | set(_TENS) | set(_HUNDREDS) | _THOUSANDS


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9₽]+|[^\s]", (text or "").lower())


def _parse_number_words(words: list[str]) -> int | None:
    total = 0
    current = 0
    seen = False
    for w in words:
        if w in _HUNDREDS:
            current += _HUNDREDS[w]; seen = True
        elif w in _TENS:
            current += _TENS[w]; seen = True
        elif w in _TEENS:
            current += _TEENS[w]; seen = True
        elif w in _UNITS:
            current += _UNITS[w]; seen = True
        elif w in _THOUSANDS:
            base = current if current > 0 else 1
            total += base * 1000
            current = 0
            seen = True
        else:
            return None
    if not seen:
        return None
    return total + current


def normalize_spoken_money_ru(text: str) -> tuple[str, bool]:
    tokens = _tokenize(text)
    out: list[str] = []
    i = 0
    changed = False
    while i < len(tokens):
        if tokens[i] in _NUM_WORDS:
            j = i
            seq = []
            while j < len(tokens) and tokens[j] in _NUM_WORDS:
                seq.append(tokens[j]); j += 1
            val = _parse_number_words(seq)
            if val is not None and 0 <= val <= 999_999:
                out.append(str(val))
                changed = True
                i = j
                continue
        tok = tokens[i]
        if tok in _CURRENCY:
            changed = True
            i += 1
            continue
        out.append(tok)
        i += 1
    normalized = " ".join(out)
    normalized = re.sub(r"\s+([,.:;!?])", r"\1", normalized).strip()
    return normalized, changed
