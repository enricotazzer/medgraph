import datetime as dt
import shutil
from pathlib import Path

import pytest

import profile_cohort as prof

REFERENCE = dt.date(2026, 1, 1)


@pytest.fixture
def profile(fhir_fixture_dir: Path, tmp_path: Path) -> prof.Profile:
    fhir = tmp_path / "fhir"
    shutil.copytree(fhir_fixture_dir, fhir)
    (fhir / "._patient-a.json").write_bytes(b"\x00\x05\x16\x07binary")  # exFAT companion
    return prof.collect(fhir, REFERENCE)


@pytest.mark.parametrize(
    ("display", "group"),
    [
        ("Creatinine [Mass/volume] in Blood", prof.KIDNEY),
        ("Glomerular filtration rate/1.73 sq M.predicted", prof.KIDNEY),
        ("Urea nitrogen [Mass/volume] in Blood", prof.KIDNEY),
        ("Microalbumin/Creatinine [Mass Ratio] in Urine", prof.URINE),
        ("Creatinine [Mass/volume] in Urine", prof.URINE),
        ("Protein [Mass/volume] in Urine by Test strip", prof.URINE),
        ("Hemoglobin [Presence] in Urine by Test strip", None),
        ("Hemoglobin [Mass/volume] in Blood", prof.HEMOGLOBIN),
        ("Hematocrit [Volume Fraction] of Blood", prof.HEMOGLOBIN),
        ("Hemoglobin.gastrointestinal.lower [Presence] in Stool", None),
        ("Hemoglobin A1c/Hemoglobin.total in Blood", prof.HBA1C),
        ("MCV [Entitic mean volume] in Red Blood Cells", prof.IRON),
        ("Body Height", None),
    ],
)
def test_lab_group(display: str, group: str | None) -> None:
    assert prof.lab_group(display) == group


def test_patients_and_bundles(profile: prof.Profile) -> None:
    assert profile.n_patients == 2
    assert profile.n_deceased == 1
    assert profile.n_other_bundles == 1
    assert profile.genders == {"female": 1, "male": 1}
    # 65 at the reference date; 75 at death.
    assert profile.age_bands == {"65-79": 2}
    assert profile.n_events_after_death == 1
    assert profile.first_encounter == dt.date(2025, 1, 10)
    assert profile.last_encounter == dt.date(2025, 6, 2)


def test_lab_statistics(profile: prof.Profile) -> None:
    creatinine = profile.loinc["38483-4"]
    assert creatinine.group == prof.KIDNEY
    assert creatinine.n_obs == 3
    assert creatinine.units == {"mg/dL": 3}
    assert prof.span_counts(creatinine) == (1, 1)  # one patient, 2025-01-10 to 2025-04-25

    hemoglobin = profile.loinc["718-7"]
    assert hemoglobin.units == {"g/dL": 2, "g/L": 1}
    assert hemoglobin.values_by_unit == {"g/dL": [12.5, 11.0], "g/L": [110.0]}
    assert prof.span_counts(hemoglobin) == (1, 0)  # two values one day apart

    assert profile.loinc["20454-5"].units == {"(non-numeric)": 1}
    assert profile.loinc["8302-2"].group is None
    assert profile.patient_lab_groups["pat-a"] == {prof.KIDNEY, prof.URINE, prof.HEMOGLOBIN}


def test_conditions_medications_and_notes(profile: prof.Profile) -> None:
    assert profile.conditions["433144002"].patients == {"pat-a"}
    assert profile.conditions["271737000"].patients == {"pat-b"}
    assert prof.is_condition_of_interest(profile.conditions["433144002"].display)
    assert not prof.is_condition_of_interest(profile.conditions["38341003"].display)
    # One request coded inline, one through a Medication resource reference.
    assert set(profile.medications) == {"314076", "310325"}
    assert profile.n_document_references == 1
    assert profile.note_lengths == [len("Assessment: chronic kidney disease stage 3.")]
    assert (profile.n_encounters_with_note, profile.n_encounters) == (1, 5)
    assert profile.report_categories == {"Laboratory": 1, "History and physical note": 1}
    assert profile.reports_with_text == {"History and physical note": 1}


def test_render_markdown(profile: prof.Profile) -> None:
    text = prof.render_markdown(profile, {"cohort": "fixture"}, top=5)
    assert text.startswith("# Synthea cohort profile: fixture")
    assert "Aggregates only" in text
    assert "Patients with an encounter dated after their death: 1." in text
    assert "| 718-7 | Hemoglobin [Mass/volume] in Blood | g/dL (2), g/L (1) |" in text
    assert "| 98979-8 | eGFR, CKD-EPI 2021 equation | **no** |  |" in text


def test_main_writes_report(fhir_fixture_dir: Path, tmp_path: Path) -> None:
    cohort = tmp_path / "fixture-cohort"
    shutil.copytree(fhir_fixture_dir, cohort / "fhir")
    out = tmp_path / "profile.md"
    assert prof.main([str(cohort), "--out", str(out)]) == 0
    assert out.read_text().startswith("# Synthea cohort profile: fixture-cohort")
