import datetime as dt

import pytest

from medgraph.normalize.time import ReportLocale, TimeParseError, parse_fhir_time, parse_report_date


@pytest.mark.parametrize(
    ("value", "date", "precision"),
    [
        ("2025", dt.date(2025, 1, 1), "year"),
        ("2025-03", dt.date(2025, 3, 1), "month"),
        ("2025-03-07", dt.date(2025, 3, 7), "day"),
    ],
)
def test_partial_fhir_dates_keep_their_precision(value: str, date: dt.date, precision: str) -> None:
    tp = parse_fhir_time(value)
    assert (tp.date, tp.precision, tp.instant) == (date, precision, None)


@pytest.mark.parametrize(
    ("value", "offset_hours"),
    [
        ("2025-03-07T10:20:30+02:00", 2),
        ("2025-03-07T10:20:30Z", 0),
        ("2025-03-07T10:20:30.123-05:00", -5),
    ],
)
def test_fhir_instants_are_timezone_aware(value: str, offset_hours: int) -> None:
    tp = parse_fhir_time(value)
    assert tp.precision == "instant"
    assert tp.instant is not None
    assert tp.instant.utcoffset() == dt.timedelta(hours=offset_hours)
    assert tp.date == dt.date(2025, 3, 7)  # the calendar date as written, not converted


@pytest.mark.parametrize(
    "value",
    ["2025-03-07T10:20:30", "2025-02-30", "07/03/2025", "", "2025-13"],
)
def test_fhir_times_that_would_need_guessing_are_rejected(value: str) -> None:
    with pytest.raises(TimeParseError):
        parse_fhir_time(value)


@pytest.mark.parametrize(
    ("text", "locale", "expected"),
    [
        ("07/03/2025", "it", dt.date(2025, 3, 7)),
        ("07/03/2025", "en-GB", dt.date(2025, 3, 7)),
        ("07/03/2025", "en-US", dt.date(2025, 7, 3)),
        ("07.03.2025", "it", dt.date(2025, 3, 7)),
        ("2025-03-07", "en-US", dt.date(2025, 3, 7)),
        ("7 marzo 2025", "it", dt.date(2025, 3, 7)),
        ("7 mar. 2025", "it", dt.date(2025, 3, 7)),
        ("7 Mar 2025", "en-GB", dt.date(2025, 3, 7)),
        ("March 7, 2025", "en-US", dt.date(2025, 3, 7)),
    ],
)
def test_report_dates(text: str, locale: ReportLocale, expected: dt.date) -> None:
    assert parse_report_date(text, locale) == expected


@pytest.mark.parametrize(
    ("text", "locale"),
    [
        ("07/03/25", "it"),  # two-digit year
        ("31/02/2025", "it"),
        ("7 marzo 2025", "en-GB"),  # Italian month name in an English report
        ("tomorrow", "en-US"),
    ],
)
def test_report_dates_that_would_need_guessing_are_rejected(
    text: str, locale: ReportLocale
) -> None:
    with pytest.raises(TimeParseError):
        parse_report_date(text, locale)
