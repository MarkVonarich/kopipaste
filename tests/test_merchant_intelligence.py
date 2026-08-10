from __future__ import annotations

from datetime import date
from decimal import Decimal

from services.merchant_intelligence import (
    EMPTY_MERCHANT_KEY,
    comparable_baseline_periods,
    fold_merchant_rows,
    merchant_baseline,
    merchant_features,
    merchant_identity,
    normalize_merchant_key,
)


def test_merchant_normalization_safe_variants_share_key():
    expected = "яндекс лавка"
    assert normalize_merchant_key("Яндекс Лавка") == expected
    assert normalize_merchant_key("  ЯНДЕКС   ЛАВКА ") == expected
    assert normalize_merchant_key("Яндекс*Лавка") == expected
    assert normalize_merchant_key("Яндекс-Лавка") == expected
    assert normalize_merchant_key("Яндекс.Лавка") == expected


def test_merchant_normalization_documents_unsupported_unicode_separation():
    assert normalize_merchant_key("Ｆｏｏ") == "Ｆｏｏ"
    assert normalize_merchant_key("Café") == "caf"
    assert normalize_merchant_key("Café") == "cafe"
    assert normalize_merchant_key("☕ Cafe") == "cafe"
    assert normalize_merchant_key("A&B") == "a b"
    assert normalize_merchant_key("shop.ru") == "shop ru"


def test_merchant_normalization_e_and_empty_semantics():
    assert normalize_merchant_key("Ёлки Палки") == "елки палки"
    assert merchant_identity("").key == EMPTY_MERCHANT_KEY
    assert merchant_identity(None).fallback is True


def test_real_fallback_display_names_are_real_keys():
    assert normalize_merchant_key("Без описания") == "без описания"
    assert normalize_merchant_key("Остальные") == "остальные"
    assert merchant_identity("Без описания").fallback is False
    assert merchant_identity("Остальные").fallback is False


def test_obviously_different_merchants_stay_separate():
    assert normalize_merchant_key("Coffee House") == "coffee house"
    assert normalize_merchant_key("Coffee Point") == "coffee point"
    assert normalize_merchant_key("lavka") != normalize_merchant_key("Яндекс Лавка")


def test_fold_merchant_rows_merges_safe_aliases_and_keeps_empty_distinct():
    folded = fold_merchant_rows([
        ("Яндекс Лавка", "RUB", Decimal("300.00"), 1),
        ("ЯНДЕКС*ЛАВКА", "RUB", Decimal("500.00"), 2),
        ("", "RUB", Decimal("100.00"), 1),
    ])
    group = folded["RUB"]
    lavka = group["яндекс лавка"]
    assert lavka["name"] == "Яндекс Лавка"
    assert lavka["total"] == Decimal("800.00")
    assert lavka["count"] == 3
    assert set(raw for raw in lavka["raw_values"]) == {"Яндекс Лавка", "ЯНДЕКС*ЛАВКА"}
    assert lavka["drillable"] is True
    assert group[EMPTY_MERCHANT_KEY]["fallback"] is True
    assert group[EMPTY_MERCHANT_KEY]["drillable"] is False


def test_merchant_features_frequency_average_and_shares_use_decimal_math():
    features = merchant_features(
        current_total=Decimal("5850.00"),
        current_count=9,
        previous_total=Decimal("2500.00"),
        previous_count=5,
        category_total=Decimal("10000.00"),
        scope_total=Decimal("12000.00"),
    )
    assert features["average_check"] == Decimal("650.00")
    assert features["previous_average_check"] == Decimal("500.00")
    assert features["frequency_delta"] == 4
    assert features["frequency_pct"] == Decimal("80.00")
    assert features["average_check_delta"] == Decimal("150.00")
    assert features["average_check_pct"] == Decimal("30.00")
    assert features["merchant_share_of_category"] == Decimal("58.50")
    assert features["merchant_share_of_total"] == Decimal("48.75")


def test_merchant_features_zero_previous_count_has_no_fake_percentage():
    features = merchant_features(current_total=Decimal("600.00"), current_count=2, previous_total=Decimal("0.00"), previous_count=0)
    assert features["frequency_delta"] == 2
    assert features["frequency_pct"] is None
    assert features["average_check_pct"] is None


def test_merchant_baseline_requires_enough_history():
    insufficient = merchant_baseline([(Decimal("100.00"), 1), (Decimal("200.00"), 1)])
    assert insufficient["sufficient_data"] is False
    assert insufficient["periods_used"] == 2

    sufficient = merchant_baseline([(Decimal("4000.00"), 4), (Decimal("6000.00"), 6), (Decimal("5000.00"), 5)])
    assert sufficient["sufficient_data"] is True
    assert sufficient["periods_used"] == 3
    assert sufficient["amount"] == Decimal("5000.00")
    assert sufficient["count"] == Decimal("5.00")
    assert sufficient["average_check"] == Decimal("1000.00")


def test_comparable_baseline_periods_month_to_date():
    assert comparable_baseline_periods(date(2026, 8, 1), date(2026, 8, 10), "current_month") == [
        (date(2026, 7, 1), date(2026, 7, 10)),
        (date(2026, 6, 1), date(2026, 6, 10)),
        (date(2026, 5, 1), date(2026, 5, 10)),
    ]


def test_comparable_baseline_periods_full_month():
    assert comparable_baseline_periods(date(2026, 7, 1), date(2026, 7, 31), "previous_month") == [
        (date(2026, 6, 1), date(2026, 6, 30)),
        (date(2026, 5, 1), date(2026, 5, 31)),
        (date(2026, 4, 1), date(2026, 4, 30)),
    ]


def test_comparable_baseline_periods_week_to_date():
    assert comparable_baseline_periods(date(2026, 8, 3), date(2026, 8, 5), "current_week") == [
        (date(2026, 7, 27), date(2026, 7, 29)),
        (date(2026, 7, 20), date(2026, 7, 22)),
        (date(2026, 7, 13), date(2026, 7, 15)),
    ]


def test_comparable_baseline_periods_custom_non_overlapping():
    assert comparable_baseline_periods(date(2026, 8, 1), date(2026, 8, 10), "custom") == [
        (date(2026, 7, 22), date(2026, 7, 31)),
        (date(2026, 7, 12), date(2026, 7, 21)),
        (date(2026, 7, 2), date(2026, 7, 11)),
    ]
