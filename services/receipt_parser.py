from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import List

log = logging.getLogger("finbot.receipt")


@dataclass
class ParsedCandidate:
    amount: float
    category: str
    op_type: str
    op_date: date
    merchant: str
    confidence: float
    raw_text: str


@dataclass
class ParseResult:
    configured: bool
    candidates: List[ParsedCandidate]
    warning: str | None = None


def _safe_date(v: str | None) -> date:
    if not v:
        return date.today()
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except Exception:
        return date.today()


def _op_type(v: str | None) -> str:
    if (v or '').strip() == 'Доходы':
        return 'Доходы'
    return 'Расходы'


def _category(v: str | None) -> str:
    return (v or 'Прочее').strip() or 'Прочее'


_DROP_WORDS = (
    'остаток', 'баланс', 'доступно', 'кэшбэк', 'кешбэк', 'бонус', 'итого', 'всего', 'за день',
    'today', 'cashback'
)
_DATE_TOTAL_RE = re.compile(r'(сегодня|вчера|январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)', re.IGNORECASE)
_HEADER_RE = re.compile(r'^\s*(сегодня|вчера|\d{1,2}\s+[а-я]+)\s*$', re.IGNORECASE)


def _looks_like_aggregate_row(text: str) -> bool:
    t = (text or '').strip().lower()
    if not t:
        return True
    if any(w in t for w in _DROP_WORDS):
        return True
    return bool(_DATE_TOTAL_RE.search(t) and not re.search(r'[a-zа-я]{4,}', t))


def _map_category(v: str | None, op_type: str) -> str:
    raw = (v or '').strip()
    t = raw.lower()
    if 'кафе' in t or 'ресторан' in t:
        return 'Заведения'
    if 'супермаркет' in t:
        return 'Продукты'
    if 'входящ' in t and 'перевод' in t:
        return 'Переводы' if op_type == 'Доходы' else 'Прочее'
    if 'алкогол' in t:
        return 'Прочее'
    return _category(raw)


def _provider_api_key() -> str:
    return (os.getenv('RECEIPT_OCR_API_KEY') or os.getenv('OPENAI_API_KEY') or '').strip()


def _extract_json(text: str) -> dict | None:
    raw = (text or '').strip()
    if raw.startswith('```'):
        raw = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
        raw = re.sub(r'\s*```$', '', raw)
    try:
        return json.loads(raw)
    except Exception:
        pass
    start = raw.find('{')
    end = raw.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except Exception:
            return None
    return None


def _classify_provider_error(exc: Exception) -> str:
    status = getattr(exc, 'status_code', None)
    code = getattr(exc, 'code', None) or getattr(getattr(exc, 'body', None), 'code', None)
    msg = str(exc).lower()
    if status in {401, 403} or 'auth' in msg or 'api key' in msg:
        return 'auth_error'
    if status == 429 and ('quota' in msg or code == 'insufficient_quota'):
        return 'insufficient_quota'
    if status == 429 or 'rate limit' in msg:
        return 'rate_limit'
    if 'connection' in msg or 'timeout' in msg or 'network' in msg:
        return 'network_error'
    return 'provider_error'


def candidates_from_provider_payload(data: dict) -> tuple[list[ParsedCandidate], str | None]:
    if not isinstance(data, dict):
        return [], 'malformed_response'
    if not data.get('ok'):
        return [], 'no_operations'
    ops = data.get('operations') or []
    if not isinstance(ops, list):
        return [], 'malformed_response'
    dropped_by_filter = 0
    out: List[ParsedCandidate] = []
    seen = set()
    for op in ops[:30]:
        if not isinstance(op, dict):
            dropped_by_filter += 1
            continue
        try:
            amount = abs(float(op.get('amount') or 0))
        except Exception:
            dropped_by_filter += 1
            continue
        if amount <= 0:
            dropped_by_filter += 1
            continue
        merchant = (op.get('merchant') or op.get('comment') or 'Из изображения').strip()[:64]
        raw = (op.get('evidence') or op.get('comment') or op.get('merchant') or '').strip()[:120]
        cat_hint = (op.get('category_hint') or '').strip()
        merged = f'{merchant} {raw} {cat_hint}'.strip()
        if _looks_like_aggregate_row(merged) or _HEADER_RE.match((merchant or '').strip()):
            dropped_by_filter += 1
            continue
        if not merchant and not cat_hint:
            dropped_by_filter += 1
            continue
        op_type = _op_type(op.get('type'))
        op_date = _safe_date(op.get('op_date'))
        key = (round(amount, 2), merchant.lower(), op_type, op_date.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append(ParsedCandidate(
            amount=amount,
            category=_map_category(op.get('category_hint'), op_type),
            op_type=op_type,
            op_date=op_date,
            merchant=merchant,
            confidence=max(0.0, min(1.0, float(op.get('confidence') or 0.0))),
            raw_text=raw,
        ))
    warning = 'partial_rows_skipped' if dropped_by_filter and out else None
    if out and any(c.confidence < 0.6 for c in out):
        warning = warning or 'low_confidence'
    if not out:
        warning = 'no_operations'
    return out, warning


def parse_receipt_image(image_bytes: bytes, user_id: int) -> ParseResult:
    provider = os.getenv('RECEIPT_OCR_PROVIDER', 'openai').strip().lower()
    api_key = _provider_api_key()
    model = os.getenv('RECEIPT_OCR_MODEL', 'gpt-4.1-mini').strip() or 'gpt-4.1-mini'

    if provider != 'openai' or not api_key:
        return ParseResult(configured=False, candidates=[], warning='provider_not_configured')

    if not image_bytes:
        return ParseResult(configured=True, candidates=[], warning='empty_image')

    # soft guard for huge payloads
    if len(image_bytes) > 8 * 1024 * 1024:
        return ParseResult(configured=True, candidates=[], warning='image_too_large')

    try:
        from openai import OpenAI
    except Exception:
        return ParseResult(configured=True, candidates=[], warning='openai_pkg_missing')

    prompt = (
        'Ты парсер финансовых операций с фото/скриншотов. '
        'Поддерживай как одиночные чеки, так и списки банковских транзакций. '
        'Верни строго JSON без markdown и без пояснений. '
        'Извлекай только реальные строки транзакций. '
        'Игнорируй агрегаты по дням и итоги: "20 мая −416,99", "Сегодня −2 496", "Итого", "Всего", "За день". '
        'Игнорируй также: баланс/остаток/доступно/кэшбэк/бонусы/номера карт/кнопки UI/вкладки/заголовки. '
        'Тип: Расходы/Доходы. Положительные операции (входящий перевод/зарплата/пополнение) — Доходы. '
        'Не подменяй сумму строки на соседний дневной итог: бери сумму, визуально связанную с конкретным мерчантом/строкой. '
        'Если не уверен — добавляй строку с низким confidence, но не выбрасывай расходы. '
        'Если receipt с товарами неуверенный — можно вернуть одну итоговую операцию по total.\n\n'
        'JSON schema:\n'
        '{"ok":true|false,"source_type":"bank_screenshot|receipt|unknown",'
        '"operations":[{"amount":123.45,"currency":"RUB","type":"Расходы|Доходы",'
        '"category_hint":"Продукты|Транспорт|Заведения|Красота|Коммунальные|Прочее|null",'
        '"merchant":"string|null","comment":"string","op_date":"YYYY-MM-DD|null","confidence":0.0,"evidence":"short"}],'
        '"ignored_rows":[{"text":"string","reason":"day_total|ui|balance|duplicate|other"}],'
        '"notes":"short"}'
    )

    client = OpenAI(api_key=api_key, timeout=30)
    b64 = base64.b64encode(image_bytes).decode('utf-8')

    try:
        log.info('receipt_ocr: provider=openai started user=%s bytes=%s model=%s', user_id, len(image_bytes), model)
        resp = client.responses.create(
            model=model,
            input=[
                {
                    'role': 'user',
                    'content': [
                        {'type': 'input_text', 'text': prompt},
                        {'type': 'input_image', 'image_url': f'data:image/jpeg;base64,{b64}'},
                    ],
                }
            ],
            temperature=0,
        )
        txt = (resp.output_text or '').strip()
        data = _extract_json(txt)
    except Exception as e:
        request_id = getattr(e, 'request_id', None)
        log.warning('receipt_ocr: failed reason=%s warning=%s status=%s code=%s request_id=%s', type(e).__name__, _classify_provider_error(e), getattr(e, 'status_code', None), getattr(e, 'code', None), request_id)
        return ParseResult(configured=True, candidates=[], warning=_classify_provider_error(e))

    if data is None:
        log.warning('receipt_ocr: malformed_json user=%s bytes=%s model=%s', user_id, len(image_bytes), model)
        return ParseResult(configured=True, candidates=[], warning='malformed_response')

    out, warning = candidates_from_provider_payload(data)
    ignored = data.get('ignored_rows') or []
    log.info('receipt_ocr: parsed candidates=%s ignored=%s warning=%s user=%s', len(out), len(ignored), warning, user_id)
    return ParseResult(configured=True, candidates=out, warning=warning)
