"""Reference ranges as printed by a laboratory, e.g. ``0,70 - 1,20`` or ``< 30``.

Ranges are only ever read from the source. medgraph never supplies a range of its own:
that would be an uncited medical statement.
"""

import re
from dataclasses import dataclass

from medgraph.normalize.numbers import NumberLocale, ParsedNumber, parse_number

_NUM = r"[+-]?\d[\d.,]*"
_BETWEEN = re.compile(rf"({_NUM})\s*(?:-|\u2013|\u2014|÷|to|a)\s*({_NUM})")
_UPPER = re.compile(rf"(?:<=?|≤|=<|fino a|up to|inferiore a|less than)\s*({_NUM})")
_LOWER = re.compile(rf"(?:>=?|≥|=>|superiore a|maggiore di|greater than|above)\s*({_NUM})")


class RangeParseError(ValueError):
    """The text is not a range this module can read."""


@dataclass(frozen=True)
class ParsedRange:
    low: ParsedNumber | None
    high: ParsedNumber | None


def parse_reference_range(text: str, locale: NumberLocale | None) -> ParsedRange:
    t = " ".join(text.strip().lower().split())
    if m := _BETWEEN.fullmatch(t):
        return ParsedRange(parse_number(m[1], locale), parse_number(m[2], locale))
    if m := _UPPER.fullmatch(t):
        return ParsedRange(None, parse_number(m[1], locale))
    if m := _LOWER.fullmatch(t):
        return ParsedRange(parse_number(m[1], locale), None)
    raise RangeParseError(f"unrecognised reference range: {text!r}")
