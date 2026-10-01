"""Numbers as printed on lab reports: decimal commas, thousands separators, comparators.

``1,2`` is 1.2 in Italian and ``1.234`` is 1234; ``1,234`` is 1234 in English but 1.234 in
Italian. A separator is read as a thousands separator only when the locale uses it that way
and the digit groups fit; with no locale, a string that could be read both ways is rejected
instead of guessed.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from medgraph.records import Comparator

NumberLocale = Literal["it", "en"]

_COMPARATORS: tuple[tuple[str, Comparator], ...] = (
    ("<=", "<="),
    ("=<", "<="),
    ("≤", "<="),
    (">=", ">="),
    ("=>", ">="),
    ("≥", ">="),
    ("<", "<"),
    (">", ">"),
)
_DECIMAL_SEPARATOR = {"it": ",", "en": "."}
_BODY = re.compile(r"[+-]?\d[\d.,]*")
# Digit groups of three after a 1-3 digit lead, e.g. 1.234.567 (str.format template).
_GROUPED = r"[1-9]\d{{0,2}}(?:{sep}\d{{3}})+"


class NumberParseError(ValueError):
    """The text is not a number this module can read without guessing."""


@dataclass(frozen=True)
class ParsedNumber:
    value: Decimal
    comparator: Comparator | None = None


def parse_number(text: str, locale: NumberLocale | None = None) -> ParsedNumber:
    """Parse ``text`` such as ``"1,2"``, ``"< 0.5"``, ``"≥90"`` or ``"250.000"``."""
    s = text.strip().replace("\u2212", "-")
    comparator: Comparator | None = None
    for prefix, comp in _COMPARATORS:
        if s.startswith(prefix):
            comparator, s = comp, s[len(prefix) :].strip()
            break
    s = re.sub(r"(?<=\d)[\s\u00a0\u202f'](?=\d{3}\b)", "", s)  # 1 234,5 and 1'234.5
    if not _BODY.fullmatch(s):
        raise NumberParseError(f"not a number: {text!r}")
    try:
        return ParsedNumber(Decimal(_to_plain(s, locale, text)), comparator)
    except InvalidOperation as exc:
        raise NumberParseError(f"not a number: {text!r}") from exc


def _to_plain(s: str, locale: NumberLocale | None, original: str) -> str:
    sign = ""
    if s[0] in "+-":
        sign, s = s[0], s[1:]
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        decimal = "." if s.rfind(".") > s.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        integer, _, fraction = s.rpartition(decimal)
        if not _is_grouped(integer, thousands):
            raise NumberParseError(f"inconsistent separators: {original!r}")
        return sign + integer.replace(thousands, "") + "." + fraction
    if not (has_dot or has_comma):
        return sign + s
    sep = "." if has_dot else ","
    if s.count(sep) > 1:
        if not _is_grouped(s, sep):
            raise NumberParseError(f"inconsistent separators: {original!r}")
        return sign + s.replace(sep, "")
    could_be_thousands = _is_grouped(s, sep)
    if locale is None:
        if could_be_thousands:
            raise NumberParseError(f"ambiguous without a locale: {original!r}")
        return sign + s.replace(sep, ".")
    if sep == _DECIMAL_SEPARATOR[locale] or not could_be_thousands:
        return sign + s.replace(sep, ".")
    return sign + s.replace(sep, "")


def _is_grouped(s: str, sep: str) -> bool:
    return re.fullmatch(_GROUPED.format(sep=re.escape(sep)), s) is not None
