import datetime as dt
import random
from decimal import Decimal
from pathlib import Path

import pytest

import generate_lab_reports as gen
from lab_report_catalog import TESTS
from medgraph.ingest.pdf import pdf_text
from medgraph.normalize.analyte_names import analyte_for_name, known_names, name_key

FAMILIES: tuple[gen.Family, ...] = (
    "table",
    "dotted",
    "colon",
    "sections",
    "two_column",
    "narrative",
)
STYLES = ("it", "en-US", "en-GB")


def source_values() -> list[gen.SourceValue]:
    def v(test: str, loinc: str, value: str, unit: str) -> gen.SourceValue:
        return gen.SourceValue(
            test=test, loinc=loinc, value=Decimal(value), unit=unit, qualitative=None
        )

    return [
        v("hemoglobin", "718-7", "13.46", "g/dL"),
        v("hematocrit", "4544-3", "41.23", "%"),
        v("creatinine", "2160-0", "1.3245", "mg/dL"),
        v("egfr", "33914-3", "45.3", "mL/min/{1.73_m2}"),
        v("ferritin", "2276-4", "45.2", "ug/L"),
        v("urine_acr", "14959-1", "35.24", "mg/g"),
        v("glucose", "2345-7", "98.2", "mg/dL"),
        v("sodium", "2951-2", "140.1", "mmol/L"),
        gen.SourceValue(
            test="nitrite", loinc="5802-4", value=None, unit="", qualitative="negative"
        ),
    ]


def squash(text: str) -> str:
    return " ".join(text.split())


def render(
    family: gen.Family, style: str, split: str = "dev"
) -> tuple[list[str], list[gen.TruthRow]]:
    rnd = random.Random(1)
    rows = gen.build_rows(source_values(), style, "F", split, 0.5, 15, rnd)
    return gen.render_rows(rows, family, gen.language_of(style), rnd)


# --- catalog ---------------------------------------------------------------------------


def test_seen_names_map_and_held_out_names_are_not_in_the_name_table() -> None:
    table = known_names()
    for test in TESTS:
        for names in (test.names_en, test.names_it):
            for name in names.seen:
                assert analyte_for_name(name) == test.registry_key, (test.key, name)
            for name in names.held_out:
                assert test.registry_key is not None
                assert name_key(name) not in table, (test.key, name)


def test_every_numeric_test_has_printing_ranges_and_a_source_unit() -> None:
    for test in TESTS:
        assert set(test.printing) == set(STYLES), test.key
        if not test.qualitative:
            assert test.key in gen.SOURCE_UNITS
            assert test.ranges or test.sex_ranges, test.key


# --- formatting and truth ----------------------------------------------------------------


def test_number_and_date_formatting() -> None:
    assert gen.fmt_number(Decimal("1.325"), 2, "it") == "1,33"  # half up
    assert gen.fmt_number(Decimal("117.0"), 0, "en") == "117"
    date = dt.date(2025, 3, 7)
    assert gen.fmt_date(date, "it", False) == "07/03/2025"
    assert gen.fmt_date(date, "en-US", False) == "03/07/2025"
    assert gen.fmt_date(date, "en-GB", True) == "7 Mar 2025"
    assert gen.fmt_date(date, "it", True) == "7 marzo 2025"
    assert gen.fmt_range((Decimal("0.70"), Decimal("1.20")), "it") == "0,70 - 1,20"
    assert gen.fmt_range((None, Decimal(30)), "en") == "< 30"


def test_uk_reports_print_si_units_and_truth_derives_from_the_printed_value() -> None:
    _, truths = render("table", "en-GB")
    by_test = {t.test: t for t in truths}
    creatinine = by_test["creatinine"]
    assert (creatinine.value_text, creatinine.unit_text) == ("117", "µmol/L")  # 1.3245 x 88.4
    assert creatinine.canonical_value is not None
    assert Decimal(creatinine.canonical_value) == Decimal(117) / Decimal("88.4")
    hematocrit = by_test["hematocrit"]
    assert (hematocrit.value_text, hematocrit.unit_text) == ("0.41", "L/L")
    assert Decimal(hematocrit.canonical_value or "") == Decimal(41)
    assert by_test["urine_acr"].unit_text == "mg/mmol"
    assert by_test["hemoglobin"].flag_text == ""  # 135 g/L inside 120-155 (female)
    assert by_test["creatinine"].flag_text == "H"  # 117 umol/L above 44-80 (female)


def test_egfr_in_ml_per_min_is_printed_as_recorded_and_has_no_canonical_value() -> None:
    value = gen.SourceValue(
        test="egfr", loinc="33914-3", value=Decimal(22), unit="mL/min", qualitative=None
    )
    row = gen.truth_row(gen.BY_LOINC["33914-3"], value, "eGFR", False, "it", "M")
    assert (row.unit_text, row.canonical_value) == ("mL/min", None)


def test_held_out_names_appear_only_in_the_test_split() -> None:
    for style in STYLES:
        _, dev = render("table", style, "dev")
        assert all(t.name_seen in (True, None) for t in dev)
    test_rows = [t for style in STYLES for _ in range(5) for t in render("table", style, "test")[1]]
    assert any(t.name_seen is False for t in test_rows)


# --- the invariant the evaluation rests on -------------------------------------------------


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("family", FAMILIES)
def test_every_truth_field_is_printed_verbatim(
    family: gen.Family, style: str, tmp_path: Path
) -> None:
    lines, truths = render(family, style)
    text = squash("\n".join(lines))
    pdf_path = tmp_path / "report.pdf"
    gen.render_pdf(lines, pdf_path, 7.5 if family == "two_column" else 9)
    from_pdf = squash(pdf_text(pdf_path))
    for truth in truths:
        fields = (
            truth.analyte_text,
            truth.value_text,
            truth.unit_text,
            truth.range_text,
            truth.flag_text,
        )
        for field in filter(None, fields):
            assert squash(field) in text, (family, style, field)
            assert squash(field) in from_pdf, (family, style, field, "pdf")


def test_pdf_rendering_is_byte_reproducible(tmp_path: Path) -> None:
    lines, _ = render("table", "it")
    gen.render_pdf(lines, tmp_path / "a.pdf", 9)
    gen.render_pdf(lines, tmp_path / "b.pdf", 9)
    assert (tmp_path / "a.pdf").read_bytes() == (tmp_path / "b.pdf").read_bytes()


def test_report_specs_balance_languages() -> None:
    cfg = gen.ReportsConfig(
        name="t",
        source_cohort="c",
        seed=1,
        max_rows=10,
        test_patient_fraction=0.5,
        dev=gen.SplitConfig(families=("table",), reports_per_cell=2, held_out_name_rate=0),
        test=gen.SplitConfig(
            families=("table", "narrative"), reports_per_cell=2, held_out_name_rate=0.5
        ),
    )
    specs = list(gen.report_specs(cfg))
    assert len(specs) == 2 * 2 + 2 * 2 * 2
    ids = [f"{s}-{f}-{st}-{k}" for s, f, st, k in specs]
    assert len(set(ids)) == len(ids)
    dev_styles = [st for s, _, st, _ in specs if s == "dev"]
    assert dev_styles.count("it") == 2
