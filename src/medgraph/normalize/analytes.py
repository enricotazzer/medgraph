"""Registry of in-scope analytes: LOINC codes, canonical units, conversions and sanity bounds.

Scope is chronic kidney disease and anemia. Observations of other analytes stay as recorded
and are reported as unmapped, never forced into a nearby analyte.

Conversion factors are exact unit arithmetic unless an analyte's ``conversion_source`` says
otherwise. Sanity bounds are deliberately wide engineering limits for catching unit mix-ups
and corrupt values; they are not clinical criteria. A value outside them is kept and shown,
but marked so that no rule uses it.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Analyte:
    key: str
    name: str
    loinc: Mapping[str, str]  # LOINC code -> what distinguishes it (specimen, method, equation)
    canonical_unit: str  # UCUM
    to_canonical: Mapping[str, Decimal]  # UCUM unit -> factor that converts to canonical
    sanity_low: Decimal  # canonical unit, inclusive
    sanity_high: Decimal
    conversion_source: str | None = None


ANALYTES: tuple[Analyte, ...] = (
    Analyte(
        key="creatinine",
        name="Creatinine (serum/plasma/blood)",
        loinc={
            "2160-0": "serum or plasma, mass/volume",
            "38483-4": "blood, mass/volume",
            "14682-9": "serum or plasma, moles/volume",
        },
        canonical_unit="mg/dL",
        to_canonical={
            "mg/dL": Decimal(1),
            "mg/L": Decimal("0.1"),
            "umol/L": Decimal(1) / Decimal("88.4"),
        },
        sanity_low=Decimal("0.05"),
        sanity_high=Decimal(40),
        conversion_source=(
            "1 mg/dL = 88.4 umol/L (KDIGO 2024 CKD guideline, Kidney Int 2024;105(4S))"
        ),
    ),
    Analyte(
        key="egfr",
        name="Estimated GFR (as reported)",
        loinc={
            "33914-3": "MDRD",
            "48642-3": "MDRD, non-Black coefficient",
            "48643-1": "MDRD, Black coefficient",
            "62238-1": "CKD-EPI (2009)",
            "88293-6": "CKD-EPI (2009), Black coefficient",
            "88294-4": "CKD-EPI (2009), non-Black coefficient",
            "98979-8": "CKD-EPI 2021 (race-free)",
        },
        # mL/min (not normalized to body surface area) cannot be converted without the
        # patient's BSA, so it is deliberately absent.
        canonical_unit="mL/min/{1.73_m2}",
        to_canonical={"mL/min/{1.73_m2}": Decimal(1)},
        sanity_low=Decimal(1),
        sanity_high=Decimal(250),
    ),
    Analyte(
        key="urine_acr",
        name="Albumin/creatinine ratio, urine",
        loinc={
            "9318-7": "albumin/creatinine",
            "14959-1": "microalbumin/creatinine",
        },
        canonical_unit="mg/g",
        to_canonical={
            "mg/g": Decimal(1),
            "ug/mg": Decimal(1),
            "mg/mmol": Decimal(1000) / Decimal("113.12"),
        },
        sanity_low=Decimal(0),
        sanity_high=Decimal(30000),
        conversion_source="mg/mmol to mg/g via the molar mass of creatinine, 113.12 g/mol",
    ),
    Analyte(
        key="urine_albumin",
        name="Albumin, urine (concentration)",
        loinc={"14957-5": "microalbumin", "1754-1": "albumin"},
        canonical_unit="mg/L",
        to_canonical={"mg/L": Decimal(1), "ug/mL": Decimal(1), "mg/dL": Decimal(10)},
        sanity_low=Decimal(0),
        sanity_high=Decimal(30000),
    ),
    Analyte(
        key="urine_protein",
        name="Protein, urine",
        loinc={
            "2888-6": "mass/volume",
            "5804-0": "mass/volume, test strip",
            "20454-5": "presence, test strip (qualitative)",
        },
        canonical_unit="mg/dL",
        to_canonical={"mg/dL": Decimal(1), "mg/L": Decimal("0.1"), "g/L": Decimal(100)},
        sanity_low=Decimal(0),
        sanity_high=Decimal(5000),
    ),
    Analyte(
        key="hemoglobin",
        name="Hemoglobin, blood",
        loinc={"718-7": "mass/volume"},
        canonical_unit="g/dL",
        to_canonical={"g/dL": Decimal(1), "g/L": Decimal("0.1")},
        sanity_low=Decimal(2),
        sanity_high=Decimal(25),
    ),
    Analyte(
        key="hematocrit",
        name="Hematocrit",
        loinc={"4544-3": "automated count", "20570-8": "by calculation"},
        canonical_unit="%",
        to_canonical={"%": Decimal(1), "L/L": Decimal(100)},
        sanity_low=Decimal(5),
        sanity_high=Decimal(80),
    ),
    Analyte(
        key="mcv",
        name="Mean corpuscular volume",
        loinc={"787-2": "automated count", "30428-7": "unspecified method"},
        canonical_unit="fL",
        to_canonical={"fL": Decimal(1), "um3": Decimal(1)},
        sanity_low=Decimal(40),
        sanity_high=Decimal(150),
    ),
    Analyte(
        key="ferritin",
        name="Ferritin, serum/plasma",
        loinc={"2276-4": "mass/volume"},
        canonical_unit="ng/mL",
        to_canonical={"ng/mL": Decimal(1), "ug/L": Decimal(1)},
        sanity_low=Decimal("0.5"),
        sanity_high=Decimal(100000),
    ),
)

BY_KEY: dict[str, Analyte] = {a.key: a for a in ANALYTES}
BY_LOINC: dict[str, Analyte] = {code: a for a in ANALYTES for code in a.loinc}


def analyte_for_loinc(code: str | None) -> Analyte | None:
    return BY_LOINC.get(code) if code else None
