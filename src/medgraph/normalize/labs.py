"""Observations of in-scope analytes to :class:`LabResult` in canonical units.

The original value and unit are always kept next to the canonical ones. ``status`` says
whether a rule may use the value: only ``"ok"`` results are eligible. Every other status
names the reason, so a flag can report "one creatinine value could not be used: unknown unit"
instead of silently ignoring it.
"""

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from medgraph.normalize.analytes import Analyte, analyte_for_loinc
from medgraph.normalize.units import to_ucum
from medgraph.records import (
    Comparator,
    Observation,
    PatientRecord,
    Quantity,
    SourceRef,
    Timepoint,
)

LOINC = "http://loinc.org"

LabStatus = Literal[
    "ok",
    "implausible",  # converted, but outside the analyte's sanity bounds
    "unit_unknown",  # unit spelling not recognised
    "unit_incompatible",  # recognised unit that cannot be converted for this analyte
    "non_numeric",  # qualitative result, e.g. "negative"
    "missing_value",
]


class LabResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    analyte: str
    loinc: str
    method: str  # what distinguishes the LOINC code: specimen, method or eGFR equation
    observation_id: str
    source: SourceRef
    effective: Timepoint | None
    status: LabStatus
    value: Decimal | None = None  # canonical unit; set when the unit converts
    unit: str | None = None  # canonical UCUM unit, set with value
    comparator: Comparator | None = None
    original: Quantity | None = None
    qualitative: str | None = None
    reference_low: Decimal | None = None  # canonical unit
    reference_high: Decimal | None = None
    detail: str | None = None


def normalize_observation(obs: Observation) -> LabResult | None:
    """Normalize ``obs`` if it measures an in-scope analyte, else return ``None``."""
    loinc = obs.code.code(LOINC)
    analyte = analyte_for_loinc(loinc)
    if analyte is None or loinc is None:
        return None
    base: dict[str, Any] = {
        "analyte": analyte.key,
        "loinc": loinc,
        "method": analyte.loinc[loinc],
        "observation_id": obs.id,
        "source": obs.source,
        "effective": obs.effective,
    }
    q = obs.value_quantity
    if q is None:
        qualitative = obs.value_concept.label if obs.value_concept else obs.value_text
        status: LabStatus = "non_numeric" if qualitative else "missing_value"
        return LabResult(**base, status=status, qualitative=qualitative)

    ucum = to_ucum(q.code) or to_ucum(q.unit)
    if ucum is None:
        detail = f"unit {q.code or q.unit!r} not recognised"
        return LabResult(**base, status="unit_unknown", original=q, detail=detail)
    factor = analyte.to_canonical.get(ucum)
    if factor is None:
        detail = f"{ucum} cannot be converted to {analyte.canonical_unit}"
        return LabResult(**base, status="unit_incompatible", original=q, detail=detail)

    value = q.value * factor
    plausible = analyte.sanity_low <= value <= analyte.sanity_high
    bounds = f"{analyte.sanity_low}-{analyte.sanity_high} {analyte.canonical_unit}"
    low, high = _reference_bounds(obs, analyte, ucum)
    return LabResult(
        **base,
        status="ok" if plausible else "implausible",
        value=value,
        unit=analyte.canonical_unit,
        comparator=q.comparator,
        original=q,
        reference_low=low,
        reference_high=high,
        detail=None if plausible else f"outside sanity bounds {bounds}",
    )


def _reference_bounds(
    obs: Observation, analyte: Analyte, value_ucum: str
) -> tuple[Decimal | None, Decimal | None]:
    """The first reference range, in canonical units; a bound without a unit is taken to be
    in the value's unit."""
    if not obs.reference_ranges:
        return None, None
    rr = obs.reference_ranges[0]

    def convert(bound: Quantity | None) -> Decimal | None:
        if bound is None:
            return None
        ucum = to_ucum(bound.code) or to_ucum(bound.unit) or value_ucum
        factor = analyte.to_canonical.get(ucum)
        return bound.value * factor if factor is not None else None

    return convert(rr.low), convert(rr.high)


def normalize_labs(record: PatientRecord) -> tuple[LabResult, ...]:
    """Lab results for every in-scope observation in ``record``, in record order."""
    results = (normalize_observation(obs) for obs in record.observations)
    return tuple(r for r in results if r is not None)
