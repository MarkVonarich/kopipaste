from services.achievements import DEFAULT_ACHIEVEMENTS
from services.categories import normalize_category_name, normalized_category_key
from services.i18n import format_money, normalize_locale, t
from services.notifications import build_inactivity_candidate


def test_achievement_catalog_has_required_size():
    assert 30 <= len(DEFAULT_ACHIEVEMENTS) <= 40
    assert len({a.key for a in DEFAULT_ACHIEVEMENTS}) == len(DEFAULT_ACHIEVEMENTS)


def test_category_normalization_collapses_spacing_and_case():
    assert normalize_category_name("  Coffee   shops  ") == "Coffee shops"
    assert normalized_category_key("  Coffee   Shops ") == normalized_category_key("coffee shops")


def test_i18n_fallback_and_money_formatting():
    assert normalize_locale("en_US") == "en"
    assert normalize_locale("de") == "ru"
    assert t("help.commands", "en").startswith("Menu commands")
    assert format_money(1234.5, "USD", "en") == "1,234.50 USD"
    assert format_money(1234, "RUB", "ru") == "1 234 RUB"


def test_inactivity_candidate_shape(monkeypatch):
    monkeypatch.setattr("services.notifications.has_financial_activity_today", lambda user_id, tz: False)
    candidate = build_inactivity_candidate(42, __import__("datetime").date(2026, 7, 19), "UTC")
    assert candidate is not None
    assert candidate.notification_type == "inactivity"
    assert candidate.dedupe_key == "inactivity:2026-07-19"
