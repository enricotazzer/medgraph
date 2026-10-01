"""Timestamps: FHIR ``date``/``dateTime`` values and dates printed on lab reports.

Nothing here guesses a timezone. A FHIR time of day without a UTC offset is rejected (the
FHIR specification requires one), and a numeric report date is read day-first or month-first
only according to the stated locale.
"""

import datetime as dt
import re
from typing import Literal

from medgraph.records import Timepoint

ReportLocale = Literal["it", "en-GB", "en-US"]

_FHIR_TIME = re.compile(
    r"(?P<year>\d{4})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2})"
    r"(?:T(?P<time>\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)(?P<tz>Z|[+-]\d{2}:\d{2})?)?)?)?"
)
_NUMERIC_DATE = re.compile(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})")
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_TEXT_DAY_FIRST = re.compile(r"(\d{1,2})\s+([a-zà-ù]+)\.?\s+(\d{4})")
_TEXT_MONTH_FIRST = re.compile(r"([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})")

_MONTHS_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
    "gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
    "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12,
}  # fmt: skip
_MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7,
    "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip


class TimeParseError(ValueError):
    """The text is not a time this module can read without guessing."""


def parse_fhir_time(value: str) -> Timepoint:
    """Parse a FHIR ``date``, ``dateTime`` or ``instant``, keeping its precision."""
    match = _FHIR_TIME.fullmatch(value.strip())
    if match is None:
        raise TimeParseError(f"not a FHIR date/dateTime: {value!r}")
    year, month, day = match["year"], match["month"], match["day"]
    try:
        if month is None:
            return Timepoint(date=dt.date(int(year), 1, 1), precision="year")
        if day is None:
            return Timepoint(date=dt.date(int(year), int(month), 1), precision="month")
        date = dt.date(int(year), int(month), int(day))
        if match["time"] is None:
            return Timepoint(date=date, precision="day")
        if match["tz"] is None:
            raise TimeParseError(f"time of day without a UTC offset: {value!r}")
        tz = "+00:00" if match["tz"] == "Z" else match["tz"]
        instant = dt.datetime.fromisoformat(f"{date.isoformat()}T{match['time']}{tz}")
    except ValueError as exc:  # impossible calendar values such as 2025-02-30
        raise TimeParseError(f"invalid date in {value!r}: {exc}") from exc
    return Timepoint(date=date, precision="instant", instant=instant)


def parse_report_date(text: str, locale: ReportLocale) -> dt.date:
    """Parse a date as printed on a lab report in ``locale``.

    Accepts ISO dates, numeric dates with a four-digit year (day-first for ``it`` and
    ``en-GB``, month-first for ``en-US``) and dates with month names in Italian or English.
    """
    t = " ".join(text.strip().lower().split())
    try:
        if m := _ISO_DATE.fullmatch(t):
            return dt.date(int(m[1]), int(m[2]), int(m[3]))
        if m := _NUMERIC_DATE.fullmatch(t):
            first, second, year = int(m[1]), int(m[2]), int(m[3])
            day, month = (second, first) if locale == "en-US" else (first, second)
            return dt.date(year, month, day)
        months = _MONTHS_IT if locale == "it" else _MONTHS_EN
        if (m := _TEXT_DAY_FIRST.fullmatch(t)) and m[2] in months:
            return dt.date(int(m[3]), months[m[2]], int(m[1]))
        if (m := _TEXT_MONTH_FIRST.fullmatch(t)) and m[1] in months:
            return dt.date(int(m[3]), months[m[1]], int(m[2]))
    except ValueError as exc:
        raise TimeParseError(f"invalid date {text!r}: {exc}") from exc
    raise TimeParseError(f"unrecognised {locale} date: {text!r}")
