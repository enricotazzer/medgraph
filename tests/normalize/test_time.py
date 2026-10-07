import datetime as dt

import pytest

from medgraph.normalize.time import (
    DateOrder,
    ReportLocale,
    TimeParseError,
    date_order_evidence,
    parse_fhir_time,
    parse_inferred_report_date,
    parse_report_date,
)


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


@pytest.mark.parametrize(
    ("text", "orders"),
    [
        ("Collected: 03/19/2019    Report date: 03/20/2019", {"month_first"}),
        ("Collection date: 18/02/2024", {"day_first"}),
        ("Data prelievo: 26.02.2023", {"day_first"}),
        ("Collected: 03/04/2025    Report date: 03/05/2025", set()),
        ("Date collected: 09/08/2024    Report date: 09/09/2024", set()),  # equal fields
        ("born 19/02/1980, collected 03/19/2025", {"day_first", "month_first"}),
        ("version 1.2.2025 and 2025-03-04", set()),  # ISO dates prove nothing about day/month
    ],
)
def test_date_order_evidence(text: str, orders: set[str]) -> None:
    assert date_order_evidence(text) == orders


@pytest.mark.parametrize(
    ("text", "order", "expected"),
    [
        ("19/02/2024", None, dt.date(2024, 2, 19)),  # its own digits settle it
        ("02/19/2024", None, dt.date(2024, 2, 19)),
        ("05/05/2024", None, dt.date(2024, 5, 5)),  # both readings agree
        ("03/04/2025", "day_first", dt.date(2025, 4, 3)),
        ("03/04/2025", "month_first", dt.date(2025, 3, 4)),
        ("16 settembre 2025", None, dt.date(2025, 9, 16)),
    ],
)
def test_inferred_report_dates(text: str, order: DateOrder | None, expected: dt.date) -> None:
    language = "it" if "settembre" in text else "en"
    assert parse_inferred_report_date(text, language, order) == expected


def test_an_ambiguous_inferred_date_is_refused() -> None:
    with pytest.raises(TimeParseError, match="date order unclear"):
        parse_inferred_report_date("03/04/2025", "en", None)
