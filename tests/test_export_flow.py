from datetime import date

from services.export_flow import clear_export_wait_flags, export_state_has_period, parse_export_date, preset_period, validate_export_period


def test_manual_start_and_manual_end_formats():
    today = date(2026, 7, 19)
    assert parse_export_date("01.07.2026", today) == date(2026, 7, 1)
    assert parse_export_date("05.07", today) == date(2026, 7, 5)
    assert parse_export_date("2026-07-19", today) == date(2026, 7, 19)


def test_end_date_earlier_than_start_is_rejected():
    ok, reason = validate_export_period(date(2026, 7, 10), date(2026, 7, 9))
    assert ok is False
    assert reason == "end_before_start"


def test_invalid_end_date_then_valid_input():
    today = date(2026, 7, 19)
    assert parse_export_date("not-a-date", today) is None
    assert parse_export_date("19.07.2026", today) == date(2026, 7, 19)


def test_export_presets_do_not_regress():
    today = date(2026, 7, 19)
    assert preset_period("7", today).start == date(2026, 7, 13)
    assert preset_period("14", today).start == date(2026, 7, 6)
    assert preset_period("month", today).start == date(2026, 7, 1)
    assert preset_period("previous_month", today).start == date(2026, 6, 1)
    assert preset_period("previous_month", today).end == date(2026, 6, 30)
    assert preset_period("year", today).start == date(2026, 1, 1)
    assert preset_period("previous_year", today).start == date(2025, 1, 1)
    assert preset_period("previous_year", today).end == date(2025, 12, 31)


def test_export_wait_flags_clear_after_inline_start_button_end():
    user_data = {
        "await_export_start": True,
        "await_export_end": True,
        "export_state": {"from": "2026-07-01", "to": "2026-07-19"},
    }
    clear_export_wait_flags(user_data)
    assert "await_export_start" not in user_data
    assert "await_export_end" not in user_data
    assert export_state_has_period(user_data)


def test_stale_download_state_is_detected_before_download():
    assert not export_state_has_period({"export_state": {}})
    assert not export_state_has_period({"export_state": {"from": "2026-07-01"}})
    assert export_state_has_period({"export_state": {"from": "2026-07-01", "to": "2026-07-19"}})
