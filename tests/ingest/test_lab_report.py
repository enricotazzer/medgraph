import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from medgraph.ingest.lab_report import (
    TranscribedRow,
    Transcription,
    interpret,
    misplacement,
    report_conventions,
    split_value_unit,
    strip_range_decoration,
    transcribe_with_rules,
)
from medgraph.ingest.pdf import NoTextLayerError, pdf_text

IT_REPORT = """\
LABORATORIO SINTETICO
Data prelievo: 07/03/2025    Data referto: 08/03/2025
ESAME                        RISULTATO  UNITA'            VALORI DI RIFERIMENTO
Emoglobina                   13,5       g/dL              12,0 - 15,5
Creatinina                   1,32       mg/dL             0,50 - 0,90           H
Proteine urinarie            Negativo                     Negativo
Emoglobina glicata (HbA1c)   6,1        %                 4,0 - 5,6             H
"""

GB_REPORT = """\
SYNTHETIC LABORATORY
Collection date: 07/03/2025
TEST                 RESULT     UNITS        REFERENCE RANGE
Creatinine           117        µmol/L       44 - 80        H
Haemoglobin          135        g/L          120 - 155
"""


def rows(*items: tuple[str, ...]) -> Transcription:
    fields = ("analyte", "value", "unit", "reference_range", "flag")
    return Transcription(
        collection_date="07/03/2025",
        rows=tuple(TranscribedRow(**dict(zip(fields, item, strict=False))) for item in items),
    )


# --- interpretation ------------------------------------------------------------------------


def test_italian_report_is_parsed_with_decimal_commas() -> None:
    transcription = rows(("Creatinina", "1,32", "mg/dL", "0,50 - 0,90", "H"))
    result = interpret(transcription, IT_REPORT, "report:x")
    assert (result.language, result.number_locale, result.date_order) == ("it", "it", "day_first")
    assert result.collection_date == dt.date(2025, 3, 7)
    (row,) = result.rows
    assert (row.status, row.analyte, row.value, row.unit) == (
        "ok",
        "creatinine",
        Decimal("1.32"),
        "mg/dL",
    )
    assert (row.reference_low, row.reference_high) == (Decimal("0.50"), Decimal("0.90"))
    assert row.source.resource_id == "row-0"


def test_uk_report_converts_si_units() -> None:
    transcription = rows(
        ("Creatinine", "117", "µmol/L", "44 - 80", "H"), ("Haemoglobin", "135", "g/L")
    )
    result = interpret(transcription, GB_REPORT, "report:x")
    assert result.date_order is None  # 07/03/2025 alone proves no order
    assert result.collection_date is None
    assert any("date order unclear" in issue for issue in result.issues)
    creatinine, hemoglobin = result.rows
    assert creatinine.value is not None
    assert abs(creatinine.value - Decimal(117) / Decimal("88.4")) < Decimal("1e-20")
    assert hemoglobin.value == Decimal("13.5")


def test_invented_values_are_rejected_as_ungrounded() -> None:
    transcription = rows(("Creatinina", "1,23", "mg/dL"), ("Ferritina", "45", "ng/mL"))
    result = interpret(transcription, IT_REPORT, "report:x")
    assert [r.status for r in result.rows] == ["ungrounded", "ungrounded"]
    assert result.rows[0].value is None


@pytest.mark.parametrize(
    ("row", "status"),
    [
        (("Proteine urinarie", "Negativo", "", "Negativo"), "non_numeric"),
        (("Emoglobina glicata (HbA1c)", "6,1", "%"), "unmapped"),
        (("Emoglobina", "13,5", ""), "unit_unknown"),
    ],
)
def test_row_statuses(row: tuple[str, ...], status: str) -> None:
    (result_row,) = interpret(rows(row), IT_REPORT, "report:x").rows
    assert result_row.status == status


def test_unreadable_date_is_an_issue_not_a_guess() -> None:
    transcription = Transcription(collection_date="08/03/2025 forse", rows=())
    result = interpret(transcription, IT_REPORT + "\n08/03/2025 forse", "report:x")
    assert result.collection_date is None
    assert result.issues


def test_report_conventions_come_from_evidence() -> None:
    assert report_conventions(IT_REPORT) == ("it", "it", "day_first", [])
    # English with only integers and an ambiguous date: nothing is proven, nothing guessed.
    assert report_conventions(GB_REPORT) == ("en", None, None, [])
    us = GB_REPORT.replace("07/03/2025", "03/19/2025").replace("135 ", "13.5")
    assert report_conventions(us) == ("en", "en", "month_first", [])
    _, separator, _, issues = report_conventions(us + "\nGlucose 5,4 mmol/L\n")
    assert (separator, issues) == (None, ["the report uses both decimal commas and decimal points"])


def test_an_ambiguous_date_is_read_when_another_date_settles_the_order() -> None:
    text = GB_REPORT.replace("07/03/2025", "07/03/2025    Report date: 19/03/2025")
    result = interpret(Transcription(collection_date="07/03/2025"), text, "r")
    assert (result.date_order, result.collection_date) == ("day_first", dt.date(2025, 3, 7))


def test_a_three_digit_group_is_refused_unless_the_report_proves_the_separator() -> None:
    row = ("Ferritin", "1.200", "ng/mL")
    text = "Collection date: 19/03/2025\nFerritin   1.200   ng/mL\n"
    (refused,) = interpret(rows(row), text, "r").rows
    assert (refused.status, refused.value) == ("unparseable_value", None)
    assert "ambiguous" in (refused.detail or "")
    proven = text + "Haemoglobin   13.5   g/dL\n"  # a decimal point elsewhere
    (read,) = interpret(rows(row), proven, "r").rows
    assert read.value == Decimal("1.200")


@pytest.mark.parametrize(
    ("line", "value", "unit", "range_text"),
    [
        ("Colesterolo totale: 214 mg/dL (rif. < 200) *", "< 200", "", "< 200"),
        ("Colesterolo totale: 214 mg/dL (rif. < 200) *", "< 200", "", ""),
        ("Colesterolo totale   214   mg/dL   < 200   *", "< 200", "mg/dL", "< 200"),
        ("eGFR  82 mL/min  [> 60]", "> 60", "mL/min", ""),
        ("Creatinina .......  1,32   mg/dL   (0,70 - 1,20)", "0,70", "mg/dL", "0,70 - 1,20"),
    ],
)
def test_a_value_copied_from_the_range_is_misplaced(
    line: str, value: str, unit: str, range_text: str
) -> None:
    name = line.split(":")[0].split("  ")[0].split(" ...")[0].strip()
    assert misplacement(name, value, unit, range_text, " ".join(line.split()))


@pytest.mark.parametrize(
    ("line", "name", "value", "unit", "range_text"),
    [
        (
            "Colesterolo totale: 214 mg/dL (rif. < 200) *",
            "Colesterolo totale",
            "214",
            "mg/dL",
            "< 200",
        ),
        (
            "Leucociti ........................  7,22    10^3/µL   (4,0 - 10,0)",
            "Leucociti",
            "7,22",
            "10^3/µL",
            "4,0 - 10,0",
        ),
        (
            "Test: Creatinine\nResult: 1.32\nUnit: mg/dL\nReference range: 0.70 - 1.20",
            "Creatinine",
            "1.32",
            "mg/dL",
            "0.70 - 1.20",
        ),
        (
            "Creatininemia 2,99 mg/dL (riferimento 0,70 -\n1,20), alto.",
            "Creatininemia",
            "2,99",
            "mg/dL",
            "0,70 - 1,20",
        ),
        ("HGB  13.0  g/dL  13.0 - 17.0", "HGB", "13.0", "g/dL", "13.0 - 17.0"),
    ],
)
def test_rows_in_order_are_not_misplaced(
    line: str, name: str, value: str, unit: str, range_text: str
) -> None:
    assert misplacement(name, value, unit, range_text, " ".join(line.split())) is None


def test_misplaced_rows_are_rejected_before_mapping() -> None:
    text = "Data prelievo: 07/03/2025\nColesterolo totale: 214 mg/dL (rif. < 200) *\n"
    (row,) = interpret(rows(("Colesterolo totale", "< 200", "", "< 200")), text, "r").rows
    assert (row.status, row.value, row.analyte) == ("misplaced", None, None)


def test_rows_keep_the_printed_quantity_with_its_digits() -> None:
    creatinine, haemoglobin = interpret(
        rows(("Creatinine", "117", "µmol/L", "44 - 80", "H"), ("Haemoglobin", "135", "g/L")),
        GB_REPORT,
        "r",
    ).rows
    assert creatinine.original is not None
    assert (creatinine.original.value, creatinine.original.unit) == (Decimal(117), "µmol/L")
    assert creatinine.original.code == "umol/L"
    assert haemoglobin.original is not None
    assert (haemoglobin.original.value, haemoglobin.original.code) == (Decimal(135), "g/L")
    it_row = interpret(rows(("Emoglobina", "13,5", "g/dL")), IT_REPORT, "r").rows[0]
    assert it_row.original is not None
    assert it_row.original.value.as_tuple().exponent == -1  # one printed decimal


def test_egfr_rows_carry_the_equation_their_name_states() -> None:
    text = "Data prelievo: 07/03/2025\nVFG (stima MDRD)  45  mL/min/1,73 m²  (> 60)\neGFR  45\n"
    stated, unstated = interpret(
        rows(("VFG (stima MDRD)", "45", "mL/min/1,73 m²", "> 60"), ("eGFR", "45")), text, "r"
    ).rows
    assert (stated.analyte, stated.method) == ("egfr", "MDRD")
    assert unstated.method == "unspecified"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[0,70 - 1,20]", "0,70 - 1,20"),
        ("(ref 0.70-1.20)", "0.70-1.20"),
        ("(rif. < 30)", "< 30"),
        ("Reference range: 13.0 - 17.0", "13.0 - 17.0"),
        ("12 - 16", "12 - 16"),
    ],
)
def test_strip_range_decoration(text: str, expected: str) -> None:
    assert strip_range_decoration(text) == expected


# --- rule-based baseline -----------------------------------------------------------------


def test_rules_read_table_rows_and_skip_headers() -> None:
    transcription = transcribe_with_rules(IT_REPORT)
    assert transcription.collection_date == "07/03/2025"
    assert [r.analyte for r in transcription.rows] == [
        "Emoglobina",
        "Creatinina",
        "Proteine urinarie",
        "Emoglobina glicata (HbA1c)",
    ]
    creatinine = transcription.rows[1]
    assert (creatinine.value, creatinine.unit, creatinine.reference_range, creatinine.flag) == (
        "1,32", "mg/dL", "0,50 - 0,90", "H",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "Creatinine ..................  1.32      mg/dL     (0.70 - 1.20)    *",
            ("Creatinine", "1.32", "mg/dL", "(0.70 - 1.20)", "*"),
        ),
        (
            "Creatinina: 1,32 mg/dL (rif. 0,50 - 0,90) *",
            ("Creatinina", "1,32", "mg/dL", "0,50 - 0,90", "*"),
        ),
        ("eGFR: 45 mL/min/1,73 m² (rif. > 60)", ("eGFR", "45", "mL/min/1,73 m²", "> 60", "")),
        (
            "  Hb                    13,5      g/dL     [12,0 - 15,5]",
            ("Hb", "13,5", "g/dL", "[12,0 - 15,5]", ""),
        ),
    ],
)
def test_rules_read_development_layouts(line: str, expected: tuple[str, ...]) -> None:
    (row,) = transcribe_with_rules(line).rows
    assert (row.analyte, row.value, row.unit, row.reference_range, row.flag) == expected


def test_rules_skip_method_notes_and_footers() -> None:
    text = "    metodo: fotometrico\nPagina 1 di 1\nReferto validato elettronicamente."
    assert transcribe_with_rules(text).rows == ()


# --- PDF -----------------------------------------------------------------------------------


def test_pdf_columns_stay_separated(tmp_path: Path) -> None:
    path = tmp_path / "r.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    pdf.setFont("Courier", 9)
    pdf.drawString(50, 700, "Volume corpuscolare medio    93,6       fL")
    pdf.save()
    assert "medio    93,6" in pdf_text(path)  # the column gap survives, unlike layout mode


def test_scanned_pdf_without_text_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    pdf.rect(50, 50, 200, 200, fill=1)  # an image-like page: no text layer
    pdf.save()
    with pytest.raises(NoTextLayerError, match="no text layer"):
        pdf_text(path)


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        ("130 g/L", "g/L", ("130", "g/L")),  # unit repeated inside the value
        ("1,32 mg/dL", "", ("1,32", "mg/dL")),  # unit only inside the value
        ("< 0,5 mg/L", "", ("< 0,5", "mg/L")),
        ("130", "g/L", ("130", "g/L")),
        ("Negativo", "", ("Negativo", "")),
        ("g/L", "g/L", ("g/L", "g/L")),  # nothing left to split: left for parsing to reject
    ],
)
def test_split_value_unit(value: str, unit: str, expected: tuple[str, str]) -> None:
    assert split_value_unit(value, unit) == expected


def test_unit_left_inside_the_value_is_still_converted() -> None:
    transcription = rows(("Haemoglobin", "135 g/L", "g/L"))
    (row,) = interpret(transcription, GB_REPORT.replace("135        g/L", "135 g/L"), "r").rows
    assert (row.status, row.value) == ("ok", Decimal("13.5"))
