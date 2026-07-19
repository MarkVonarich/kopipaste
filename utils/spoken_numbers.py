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

_EN_UNITS = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4,
    'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9,
}
_EN_TEENS = {
    'ten': 10, 'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14,
    'fifteen': 15, 'sixteen': 16, 'seventeen': 17, 'eighteen': 18, 'nineteen': 19,
}
_EN_TENS = {
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
}
_EN_SCALES = {'hundred': 100, 'thousand': 1000}
_EN_NUM_WORDS = set(_EN_UNITS) | set(_EN_TEENS) | set(_EN_TENS) | set(_EN_SCALES) | {'and'}
_EN_MAJOR_CURRENCY = {
    'dollar': 'USD', 'dollars': 'USD', 'buck': 'USD', 'bucks': 'USD',
    'euro': 'EUR', 'euros': 'EUR',
    'pound': 'GBP', 'pounds': 'GBP',
    'ruble': 'RUB', 'rubles': 'RUB',
}
_EN_MINOR_CURRENCY = {'cent', 'cents'}


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


def _parse_en_number_words(words: list[str]) -> int | None:
    total = 0
    current = 0
    seen = False
    for w in words:
        if w == 'and':
            continue
        if w in _EN_UNITS:
            current += _EN_UNITS[w]; seen = True
        elif w in _EN_TEENS:
            current += _EN_TEENS[w]; seen = True
        elif w in _EN_TENS:
            current += _EN_TENS[w]; seen = True
        elif w == 'hundred':
            current = (current or 1) * 100; seen = True
        elif w == 'thousand':
            total += (current or 1) * 1000
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


def normalize_spoken_money_en(text: str) -> tuple[str, bool]:
    tokens = _tokenize(text)
    out: list[str] = []
    i = 0
    changed = False
    while i < len(tokens):
        if tokens[i] in _EN_NUM_WORDS:
            j = i
            seq = []
            while j < len(tokens) and tokens[j] in _EN_NUM_WORDS:
                seq.append(tokens[j]); j += 1
            val = _parse_en_number_words(seq)
            if val is not None and 0 <= val <= 999_999:
                if j < len(tokens) and tokens[j] in _EN_MAJOR_CURRENCY:
                    code = _EN_MAJOR_CURRENCY[tokens[j]]
                    k = j + 1
                    cents = None
                    if k < len(tokens) and tokens[k] in _EN_NUM_WORDS:
                        cseq = []
                        while k < len(tokens) and tokens[k] in _EN_NUM_WORDS:
                            cseq.append(tokens[k]); k += 1
                        cval = _parse_en_number_words(cseq)
                        if cval is not None and 0 <= cval <= 99 and k < len(tokens) and tokens[k] in _EN_MINOR_CURRENCY:
                            cents = cval
                            k += 1
                    out.append(f"{val}.{cents:02d}" if cents is not None else str(val))
                    out.append(code)
                    changed = True
                    i = k
                    continue
                if j < len(tokens) and tokens[j] in _EN_MINOR_CURRENCY:
                    out.append(f"0.{val:02d}")
                    changed = True
                    i = j + 1
                    continue
                out.append(str(val))
                changed = True
                i = j
                continue
        tok = tokens[i]
        if tok in _EN_MAJOR_CURRENCY:
            out.append(_EN_MAJOR_CURRENCY[tok])
            changed = True
            i += 1
            continue
        out.append(tok)
        i += 1
    normalized = " ".join(out)
    normalized = re.sub(r"\s+([,.:;!?])", r"\1", normalized).strip()
    return normalized, changed


def normalize_spoken_money(text: str) -> tuple[str, bool, str]:
    ru_text, ru_changed = normalize_spoken_money_ru(text)
    en_text, en_changed = normalize_spoken_money_en(ru_text)
    if en_changed:
        return en_text, True, 'en'
    if ru_changed:
        return ru_text, True, 'ru'
    return text, False, 'unknown'
