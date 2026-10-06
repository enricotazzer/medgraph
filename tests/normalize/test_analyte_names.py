import pytest

from medgraph.normalize.analyte_names import analyte_for_name


@pytest.mark.parametrize(
    ("name", "key"),
    [
        ("Creatinine", "creatinine"),
        ("CREATININA SIERICA", "creatinine"),
        ("Creatinina (siero)", "creatinine"),  # one trailing parenthetical is ignored
        ("eGFR (MDRD)", "egfr"),
        ("eGFR CKD-EPI", "egfr"),
        ("Velocità di filtrazione glomerulare stimata", "egfr"),  # accents normalized
        ("Hb:", "hemoglobin"),  # trailing colon
        ("Emoglobina ....", "hemoglobin"),  # dot leaders
        ("Rapporto albumina/creatinina", "urine_acr"),
        ("Ferritina", "ferritin"),
    ],
)
def test_listed_names_map(name: str, key: str) -> None:
    assert analyte_for_name(name) == key


@pytest.mark.parametrize(
    "name",
    [
        "Emoglobina glicata (HbA1c)",  # glycated hemoglobin is not hemoglobin
        "HbA1c",
        "MCH",
        "Proteine totali",  # serum total protein, not urine protein
        "Albumina",  # serum albumin
        "S-Creatinine",  # a real spelling, deliberately not listed
        "",
    ],
)
def test_unlisted_names_stay_unmapped(name: str) -> None:
    assert analyte_for_name(name) is None
