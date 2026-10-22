"""Records the rules cannot read hold checks back (rules/unread.py, ADR 0013)."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from medgraph.graph.build import build_patient_graph
from medgraph.ingest.fhir import parse_bundle
from medgraph.rules import unread
from medgraph.rules.diagnoses import ANAEMIA_DIAGNOSES, DIALYSIS_PROCEDURES, KIDNEY_DIAGNOSES
from medgraph.rules.egfr import ckd_epi_2021, whole
from medgraph.rules.flags import evaluate
from medgraph.rules.medications import EPOETIN, METFORMIN, NSAIDS, RAS_INHIBITORS
from medgraph.rules.model import PatientRules
from medgraph.rules.plain import FLAG_TEXTS, NOTE_TEXTS

LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
ICD9 = "http://hl7.org/fhir/sid/icd-9-cm"
ICD10 = "http://hl7.org/fhir/sid/icd-10"
ATC = "http://www.whocc.no/atc"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
LOCAL = "http://lab.example.org/codes"
AS_OF = dt.date(2026, 1, 1)
SUBJECT = {"reference": "urn:uuid:p1"}
LOW = 1.5  # creatinine, mg/dL: eGFR 39.6 for a woman of 60


def coded(system: str, code: str, display: str | None = None) -> dict[str, Any]:
    coding: dict[str, Any] = {"system": system, "code": code}
    if display:
        coding["display"] = display
    return {"coding": [coding]}


def observation(
    id_: str,
    day: str,
    value: float,
    unit: str,
    code: dict[str, Any],
    category: str | None = "laboratory",
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "Observation",
        "id": id_,
        "subject": SUBJECT,
        "status": "final",
        "code": code,
        "effectiveDateTime": f"{day}T08:00:00+00:00",
        "valueQuantity": {"value": Decimal(str(value)), "unit": unit},
    }
    if category:
        resource["category"] = [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": category,
                    }
                ]
            }
        ]
    return resource


def creatinine(id_: str, day: str, value: float = LOW) -> dict[str, Any]:
    return observation(id_, day, value, "mg/dL", coded(LOINC, "2160-0"))


def condition(id_: str, code: dict[str, Any], onset: str = "2015-01-01") -> dict[str, Any]:
    return {
        "resourceType": "Condition",
        "id": id_,
        "subject": SUBJECT,
        "code": code,
        "onsetDateTime": f"{onset}T00:00:00+00:00",
    }


def procedure(id_: str, code: dict[str, Any], day: str = "2025-12-20") -> dict[str, Any]:
    return {
        "resourceType": "Procedure",
        "id": id_,
        "subject": SUBJECT,
        "status": "completed",
        "code": code,
        "performedDateTime": f"{day}T08:00:00+00:00",
    }


def medicine(id_: str, code: dict[str, Any], day: str = "2024-06-01") -> dict[str, Any]:
    return {
        "resourceType": "MedicationRequest",
        "id": id_,
        "subject": SUBJECT,
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": code,
        "authoredOn": f"{day}T08:00:00+00:00",
    }


def run(*resources: dict[str, Any], guard: bool = True) -> PatientRules:
    patient = {"resourceType": "Patient", "id": "p1", "gender": "female", "birthDate": "1965-01-01"}
    bundle = {
        "resourceType": "Bundle",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in (patient, *resources)],
    }
    graph = build_patient_graph(parse_bundle(bundle, "sha256:test"))
    return evaluate(graph, AS_OF, guard=guard)


def flags(result: PatientRules) -> set[str]:
    return {f.rule for f in result.flags}


def held(result: PatientRules) -> set[str]:
    return {u.rule for u in result.unchecked}


def creatinine_for(target: int) -> float:
    for hundredths in range(30, 1500):
        value = Decimal(hundredths) / 100
        if whole(ckd_epi_2021(value, 60, "female")) == target:
            return float(value)
    raise AssertionError(target)


CKD_VALUES = (creatinine("c1", "2025-01-01"), creatinine("c2", "2025-06-01"))
CKD_RULES = {
    "ckd-criteria-no-diagnosis",
    "ckd-gfr-follow-up",
    "ckd-albuminuria-follow-up",
    "ckd-haemoglobin-follow-up",
}


def test_criteria_met_alone_raises_the_flag() -> None:
    result = run(*CKD_VALUES)
    assert "ckd-criteria-no-diagnosis" in flags(result)
    assert result.unread == ()
    assert result.unchecked == ()


@pytest.mark.parametrize(
    "code",
    [
        coded(ICD9, "585.3", "Malattia renale cronica, stadio III (moderata)"),
        coded(SNOMED, "709044004", "Chronic kidney disease (disorder)"),
        coded(ICD10, "N18.3", "Chronic kidney disease, stage 3"),
        coded(SNOMED, "123", "Nefropatia diabetica"),
        coded(ICD9, "791.0", "Proteinuria"),
    ],
)
def test_a_kidney_diagnosis_under_an_unlisted_code_holds_the_flag_back(
    code: dict[str, Any],
) -> None:
    result = run(*CKD_VALUES, condition("k1", code))
    assert "ckd-criteria-no-diagnosis" not in flags(result)
    assert held(result) >= CKD_RULES
    (record,) = result.unread
    assert record.node == "condition:k1"
    assert record.topics == ("kidney",)
    assert record.reason == "name"
    assert "kidney disease" in record.why
    # The guard is what held it back: the values still meet the criteria.
    assert "ckd-criteria-no-diagnosis" in flags(
        run(*CKD_VALUES, condition("k1", code), guard=False)
    )
    assert next(a for a in result.assessments if a.rule == "ckd-criteria").status == "met"


def test_a_condition_with_only_an_unlisted_code_holds_back_kidney_and_anaemia() -> None:
    result = run(*CKD_VALUES, condition("k1", coded(ICD9, "585.3")))
    (record,) = result.unread
    assert record.reason == "no_name"
    assert record.topics == ("kidney", "anaemia")
    assert {"anaemia-criteria-no-diagnosis", *CKD_RULES} <= held(result)


@pytest.mark.parametrize(
    "code",
    [
        coded(ICD10, "K02.9", "Dental caries, unspecified"),  # Synthea's dental module
        coded(SNOMED, "40930008", "Hypothyroidism (disorder)"),
        coded(SNOMED, "363732003", "Adrenal insufficiency (disorder)"),  # "adrenal", not "renal"
        coded(SNOMED, "433144002", "Chronic kidney disease stage 3 (disorder)"),  # listed
    ],
)
def test_unrelated_or_listed_conditions_are_read(code: dict[str, Any]) -> None:
    assert run(*CKD_VALUES, condition("x1", code)).unread == ()


def test_an_unlisted_anaemia_diagnosis_holds_only_the_anaemia_check() -> None:
    hb = observation("h1", "2025-12-01", 10.0, "g/dL", coded(LOINC, "718-7"))
    result = run(hb, condition("a1", coded(SNOMED, "87522002", "Iron deficiency anemia")))
    assert "anaemia-criteria-no-diagnosis" not in flags(result)
    assert held(result) == {"anaemia-criteria-no-diagnosis"}
    assert "anaemia-criteria-no-diagnosis" in flags(run(hb, guard=False))


@pytest.mark.parametrize(
    ("display", "unread_"),
    [
        ("Hemodialysis (procedure)", True),
        ("Emodialisi", True),
        ("Transplant of kidney (procedure)", True),
        ("Trapianto di rene", True),
        ("Pretransplant evaluation of kidney recipient (procedure)", False),
        ("Renal disorder medication review (procedure)", False),
        ("Renal dialysis (procedure)", False),  # listed under its code below
    ],
)
def test_dialysis_and_kidney_transplant_procedures(display: str, unread_: bool) -> None:
    code = "265764009" if display.startswith("Renal dialysis") else "999"
    result = run(*CKD_VALUES, procedure("d1", coded(SNOMED, code, display)))
    assert bool(result.unread) is unread_


def test_dental_procedures_by_code_only_are_read() -> None:
    dental = procedure("t1", {"coding": [{"system": "http://www.ada.org/cdt", "code": "D1203"}]})
    assert run(*CKD_VALUES, dental).unread == ()
    other = procedure("t2", coded("http://example.org/procedures", "D1203"))
    assert [u.reason for u in run(*CKD_VALUES, other).unread] == ["no_name"]


def test_a_lab_result_coded_outside_loinc_holds_back_every_check() -> None:
    local = observation("x1", "2025-11-01", 0.9, "mg/dL", coded(LOCAL, "CREA", "Creat. siero"))
    result = run(*CKD_VALUES, local)
    assert flags(result) == set()
    assert held(result) == set(unread.DEPENDS)
    (record,) = result.unread
    assert record.reason == "lab_code"
    assert "outside LOINC" in record.why


def test_a_result_outside_loinc_counts_by_its_name_whatever_its_category() -> None:
    named = observation("x1", "2025-11-01", 13.0, "g/dL", coded(LOCAL, "HB", "Emoglobina"), None)
    assert [u.reason for u in run(*CKD_VALUES, named).unread] == ["name"]
    vital = observation("v1", "2025-11-01", 120, "mm[Hg]", coded(LOCAL, "PA", "Pressione"), None)
    assert run(*CKD_VALUES, vital).unread == ()


def test_loinc_results_outside_the_registry_are_read() -> None:
    cholesterol = observation("x1", "2025-11-01", 190, "mg/dL", coded(LOINC, "2093-3", "Chol"))
    assert run(*CKD_VALUES, cholesterol).unread == ()


def test_metformin_coded_in_atc_holds_back_the_metformin_checks_only() -> None:
    low = creatinine("c1", "2025-10-01", creatinine_for(25))
    atc = medicine("m1", coded(ATC, "A10BA02", "Metformina cloridrato 500 mg"))
    listed = medicine("m2", coded(RXNORM, "860975"))
    assert "metformin-egfr-below-30" in flags(run(listed, low))
    result = run(listed, atc, low)
    assert "metformin-egfr-below-30" not in flags(result)
    assert held(result) == {
        "metformin-egfr-below-30",
        "metformin-egfr-30-44",
        "metformin-no-gfr-test",
    }
    (record,) = result.unread
    assert record.topics == ("metformin",)


def test_an_unlisted_rxnorm_nsaid_holds_back_the_note() -> None:
    ras = medicine("l1", coded(RXNORM, "314076"))
    nsaid = medicine("n1", coded(RXNORM, "197806", "ibuprofen 600 MG Oral Tablet"))
    result = run(ras, nsaid, creatinine("c1", "2025-03-01", 0.8))
    assert result.notes == ()
    assert held(result) == {"ras-inhibitor-nsaid"}


def test_records_after_the_evaluation_date_are_ignored() -> None:
    later = condition("k1", coded(SNOMED, "709044004", "Chronic kidney disease"), "2026-03-01")
    assert run(*CKD_VALUES, later).unread == ()


SCENARIOS = [
    (*CKD_VALUES, condition("k1", coded(ICD9, "585.3"))),
    (*CKD_VALUES, procedure("d1", coded(SNOMED, "999", "Hemodialysis"))),
    (medicine("m1", coded(RXNORM, "860975")), medicine("m2", coded(ATC, "A10BA02", "Metformina"))),
    (
        medicine("e1", coded(RXNORM, "205923")),
        observation("x1", "2025-11-01", 11, "g/dL", coded(LOCAL, "HB", "Hb")),
    ),
]


@pytest.mark.parametrize("resources", SCENARIOS)
def test_the_guard_only_ever_removes_flags_and_notes(resources: tuple[dict[str, Any], ...]) -> None:
    on, off = run(*resources), run(*resources, guard=False)
    assert set(on.flags) <= set(off.flags)
    assert set(on.notes) <= set(off.notes)
    assert on.assessments == off.assessments
    assert {f.rule for f in off.flags if f not in on.flags} <= held(on)


def test_every_flag_and_note_rule_lists_its_topics() -> None:
    assert set(unread.DEPENDS) == set(FLAG_TEXTS) | set(NOTE_TEXTS)


def test_the_name_patterns_cover_every_listed_display() -> None:
    """A code added to a list needs a name the guard recognises, so the same thing under an
    unlisted code is held back too."""
    fold = unread.fold
    for display in (*KIDNEY_DIAGNOSES.values(),):
        assert unread.KIDNEY_CONDITION.search(fold(display)), display
    for display in DIALYSIS_PROCEDURES.values():
        assert unread.KIDNEY_PROCEDURE.search(fold(display)), display
    for display in ANAEMIA_DIAGNOSES.values():
        assert unread.ANAEMIA_CONDITION.search(fold(display)), display
    drugs = {
        "metformin": METFORMIN.codes.values(),
        "epoetin": EPOETIN.codes.values(),
        "ras_nsaid": [
            *NSAIDS.values(),
            *(d for drug, _ in RAS_INHIBITORS for d in drug.codes.values()),
        ],
    }
    for topic, displays in drugs.items():
        for display in displays:
            assert unread.MEDICINES[topic].search(fold(display)), display  # type: ignore[index]
