from services.export_xlsx import export_comment, export_source_label


def test_export_comment_blanks_legacy_fallbacks_and_preserves_meaningful_text():
    assert export_comment("From Telegram") == ""
    assert export_comment("telegram") == ""
    assert export_comment("Yota subscription") == "Yota subscription"


def test_export_source_labels_are_localized():
    assert export_source_label("voice", "ru") == "Голос"
    assert export_source_label("ocr", "ru") == "Фото / OCR"
    assert export_source_label("reminder", "en") == "Reminder"
