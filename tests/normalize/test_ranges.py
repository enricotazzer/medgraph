from decimal import Decimal

import pytest

from medgraph.normalize.numbers import NumberLocale
from medgraph.normalize.ranges import RangeParseError, parse_reference_range


@pytest.mark.parametrize(
    ("text", "locale", "low", "high"),
    [
        ("0,70 - 1,20", "it", "0.70", "1.20"),
        ("13.0\u201317.0", "en", "13.0", "17.0"),  # en dash
        ("12 a 16", "it", "12", "16"),
        ("-2 - 3", "en", "-2", "3"),
        ("< 30", "en", None, "30"),
        ("fino a 5", "it", None, "5"),
        ("Up to 5", "en", None, "5"),
        (">= 60", "en", "60", None),
        ("superiore a 60", "it", "60", None),
    ],
)
def test_parse_reference_range(
    text: str, locale: NumberLocale, low: str | None, high: str | None
) -> None:
    parsed = parse_reference_range(text, locale)
    assert (parsed.low.value if parsed.low else None) == (Decimal(low) if low else None)
    assert (parsed.high.value if parsed.high else None) == (Decimal(high) if high else None)


@pytest.mark.parametrize("text", ["M: 13-17 F: 12-15", "see note", ""])
def test_unrecognised_ranges_are_rejected(text: str) -> None:
    with pytest.raises(RangeParseError):
        parse_reference_range(text, "en")
