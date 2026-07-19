from services.receipt_parser import _classify_provider_error, _extract_json, candidates_from_provider_payload


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
