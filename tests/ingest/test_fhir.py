import base64
import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from medgraph.ingest.fhir import BundleError, parse_bundle, read_bundle_file

LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
PATIENT = {"resourceType": "Patient", "id": "p1", "gender": "female", "birthDate": "1960-05"}
SUBJECT = {"reference": "urn:uuid:p1"}


def bundle(*resources: dict[str, Any]) -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in resources],
    }


def encounter(id_: str, start: str = "2025-01-10T09:00:00+00:00") -> dict[str, Any]:
    return {
        "resourceType": "Encounter",
        "id": id_,
        "subject": SUBJECT,
        "class": {"code": "AMB"},
        "period": {"start": start, "end": start},
    }


def observation(id_: str, **fields: Any) -> dict[str, Any]:
    return {
        "resourceType": "Observation",
        "id": id_,
        "subject": SUBJECT,
        "code": {"coding": [{"system": LOINC, "code": "718-7", "display": "Hemoglobin"}]},
        "effectiveDateTime": "2025-01-10T09:00:00+00:00",
        **fields,
    }


def text_attachment(text: str) -> dict[str, Any]:
    return {
        "contentType": "text/plain; charset=utf-8",
        "data": base64.b64encode(text.encode()).decode(),
    }


def codes(record_issues: Any) -> list[str]:
    return [issue.code for issue in record_issues]


# --- fixture files ---------------------------------------------------------------------


def test_reads_fixture_patient(fhir_fixture_dir: Path) -> None:
    path = fhir_fixture_dir / "patient-a.json"
    record = read_bundle_file(path)

    assert record.source == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    assert record.patient.gender == "female"
    assert record.patient.birth_date is not None
    assert record.patient.birth_date.date == dt.date(1960, 5, 1)
    assert len(record.encounters) == 3
    assert len(record.observations) == 8
    assert len(record.conditions) == 2
    assert len(record.diagnostic_reports) == 2
    assert record.issues == ()

    creatinine = [o for o in record.observations if o.code.code(LOINC) == "38483-4"]
    assert [o.value_quantity.value for o in creatinine if o.value_quantity] == [
        Decimal("1.4"),
        Decimal("1.5"),
        Decimal("1.6"),
    ]
    first = creatinine[0]
    assert first.encounter_id is None  # the fixture's observations carry no encounter
    assert first.source.resource_id == "obs-a1"
    assert first.effective is not None
    assert first.effective.instant == dt.datetime(2025, 1, 10, 9, tzinfo=dt.UTC)

    (med,) = record.medication_requests
    assert med.medication.code("http://www.nlm.nih.gov/research/umls/rxnorm") == "314076"
    # Same text, but the report has no encounter, so the two are kept apart.
    assert len(record.notes) == 2
    assert {n.text for n in record.notes} == {"Assessment: chronic kidney disease stage 3."}


def test_deceased_patient_and_medication_reference(fhir_fixture_dir: Path) -> None:
    record = read_bundle_file(fhir_fixture_dir / "patient-b.json")
    assert record.patient.deceased is not None
    assert record.patient.deceased.date == dt.date(2025, 6, 1)
    (med,) = record.medication_requests
    assert med.medication.code("http://www.nlm.nih.gov/research/umls/rxnorm") == "310325"
    (issue,) = record.issues
    assert issue.code == "event_after_death"
    assert issue.source is not None
    assert issue.source.resource_id == "enc-b2"


def test_non_patient_bundle_is_rejected(fhir_fixture_dir: Path) -> None:
    with pytest.raises(BundleError, match="found 0"):
        read_bundle_file(fhir_fixture_dir / "hospitalInformation.json")


def test_values_keep_their_written_digits(tmp_path: Path) -> None:
    resource = observation("o1", valueQuantity={"value": 0, "unit": "g/dL"})
    text = json.dumps(bundle(PATIENT, resource)).replace('"value": 0', '"value": 1.20')
    path = tmp_path / "bundle.json"
    path.write_text(text)
    (obs,) = read_bundle_file(path).observations
    assert obs.value_quantity is not None
    assert str(obs.value_quantity.value) == "1.20"


# --- built bundles: edge cases ---------------------------------------------------------


def test_bundle_must_hold_exactly_one_patient() -> None:
    with pytest.raises(BundleError, match="not a FHIR Bundle"):
        parse_bundle({"resourceType": "Patient"}, "s")
    with pytest.raises(BundleError, match="found 2"):
        parse_bundle(bundle(PATIENT, {**PATIENT, "id": "p2"}), "s")


def test_partial_birth_date_keeps_month_precision() -> None:
    record = parse_bundle(bundle(PATIENT), "s")
    assert record.patient.birth_date is not None
    assert record.patient.birth_date.precision == "month"


def test_time_without_offset_is_an_issue_not_a_crash() -> None:
    record = parse_bundle(
        bundle(PATIENT, observation("o1", effectiveDateTime="2025-01-10T09:00:00")), "s"
    )
    (obs,) = record.observations
    assert obs.effective is None
    assert codes(record.issues) == ["invalid_time"]


def test_references_resolve_to_encounters() -> None:
    record = parse_bundle(
        bundle(PATIENT, encounter("e1"), observation("o1", encounter={"reference": "urn:uuid:e1"})),
        "s",
    )
    assert record.observations[0].encounter_id == "e1"
    assert record.issues == ()


@pytest.mark.parametrize(
    ("reference", "issue"),
    [
        ("urn:uuid:missing", ["unresolved_reference"]),
        ("urn:uuid:p1", ["unexpected_reference_type"]),
        ("Encounter?identifier=http://example.org|1", []),  # external, not an error
    ],
)
def test_reference_problems(reference: str, issue: list[str]) -> None:
    record = parse_bundle(
        bundle(PATIENT, observation("o1", encounter={"reference": reference})), "s"
    )
    assert record.observations[0].encounter_id is None
    assert codes(record.issues) == issue


def test_invalid_resource_is_skipped_with_an_error_and_others_survive() -> None:
    condition = {"resourceType": "Condition", "id": "c1", "subject": SUBJECT}  # no code
    record = parse_bundle(bundle(PATIENT, condition, observation("o1")), "s")
    assert record.conditions == ()
    assert len(record.observations) == 1
    (issue,) = record.issues
    assert (issue.code, issue.severity) == ("invalid_resource", "error")
    assert issue.source is not None
    assert issue.source.resource_id == "c1"


def test_out_of_scope_resources_are_counted() -> None:
    claims = [{"resourceType": "Claim", "id": f"cl{i}"} for i in range(2)]
    record = parse_bundle(bundle(PATIENT, *claims), "s")
    assert record.skipped_resources == {"Claim": 2}


def test_note_carried_by_two_resources_is_one_note() -> None:
    doc = {
        "resourceType": "DocumentReference",
        "id": "doc1",
        "subject": SUBJECT,
        "content": [{"attachment": text_attachment("Plan: recheck labs.")}],
        "context": {"encounter": [{"reference": "urn:uuid:e1"}]},
    }
    report = {
        "resourceType": "DiagnosticReport",
        "id": "rep1",
        "subject": SUBJECT,
        "code": {"coding": [{"system": LOINC, "code": "34117-2"}]},
        "encounter": {"reference": "urn:uuid:e1"},
        "presentedForm": [text_attachment("Plan: recheck labs.")],
        "result": [{"reference": "urn:uuid:o1"}],
    }
    record = parse_bundle(bundle(PATIENT, encounter("e1"), doc, report, observation("o1")), "s")
    (note,) = record.notes
    assert note.id == "doc1"
    assert [s.resource_type for s in note.sources] == ["DocumentReference", "DiagnosticReport"]
    assert note.encounter_id == "e1"
    (parsed_report,) = record.diagnostic_reports
    assert parsed_report.note_id == "doc1"
    assert parsed_report.result_ids == ("o1",)


def test_non_text_attachments_are_skipped_with_an_info_issue() -> None:
    doc = {
        "resourceType": "DocumentReference",
        "id": "doc1",
        "subject": SUBJECT,
        "content": [{"attachment": {"contentType": "application/pdf", "data": "JVBERi0="}}],
    }
    record = parse_bundle(bundle(PATIENT, doc), "s")
    assert record.notes == ()
    assert [(i.code, i.severity) for i in record.issues] == [("unsupported_attachment", "info")]


def test_value_variants() -> None:
    integer = observation("o1", valueInteger=3)
    ranged = observation("o2", valueRange={"low": {"value": 1}})
    record = parse_bundle(bundle(PATIENT, integer, ranged), "s")
    first, second = record.observations
    assert first.value_quantity is not None
    assert (first.value_quantity.value, first.value_quantity.unit) == (Decimal(3), None)
    assert second.value_quantity is None
    assert codes(record.issues) == ["unsupported_value"]


def test_procedure_performed_datetime_becomes_a_point_period() -> None:
    procedure = {
        "resourceType": "Procedure",
        "id": "pr1",
        "subject": SUBJECT,
        "code": {"coding": [{"system": SNOMED, "code": "73761001"}]},
        "performedDateTime": "2025-02-01T08:00:00+00:00",
    }
    (parsed,) = parse_bundle(bundle(PATIENT, procedure), "s").procedures
    assert parsed.performed.start == parsed.performed.end
    assert parsed.performed.start is not None
