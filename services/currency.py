# services/currency.py — v2025.08.19-03
__version__ = "2025.08.19-03"

import os, json, time, logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from pathlib import Path
from typing import Dict, Optional, Tuple, List
import requests

try:
    from db.queries import get_user_currency
except Exception:
    def get_user_currency(user_id: int) -> str:
        return "RUB"

log = logging.getLogger(__name__)

FX_CODES: List[str] = [
    "USD","EUR","RUB","KZT","UAH","TRY","GBP","CNY","BYN",
    "GEL","RSD","AED","THB","VND","KRW","AMD","AZN","EGP",
]
CB_API_KEY = os.environ.get("CURRENCYBEACON_API_KEY", "")
CB_URL = "https://api.currencybeacon.com/v1/latest"
NBRB_URL = "https://www.nbrb.by/api/exrates/rates?periodicity=0"

_STATE_DIR = Path("/root/bot_finuchet/.state"); _STATE_DIR.mkdir(parents=True, exist_ok=True)
_CACHE_FILE = _STATE_DIR / "fx_cache.json"
_FX: Dict[str, float] = {}
_FX_DATE: Optional[str] = None
_LOADED_FROM = {"cb": False, "nbrb": False}

def _save_cache():
    try:
        _CACHE_FILE.write_text(json.dumps({
            "fx_date": _FX_DATE, "fx": _FX, "loaded_from": _LOADED_FROM, "ts": time.time()
        }, ensure_ascii=False))
    except Exception as e:
        log.warning("FX cache save failed: %s", e)

def _load_cache():
    global _FX, _FX_DATE, _LOADED_FROM
    try:
        if _CACHE_FILE.exists():
            data = json.loads(_CACHE_FILE.read_text())
            _FX = {k: float(v) for k, v in (data.get("fx") or {}).items()}
            _FX_DATE = data.get("fx_date")
            _LOADED_FROM = data.get("loaded_from", {"cb": False, "nbrb": False})
    except Exception as e:
        log.warning("FX cache load failed: %s", e)

def _fetch_currencybeacon() -> Dict[str, float]:
    if not CB_API_KEY:
        return {}
    try:
        r = requests.get(CB_URL, params={"api_key": CB_API_KEY, "base": "USD"}, timeout=10)
        r.raise_for_status()
        js = r.json()
        rates = js.get("rates") or {}
        out = {}
        for k, v in rates.items():
            k = str(k).upper()
            if isinstance(v, (int, float)) and v > 0:
                out[k] = float(v)
        return out
    except Exception as e:
        log.warning("CurrencyBeacon fetch failed: %s", e)
        return {}

def _fetch_nbrb_byn_per_foreign() -> Dict[str, float]:
    try:
        r = requests.get(NBRB_URL, timeout=10)
        r.raise_for_status()
        arr = r.json()
        out: Dict[str, float] = {}
        for row in arr:
            code = row.get("Cur_Abbreviation")
            rate = row.get("Cur_OfficialRate")
            scale = row.get("Cur_Scale") or 1
            if code and isinstance(rate, (int, float)) and rate > 0 and scale > 0:
                out[str(code).upper()] = float(rate) / float(scale)
        return out
    except Exception as e:
        log.warning("NBRB fetch failed: %s", e)
        return {}

def update_fx_rates(op_date: Optional[date] = None) -> Dict[str, float]:
    global _FX, _FX_DATE, _LOADED_FROM
    _load_cache()
    today_str = (op_date or date.today()).isoformat()
    need_fetch = (_FX_DATE != today_str) or not _FX

    cb_ok = False; nbrb_ok = False
    fx: Dict[str, float] = dict(_FX)

    if need_fetch:
        cb = _fetch_currencybeacon()
        if cb:
            fx = cb; cb_ok = True
        nbrb = _fetch_nbrb_byn_per_foreign()
        if nbrb and nbrb.get("USD"):
            fx["BYN"] = float(nbrb["USD"]); nbrb_ok = True

        _FX = fx; _FX_DATE = today_str; _LOADED_FROM = {"cb": cb_ok, "nbrb": nbrb_ok}
        _save_cache()
        log.info("FX updated for %s: %d codes (CB:%s, NBRB:%s)", today_str, len(_FX), cb_ok, nbrb_ok)
    return dict(_FX)

def get_rate(src: str, dst: str) -> Optional[float]:
    if not src or not dst:
        return None
    src = src.upper(); dst = dst.upper()
    if src == dst:
        return 1.0
    if not _FX:
        update_fx_rates()
    r_src = _FX.get(src); r_dst = _FX.get(dst)
    if not r_src or not r_dst:
        return None
    try:
        return float(r_dst) / float(r_src)
    except ZeroDivisionError:
        return None

_SYMBOL_MAP = {"$":"USD","€":"EUR","₽":"RUB","₴":"UAH","₺":"TRY","₸":"KZT","¥":"CNY","£":"GBP"}
_ALIAS_MAP = {
    "usd":"USD","dollar":"USD","bucks":"USD","бакс":"USD","баксов":"USD",
    "eur":"EUR","euro":"EUR","евро":"EUR",
    "rub":"RUB","р":"RUB","руб":"RUB","руб.":"RUB","₽":"RUB",
    "byn":"BYN","белр":"BYN",
    "kzt":"KZT","тг":"KZT","тенге":"KZT","₸":"KZT",
    "uah":"UAH","грн":"UAH","₴":"UAH",
    "try":"TRY","lira":"TRY","лира":"TRY","₺":"TRY",
    "gbp":"GBP","funt":"GBP","фунт":"GBP","£":"GBP",
    "cny":"CNY","yuan":"CNY","юань":"CNY","¥":"CNY",
    "gel":"GEL","rsd":"RSD","aed":"AED","thb":"THB","vnd":"VND","krw":"KRW","amd":"AMD","azn":"AZN","egp":"EGP",
}

def detect_currency_token(text: str) -> Optional[str]:
    if not text:
        log.info("FX DETECT: empty -> None"); return None
    s = str(text)
    for ch, iso in _SYMBOL_MAP.items():
        if ch in s:
            log.info('FX DETECT: found symbol "%s" -> %s in=%r', ch, iso, s[:160])
            return iso
    lowered = s.lower()
    tokens = []
    buf = ""
    for ch in lowered:
        if ch.isalnum():
            buf += ch
        else:
            if buf:
                tokens.append(buf); buf = ""
    if buf: tokens.append(buf)

    for t in tokens:
        if len(t) == 3 and t.isalpha():
            up = t.upper()
            if up in FX_CODES:
                log.info('FX DETECT: 3-letter "%s" -> %s in=%r', t, up, s[:160])
                return up
        if t in _ALIAS_MAP:
            iso = _ALIAS_MAP[t]
            log.info('FX DETECT: alias "%s" -> %s in=%r', t, iso, s[:160])
            return iso
    log.info("FX DETECT: none in=%r", s[:160])
    return None

def convert_amount_if_needed(user_id: int, amt: int, src_curr: Optional[str], op_date: Optional[date] = None) -> Tuple[int, Optional[str]]:
    try:
        dst = (get_user_currency(user_id) or "RUB").upper()
    except Exception:
        dst = "RUB"
    src = (src_curr or "").upper() if src_curr else None
    if not src or src == dst:
        log.info("FX CONVERT skip: user=%s amt=%s src=%s dst=%s (no-change)", user_id, amt, src, dst)
        return amt, None

    update_fx_rates(op_date or date.today())
    rate = get_rate(src, dst)
    if not rate or rate <= 0:
        log.warning("FX CONVERT rate-missing: user=%s src=%s dst=%s", user_id, src, dst)
        return amt, None

    converted = int(Decimal(amt * rate).quantize(0, rounding=ROUND_HALF_UP))
    note = f"{amt} {src} → {converted} {dst}"
    log.info("FX CONVERT ok: user=%s src=%s dst=%s amt=%s rate=%.6f out=%s", user_id, src, dst, amt, rate, converted)
    return converted, note
