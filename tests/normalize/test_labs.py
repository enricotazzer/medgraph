from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from medgraph.ingest.fhir import read_bundle_file
from medgraph.normalize.labs import normalize_labs, normalize_observation
from medgraph.records import (
    CodeableConcept,
    Coding,
    Observation,
    Quantity,
    ReferenceRange,
    SourceRef,
)

LOINC = "http://loinc.org"
SOURCE = SourceRef(source="sha256:test", resource_type="Observation", resource_id="o1")


def observation(loinc: str, **fields: Any) -> Observation:
    return Observation(
        id="o1",
        source=SOURCE,
        code=CodeableConcept(codings=(Coding(system=LOINC, code=loinc),)),
        **fields,
    )


def test_converts_to_canonical_unit_and_keeps_the_original() -> None:
    original = Quantity(value=Decimal("106.08"), unit="µmol/L")
    result = normalize_observation(observation("2160-0", value_quantity=original))
    assert result is not None
    assert result.analyte == "creatinine"
    assert result.status == "ok"
    assert result.unit == "mg/dL"
    assert result.value is not None
    assert abs(result.value - Decimal("1.2")) < Decimal("1e-20")
    assert result.original == original
    assert result.source == SOURCE


def test_ucum_code_is_preferred_over_display_unit() -> None:
    q = Quantity(value=Decimal(135), unit="grams per litre", code="g/L")
    result = normalize_observation(observation("718-7", value_quantity=q))
    assert result is not None
    assert (result.status, result.value) == ("ok", Decimal("13.5"))


def test_implausible_values_are_kept_but_marked() -> None:
    q = Quantity(value=Decimal("97.3"), unit="mg/dL", code="mg/dL")
    result = normalize_observation(observation("38483-4", value_quantity=q))
    assert result is not None
    assert result.status == "implausible"
    assert result.value == Decimal("97.3")
    assert result.detail is not None
    assert "sanity bounds" in result.detail


def test_egfr_not_normalized_to_body_surface_is_incompatible() -> None:
    q = Quantity(value=Decimal(22), unit="mL/min", code="mL/min")
    result = normalize_observation(observation("33914-3", value_quantity=q))
    assert result is not None
    assert result.status == "unit_incompatible"
    assert result.value is None
    assert result.method == "MDRD"
    assert result.original == q


def test_unknown_unit() -> None:
    q = Quantity(value=Decimal(1), code="{presence}")
    result = normalize_observation(observation("20454-5", value_quantity=q))
    assert result is not None
    assert (result.status, result.value) == ("unit_unknown", None)


def test_comparator_is_preserved() -> None:
    q = Quantity(value=Decimal(90), code="mL/min/{1.73_m2}", comparator=">")
    result = normalize_observation(observation("98979-8", value_quantity=q))
    assert result is not None
    assert (result.status, result.value, result.comparator) == ("ok", Decimal(90), ">")
    assert result.method == "CKD-EPI 2021 (race-free)"


def test_qualitative_and_missing_values() -> None:
    negative = CodeableConcept(text="Negative")
    qualitative = normalize_observation(observation("20454-5", value_concept=negative))
    assert qualitative is not None
    assert (qualitative.status, qualitative.qualitative) == ("non_numeric", "Negative")
    missing = normalize_observation(observation("718-7"))
    assert missing is not None
    assert missing.status == "missing_value"


def test_out_of_scope_observations_are_not_normalized() -> None:
    q = Quantity(value=Decimal(165), code="cm")
    assert normalize_observation(observation("8302-2", value_quantity=q)) is None


def test_reference_range_is_converted_and_unitless_bounds_take_the_value_unit() -> None:
    rr = ReferenceRange(
        low=Quantity(value=Decimal(120), code="g/L"), high=Quantity(value=Decimal(160))
    )
    q = Quantity(value=Decimal(110), code="g/L")
    result = normalize_observation(observation("718-7", value_quantity=q, reference_ranges=(rr,)))
    assert result is not None
    assert (result.reference_low, result.reference_high) == (Decimal(12), Decimal(16))


def test_normalize_labs_on_fixture(fhir_fixture_dir: Path) -> None:
    results = normalize_labs(read_bundle_file(fhir_fixture_dir / "patient-a.json"))
    by_analyte: dict[str, list[str]] = {}
    for r in results:
        by_analyte.setdefault(r.analyte, []).append(r.status)
    # Height and the stool test are out of scope.
    assert by_analyte == {
        "creatinine": ["ok", "ok", "ok"],
        "egfr": ["ok"],
        "hemoglobin": ["ok"],
        "urine_protein": ["non_numeric"],
    }


@pytest.mark.parametrize("status", ["ok", "implausible"])
def test_value_is_present_exactly_when_converted(status: str) -> None:
    value = "1.0" if status == "ok" else "99"
    q = Quantity(value=Decimal(value), code="mg/dL")
    result = normalize_observation(observation("2160-0", value_quantity=q))
    assert result is not None
    assert result.status == status
    assert result.value is not None
    assert result.unit == "mg/dL"
