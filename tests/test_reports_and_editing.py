from datetime import date

from jobs.daily import build_monthly_report_text, build_weekly_report_text


def test_weekly_and_monthly_reports_include_search_hashtags(monkeypatch):
    monkeypatch.setattr("jobs.daily._sum_by_type", lambda user_id, start, end: (0, 0))
    weekly = build_weekly_report_text(1, date(2026, 7, 13), date(2026, 7, 19))
    monthly = build_monthly_report_text(1, date(2026, 7, 1), date(2026, 7, 31))
    assert "#ИтогНедели" in weekly
    assert "#ИтогМесяца" in monthly
