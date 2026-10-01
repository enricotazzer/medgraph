from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from medgraph.normalize.numbers import NumberLocale, NumberParseError, parse_number


@pytest.mark.parametrize(
    ("text", "locale", "value", "comparator"),
    [
        ("1,2", "it", "1.2", None),
        ("0,123", "it", "0.123", None),
        ("1.234", "it", "1234", None),  # Italian thousands separator
        ("250.000", "it", "250000", None),
        ("7.5", "it", "7.5", None),  # cannot be a thousands grouping
        ("1.234,5", "it", "1234.5", None),
        ("1 234,5", "it", "1234.5", None),
        ("1,234", "en", "1234", None),
        ("1,2", "en", "1.2", None),  # cannot be a thousands grouping
        ("1,234.5", "en", "1234.5", None),
        ("1.20", "en", "1.20", None),  # trailing zero preserved
        ("< 0,5", "it", "0.5", "<"),
        ("<=5", "en", "5", "<="),
        ("≥90", None, "90", ">="),
        ("> 60", None, "60", ">"),
        ("\u22123", "en", "-3", None),  # Unicode minus sign
        ("+4", None, "4", None),
    ],
)
def test_parse_number(
    text: str, locale: NumberLocale | None, value: str, comparator: str | None
) -> None:
    parsed = parse_number(text, locale)
    assert parsed.value == Decimal(value)
    assert str(parsed.value) == value
    assert parsed.comparator == comparator


@pytest.mark.parametrize(
    ("text", "locale"),
    [
        ("1,234", None),  # 1234 or 1.234?
        ("1.234", None),
        ("abc", "en"),
        ("", "it"),
        ("1,2,3", "it"),
        ("1.23,4", "it"),
        ("12-3", "en"),
        (",5", "it"),
    ],
)
def test_parse_number_rejects_what_it_would_have_to_guess(
    text: str, locale: NumberLocale | None
) -> None:
    with pytest.raises(NumberParseError):
        parse_number(text, locale)


def _format(value: Decimal, locale: NumberLocale, grouped: bool) -> str:
    text = f"{abs(value):,.3f}" if grouped else f"{abs(value):.3f}"
    if locale == "it":
        text = text.replace(",", "_").replace(".", ",").replace("_", ".")
    return ("-" if value < 0 else "") + text


@given(
    value=st.decimals(
        min_value=Decimal(-1_000_000),
        max_value=Decimal(1_000_000),
        places=3,
        allow_nan=False,
        allow_infinity=False,
    ),
    locale=st.sampled_from(["it", "en"]),
    grouped=st.booleans(),
)
def test_numbers_formatted_by_locale_round_trip(
    value: Decimal, locale: NumberLocale, grouped: bool
) -> None:
    assert parse_number(_format(value, locale, grouped), locale).value == value
