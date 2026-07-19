from services.currency import detect_currency_token
from utils.parsing import parse_user_input
from utils.spoken_numbers import normalize_spoken_money


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


def test_english_income_semantics_amount_and_currency():
    text, _changed, _lang = normalize_spoken_money("salary one thousand five hundred dollars")
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "salary"
    assert amount == 1500
    assert currency == "USD"


def test_english_no_currency_uses_plain_amount():
    text, _changed, _lang = normalize_spoken_money("coffee two")
    merch, amount, _dt, currency = parse_user_input(text)
    assert merch == "coffee"
    assert amount == 2
    assert currency is None
