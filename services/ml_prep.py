from __future__ import annotations

import re


_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)

# keep letters/digits/spaces and currency symbols/percent.
# numbers are replaced by <num> later.
_PUNCT_RE = re.compile(r"[^\w\s₽$€£¥₸₹₴₾%]", flags=re.UNICODE)
_NUM_RE = re.compile(r"\d+(?:[\s\.,]\d+)*", flags=re.UNICODE)
_DATE_WORDS_RE = re.compile(r"\b(сегодня|вчера|завтра|позавчера|пн|вт|ср|чт|пт|сб|вс)\b", flags=re.UNICODE)
_CURRENCY_NOISE_RE = re.compile(r"\b(руб|рублей|р|usd|eur|kzt|byn|tenge|тенге)\b", flags=re.UNICODE)


def normalize_for_ml(text: str) -> str:
    """
    Conservative normalization for ML features:
    - lowercase + trim + collapse spaces
    - remove emoji
    - replace noisy punctuation with spaces (currency symbols are preserved)
    - replace numbers with <num>
    """
    s = (text or "").lower().strip()
    s = _EMOJI_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _NUM_RE.sub(" <num> ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_alias_text(raw_text: str) -> str:
    """
    Normalization for global/personal alias learning (human-readable alias key):
    - lowercase
    - remove emoji and punctuation noise
    - strip numbers/currency/date words
    - collapse spaces
    """
    s = (raw_text or "").lower().strip()
    s = _EMOJI_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = _NUM_RE.sub(" ", s)
    s = _DATE_WORDS_RE.sub(" ", s)
    s = _CURRENCY_NOISE_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
