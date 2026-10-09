"""Every number a rule uses appears in the quote of the source it cites, and the formulas give
the published results."""

import math
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from medgraph.rag.dailymed import LabelLock
from medgraph.rag.documents import load_documents
from medgraph.rag.quotes import locator_pages
from medgraph.rules import anaemia, ckd, egfr
from medgraph.rules.ckd import albuminuria_category, gfr_category
from medgraph.rules.egfr import ckd_epi_2021, mdrd_2006, whole
from medgraph.rules.sources import SOURCES

DOCUMENTS = Path(__file__).parents[2] / "configs" / "knowledge" / "documents.yaml"
LOCK = DOCUMENTS.with_name("labels-dev-1000.lock.json")


def quote(source_id: str) -> str:
    return SOURCES[source_id].quote


def test_every_source_has_a_document_locator_and_quote() -> None:
    stored = {d.id: d for d in load_documents(DOCUMENTS)}
    locked = {
        f"label:{label.setid}@v{label.version}"
        for label in LabelLock.model_validate_json(LOCK.read_text(encoding="utf-8"))
        .labels()
        .values()
    }
    for s in SOURCES.values():
        assert s.document
        assert s.locator
        assert len(s.quote) > 40
        assert s.locator in s.citation
        if s.stored.startswith("label:"):
            # A label statement cites the version the committed lock pins, and its section.
            assert s.stored in locked, f"{s.id}: {s.stored} is not the pinned label version"
            assert s.locator.startswith("section ")
            assert s.stored.split("@")[0].removeprefix("label:") in s.document
        else:
            assert s.stored in stored
            if stored[s.stored].format == "pdf":
                assert locator_pages(s.locator), f"{s.id}: the locator names no page"


def test_ckd_epi_coefficients_are_the_cited_ones() -> None:
    q = quote("ckd-epi-2021")
    for number in ("142", "0.7", "0.9", "-0.241", "-0.302", "-1.200", "0.9938", "1.012", "18"):
        assert number in q, number
    assert str(egfr.CONSTANT) in q
    assert str(egfr.AGE_FACTOR) in q
    assert str(egfr.FEMALE_FACTOR) in q
    assert str(egfr.EXPONENT_ABOVE_KAPPA) in q
    assert {str(v) for v in egfr.KAPPA.values()} <= set(q.replace(",", " ").split()) | {
        "0.7",
        "0.9",
    }


def test_kdigo_thresholds_are_the_cited_ones() -> None:
    criteria = quote("kdigo-2024-criteria")
    assert f"GFR <{ckd.GFR_ABNORMAL_BELOW} ml/min" in criteria
    assert f"ACR >={ckd.ACR_ABNORMAL_FROM} mg/g" in criteria
    assert "minimum of 3 months" in criteria
    categories = quote("kdigo-2024-categories").replace("\u2013", "-").replace("\u2265", ">=")
    for text in (
        "G1 >=90",
        "G2 60-89",
        "G3a 45-59",
        "G3b 30-44",
        "G4 15-29",
        "G5 Kidney failure <15",
    ):
        assert text in categories
    assert f"<{ckd.ACR_ABNORMAL_FROM} mg/g" in categories
    assert "30-300 mg/g" in categories
    assert ">300 mg/g" in categories
    rows = SOURCES["kdigo-2024-categories"].table.replace("\u2013", "-").split("; ")
    assert "A1 Normal to mildly increased <30 mg/g" in rows
    assert "A3 Severely increased >300 mg/g" in rows


def test_who_cutoffs_are_the_cited_ones() -> None:
    q = quote("who-2024-cutoffs").replace("\u2013", "-")
    for months, sex, expected in [
        (6, None, "Children, 6-23 months <105"),
        (24, None, "Children, 24-59 months <110"),
        (60, None, "Children, 5-11 years <115"),
        (12 * 12, None, "Children, 12-14 years, boys <120"),
        (15 * 12, "female", "Adults, 15-65 years, nonpregnant women <120"),
        (15 * 12, "male", "Adults, 15-65 years, men <130"),
    ]:
        group = anaemia.who_group(months, sex, pregnant=False)
        assert isinstance(group, anaemia.Group)
        assert expected in q
        assert expected.endswith(f"<{group.cutoffs[0]}")
    assert SOURCES["who-2024-cutoffs"].table.endswith(
        "first trimester <110; second trimester <105; third trimester <110"
    )
    assert (Decimal(110), Decimal(105), Decimal(110)) == anaemia.PREGNANCY_CUTOFFS
    assert f"Smoker, quantity unknown {anaemia.SMOKER_ADJUSTMENT}" in quote("who-2024-smoking")


# --- CKD-EPI 2021 ------------------------------------------------------------------------------


def reference(scr: float, age: int, female: bool) -> float:
    """The published equation, written independently in floating point."""
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    value = 142 * min(scr / kappa, 1) ** alpha * max(scr / kappa, 1) ** -1.200 * 0.9938**age
    return value * (1.012 if female else 1)


@pytest.mark.parametrize(
    ("scr", "age", "sex", "expected"),
    [
        # Hand-computed: 142 x (1.0/0.9)^-1.2 x 0.9938^50 = 91.69
        ("1.0", 50, "male", Decimal("91.69")),
        # 142 x (1.0/0.7)^-1.2 x 0.9938^60 x 1.012 = 64.50
        ("1.0", 60, "female", Decimal("64.50")),
        # Below kappa the alpha exponent applies:
        # 142 x (0.6/0.7)^-0.241 x 0.9938^30 x 1.012 = 142 x 1.03785 x 0.82979 x 1.012 = 123.76
        ("0.6", 30, "female", Decimal("123.76")),
    ],
)
def test_ckd_epi_worked_examples(scr: str, age: int, sex: str, expected: Decimal) -> None:
    value = ckd_epi_2021(Decimal(scr), age, sex)  # type: ignore[arg-type]
    assert value.quantize(Decimal("0.01")) == expected
    assert math.isclose(float(value), reference(float(scr), age, sex == "female"), rel_tol=1e-12)


@given(
    scr=st.decimals(min_value="0.2", max_value="15", places=2),
    age=st.integers(min_value=18, max_value=100),
    female=st.booleans(),
)
def test_ckd_epi_matches_the_independent_reference(scr: Decimal, age: int, female: bool) -> None:
    sex = "female" if female else "male"
    assert math.isclose(
        float(ckd_epi_2021(scr, age, sex)), reference(float(scr), age, female), rel_tol=1e-12
    )


@given(
    scr=st.decimals(min_value="0.2", max_value="14", places=2),
    age=st.integers(min_value=18, max_value=99),
    female=st.booleans(),
)
def test_ckd_epi_falls_with_creatinine_and_age(scr: Decimal, age: int, female: bool) -> None:
    sex = "female" if female else "male"
    base = ckd_epi_2021(scr, age, sex)
    assert ckd_epi_2021(scr + Decimal("0.1"), age, sex) < base
    assert ckd_epi_2021(scr, age + 1, sex) < base


def test_ckd_epi_is_continuous_at_kappa_and_refuses_children() -> None:
    at = ckd_epi_2021(Decimal("0.7"), 40, "female")
    assert abs(ckd_epi_2021(Decimal("0.7000001"), 40, "female") - at) < Decimal("0.001")
    with pytest.raises(ValueError, match="18"):
        ckd_epi_2021(Decimal("0.5"), 17, "female")
    with pytest.raises(ValueError, match="positive"):
        ckd_epi_2021(Decimal(0), 40, "male")


def test_mdrd_worked_example() -> None:
    # 175 x 1.0^-1.154 x 50^-0.203 = 175 x 0.45197 = 79.09
    assert mdrd_2006(Decimal("1.0"), 50, "male").quantize(Decimal("0.01")) == Decimal("79.09")
    female = mdrd_2006(Decimal("1.0"), 50, "female") / mdrd_2006(Decimal("1.0"), 50, "male")
    assert female.quantize(Decimal("0.001")) == Decimal("0.742")


@pytest.mark.parametrize(
    ("value", "rounded", "category"),
    [
        ("90", 90, "G1"),
        ("89.4", 89, "G2"),
        ("59.5", 60, "G2"),  # categories use the whole number, rounded half up
        ("59.49", 59, "G3a"),
        ("45", 45, "G3a"),
        ("44.9", 45, "G3a"),
        ("44.4", 44, "G3b"),
        ("30", 30, "G3b"),
        ("29", 29, "G4"),
        ("15", 15, "G4"),
        ("14.4", 14, "G5"),
    ],
)
def test_gfr_categories(value: str, rounded: int, category: str) -> None:
    assert whole(Decimal(value)) == rounded
    assert gfr_category(rounded) == category


@pytest.mark.parametrize(
    ("acr", "category"),
    [("29.9", "A1"), ("30", "A2"), ("300", "A2"), ("300.1", "A3")],
)
def test_albuminuria_categories(acr: str, category: str) -> None:
    assert albuminuria_category(Decimal(acr)) == category


@pytest.mark.parametrize(
    ("value", "comparator", "abnormal"),
    [
        ("30", None, True),
        ("29.9", None, False),
        ("30", "<", False),  # below 30: normal
        ("31", "<", None),  # below 31: could be either
        ("30", "<=", None),
        ("30", ">=", True),
        ("29", ">", None),
    ],
)
def test_acr_with_comparators(value: str, comparator: str | None, abnormal: bool | None) -> None:
    assert ckd.acr_abnormal(Decimal(value), comparator) is abnormal  # type: ignore[arg-type]
