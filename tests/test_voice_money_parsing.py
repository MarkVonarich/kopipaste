from services.currency import detect_currency_token
from utils.parsing import parse_user_input
from utils.spoken_numbers import normalize_spoken_money
from decimal import Decimal


def test_russian_voice_money_still_normalizes():
    text, changed, lang = normalize_spoken_money("Кофе двести пятьдесят")
    assert changed is True
    assert lang == "ru"
    assert text == "кофе 250"
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "кофе"
    assert amount == 250
    assert currency is None


def test_english_dollars_voice_phrase():
    text, changed, lang = normalize_spoken_money("coffee two dollars")
    assert changed is True
    assert lang == "en"
    assert text == "coffee 2 USD"
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "coffee"
    assert amount == 2
    assert currency == "USD"


def test_english_article_and_comma_voice_phrase():
    text, _changed, _lang = normalize_spoken_money("a coffee, two dollars")
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "a coffee"
    assert amount == 2
    assert currency == "USD"


def test_english_decimal_money_phrase():
    text, changed, lang = normalize_spoken_money("taxi twelve dollars fifty cents")
    assert changed is True
    assert lang == "en"
    assert text == "taxi 12.50 USD"
    assert detect_currency_token(text) == "USD"
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "taxi"
    assert amount == Decimal("12.50")
    assert currency == "USD"


def test_fractional_numeric_amount_is_accepted_as_decimal():
    cases = {
        "taxi 12.50 USD": Decimal("12.50"),
        "coffee 2.99": Decimal("2.99"),
        "пирожок 10,01": Decimal("10.01"),
    }
    for raw, expected in cases.items():
        _merch, amount, _dt, _currency = parse_user_input(raw)
        assert amount == expected


def test_zero_fraction_numeric_amount_still_parses_as_integer():
    merch, amount, _dt, currency = parse_user_input("taxi 12.00 USD")
    assert merch == "taxi"
    assert amount == Decimal("12.00")
    assert currency == "USD"


def test_english_income_semantics_amount_and_currency():
    text, _changed, _lang = normalize_spoken_money("salary one thousand five hundred dollars")
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "salary"
    assert amount == Decimal("1500.00")
    assert currency == "USD"


def test_english_no_currency_uses_plain_amount():
    text, _changed, _lang = normalize_spoken_money("coffee two")
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "coffee"
    assert amount == Decimal("2.00")
    assert currency is None


def test_russian_spoken_rubles_and_kopecks_normalize_to_decimal():
    text, changed, lang = normalize_spoken_money("Чижик двести шестнадцать рублей тридцать четыре копейки")
    assert changed is True
    assert lang == "ru"
    assert text == "чижик 216.34"
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "чижик"
    assert amount == Decimal("216.34")
    assert currency is None


def test_more_than_two_decimals_are_rejected():
    for raw in ["кофе 10.123", "кофе 10,123"]:
        try:
            parse_user_input(raw)
        except ValueError as e:
            assert str(e) == "bad_amount"
        else:
            raise AssertionError(f"{raw} must be rejected")
