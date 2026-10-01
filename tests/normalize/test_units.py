import pytest

from medgraph.normalize.analytes import ANALYTES
from medgraph.normalize.units import to_ucum


@pytest.mark.parametrize(
    ("text", "ucum"),
    [
        ("mg/dl", "mg/dL"),
        ("MG/DL", "mg/dL"),
        ("µmol/L", "umol/L"),  # micro sign
        ("μmol/l", "umol/L"),  # Greek mu
        ("mcmol/L", "umol/L"),
        ("umol/L", "umol/L"),
        ("g/L", "g/L"),
        ("µg/L", "ug/L"),
        ("mcg/L", "ug/L"),
        ("mL/min/1.73m²", "mL/min/{1.73_m2}"),
        ("ml/min/1,73 m2", "mL/min/{1.73_m2}"),
        ("mL/min/1.73 m^2", "mL/min/{1.73_m2}"),
        ("ml/min/1.73 mq", "mL/min/{1.73_m2}"),
        ("mL/min/{1.73_m2}", "mL/min/{1.73_m2}"),
        ("mL/min", "mL/min"),
        ("mg/g creat", "mg/g"),
        ("mg/g creatinina", "mg/g"),
        ("mg/mmol", "mg/mmol"),
        ("fl", "fL"),
        ("%", "%"),
        ("L/L", "L/L"),
    ],
)
def test_known_spellings(text: str, ucum: str) -> None:
    assert to_ucum(text) == ucum


@pytest.mark.parametrize("text", ["furlongs", "", None, "{presence}", "mg"])
def test_unknown_spellings_are_not_guessed(text: str | None) -> None:
    assert to_ucum(text) is None


def test_every_registry_unit_is_reachable() -> None:
    for analyte in ANALYTES:
        for unit in analyte.to_canonical:
            assert to_ucum(unit) == unit, f"{analyte.key}: {unit}"
