"""Analyte names as printed on lab reports, in English and Italian, to registry keys.

A deliberately plain lookup: a name maps only if it is listed here, after case, spacing,
punctuation and one trailing parenthetical (``Creatinine (serum)``) are normalized away.
Names that are not listed stay unmapped and are reported as such, never matched by
similarity: "Emoglobina glicata" must not become hemoglobin.
"""

import re

_NAMES: dict[str, tuple[str, ...]] = {
    "creatinine": (
        "creatinine",
        "serum creatinine",
        "creatinine, serum",
        "crea",
        "creat",
        "creatinina",
        "creatinina sierica",
        "creatininemia",
    ),
    "egfr": (
        "egfr",
        "estimated gfr",
        "gfr, estimated",
        "egfr mdrd",
        "egfr ckd-epi",
        "estimated glomerular filtration rate",
        "vfg stimato",
        "vfg",
        "filtrato glomerulare stimato",
        "velocita di filtrazione glomerulare stimata",
    ),
    "urine_acr": (
        "albumin/creatinine ratio",
        "urine albumin/creatinine ratio",
        "albumin creatinine ratio",
        "microalbumin/creatinine ratio",
        "acr",
        "uacr",
        "rapporto albumina/creatinina",
        "rapporto albumina/creatinina urinaria",
        "microalbuminuria/creatininuria",
    ),
    "urine_albumin": (
        "urine albumin",
        "microalbumin",
        "microalbumin, urine",
        "albumina urinaria",
        "microalbuminuria",
    ),
    "urine_protein": (
        "urine protein",
        "protein, urine",
        "proteinuria",
        "proteine urinarie",
    ),
    "hemoglobin": ("hemoglobin", "haemoglobin", "hgb", "hb", "emoglobina"),
    "hematocrit": ("hematocrit", "haematocrit", "hct", "ematocrito"),
    "mcv": (
        "mcv",
        "mean corpuscular volume",
        "mean cell volume",
        "volume corpuscolare medio",
        "volume globulare medio",
    ),
    "ferritin": ("ferritin", "serum ferritin", "ferritina"),
}

_ACCENTS = str.maketrans("àèéìòù", "aeeiou")


def name_key(name: str) -> str:
    """Normalize a printed analyte name for lookup."""
    k = name.strip().lower().translate(_ACCENTS)
    k = k.replace("\u2013", "-").replace("\u2014", "-")
    k = re.sub(r"[\s.:*]+$", "", k)  # trailing colons, dot leaders, flag stars
    return " ".join(k.replace("(", " (").split()).replace("( ", "(")


_LOOKUP: dict[str, str] = {name_key(n): key for key, names in _NAMES.items() for n in names}


def analyte_for_name(name: str) -> str | None:
    """Registry key for a printed analyte name, or ``None`` if the name is not listed."""
    k = name_key(name)
    if k in _LOOKUP:
        return _LOOKUP[k]
    # One trailing parenthetical: "eGFR (MDRD)", "Creatinina (siero)".
    stripped = re.sub(r"\s*\([^()]*\)$", "", k)
    if stripped != k and stripped in _LOOKUP:
        return _LOOKUP[stripped]
    return None


def known_names() -> frozenset[str]:
    """Every listed name, normalized: lets tests keep held-out names out of this table."""
    return frozenset(_LOOKUP)
