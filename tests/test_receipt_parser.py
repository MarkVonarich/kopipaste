from decimal import Decimal

from services.receipt_parser import _classify_provider_error, _extract_json, _provider_api_key, candidates_from_provider_payload, ocr_credential_diagnostic


def test_receipt_payload_one_valid_operation():
    ops, warning = candidates_from_provider_payload({
        "ok": True,
        "operations": [{
            "amount": 250,
            "type": "Расходы",
            "category_hint": "Заведения",
            "merchant": "Coffee",
            "op_date": "2026-07-19",
            "confidence": 0.9,
        }],
    })
    assert len(ops) == 1
    assert ops[0].merchant == "Coffee"
    assert warning is None


def test_receipt_payload_several_operations():
    ops, warning = candidates_from_provider_payload({
        "ok": True,
        "operations": [
            {"amount": 100, "type": "Расходы", "merchant": "Shop", "confidence": 0.9},
            {"amount": 200, "type": "Доходы", "merchant": "Refund", "confidence": 0.8},
        ],
    })
    assert [op.op_type for op in ops] == ["Расходы", "Доходы"]
    assert warning is None


def test_receipt_payload_keeps_integer_and_decimal_bank_rows():
    ops, warning = candidates_from_provider_payload({
        "ok": True,
        "source_type": "bank_screenshot",
        "operations": [
            {"amount": "216.34", "type": "Расходы", "category_hint": "Супермаркет", "merchant": "Чижик", "confidence": 0.9, "evidence": "Чижик -216,34 ₽"},
            {"amount": "285", "type": "Расходы", "category_hint": "Кафе", "merchant": "Дринкит", "confidence": 0.9, "evidence": "Дринкит -285 ₽"},
            {"amount": "12.50", "type": "Доходы", "merchant": "Кэшбэк", "confidence": 0.9, "evidence": "Кэшбэк +12,50"},
            {"amount": "501.34", "type": "Расходы", "merchant": "Сегодня", "confidence": 0.9, "evidence": "Сегодня -501,34"},
        ],
    })
    assert [(op.merchant, op.amount, op.category) for op in ops] == [
        ("Чижик", Decimal("216.34"), "Продукты"),
        ("Дринкит", Decimal("285.00"), "Заведения"),
    ]
    assert warning == "partial_rows_skipped"


def test_receipt_payload_partial_row_failure_keeps_valid_rows():
    ops, warning = candidates_from_provider_payload({
        "ok": True,
        "operations": [
            {"amount": 0, "merchant": "bad"},
            {"amount": 300, "type": "Расходы", "merchant": "Taxi", "confidence": 0.9},
        ],
    })
    assert len(ops) == 1
    assert warning == "partial_rows_skipped"


def test_extract_json_from_markdown_response():
    assert _extract_json('```json\n{"ok": true, "operations": []}\n```') == {"ok": True, "operations": []}


def test_quota_error_classification():
    class QuotaError(Exception):
        status_code = 429
        code = "insufficient_quota"

    assert _classify_provider_error(QuotaError("quota")) == "insufficient_quota"


def test_no_operation_result():
    ops, warning = candidates_from_provider_payload({"ok": False, "operations": []})
    assert ops == []
    assert warning == "no_operations"


def test_ocr_credential_precedence_prefers_openai_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("RECEIPT_OCR_API_KEY", "receipt-secret")
    diag = ocr_credential_diagnostic()
    assert diag == {
        "OPENAI_API_KEY_configured": True,
        "RECEIPT_OCR_API_KEY_configured": True,
        "selected_source": "OPENAI_API_KEY",
    }
    assert _provider_api_key() == "openai-secret"


def test_ocr_credential_diagnostic_hides_values(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RECEIPT_OCR_API_KEY", "receipt-secret")
    diag = ocr_credential_diagnostic()
    assert diag["selected_source"] == "RECEIPT_OCR_API_KEY"
    assert "receipt-secret" not in str(diag)
