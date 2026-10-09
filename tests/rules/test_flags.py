"""The rules end to end, from small FHIR records to assessments and flags."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from medgraph.graph.build import build_patient_graph
from medgraph.ingest.fhir import parse_bundle
from medgraph.rules import anaemia
from medgraph.rules.flags import evaluate
from medgraph.rules.model import PatientRules

LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
AS_OF = dt.date(2026, 1, 1)
SUBJECT = {"reference": "urn:uuid:p1"}


def lab(
    id_: str, day: str, value: float, loinc: str = "2160-0", unit: str = "mg/dL"
) -> dict[str, Any]:
    return {
        "resourceType": "Observation",
        "id": id_,
        "subject": SUBJECT,
        "code": {"coding": [{"system": LOINC, "code": loinc}]},
        "effectiveDateTime": f"{day}T08:00:00+00:00",
        "valueQuantity": {
            "value": Decimal(str(value)),
            "unit": unit,
            "system": "http://unitsofmeasure.org",
            "code": unit,
        },
    }


def hb(id_: str, day: str, g_dl: float) -> dict[str, Any]:
    return lab(id_, day, g_dl, loinc="718-7", unit="g/dL")


def acr(id_: str, day: str, mg_g: float) -> dict[str, Any]:
    return lab(id_, day, mg_g, loinc="14959-1", unit="mg/g")


def condition(
    id_: str, code: str, onset: str = "2015-01-01", abated: str | None = None
) -> dict[str, Any]:
    resource: dict[str, Any] = {
        "resourceType": "Condition",
        "id": id_,
        "subject": SUBJECT,
        "code": {"coding": [{"system": SNOMED, "code": code}]},
        "onsetDateTime": f"{onset}T00:00:00+00:00",
    }
    if abated:
        resource["abatementDateTime"] = f"{abated}T00:00:00+00:00"
    return resource


def smoking(id_: str, day: str, text: str) -> dict[str, Any]:
    return {
        "resourceType": "Observation",
        "id": id_,
        "subject": SUBJECT,
        "code": {"coding": [{"system": LOINC, "code": "72166-2"}]},
        "effectiveDateTime": f"{day}T08:00:00+00:00",
        "valueCodeableConcept": {"text": text},
    }


def run(
    *resources: dict[str, Any],
    gender: str = "female",
    born: str = "1965-01-01",
    died: str | None = None,
) -> PatientRules:
    patient: dict[str, Any] = {
        "resourceType": "Patient",
        "id": "p1",
        "gender": gender,
        "birthDate": born,
    }
    if died:
        patient["deceasedDateTime"] = f"{died}T00:00:00+00:00"
    bundle = {
        "resourceType": "Bundle",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in (patient, *resources)],
    }
    return evaluate(build_patient_graph(parse_bundle(bundle, "sha256:test")), AS_OF)


def status(result: PatientRules, rule: str) -> str:
    return next(a.status for a in result.assessments if a.rule == rule)


def flags(result: PatientRules) -> set[str]:
    return {f.rule for f in result.flags}


# A woman born 1965 (60 in 2025): creatinine 1.5 mg/dL gives eGFR 39.6 (G3b); 0.8 gives 84.
LOW = 1.5
NORMAL = 0.8


# --- CKD criteria --------------------------------------------------------------------------------


@pytest.mark.parametrize(("second", "met"), [("2025-04-10", False), ("2025-04-11", True)])
def test_persistence_needs_90_days(second: str, met: bool) -> None:
    # 2025-01-11 to 2025-04-10 is 89 days; to 2025-04-11, 90.
    result = run(lab("c1", "2025-01-11", LOW), lab("c2", second, LOW))
    assert status(result, "ckd-criteria") == ("met" if met else "not_met")
    assert ("ckd-criteria-no-diagnosis" in flags(result)) is met


def test_a_normal_value_between_breaks_the_run() -> None:
    result = run(
        lab("c1", "2025-01-01", LOW), lab("c2", "2025-03-01", NORMAL), lab("c3", "2025-06-01", LOW)
    )
    assert status(result, "ckd-criteria") == "not_met"


def test_criteria_count_only_while_the_latest_value_is_abnormal() -> None:
    result = run(
        lab("c1", "2024-01-01", LOW), lab("c2", "2024-06-01", LOW), lab("c3", "2025-06-01", NORMAL)
    )
    assert status(result, "ckd-criteria") == "not_met"


def test_flag_evidence_is_the_values_that_establish_persistence() -> None:
    result = run(
        lab("c1", "2025-01-01", LOW),
        lab("c2", "2025-02-01", LOW),
        lab("c3", "2025-05-01", LOW),
        lab("c4", "2025-08-01", LOW),
    )
    (flag,) = [f for f in result.flags if f.rule == "ckd-criteria-no-diagnosis"]
    assert [e.node for e in flag.evidence] == ["lab_result:c1", "lab_result:c3", "lab_result:c4"]
    assert "ckd-epi-2021" in flag.sources
    assert "has" not in flag.statement.lower().split()  # never "the patient has CKD"
    assert flag.statement.endswith("This may be worth discussing with a doctor.")
    assert any("administrative gender" in lim for lim in flag.limitations)


def test_albuminuria_alone_meets_the_criteria() -> None:
    result = run(acr("a1", "2025-01-01", 45), acr("a2", "2025-06-01", 52))
    assessment = next(a for a in result.assessments if a.rule == "ckd-criteria")
    assert assessment.status == "met"
    assert assessment.category == "A2"


def test_recorded_kidney_diagnosis_means_no_criteria_flag() -> None:
    values = (lab("c1", "2025-01-01", LOW), lab("c2", "2025-06-01", LOW))
    assert "ckd-criteria-no-diagnosis" in flags(run(*values))
    coded = run(*values, condition("k1", "433144002"))
    assert "ckd-criteria-no-diagnosis" not in flags(coded)
    # A diagnosis abated before this episode began does not cover it.
    old = run(*values, condition("k1", "433144002", onset="2010-01-01", abated="2012-01-01"))
    assert "ckd-criteria-no-diagnosis" in flags(old)


def test_no_egfr_for_children_or_unknown_gender() -> None:
    child = run(lab("c1", "2025-01-01", LOW), lab("c2", "2025-06-01", LOW), born="2010-01-01")
    assessment = next(a for a in child.assessments if a.rule == "ckd-criteria")
    assert assessment.status == "not_assessable"
    assert assessment.reason is not None
    assert "under 18" in assessment.reason
    unknown = run(lab("c1", "2025-01-01", LOW), gender="unknown")
    assert unknown.computed_egfr == ()


# --- anaemia -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("gender", "g_dl", "met"),
    [("female", 11.9, True), ("female", 12.0, False), ("male", 12.9, True), ("male", 13.0, False)],
)
def test_anaemia_uses_the_latest_value_and_the_cutoff_for_sex(
    gender: str, g_dl: float, met: bool
) -> None:
    result = run(hb("h0", "2024-01-01", 9.0), hb("h1", "2025-06-01", g_dl), gender=gender)
    assert status(result, "anaemia-criteria") == ("met" if met else "not_met")
    assert ("anaemia-criteria-no-diagnosis" in flags(result)) is met


def test_current_smoker_raises_the_cutoff_by_3() -> None:
    value = hb("h1", "2025-06-01", 13.2)  # 132 g/L: above 130, below 133
    assert status(run(value, gender="male"), "anaemia-criteria") == "not_met"
    smoker = run(
        value, smoking("s1", "2025-01-01", "Smokes tobacco daily (finding)"), gender="male"
    )
    assessment = next(a for a in smoker.assessments if a.rule == "anaemia-criteria")
    assert assessment.status == "met"
    assert "who-2024-smoking" in assessment.sources
    former = run(value, smoking("s1", "2025-01-01", "Ex-smoker (finding)"), gender="male")
    assert status(former, "anaemia-criteria") == "not_met"


@pytest.mark.parametrize(
    ("g_dl", "expected"), [(10.4, "met"), (10.7, "not_assessable"), (11.0, "not_met")]
)
def test_pregnancy_without_a_trimester(g_dl: float, expected: str) -> None:
    result = run(hb("h1", "2025-06-01", g_dl), condition("p", "72892002", onset="2025-03-01"))
    assert status(result, "anaemia-criteria") == expected


def test_no_who_cutoff_over_65_or_under_6_months() -> None:
    old = run(hb("h1", "2025-06-01", 10.0), born="1950-01-01")
    assessment = next(a for a in old.assessments if a.rule == "anaemia-criteria")
    assert assessment.status == "not_assessable"
    assert "who-2024-age-gap" in assessment.sources
    baby = run(hb("h1", "2025-06-01", 10.0), born="2025-02-01")
    assert status(baby, "anaemia-criteria") == "not_assessable"


@pytest.mark.parametrize(
    ("months", "expected"),
    [
        (5, "under 6 months"),
        (6, 105),
        (23, 105),
        (24, 110),
        (59, 110),
        (60, 115),
        (143, 115),
        (144, 120),
        (179, 120),
        (65 * 12 + 11, 130),
        (66 * 12, "over 65"),
    ],
)
def test_who_age_groups(months: int, expected: int | str) -> None:
    group = anaemia.who_group(months, "male", pregnant=False)
    if isinstance(expected, str):
        assert isinstance(group, str)
        assert expected in group
    else:
        assert isinstance(group, anaemia.Group)
        assert group.cutoffs == (Decimal(expected),)


def test_anaemia_diagnosis_covers_the_flag_only_if_ongoing() -> None:
    low = hb("h1", "2025-06-01", 10.0)
    assert "anaemia-criteria-no-diagnosis" not in flags(run(low, condition("a", "271737000")))
    resolved = condition("a", "271737000", onset="2015-01-01", abated="2016-01-01")
    assert "anaemia-criteria-no-diagnosis" in flags(run(low, resolved))


# --- follow-up -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("last", "due"), [("2025-01-01", False), ("2024-12-31", True)])
def test_gfr_test_due_after_12_months(last: str, due: bool) -> None:
    result = run(condition("k", "431856006"), lab("c1", last, NORMAL), acr("a1", "2025-06-01", 10))
    assert ("ckd-gfr-follow-up" in flags(result)) is due


def test_albuminuria_never_tested_is_due() -> None:
    result = run(condition("k", "431856006"), lab("c1", "2025-10-01", NORMAL))
    (flag,) = [f for f in result.flags if f.rule == "ckd-albuminuria-follow-up"]
    assert "none is recorded" in flag.statement
    assert flag.sources == ("kdigo-2024-monitoring",)


def test_haemoglobin_interval_follows_the_stage() -> None:
    # Coded stage 3, no creatinine: 12 months.
    stage3 = run(condition("k", "433144002"), hb("h1", "2025-02-01", 13.5))
    assert "ckd-haemoglobin-follow-up" not in flags(stage3)
    # eGFR in G4 (creatinine 3.0): 6 months, so a test 11 months ago is overdue.
    g4 = run(
        lab("c1", "2025-01-01", 3.0), lab("c2", "2025-11-01", 3.0), hb("h1", "2025-02-01", 13.5)
    )
    assert "ckd-haemoglobin-follow-up" in flags(g4)
    (flag,) = [f for f in g4.flags if f.rule == "ckd-haemoglobin-follow-up"]
    assert "twice per year" in flag.statement
    assert "kdigo-2012-anaemia-intervals" in flag.sources


def test_kidney_failure_is_outside_the_monitoring_rules() -> None:
    result = run(condition("k", "433144002"), condition("e", "46177005"))
    assert {a.status for a in result.assessments if "follow-up" in a.rule} == {"not_assessable"}
    assert not {f for f in flags(result) if "follow-up" in f}


def test_deceased_patients_get_no_flags() -> None:
    result = run(hb("h1", "2024-06-01", 9.0), died="2025-01-01")
    assert result.evaluated is False
    assert result.flags == ()


def test_values_after_the_evaluation_date_are_ignored() -> None:
    result = run(hb("h1", "2025-06-01", 13.5), hb("h2", "2026-06-01", 9.0))
    assert status(result, "anaemia-criteria") == "not_met"


def test_every_cited_source_is_registered() -> None:
    from medgraph.rules.sources import SOURCES

    result = run(
        lab("c1", "2025-01-01", LOW),
        lab("c2", "2025-06-01", LOW),
        hb("h1", "2024-01-01", 10.0),
        smoking("s1", "2023-01-01", "Smokes tobacco daily (finding)"),
    )
    cited = {s for a in result.assessments for s in a.sources} | {
        s for f in result.flags for s in f.sources
    }
    assert cited
    assert cited <= set(SOURCES)


# --- medications (drug labels) and dialysis --------------------------------------------------

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"


def med(id_: str, rxcui: str, day: str = "2024-06-01", state: str = "active") -> dict[str, Any]:
    return {
        "resourceType": "MedicationRequest",
        "id": id_,
        "subject": SUBJECT,
        "status": state,
        "intent": "order",
        "medicationCodeableConcept": {"coding": [{"system": RXNORM, "code": rxcui}]},
        "authoredOn": f"{day}T08:00:00+00:00",
    }


def dialysis(id_: str, day: str) -> dict[str, Any]:
    return {
        "resourceType": "Procedure",
        "id": id_,
        "subject": SUBJECT,
        "status": "completed",
        "code": {"coding": [{"system": SNOMED, "code": "265764009"}]},
        "performedDateTime": f"{day}T08:00:00+00:00",
    }


def creatinine_for(target: int, age: int = 60) -> float:
    """A creatinine (mg/dL, two decimals) whose CKD-EPI 2021 eGFR rounds to ``target`` for a
    woman of ``age``."""
    from medgraph.rules.egfr import ckd_epi_2021, whole

    for hundredths in range(30, 1500):
        value = Decimal(hundredths) / 100
        if whole(ckd_epi_2021(value, age, "female")) == target:
            return float(value)
    raise AssertionError(f"no creatinine gives eGFR {target}")


METFORMIN = "860975"


@pytest.mark.parametrize(
    ("egfr", "rule"),
    [(29, "metformin-egfr-below-30"), (30, "metformin-egfr-30-44"), (44, "metformin-egfr-30-44")],
)
def test_metformin_flags_follow_the_label_thresholds(egfr: int, rule: str) -> None:
    result = run(med("m1", METFORMIN), lab("c1", "2025-10-01", creatinine_for(egfr)))
    assert status(result, "metformin-egfr") == "met"
    (flag,) = [f for f in result.flags if f.rule.startswith("metformin")]
    assert flag.rule == rule
    assert flag.kind == "medication_threshold"
    assert f"latest eGFR of {egfr} mL/min/1.73 m2" in flag.statement
    assert flag.sources == (
        ("label-metformin-egfr-30",) if egfr < 30 else ("label-metformin-egfr-45",)
    )
    assert [e.node for e in flag.evidence] == ["medication_request:m1", "lab_result:c1"]


def test_metformin_at_45_or_stopped_raises_nothing() -> None:
    result = run(med("m1", METFORMIN), lab("c1", "2025-10-01", creatinine_for(45)))
    assert status(result, "metformin-egfr") == "not_met"
    assert not flags(result)
    stopped = run(
        med("m1", METFORMIN, "2020-01-01"),
        med("m2", METFORMIN, "2024-01-01", state="stopped"),
        lab("c1", "2025-10-01", creatinine_for(20)),
    )
    assert "metformin-egfr" not in {a.rule for a in stopped.assessments}
    # Requested after the evaluation date: not in the record yet.
    later = run(med("m1", METFORMIN, "2026-02-01"), lab("c1", "2025-10-01", creatinine_for(20)))
    assert "metformin-egfr-below-30" not in flags(later)


def test_metformin_without_a_gfr_test_gets_a_note_not_a_flag() -> None:
    result = run(med("m1", METFORMIN))
    assert status(result, "metformin-egfr") == "not_assessable"
    assert not flags(result)
    assert [n.rule for n in result.notes] == ["metformin-no-gfr-test"]
    assert result.notes[0].sources == ("label-metformin-renal-assessment",)
    # A creatinine that gives no eGFR (a child) is a test: no note, still not assessable.
    child = run(med("m1", METFORMIN), lab("c1", "2025-10-01", 0.5), born="2012-01-01")
    assert status(child, "metformin-egfr") == "not_assessable"
    assert child.notes == ()


EPOETIN = "205923"


@pytest.mark.parametrize(("last_hb", "due"), [("2025-12-01", False), ("2025-11-30", True)])
def test_epoetin_needs_haemoglobin_at_least_monthly(last_hb: str, due: bool) -> None:
    # A month before 2026-01-01 is 2025-12-01.
    result = run(med("e1", EPOETIN), hb("h1", last_hb, 11.0))
    assert status(result, "epoetin-haemoglobin-monthly") == ("met" if due else "not_met")
    assert ("epoetin-haemoglobin-monthly" in flags(result)) is due
    if due:
        (flag,) = [f for f in result.flags if f.rule == "epoetin-haemoglobin-monthly"]
        assert flag.kind == "medication_test_due"
        assert flag.sources == ("label-epogen-haemoglobin",)


def test_ras_inhibitor_with_an_nsaid_gets_a_note() -> None:
    result = run(med("l1", "314076"), med("n1", "849574"), lab("c1", "2025-03-01", NORMAL))
    (note,) = result.notes
    assert note.rule == "ras-inhibitor-nsaid"
    assert note.sources == ("label-zestril-nsaid",)
    assert "latest GFR test in the record is from 2025-03-01" in note.statement
    assert not flags(result)
    # The NSAID stopped, or only low-dose aspirin: no note.
    assert run(med("l1", "314076"), med("n1", "849574", state="stopped")).notes == ()
    assert run(med("l1", "314076"), med("a1", "243670")).notes == ()


CKD_RUN = (lab("c1", "2025-01-01", LOW), lab("c2", "2025-06-01", LOW))


@pytest.mark.parametrize(("session", "on"), [("2025-12-02", True), ("2025-12-01", False)])
def test_dialysis_in_the_last_30_days_takes_ckd_out_of_the_monitoring_rules(
    session: str, on: bool
) -> None:
    # 2025-12-02 is 30 days before 2026-01-01; 2025-12-01 is 31.
    result = run(*CKD_RUN, dialysis("d1", session))
    gfr = next(a for a in result.assessments if a.rule == "ckd-gfr-follow-up")
    assert (gfr.status == "not_assessable") is on
    if on:
        assert gfr.reason is not None
        assert "renal dialysis is recorded on 2025-12-02" in gfr.reason
    # A session during the episode counts as recorded kidney disease either way.
    assert "ckd-criteria-no-diagnosis" not in flags(result)


def test_dialysis_before_the_episode_does_not_count_as_its_diagnosis() -> None:
    result = run(*CKD_RUN, dialysis("d1", "2020-01-01"))
    assert "ckd-criteria-no-diagnosis" in flags(result)
