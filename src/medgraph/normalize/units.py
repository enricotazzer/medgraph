"""Unit spellings to UCUM codes.

Only spellings listed here are recognised; anything else returns ``None`` so the caller
records an issue instead of guessing. Recognising a unit says nothing about whether a value
in it can be converted for a given analyte: that is the analyte registry's job.
"""

import re

# Lookup key (see _key) -> UCUM code.
_UNITS: dict[str, str] = {
    "mg/dl": "mg/dL",
    "mg/l": "mg/L",
    "g/dl": "g/dL",
    "g/l": "g/L",
    "umol/l": "umol/L",
    "mmol/l": "mmol/L",
    "ng/ml": "ng/mL",
    "ug/l": "ug/L",
    "ug/ml": "ug/mL",
    "mg/g": "mg/g",
    "mg/gcreat": "mg/g",
    "mg/gcreatinine": "mg/g",
    "mg/gcreatinina": "mg/g",
    "mg/gcrea": "mg/g",
    "ug/mg": "ug/mg",
    "ug/mgcreat": "ug/mg",
    "mg/mmol": "mg/mmol",
    "mg/mmolcreat": "mg/mmol",
    "mg/mmolcreatinine": "mg/mmol",
    "mg/mmolcreatinina": "mg/mmol",
    "ml/min": "mL/min",
    "ml/min/{1.73_m2}": "mL/min/{1.73_m2}",
    "ml/min/1.73m2": "mL/min/{1.73_m2}",
    "ml/min/1.73mq": "mL/min/{1.73_m2}",  # Italian: metri quadri
    "ml/min/1.73": "mL/min/{1.73_m2}",
    "ml/min/1.73sqm": "mL/min/{1.73_m2}",
    "%": "%",
    "l/l": "L/L",
    "fl": "fL",
    "um3": "um3",
}


def _key(text: str) -> str:
    k = text.strip().lower()
    k = k.replace("µ", "u").replace("μ", "u").replace("mcg", "ug").replace("mcmol", "umol")
    k = k.replace("²", "2").replace("^", "").replace("³", "3").replace(",", ".")
    k = k.replace("m.q.", "mq").replace("sq.m", "sqm").replace("sq m", "sqm")
    k = re.sub(r"\s+", "", k)
    return k.replace("litre", "l").replace("liter", "l")


def to_ucum(text: str | None) -> str | None:
    """UCUM code for a unit as written, or ``None`` if the spelling is not recognised."""
    if not text:
        return None
    return _UNITS.get(_key(text))
