"""Lab reports (plain text, or PDFs with a text layer) to lab results.

Two steps, kept apart on purpose:

1. **Transcription** copies each result row exactly as printed: analyte name, value, unit,
   reference range and flag. This is the language task: an LLM does it
   (:func:`transcribe_with_llm`), and :func:`transcribe_with_rules` is the rule-based baseline.
2. **Interpretation** (:func:`interpret`) is deterministic code. A transcribed field must occur
   verbatim in the report or the whole row is rejected as ungrounded, which catches invented
   values. Number parsing, units, analyte names and conversion then reuse
   ``medgraph.normalize``, the same path FHIR observations take.
"""

import datetime as dt
import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from medgraph.agent.llm import LLMResponse, OllamaClient
from medgraph.normalize.analyte_names import analyte_for_name
from medgraph.normalize.analytes import BY_KEY
from medgraph.normalize.labs import convert_quantity
from medgraph.normalize.numbers import NumberLocale, NumberParseError, parse_number
from medgraph.normalize.ranges import RangeParseError, parse_reference_range
from medgraph.normalize.time import ReportLocale, TimeParseError, parse_report_date
from medgraph.records import Comparator, Quantity, SourceRef

# --- transcription ---------------------------------------------------------------------


class TranscribedRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    analyte: str
    value: str
    unit: str = ""
    reference_range: str = ""
    flag: str = ""


class Transcription(BaseModel):
    model_config = ConfigDict(frozen=True)

    collection_date: str = ""
    rows: tuple[TranscribedRow, ...] = ()


# v3 (tuned on the dev split only): v2 put units and ranges inside value on colon layouts and
# turned flags into "<", ">" or "*" prefixes on the value.
PROMPT_VERSION = "transcribe-v3"
SYSTEM_PROMPT = """\
You transcribe laboratory reports into JSON.
For every test result in the report, output one row with five separate fields:
- analyte: the test name exactly as printed
- value: the result only: the number exactly as printed, with its original decimal \
separator, or words such as "Negative". Copy a < or > sign only if it is printed \
directly before the number. Never put a unit, a reference range or a flag in value.
- unit: the unit exactly as printed, or "" if there is none
- reference_range: the reference interval exactly as printed, or ""
- flag: the abnormality marker exactly as printed (for example H, L, * or an arrow), \
or "". A flag is not a < or > sign, and it never goes in value.
Also output collection_date: the specimen collection date exactly as printed, or "".
Example: the line "Sodium: 141 mmol/L (ref 135 - 145) H" becomes analyte "Sodium", \
value "141", unit "mmol/L", reference_range "135 - 145", flag "H".
Copy characters exactly. Do not translate, convert units, compute, correct or reorder \
anything, and do not add rows that are not in the report. Skip headers, addresses, \
comments and method notes."""

_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        field: {"type": "string"}
        for field in ("analyte", "value", "unit", "reference_range", "flag")
    },
    "required": ["analyte", "value", "unit", "reference_range", "flag"],
}
TRANSCRIPTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "collection_date": {"type": "string"},
        "rows": {"type": "array", "items": _ROW_SCHEMA},
    },
    "required": ["collection_date", "rows"],
}


def transcribe_with_llm(
    text: str, client: OllamaClient, num_ctx: int = 4096
) -> tuple[Transcription, LLMResponse]:
    response = client.chat_json(SYSTEM_PROMPT, text, TRANSCRIPTION_SCHEMA, num_ctx=num_ctx)
    return Transcription.model_validate(response.content), response


_VALUE = re.compile(
    r"(?:[<>]=?|≤|≥)?\s?\d[\d.,]*|negativ[oe]|positiv[oe]|tracce|trace|assente|absent",
    re.IGNORECASE,
)
_RANGE = re.compile(r"(?:[<>]=?|≤|≥)?\s?\d[\d.,]*(?:\s*(?:-|\u2013)\s*\d[\d.,]*)?")
_FLAG = re.compile(r"H|L|\*|\*\*|HH|LL|A|B")
_DATE_LINE = re.compile(
    r"(?:data (?:del )?prelievo|data di prelievo|collection date|date collected|collected(?: on)?)"
    r"\s*:?\s*(?P<date>\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2} [A-Za-z]+\.? \d{4}"
    r"|[A-Za-z]+\.? \d{1,2},? \d{4})",
    re.IGNORECASE,
)
_COLON_ROW = re.compile(
    rf"(?P<name>[^:]{{2,60}}):\s+(?P<value>{_VALUE.pattern})"
    r"(?:\s+(?P<unit>[^\s()*]+(?:\s[^\s()*]+)?))?"
    r"(?:\s+\((?:ref|rif)\.?:?\s+(?P<range>[^)]*)\))?"
    r"(?:\s+(?P<flag>H|L|\*{1,2}))?",
    re.IGNORECASE,
)
_RANGE_DECORATION = re.compile(
    r"^[\[(]?\s*(?:ref(?:erence)?(?: range| interval)?|rif(?:\.|erimento)?|v\.?r\.?)?\s*:?\s*"
    r"|\s*[\])]$",
    re.IGNORECASE,
)


def strip_range_decoration(text: str) -> str:
    """``[0.70 - 1.20]`` or ``(ref: 0.70-1.20)`` to the interval itself."""
    return _RANGE_DECORATION.sub("", text.strip()).strip()


def transcribe_with_rules(text: str) -> Transcription:
    """Rule-based baseline: one result per line, columns separated by two or more spaces,
    dot leaders or a colon. Built on the development layouts only."""
    rows: list[TranscribedRow] = []
    date = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not date and (m := _DATE_LINE.search(line)):
            date = m["date"]
            continue
        if m := _COLON_ROW.fullmatch(line):  # "Name: value unit (ref range) flag"
            rows.append(
                TranscribedRow(
                    analyte=m["name"].strip(),
                    value=m["value"],
                    unit=m["unit"] or "",
                    reference_range=m["range"] or "",
                    flag=m["flag"] or "",
                )
            )
            continue
        line = re.sub(r"\s*\.{3,}\s*", "  ", line)  # dot leaders
        cells = [c.strip() for c in re.split(r"\s{2,}|\t", line) if c.strip()]
        if len(cells) < 2 or not _VALUE.fullmatch(cells[1]):
            continue
        unit = range_ = flag = ""
        for cell in cells[2:]:
            if _FLAG.fullmatch(cell):
                flag = cell
            elif _RANGE.fullmatch(strip_range_decoration(cell)):
                range_ = cell
            elif not unit:
                unit = cell
        rows.append(
            TranscribedRow(
                analyte=cells[0], value=cells[1], unit=unit, reference_range=range_, flag=flag
            )
        )
    return Transcription(collection_date=date, rows=tuple(rows))


# --- interpretation --------------------------------------------------------------------

RowStatus = Literal[
    "ok",
    "implausible",
    "unit_unknown",
    "unit_incompatible",
    "non_numeric",
    "unparseable_value",
    "unmapped",  # analyte name not in the name table
    "ungrounded",  # a transcribed field does not occur in the report: rejected
]

_QUALITATIVE = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ ]*")
_IT_WORDS = (
    "risultato", "esame", "riferimento", "unità", "prelievo", "referto", "emoglobina",
    "creatinina", "glucosio", "ematocrito", "piastrine", "colesterolo", "pagina",
)  # fmt: skip
_EN_WORDS = (
    "result", "test", "reference", "units", "collected", "collection", "report", "hemoglobin",
    "haemoglobin", "creatinine", "glucose", "platelets", "cholesterol", "page",
)  # fmt: skip
_SI_UNITS = re.compile(r"µmol/l|umol/l|mg/mmol|(?<![a-z])g/l\b|(?<![a-z])l/l\b", re.IGNORECASE)


class ReportRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int
    source: SourceRef
    analyte_text: str
    value_text: str
    unit_text: str
    range_text: str
    flag_text: str
    status: RowStatus
    analyte: str | None = None  # registry key
    value: Decimal | None = None  # canonical unit
    unit: str | None = None
    comparator: Comparator | None = None
    reference_low: Decimal | None = None  # canonical unit
    reference_high: Decimal | None = None
    detail: str | None = None


class ReportResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    number_locale: NumberLocale
    date_locale: ReportLocale
    collection_date: dt.date | None
    collection_date_text: str
    rows: tuple[ReportRow, ...]
    issues: tuple[str, ...] = ()


def detect_locale(text: str) -> tuple[NumberLocale, ReportLocale]:
    """Italian or English by vocabulary; English dates are read day-first when SI units
    (umol/L, g/L, mg/mmol) suggest a UK-style lab, month-first otherwise."""
    lowered = text.lower()
    italian = sum(lowered.count(w) for w in _IT_WORDS)
    english = sum(lowered.count(w) for w in _EN_WORDS)
    if italian > english:
        return "it", "it"
    return "en", "en-GB" if _SI_UNITS.search(text) else "en-US"


def _squash(text: str) -> str:
    return " ".join(text.split())


def interpret(transcription: Transcription, text: str, source: str) -> ReportResult:
    """Turn a transcription of ``text`` into normalized rows; never trusts the transcriber."""
    number_locale, date_locale = detect_locale(text)
    haystack = _squash(text)
    issues: list[str] = []
    collection_date = None
    if transcription.collection_date:
        try:
            collection_date = parse_report_date(transcription.collection_date, date_locale)
        except TimeParseError as exc:
            issues.append(f"collection date not read: {exc}")
    rows = tuple(
        _interpret_row(i, row, haystack, number_locale, source)
        for i, row in enumerate(transcription.rows)
    )
    return ReportResult(
        source=source,
        number_locale=number_locale,
        date_locale=date_locale,
        collection_date=collection_date,
        collection_date_text=transcription.collection_date,
        rows=rows,
        issues=tuple(issues),
    )


_NUMBER_THEN_UNIT = re.compile(r"(?P<number>(?:[<>]=?|≤|≥)?\s?\d[\d.,]*)\s+(?P<unit>\S.*)")


def split_value_unit(value: str, unit: str) -> tuple[str, str]:
    """Separate a unit that a transcriber left inside the value (``"130 g/L"``).

    Mechanical only: strips exactly the transcribed unit from the end of the value, or, when
    no unit was transcribed, splits a leading number from what follows it.
    """
    v = value.strip()
    if unit and v.endswith(unit) and v != unit:
        return v[: -len(unit)].strip(), unit
    if not unit and (m := _NUMBER_THEN_UNIT.fullmatch(v)):
        return m["number"], m["unit"]
    return v, unit


def _interpret_row(
    index: int, row: TranscribedRow, haystack: str, locale: NumberLocale, source: str
) -> ReportRow:
    base: dict[str, Any] = {
        "index": index,
        "source": SourceRef(
            source=source, resource_type="LabReportRow", resource_id=f"row-{index}"
        ),
        "analyte_text": row.analyte,
        "value_text": row.value,
        "unit_text": row.unit,
        "range_text": row.reference_range,
        "flag_text": row.flag,
    }
    fields = (row.analyte, row.value, row.unit, row.reference_range, row.flag)
    missing = [f for f in fields if f and _squash(f) not in haystack]
    if missing or not row.analyte or not row.value:
        detail = f"not found in the report: {missing!r}" if missing else "empty analyte or value"
        return ReportRow(**base, status="ungrounded", detail=detail)

    key = analyte_for_name(row.analyte)
    if key is None:
        return ReportRow(**base, status="unmapped", detail="analyte name not in the name table")
    analyte = BY_KEY[key]
    base["analyte"] = key
    value_text, unit_text = split_value_unit(row.value, row.unit)
    if _QUALITATIVE.fullmatch(value_text):
        return ReportRow(**base, status="non_numeric")
    try:
        number = parse_number(value_text, locale)
    except NumberParseError as exc:
        return ReportRow(**base, status="unparseable_value", detail=str(exc))

    conversion = convert_quantity(
        analyte, Quantity(value=number.value, unit=unit_text or None, comparator=number.comparator)
    )
    low = high = None
    if conversion.ucum is not None and row.reference_range:
        factor = analyte.to_canonical.get(conversion.ucum)
        try:
            parsed = parse_reference_range(strip_range_decoration(row.reference_range), locale)
        except (RangeParseError, NumberParseError):
            parsed = None
        if parsed is not None and factor is not None:
            low = parsed.low.value * factor if parsed.low else None
            high = parsed.high.value * factor if parsed.high else None
    return ReportRow(
        **base,
        status=conversion.status,
        value=conversion.value,
        unit=analyte.canonical_unit if conversion.value is not None else None,
        comparator=number.comparator,
        reference_low=low,
        reference_high=high,
        detail=conversion.detail,
    )
