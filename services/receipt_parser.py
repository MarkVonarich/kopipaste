from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import List


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


def parse_receipt_image(image_bytes: bytes, user_id: int) -> ParseResult:
    """
    Provider abstraction v1.
    If no OCR provider key is configured, returns configured=False for graceful fallback.
    """
    _ = (image_bytes, user_id)
    provider_key = os.getenv('RECEIPT_OCR_API_KEY', '').strip()
    if not provider_key:
        return ParseResult(configured=False, candidates=[], warning='provider_not_configured')

    # OCR provider integration is intentionally not implemented in v1 here.
    # Keep safe behavior and avoid auto-writing operations.
    return ParseResult(configured=True, candidates=[], warning='provider_stub_no_parse')
