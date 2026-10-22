"""The plain-language texts of the personal view, on flags and notes the real rules raise."""

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from evaluate_flags import FLAG_RULES, NOTE_RULES
from medgraph.agent.evidence import build_pack
from medgraph.agent.explain import template
from medgraph.agent.verify import language_problem, numbers_in, wording_problems
from medgraph.graph.build import build_patient_graph
from medgraph.ingest.fhir import parse_bundle
from medgraph.rules.egfr import ckd_epi_2021, whole
from medgraph.rules.flags import evaluate
from medgraph.rules.model import Flag, Note, PatientRules, Topic, UnreadReason
from medgraph.rules.plain import (
    CHECK_NAMES,
    CLOSING_IT,
    FLAG_TEXTS,
    LANGUAGES,
    NOTE_TEXTS,
    UNCHECKED,
    Language,
    Text,
    flag_texts,
    personal_view,
    show_date,
    why_it,
)
from medgraph.rules.sources import SOURCES
from medgraph.rules.unread import TOPIC_WORDS, why

LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
AS_OF = dt.date(2026, 1, 1)
SUBJECT = {"reference": "urn:uuid:p1"}


def lab(id_: str, day: str, value: float, loinc: str = "2160-0", unit: str = "mg/dL") -> Any:
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


def hb(id_: str, day: str, g_dl: float) -> Any:
    return lab(id_, day, g_dl, loinc="718-7", unit="g/dL")


def condition(id_: str, code: str) -> Any:
    return {
        "resourceType": "Condition",
        "id": id_,
        "subject": SUBJECT,
        "code": {"coding": [{"system": SNOMED, "code": code}]},
        "onsetDateTime": "2015-01-01T00:00:00+00:00",
    }


def med(id_: str, rxcui: str) -> Any:
    return {
        "resourceType": "MedicationRequest",
        "id": id_,
        "subject": SUBJECT,
        "status": "active",
        "intent": "order",
        "medicationCodeableConcept": {"coding": [{"system": RXNORM, "code": rxcui}]},
        "authoredOn": "2024-06-01T08:00:00+00:00",
    }


def creatinine_for(target: int) -> float:
    """A creatinine whose CKD-EPI 2021 eGFR rounds to ``target`` for a woman of 60."""
    for hundredths in range(30, 1500):
        value = Decimal(hundredths) / 100
        if whole(ckd_epi_2021(value, 60, "female")) == target:
            return float(value)
    raise AssertionError(target)


def run(*resources: Any) -> PatientRules:
    patient = {"resourceType": "Patient", "id": "p1", "gender": "female", "birthDate": "1965-01-01"}
    bundle = {
        "resourceType": "Bundle",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in (patient, *resources)],
    }
    return evaluate(build_patient_graph(parse_bundle(bundle, "sha256:test")), AS_OF)


# Patients that between them raise every flag and note (a woman born 1965).
SCENARIOS = {
    # eGFR about 40 twice, 5 months apart: CKD criteria met, no urine albumin, no haemoglobin.
    "ckd": run(lab("c1", "2025-01-01", 1.5), lab("c2", "2025-06-01", 1.5)),
    "anaemia": run(hb("h1", "2025-10-01", 10.0)),
    # Recorded CKD stage, last creatinine 2024-12-31.
    "stage": run(condition("k", "431856006"), lab("c1", "2024-12-31", 0.8)),
    # eGFR in G4: haemoglobin every 6 months.
    "g4": run(
        lab("c1", "2025-01-01", 3.0), lab("c2", "2025-11-01", 3.0), hb("h1", "2025-02-01", 13.5)
    ),
    "metformin-29": run(med("m1", "860975"), lab("c1", "2025-10-01", creatinine_for(29))),
    "metformin-40": run(med("m1", "860975"), lab("c1", "2025-10-01", creatinine_for(40))),
    "metformin-no-test": run(med("m1", "860975")),
    "epoetin": run(med("e1", "205923"), hb("h1", "2025-11-30", 11.0)),
    "ras-nsaid": run(med("l1", "314076"), med("n1", "849574"), lab("c1", "2025-03-01", 0.8)),
}
CASES: list[tuple[str, Flag | Note, dict[str, Text]]] = [
    *((name, f, flag_texts(r, f)) for name, r in SCENARIOS.items() for f in r.flags),
    *((name, n, NOTE_TEXTS[n.rule](n)) for name, r in SCENARIOS.items() for n in r.notes),
]
IDS = [f"{name}:{item.rule}" for name, item, _ in CASES]


def test_every_flag_and_note_rule_has_texts_and_the_scenarios_raise_them_all() -> None:
    assert set(FLAG_TEXTS) == set(FLAG_RULES)
    assert set(NOTE_TEXTS) == set(NOTE_RULES)
    assert {item.rule for _, item, _ in CASES} == set(FLAG_RULES) | set(NOTE_RULES)


@pytest.mark.parametrize(("name", "item", "texts"), CASES, ids=IDS)
def test_why_paraphrases_a_cited_source_and_uses_only_its_numbers(
    name: str, item: Flag | Note, texts: dict[str, Text]
) -> None:
    for language in LANGUAGES:
        text = texts[language]
        assert text.source in item.sources
        source = SOURCES[text.source]
        allowed = set().union(*numbers_in(f"{source.quote} {source.citation}", "en"))
        for values in numbers_in(text.why, language):
            assert values & allowed, f"{language}: {values} not in {text.source}"


@pytest.mark.parametrize(("name", "item", "texts"), CASES, ids=IDS)
def test_texts_pass_the_explanation_checks_in_their_language(
    name: str, item: Flag | Note, texts: dict[str, Text]
) -> None:
    for language in LANGUAGES:
        text = texts[language]
        for field in (text.title, text.shows, text.why, text.limit, text.closing):
            assert field
            assert wording_problems(field) == [], f"{language}: {field}"
            assert language_problem(field, language) is None, f"{language}: {field}"


@pytest.mark.parametrize(("name", "item", "texts"), CASES, ids=IDS)
def test_a_flag_ends_with_its_own_closing_sentence(
    name: str, item: Flag | Note, texts: dict[str, Text]
) -> None:
    if isinstance(item, Flag):
        assert item.statement.endswith(texts["en"].closing)
    assert texts["it"].closing == CLOSING_IT[texts["en"].closing]


def test_values_and_dates_come_from_the_evidence() -> None:
    (flag,) = SCENARIOS["metformin-29"].flags
    texts = flag_texts(SCENARIOS["metformin-29"], flag)
    assert "is 29 mL/min/1.73 m2 (1 October 2025)" in texts["en"].shows
    assert "è 29 mL/min/1.73 m2 (01/10/2025)" in texts["it"].shows
    assert texts["en"].title == "Metformin with an eGFR below 30"
    (anaemia,) = SCENARIOS["anaemia"].flags
    shows = flag_texts(SCENARIOS["anaemia"], anaemia)["en"].shows
    assert "100 g/L on 1 October 2025" in shows
    assert "(120 g/L)" in shows


def test_follow_up_texts_take_the_interval_and_the_basis_from_the_rule() -> None:
    g4 = SCENARIOS["g4"]
    (flag,) = [f for f in g4.flags if f.rule == "ckd-haemoglobin-follow-up"]
    texts = flag_texts(g4, flag)
    assert texts["en"].title == "No haemoglobin test in the last 6 months"
    assert texts["it"].title == "Nessun test dell'emoglobina negli ultimi 6 mesi"
    assert texts["en"].shows.startswith("Your kidney results meet the criteria")
    assert "from 1 February 2025" in texts["en"].shows
    stage = SCENARIOS["stage"]
    (gfr,) = [f for f in stage.flags if f.rule == "ckd-gfr-follow-up"]
    shows = flag_texts(stage, gfr)
    assert shows["en"].shows.startswith(
        "A diagnosis of chronic kidney disease stage 2 is recorded."
    )
    assert "di stadio 2." in shows["it"].shows
    (albumin,) = [f for f in SCENARIOS["ckd"].flags if f.rule == "ckd-albuminuria-follow-up"]
    assert flag_texts(SCENARIOS["ckd"], albumin)["en"].shows.endswith(
        "Your record has no urine albumin test."
    )


def test_the_italian_template_takes_the_interval_from_the_flag() -> None:
    """The Italian template said "12 mesi" for every haemoglobin follow-up, also at 6 months."""
    g4 = SCENARIOS["g4"]
    (flag,) = [f for f in g4.flags if f.rule == "ckd-haemoglobin-follow-up"]
    first = template(flag, build_pack("p1", flag, ()), "it")[0]
    assert first.text == "Nessun test dell'emoglobina negli ultimi 6 mesi."


def test_charts_draw_the_thresholds_the_rules_applied_with_their_source() -> None:
    lines = {(x["analyte"], x["value"]): x for x in personal_view(SCENARIOS["anaemia"])["lines"]}
    hb_line = lines[("hemoglobin", 12.0)]  # WHO 120 g/L for a woman, in the series' g/dL
    assert hb_line["label"] == {"en": "120 g/L · WHO 2024", "it": "120 g/L · OMS 2024"}
    ckd = {(x["analyte"], x["value"]) for x in personal_view(SCENARIOS["ckd"])["lines"]}
    assert {("egfr", 60.0), ("urine_acr", 30.0)} <= ckd
    metformin = {
        (x["analyte"], x["value"]) for x in personal_view(SCENARIOS["metformin-40"])["lines"]
    }
    assert {("egfr", 30.0), ("egfr", 45.0)} <= metformin


def test_the_personal_view_follows_the_order_of_the_flags_and_notes() -> None:
    rules = SCENARIOS["ckd"]
    view = personal_view(rules)
    assert len(view["flags"]) == len(rules.flags)
    titles = [x["en"]["title"] for x in view["flags"]]
    assert titles == [flag_texts(rules, f)["en"].title for f in rules.flags]
    assert personal_view(SCENARIOS["ras-nsaid"])["notes"][0]["it"]["title"].startswith(
        "Lisinopril insieme"
    )


@pytest.mark.parametrize(
    ("day", "en", "it"),
    [
        (dt.date(2024, 12, 30), "30 December 2024", "30/12/2024"),
        (dt.date(2025, 3, 1), "1 March 2025", "01/03/2025"),
    ],
)
def test_dates(day: dt.date, en: str, it: str) -> None:
    assert show_date(day, "en") == en
    assert show_date(day, "it") == it


def test_real_rules_never_fall_back_and_a_malformed_note_does() -> None:
    for rules in SCENARIOS.values():
        view = personal_view(rules)
        assert not any(x.get("fallback") for x in view["flags"] + view["notes"])
    rules = SCENARIOS["ras-nsaid"]
    bare = Note(
        rule="ras-inhibitor-nsaid", title="t", statement="s", sources=("label-zestril-nsaid",)
    )
    (shown,) = personal_view(rules.model_copy(update={"notes": (bare,)}))["notes"]
    assert shown["fallback"] is True
    assert shown["it"]["title"] == shown["en"]["title"] == "t"


# --- checks not done (rules/unread.py) ---------------------------------------------------------


def coded_condition(id_: str, system: str, code: str, display: str | None) -> Any:
    coding = {"system": system, "code": code} | ({"display": display} if display else {})
    resource = condition(id_, code)
    resource["code"] = {"coding": [coding]}
    return resource


UNREAD = run(
    lab("c1", "2025-01-01", 1.5),
    lab("c2", "2025-06-01", 1.5),
    coded_condition("k1", "http://hl7.org/fhir/sid/icd-9-cm", "585.3", "Malattia renale cronica"),
    coded_condition("k2", "http://hl7.org/fhir/sid/icd-9-cm", "285.9", None),
)


def test_every_check_has_a_name_in_both_languages() -> None:
    assert set(CHECK_NAMES) == set(FLAG_RULES) | set(NOTE_RULES)
    for names in CHECK_NAMES.values():
        assert set(names) == set(LANGUAGES)


def test_held_back_checks_are_shown_and_their_flags_are_not() -> None:
    assert UNREAD.flags == ()
    view = personal_view(UNREAD)
    assert view["flags"] == []
    unchecked = view["unchecked"]
    assert unchecked["en"]["title"] == "Some checks could not be done"
    assert [r["node"] for r in unchecked["records"]] == ["condition:k1", "condition:k2"]
    # Metformin's three rules share one name; each check is listed once.
    names = [c["en"] for c in unchecked["checks"]]
    assert len(names) == len(set(names))
    assert personal_view(SCENARIOS["ckd"])["unchecked"] is None


def unchecked_texts() -> list[tuple[str, str]]:
    texts = [(lang, v) for lang in LANGUAGES for v in UNCHECKED[lang].values()]
    texts += [(lang, names[lang]) for names in CHECK_NAMES.values() for lang in LANGUAGES]
    topic_sets: list[tuple[Topic, ...]] = [(t,) for t in TOPIC_WORDS] + [("kidney", "anaemia")]
    reasons: list[UnreadReason] = ["name", "no_name", "lab_code"]
    for reason in reasons:
        for topics in topic_sets:
            texts += [("en", why(reason, topics)), ("it", why_it(reason, topics))]
    return texts


@pytest.mark.parametrize(("language", "text"), unchecked_texts())
def test_unchecked_texts_pass_the_explanation_checks(language: Language, text: str) -> None:
    assert text
    assert wording_problems(text) == [], text
    assert language_problem(text, language) is None, text
