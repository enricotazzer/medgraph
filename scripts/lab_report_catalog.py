"""What the synthetic lab-report generator can print: names, units, precision and ranges.

Names of in-scope analytes come in two sets. ``seen`` names are in the extraction pipeline's
name table (``medgraph.normalize.analyte_names``); ``held_out`` names are realistic variants
that are deliberately *not* in it, so the evaluation measures how name mapping copes with
spellings nobody tuned it on. A test keeps the two sets disjoint.

Reference ranges here are **illustrative test data**: typical-looking adult intervals that
make the synthetic documents realistic. They are printed on synthetic reports only, every
report is marked synthetic, and medgraph never reads them back as medical knowledge.
"""

from dataclasses import dataclass, field
from decimal import Decimal

Style = str  # "it" | "en-US" | "en-GB"


@dataclass(frozen=True)
class Names:
    seen: tuple[str, ...]
    held_out: tuple[str, ...] = ()


@dataclass(frozen=True)
class Printing:
    """How a style prints the test: unit text, UCUM unit, decimals and the factor that turns
    the Synthea value (in ``source_unit``) into the printed unit."""

    unit_text: str
    ucum: str
    decimals: int
    factor: Decimal = Decimal(1)


@dataclass(frozen=True)
class Test:
    key: str
    section: str  # "hematology" | "chemistry" | "urine"
    loinc: tuple[str, ...]
    names_en: Names
    names_it: Names
    printing: dict[Style, Printing]
    # Illustrative interval in the *printed* unit of each style; (low, high) or one bound.
    ranges: dict[Style, tuple[Decimal | None, Decimal | None]] = field(default_factory=dict)
    sex_ranges: dict[str, dict[Style, tuple[Decimal | None, Decimal | None]]] = field(
        default_factory=dict
    )
    registry_key: str | None = None  # medgraph analyte key, for in-scope analytes
    qualitative: bool = False


def d(text: str | int) -> Decimal:  # never a float: floats are inexact
    return Decimal(text)


def same(printing: Printing) -> dict[Style, Printing]:
    return {"it": printing, "en-US": printing, "en-GB": printing}


def rng(low: str | None, high: str | None) -> tuple[Decimal | None, Decimal | None]:
    return (d(low) if low else None, d(high) if high else None)


def all_styles(
    low: str | None, high: str | None
) -> dict[Style, tuple[Decimal | None, Decimal | None]]:
    r = rng(low, high)
    return {"it": r, "en-US": r, "en-GB": r}


CREATININE_SI = Printing("µmol/L", "umol/L", 0, d("88.4"))
CREATININE_CONV = Printing("mg/dL", "mg/dL", 2)

TESTS: tuple[Test, ...] = (
    # --- hematology -------------------------------------------------------------------
    Test(
        key="wbc",
        section="hematology",
        loinc=("6690-2",),
        names_en=Names(("White blood cells", "WBC", "Leukocytes")),
        names_it=Names(("Globuli bianchi", "Leucociti", "WBC")),
        printing={
            "it": Printing("10^3/µL", "10*3/uL", 2),
            "en-US": Printing("x10^3/µL", "10*3/uL", 1),
            "en-GB": Printing("x10^9/L", "10*9/L", 1),
        },
        ranges=all_styles("4.0", "10.0"),
    ),
    Test(
        key="hemoglobin",
        section="hematology",
        loinc=("718-7",),
        names_en=Names(
            ("Hemoglobin", "Haemoglobin", "HGB", "Hb"),
            ("Hgb (whole blood)", "B-Hemoglobin", "Haemoglobin conc."),
        ),
        names_it=Names(
            ("Emoglobina", "Hb", "HGB"), ("HGB - Emoglobina", "Emoglobina totale", "B-Emoglobina")
        ),
        printing={
            "it": Printing("g/dL", "g/dL", 1),
            "en-US": Printing("g/dL", "g/dL", 1),
            "en-GB": Printing("g/L", "g/L", 0, d(10)),
        },
        sex_ranges={
            "male": {
                "it": rng("13.0", "17.0"),
                "en-US": rng("13.0", "17.0"),
                "en-GB": rng("130", "170"),
            },
            "female": {
                "it": rng("12.0", "15.5"),
                "en-US": rng("12.0", "15.5"),
                "en-GB": rng("120", "155"),
            },
        },
        registry_key="hemoglobin",
    ),
    Test(
        key="hematocrit",
        section="hematology",
        loinc=("4544-3", "20570-8"),
        names_en=Names(("Hematocrit", "Haematocrit", "HCT"), ("Packed cell volume", "PCV")),
        names_it=Names(("Ematocrito", "HCT"), ("HCT - Ematocrito", "Valore ematocrito")),
        printing={
            "it": Printing("%", "%", 1),
            "en-US": Printing("%", "%", 1),
            "en-GB": Printing("L/L", "L/L", 2, d("0.01")),
        },
        sex_ranges={
            "male": {
                "it": rng("40.0", "52.0"),
                "en-US": rng("40.0", "52.0"),
                "en-GB": rng("0.40", "0.52"),
            },
            "female": {
                "it": rng("36.0", "46.0"),
                "en-US": rng("36.0", "46.0"),
                "en-GB": rng("0.36", "0.46"),
            },
        },
        registry_key="hematocrit",
    ),
    Test(
        key="mcv",
        section="hematology",
        loinc=("787-2", "30428-7"),
        names_en=Names(
            ("MCV", "Mean cell volume", "Mean corpuscular volume"), ("RBC MCV", "Mean RBC volume")
        ),
        names_it=Names(
            ("MCV", "Volume corpuscolare medio"),
            ("MCV - Vol. corpuscolare medio", "Volume medio eritrocitario"),
        ),
        printing=same(Printing("fL", "fL", 1)),
        ranges=all_styles("80.0", "100.0"),
        registry_key="mcv",
    ),
    Test(
        key="platelets",
        section="hematology",
        loinc=("777-3",),
        names_en=Names(("Platelets", "PLT", "Platelet count")),
        names_it=Names(("Piastrine", "PLT")),
        printing={
            "it": Printing("10^3/µL", "10*3/uL", 0),
            "en-US": Printing("x10^3/µL", "10*3/uL", 0),
            "en-GB": Printing("x10^9/L", "10*9/L", 0),
        },
        ranges=all_styles("150", "400"),
    ),
    Test(
        key="mch",
        section="hematology",
        loinc=("785-6",),
        names_en=Names(("MCH", "Mean cell haemoglobin")),
        names_it=Names(("MCH", "Contenuto emoglobinico medio")),
        printing=same(Printing("pg", "pg", 1)),
        ranges=all_styles("27.0", "33.0"),
    ),
    # --- chemistry --------------------------------------------------------------------
    Test(
        key="glucose",
        section="chemistry",
        loinc=("2339-0", "2345-7"),
        names_en=Names(("Glucose", "Fasting glucose")),
        names_it=Names(("Glucosio", "Glicemia")),
        printing=same(Printing("mg/dL", "mg/dL", 0)),
        ranges=all_styles("70", "99"),
    ),
    Test(
        key="bun",
        section="chemistry",
        loinc=("6299-2", "3094-0"),
        names_en=Names(("Urea nitrogen (BUN)", "BUN")),
        names_it=Names(("Azoto ureico (BUN)", "BUN")),
        printing=same(Printing("mg/dL", "mg/dL", 0)),
        ranges=all_styles("7", "20"),
    ),
    Test(
        key="creatinine",
        section="chemistry",
        loinc=("2160-0", "38483-4"),
        names_en=Names(
            ("Creatinine", "Serum creatinine", "CREA", "Creatinine, serum"),
            ("S-Creatinine", "Creatinine (enzymatic)", "Plasma creatinine"),
        ),
        names_it=Names(
            ("Creatinina", "Creatinina sierica", "CREA", "Creatininemia"),
            ("S-Creatinina", "Creatinina (metodo enzimatico)", "Creatinina plasmatica"),
        ),
        printing={"it": CREATININE_CONV, "en-US": CREATININE_CONV, "en-GB": CREATININE_SI},
        sex_ranges={
            "male": {
                "it": rng("0.70", "1.20"),
                "en-US": rng("0.70", "1.20"),
                "en-GB": rng("62", "106"),
            },
            "female": {
                "it": rng("0.50", "0.90"),
                "en-US": rng("0.50", "0.90"),
                "en-GB": rng("44", "80"),
            },
        },
        registry_key="creatinine",
    ),
    Test(
        key="egfr",
        section="chemistry",
        loinc=("33914-3",),
        names_en=Names(
            ("eGFR", "eGFR (MDRD)", "Estimated GFR", "eGFR MDRD"),
            ("GFR (estimated, MDRD)", "MDRD eGFR", "Est. glomerular filtration"),
        ),
        names_it=Names(
            ("VFG stimato", "eGFR (MDRD)", "Filtrato glomerulare stimato", "eGFR"),
            ("VFG (stima MDRD)", "Velocità filtraz. glomerulare", "GFR stimato MDRD"),
        ),
        printing={
            "it": Printing("mL/min/1,73 m²", "mL/min/{1.73_m2}", 0),
            "en-US": Printing("mL/min/1.73m²", "mL/min/{1.73_m2}", 0),
            "en-GB": Printing("mL/min/1.73m2", "mL/min/{1.73_m2}", 0),
        },
        ranges=all_styles("60", None),
        registry_key="egfr",
    ),
    Test(
        key="sodium",
        section="chemistry",
        loinc=("2947-0", "2951-2"),
        names_en=Names(("Sodium", "Na")),
        names_it=Names(("Sodio", "Na")),
        printing=same(Printing("mmol/L", "mmol/L", 0)),
        ranges=all_styles("136", "145"),
    ),
    Test(
        key="potassium",
        section="chemistry",
        loinc=("6298-4", "2823-3"),
        names_en=Names(("Potassium", "K")),
        names_it=Names(("Potassio", "K")),
        printing=same(Printing("mmol/L", "mmol/L", 1)),
        ranges=all_styles("3.5", "5.1"),
    ),
    Test(
        key="calcium",
        section="chemistry",
        loinc=("49765-1", "17861-6"),
        names_en=Names(("Calcium", "Total calcium")),
        names_it=Names(("Calcio", "Calcemia")),
        printing=same(Printing("mg/dL", "mg/dL", 1)),
        ranges=all_styles("8.6", "10.3"),
    ),
    Test(
        key="cholesterol",
        section="chemistry",
        loinc=("2093-3",),
        names_en=Names(("Total cholesterol", "Cholesterol, total")),
        names_it=Names(("Colesterolo totale",)),
        printing=same(Printing("mg/dL", "mg/dL", 0)),
        ranges=all_styles(None, "200"),
    ),
    Test(
        key="triglycerides",
        section="chemistry",
        loinc=("2571-8",),
        names_en=Names(("Triglycerides",)),
        names_it=Names(("Trigliceridi",)),
        printing=same(Printing("mg/dL", "mg/dL", 0)),
        ranges=all_styles(None, "150"),
    ),
    Test(
        key="hdl",
        section="chemistry",
        loinc=("2085-9",),
        names_en=Names(("HDL cholesterol",)),
        names_it=Names(("Colesterolo HDL",)),
        printing=same(Printing("mg/dL", "mg/dL", 0)),
        ranges=all_styles("40", None),
    ),
    Test(
        key="alt",
        section="chemistry",
        loinc=("1742-6",),
        names_en=Names(("ALT", "Alanine aminotransferase")),
        names_it=Names(("ALT (GPT)", "Alanina aminotransferasi")),
        printing=same(Printing("U/L", "U/L", 0)),
        ranges=all_styles(None, "41"),
    ),
    Test(
        key="hba1c",
        section="chemistry",
        loinc=("4548-4",),
        names_en=Names(("HbA1c", "Hemoglobin A1c")),
        names_it=Names(("Emoglobina glicata (HbA1c)", "HbA1c")),
        printing=same(Printing("%", "%", 1)),
        ranges=all_styles("4.0", "5.6"),
    ),
    Test(
        key="ferritin",
        section="chemistry",
        loinc=("2276-4",),
        names_en=Names(("Ferritin", "Serum ferritin"), ("S-Ferritin", "Ferritin (serum)")),
        names_it=Names(("Ferritina",), ("Ferritina sierica", "S-Ferritina")),
        printing={
            "it": Printing("ng/mL", "ng/mL", 0),
            "en-US": Printing("ng/mL", "ng/mL", 0),
            "en-GB": Printing("µg/L", "ug/L", 0),
        },
        sex_ranges={
            "male": all_styles("30", "400"),
            "female": all_styles("15", "150"),
        },
        registry_key="ferritin",
    ),
    # --- urine ------------------------------------------------------------------------
    Test(
        key="urine_acr",
        section="urine",
        loinc=("14959-1", "9318-7"),
        names_en=Names(
            ("Albumin/creatinine ratio", "Urine albumin/creatinine ratio", "ACR", "UACR"),
            ("Microalb/Creat ratio", "Albumin:creatinine ratio", "U-ACR"),
        ),
        names_it=Names(
            ("Rapporto albumina/creatinina", "Microalbuminuria/creatininuria"),
            ("Rapp. albumina/creatinina urinaria", "ACR urinario", "Albumina/creatinina (urine)"),
        ),
        printing={
            "it": Printing("mg/g", "mg/g", 1),
            "en-US": Printing("mg/g", "mg/g", 1),
            "en-GB": Printing("mg/mmol", "mg/mmol", 1, d("113.12") / d(1000)),
        },
        ranges={"it": rng(None, "30"), "en-US": rng(None, "30"), "en-GB": rng(None, "3.0")},
        registry_key="urine_acr",
    ),
    Test(
        key="urine_protein",
        section="urine",
        loinc=("5804-0",),
        names_en=Names(
            ("Urine protein", "Protein, urine"), ("U-Protein (dipstick)", "Urinary protein")
        ),
        names_it=Names(
            ("Proteine urinarie", "Proteinuria"), ("Proteine (urine)", "Proteine nelle urine")
        ),
        printing=same(Printing("mg/dL", "mg/dL", 0)),
        ranges=all_styles(None, "15"),
        registry_key="urine_protein",
    ),
    Test(
        key="nitrite",
        section="urine",
        loinc=("5802-4",),
        names_en=Names(("Nitrite",)),
        names_it=Names(("Nitriti",)),
        printing=same(Printing("", "", 0)),
        qualitative=True,
    ),
    Test(
        key="leukocyte_esterase",
        section="urine",
        loinc=("5799-2",),
        names_en=Names(("Leukocyte esterase",)),
        names_it=Names(("Esterasi leucocitaria",)),
        printing=same(Printing("", "", 0)),
        qualitative=True,
    ),
)

BY_LOINC: dict[str, Test] = {code: t for t in TESTS for code in t.loinc}

# The unit each test has in Synthea. A value recorded in another unit is not printed
# (eGFR in mL/min is the exception the generator handles explicitly: printed as recorded).
SOURCE_UNITS: dict[str, str] = {
    "wbc": "10*3/uL", "hemoglobin": "g/dL", "hematocrit": "%", "mcv": "fL",
    "platelets": "10*3/uL", "mch": "pg", "glucose": "mg/dL", "bun": "mg/dL",
    "creatinine": "mg/dL", "egfr": "mL/min/{1.73_m2}", "sodium": "mmol/L",
    "potassium": "mmol/L", "calcium": "mg/dL", "cholesterol": "mg/dL",
    "triglycerides": "mg/dL", "hdl": "mg/dL", "alt": "U/L", "hba1c": "%",
    "ferritin": "ug/L", "urine_acr": "mg/g", "urine_protein": "mg/dL",
}  # fmt: skip

QUALITATIVE_WORDS = {
    "negative": {"it": "Negativo", "en": "Negative"},
    "positive": {"it": "Positivo", "en": "Positive"},
    "trace": {"it": "Tracce", "en": "Trace"},
}

SECTION_TITLES = {
    "hematology": {"it": "EMATOLOGIA", "en": "HEMATOLOGY"},
    "chemistry": {"it": "CHIMICA CLINICA", "en": "CLINICAL CHEMISTRY"},
    "urine": {"it": "ESAME URINE", "en": "URINALYSIS"},
}
