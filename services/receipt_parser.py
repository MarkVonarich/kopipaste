from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import List

log = logging.getLogger("finbot.receipt")


@dataclass
class ParsedCandidate:
    amount: int
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


def parse_receipt_image(image_bytes: bytes, user_id: int) -> ParseResult:
    provider = os.getenv('RECEIPT_OCR_PROVIDER', 'openai').strip().lower()
    api_key = os.getenv('RECEIPT_OCR_API_KEY', '').strip()
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
        'Верни строго JSON без markdown и без пояснений. '
        'Извлекай только реальные операции, игнорируй баланс, кешбэк, номера карт, кнопки UI, заголовки. '
        'Тип: Расходы/Доходы. Если не уверен — лучше не добавляй строку. '
        'Если receipt с товарами неуверенный — можно вернуть одну итоговую операцию по total.\n\n'
        'JSON schema:\n'
        '{"ok":true|false,"source_type":"bank_screenshot|receipt|unknown",'
        '"operations":[{"amount":123.45,"currency":"RUB","type":"Расходы|Доходы",'
        '"category_hint":"Продукты|Транспорт|Заведения|Красота|Коммунальные|Прочее|null",'
        '"merchant":"string|null","comment":"string","op_date":"YYYY-MM-DD|null","confidence":0.0}],'
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
        data = json.loads(txt)
    except Exception as e:
        log.warning('receipt_ocr: failed reason=%s', type(e).__name__)
        return ParseResult(configured=True, candidates=[], warning='provider_error')

    if not isinstance(data, dict) or not data.get('ok'):
        return ParseResult(configured=True, candidates=[], warning='not_confident')

    ops = data.get('operations') or []
    out: List[ParsedCandidate] = []
    seen = set()
    for op in ops:
        try:
            amount = int(round(float(op.get('amount') or 0)))
        except Exception:
            continue
        if amount <= 0:
            continue
        merchant = (op.get('merchant') or op.get('comment') or 'Из изображения').strip()[:64]
        raw = (op.get('comment') or op.get('merchant') or '').strip()[:120]
        key = (amount, merchant.lower(), _op_type(op.get('type')))
        if key in seen:
            continue
        seen.add(key)
        out.append(ParsedCandidate(
            amount=amount,
            category=_category(op.get('category_hint')),
            op_type=_op_type(op.get('type')),
            op_date=_safe_date(op.get('op_date')),
            merchant=merchant,
            confidence=max(0.0, min(1.0, float(op.get('confidence') or 0.0))),
            raw_text=raw,
        ))

    log.info('receipt_ocr: parsed candidates=%s user=%s', len(out), user_id)
    return ParseResult(configured=True, candidates=out, warning=('low_confidence' if any(c.confidence < 0.6 for c in out) else None))
