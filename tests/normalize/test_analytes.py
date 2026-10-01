from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from medgraph.normalize.analytes import ANALYTES, BY_KEY, BY_LOINC, Analyte, analyte_for_loinc

CONVERSIONS = [(a.key, unit) for a in ANALYTES for unit in a.to_canonical]


def test_loinc_codes_are_unique_across_analytes() -> None:
    assert len(BY_LOINC) == sum(len(a.loinc) for a in ANALYTES)


@pytest.mark.parametrize("analyte", ANALYTES, ids=lambda a: a.key)
def test_registry_entries_are_consistent(analyte: Analyte) -> None:
    assert analyte.to_canonical[analyte.canonical_unit] == 1
    assert analyte.sanity_low < analyte.sanity_high
    assert all(factor > 0 for factor in analyte.to_canonical.values())
    non_trivial = any(f not in (1, 10, 100, Decimal("0.1")) for f in analyte.to_canonical.values())
    if non_trivial:
        assert analyte.conversion_source, "non-trivial factors need a cited source"


@pytest.mark.parametrize(("key", "unit"), CONVERSIONS)
@given(data=st.data())
def test_conversions_round_trip(key: str, unit: str, data: st.DataObject) -> None:
    factor = BY_KEY[key].to_canonical[unit]
    value = data.draw(st.decimals(min_value=Decimal("0.001"), max_value=Decimal(100_000), places=3))
    back = (value * factor) / factor
    assert abs(back - value) <= value * Decimal("1e-20")


@pytest.mark.parametrize(
    ("key", "unit", "value", "canonical"),
    [
        ("creatinine", "umol/L", "88.4", "1"),
        ("creatinine", "umol/L", "106.08", "1.2"),
        ("creatinine", "mg/L", "12", "1.2"),
        ("hemoglobin", "g/L", "135", "13.5"),
        ("hematocrit", "L/L", "0.42", "42"),
        ("urine_albumin", "mg/dL", "3", "30"),
        ("urine_acr", "mg/mmol", "3", "26.521"),  # KDIGO's "3 mg/mmol ~ 30 mg/g" is a rounding
    ],
)
def test_known_conversions(key: str, unit: str, value: str, canonical: str) -> None:
    converted = Decimal(value) * BY_KEY[key].to_canonical[unit]
    assert converted.quantize(Decimal(canonical)) == Decimal(canonical)


def test_egfr_in_ml_per_min_is_not_convertible() -> None:
    assert "mL/min" not in BY_KEY["egfr"].to_canonical


def test_lookup_by_loinc() -> None:
    analyte = analyte_for_loinc("33914-3")
    assert analyte is not None
    assert analyte.key == "egfr"
    assert analyte.loinc["33914-3"] == "MDRD"
    assert analyte_for_loinc("8302-2") is None
    assert analyte_for_loinc(None) is None
