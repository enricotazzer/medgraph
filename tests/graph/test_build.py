import datetime as dt
import os
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from medgraph.graph.build import AttachedReport, build_patient_graph, printed_value_match
from medgraph.graph.check import check_coverage, check_graph
from medgraph.graph.schema import NodeData, edge_data, graph_info, node_data
from medgraph.graph.store import graph_digest
from medgraph.graph.timeline import build_timeline, series_members
from medgraph.ingest.fhir import parse_bundle, read_bundle_file
from medgraph.ingest.lab_report import TranscribedRow, Transcription, interpret
from medgraph.records import PatientRecord, Quantity, Timepoint

LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
UCUM = "http://unitsofmeasure.org"
SUBJECT = {"reference": "urn:uuid:p1"}


# --- bundle builders ------------------------------------------------------------------------


def bundle(*resources: dict[str, Any]) -> dict[str, Any]:
    patient = {"resourceType": "Patient", "id": "p1", "gender": "female"}
    return {
        "resourceType": "Bundle",
        "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r} for r in (patient, *resources)],
    }


def encounter(id_: str, start: str) -> dict[str, Any]:
    return {
        "resourceType": "Encounter",
        "id": id_,
        "subject": SUBJECT,
        "class": {"code": "AMB"},
        "period": {"start": start, "end": start},
    }


def lab(
    id_: str,
    value: float,
    unit: str = "mg/dL",
    loinc: str = "2160-0",
    when: str = "2025-03-07T08:00:00+00:00",
    **fields: Any,
) -> dict[str, Any]:
    return {
        "resourceType": "Observation",
        "id": id_,
        "subject": SUBJECT,
        "category": [{"coding": [{"code": "laboratory"}]}],
        "code": {"coding": [{"system": LOINC, "code": loinc}]},
        "effectiveDateTime": when,
        "valueQuantity": {"value": Decimal(str(value)), "unit": unit, "system": UCUM, "code": unit},
        **fields,
    }


def condition(id_: str, code: str, display: str) -> dict[str, Any]:
    return {
        "resourceType": "Condition",
        "id": id_,
        "subject": SUBJECT,
        "code": {"coding": [{"system": SNOMED, "code": code, "display": display}]},
        "onsetDateTime": "2024-01-01T00:00:00+00:00",
    }


def record_of(*resources: dict[str, Any]) -> PatientRecord:
    return parse_bundle(bundle(*resources), "sha256:bundle")


def report(text: str, *rows: tuple[str, ...], date: str = "07/03/2025") -> AttachedReport:
    fields = ("analyte", "value", "unit", "reference_range", "flag")
    transcription = Transcription(
        collection_date=date,
        rows=tuple(TranscribedRow(**dict(zip(fields, row, strict=False))) for row in rows),
    )
    return AttachedReport(name="report.txt", result=interpret(transcription, text, "sha256:report"))


IT_REPORT = """\
LABORATORIO SINTETICO
Data prelievo: 07/03/2025
ESAME              RISULTATO  UNITA'   VALORI DI RIFERIMENTO
Creatinina         1,32       mg/dL    0,50 - 0,90    H
Emoglobina         13,5       g/dL     12,0 - 15,5
Glucosio           92         mg/dL    70 - 99
"""


def kinds_of(graph: Any, kind: str) -> list[str]:
    return [n for n in graph if node_data(graph, n).kind == kind]


# --- the fixture patient ----------------------------------------------------------------------


def test_fixture_patient_graph(fhir_fixture_dir: Path) -> None:
    record = read_bundle_file(fhir_fixture_dir / "patient-a.json")
    graph = build_patient_graph(record)
    assert check_graph(graph) == []
    assert check_coverage(graph, record) == []
    assert graph_info(graph).patient.id == "pat-a"

    # Reason from the data: lisinopril was prescribed for hypertension.
    assert graph.has_edge("condition:cond-a2", "medication_request:med-a1", "treated_by")
    # Cited guideline: CKD stage 3 is assessed with eGFR, ACR and creatinine.
    monitored = {
        v for _, v, k in graph.out_edges("condition:cond-a1", keys=True) if k == "monitored_by"
    }
    assert monitored == {"analyte:egfr", "analyte:urine_acr", "analyte:creatinine"}
    edge = edge_data(graph, "condition:cond-a1", "analyte:egfr", "monitored_by")
    assert edge.citation is not None
    assert "Practice Point 2.1.1" in edge.citation
    assert edge.detail is not None
    assert "at least annually" in edge.detail
    # Hypertension is outside the table: no monitoring link.
    assert not any(k == "monitored_by" for *_, k in graph.out_edges("condition:cond-a2", keys=True))

    # The three creatinine values form one series, linked in time order.
    assert series_members(graph)["creatinine"] == [
        "lab_result:obs-a1",
        "lab_result:obs-a2",
        "lab_result:obs-a3",
    ]
    assert graph.has_edge("lab_result:obs-a1", "lab_result:obs-a2", "precedes")
    assert graph.has_edge("lab_result:obs-a2", "lab_result:obs-a3", "precedes")
    # Body height is a record too, but no analyte.
    assert "observation:obs-a6" in graph
    assert not any(k == "measures" for *_, k in graph.out_edges("observation:obs-a6", keys=True))


def test_build_is_deterministic_across_processes(fhir_fixture_dir: Path) -> None:
    """Same digest under different string-hash seeds, so no set order leaks into the graph."""
    script = (
        "import sys; from pathlib import Path;"
        "from medgraph.ingest.fhir import read_bundle_file;"
        "from medgraph.graph.build import build_patient_graph;"
        "from medgraph.graph.store import graph_digest;"
        "print(graph_digest(build_patient_graph(read_bundle_file(Path(sys.argv[1])))))"
    )
    path = str(fhir_fixture_dir / "patient-a.json")
    digests = {
        subprocess.run(
            [sys.executable, "-c", script, path],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        for seed in ("1", "2")
    }
    assert len(digests) == 1
    assert digests == {graph_digest(build_patient_graph(read_bundle_file(Path(path))))}


# --- records and edges ------------------------------------------------------------------------


def test_encounters_link_records_and_follow_time_not_file_order() -> None:
    record = record_of(
        encounter("late", "2025-05-01T09:00:00+00:00"),
        encounter("early", "2025-01-01T09:00:00+00:00"),
        lab("o1", 1.1, encounter={"reference": "urn:uuid:early"}),
    )
    graph = build_patient_graph(record)
    assert graph.has_edge("lab_result:o1", "encounter:early", "occurred_during")
    assert graph.has_edge("encounter:early", "encounter:late", "precedes")
    assert not graph.has_edge("encounter:late", "encounter:early", "precedes")


def test_reason_naming_a_condition_that_failed_to_parse_is_reported_not_invented() -> None:
    broken = {"resourceType": "Condition", "id": "c1", "subject": SUBJECT}  # no code
    medication = {
        "resourceType": "MedicationRequest",
        "id": "m1",
        "subject": SUBJECT,
        "medicationCodeableConcept": {"coding": [{"code": "310325"}]},
        "reasonReference": [{"reference": "urn:uuid:c1"}],
    }
    graph = build_patient_graph(record_of(broken, medication))
    assert "condition:c1" not in graph  # no attribute-less node created by the edge
    assert check_graph(graph) == []
    (issue,) = graph_info(graph).build_issues
    assert "treated_by" in issue
    assert "condition:c1 not in graph" in issue


def test_values_that_cannot_be_used_stay_visible_but_out_of_series() -> None:
    record = record_of(
        lab("ok", 1.1),
        lab("odd-unit", 1.2, unit="furlongs"),
        lab("undated", 1.3, effectiveDateTime=None),
    )
    graph = build_patient_graph(record)
    odd = node_data(graph, "lab_result:odd-unit")
    undated = node_data(graph, "lab_result:undated")
    assert (odd.usable, odd.status) == (False, "unit_unknown")
    assert odd.detail is not None
    assert odd.detail.startswith("unit_unknown")
    assert (undated.usable, undated.status, undated.detail) == (False, "ok", "no date")
    assert series_members(graph) == {"creatinine": ["lab_result:ok"]}
    notes = [n.text for n in build_timeline(graph).notes]
    assert any("2 value(s) set aside (no date 1, unit_unknown 1)" in t for t in notes)
    assert any("only one usable value (2025-03-07)" in t for t in notes)
    assert check_graph(graph) == []


# --- lab reports ------------------------------------------------------------------------------


def test_report_row_repeating_a_fhir_result_is_counted_once() -> None:
    record = record_of(lab("o1", 1.32), lab("o2", 13.5, unit="g/dL", loinc="718-7"))
    attached = report(
        IT_REPORT,
        ("Creatinina", "1,32", "mg/dL", "0,50 - 0,90", "H"),
        ("Emoglobina", "13,5", "g/dL", "12,0 - 15,5"),
        ("Glucosio", "92", "mg/dL", "70 - 99"),
    )
    graph = build_patient_graph(record, [attached])
    assert check_graph(graph) == []
    assert check_coverage(graph, record, (attached,)) == []
    rows = sorted(kinds_of(graph, "report_row"))
    creatinine_row, haemoglobin_row, glucose_row = (
        f"report_row:sha256:report:{i}" for i in range(3)
    )
    assert rows == sorted([creatinine_row, haemoglobin_row, glucose_row])
    assert graph.has_edge(creatinine_row, "lab_result:o1", "same_measurement")
    assert graph.has_edge(haemoglobin_row, "lab_result:o2", "same_measurement")
    assert graph.has_edge(creatinine_row, "lab_report:sha256:report", "part_of")
    # Glucose is out of scope: kept as a row, linked to its report, measuring nothing.
    glucose = node_data(graph, glucose_row)
    assert (glucose.status, glucose.usable, glucose.analyte) == ("unmapped", False, None)

    timeline = build_timeline(graph)
    (creatinine,) = (s for s in timeline.series if s.analyte == "creatinine")
    (point,) = creatinine.points
    assert point.node == "lab_result:o1"
    assert point.corroborated_by == (creatinine_row,)
    assert [s.resource_type for s in point.sources] == ["Observation", "LabReportRow"]
    notes = [n.text for n in timeline.notes]
    assert any("2 of 2 usable row(s) repeat a recorded result" in t for t in notes)
    assert any("1 row(s) with a test name not in the analyte name table" in t for t in notes)


def test_report_row_with_another_value_is_its_own_point() -> None:
    record = record_of(lab("o1", 1.40))
    attached = report(IT_REPORT, ("Creatinina", "1,32", "mg/dL", "0,50 - 0,90", "H"))
    graph = build_patient_graph(record, [attached])
    assert not any(k == "same_measurement" for *_, k in graph.edges(keys=True))
    # Same day, different values: both are points. The report gives only a date, which
    # sorts before the times recorded that day.
    (creatinine,) = build_timeline(graph).series
    assert [p.value for p in creatinine.points] == [Decimal("1.32"), Decimal("1.4")]
    assert check_graph(graph) == []


def test_undated_report_keeps_its_rows_off_the_timeline() -> None:
    text = "Collection date: 03/04/2025\nCreatinine  1.1  mg/dL  0.5 - 0.9\n"
    attached = report(text, ("Creatinine", "1.1", "mg/dL", "0.5 - 0.9"), date="03/04/2025")
    assert attached.result.collection_date is None  # date order unclear: refused
    graph = build_patient_graph(record_of(), [attached])
    (row,) = kinds_of(graph, "report_row")
    data = node_data(graph, row)
    assert (data.status, data.usable, data.detail) == ("ok", False, "no date")
    assert series_members(graph) == {}
    report_data = node_data(graph, "lab_report:sha256:report")
    assert report_data.status == "undated"
    assert report_data.detail is not None
    assert "date order unclear" in report_data.detail
    notes = [n.text for n in build_timeline(graph).notes]
    assert any("no collection date" in t and "off the timeline" in t for t in notes)
    assert check_graph(graph) == []


def test_rejected_rows_are_review_items() -> None:
    text = "Data prelievo: 07/03/2025\nColesterolo totale: 214 mg/dL (rif. < 200) *\n"
    attached = report(text, ("Colesterolo totale", "< 200", "", "< 200"))
    graph = build_patient_graph(record_of(), [attached])
    (row,) = kinds_of(graph, "report_row")
    assert node_data(graph, row).status == "misplaced"
    notes = [n.text for n in build_timeline(graph).notes]
    assert any("1 row(s) rejected by the extraction checks (misplaced 1)" in t for t in notes)


def test_the_same_report_cannot_be_attached_twice() -> None:
    attached = report(IT_REPORT, ("Creatinina", "1,32", "mg/dL"))
    with pytest.raises(ValueError, match="duplicate node"):
        build_patient_graph(record_of(), [attached, attached])


# --- the same-measurement rule ----------------------------------------------------------------

DAY = Timepoint(date=dt.date(2025, 3, 7), precision="day")


def measurement(kind: str, value: str, printed: Quantity | None = None, **fields: Any) -> NodeData:
    defaults: dict[str, Any] = {
        "kind": kind,
        "label": "x",
        "start": DAY,
        "status": "ok",
        "usable": True,
        "analyte": "creatinine",
        "value": Decimal(value),
        "unit": "mg/dL",
        "original": printed,
    }
    return NodeData(**{**defaults, **fields})


def row(printed: str, unit: str = "umol/L", **fields: Any) -> NodeData:
    factor = {"umol/L": Decimal(1) / Decimal("88.4"), "mg/dL": Decimal(1)}[unit]
    q = Quantity(value=Decimal(printed), unit=unit, code=unit)
    return measurement("report_row", str(Decimal(printed) * factor), q, **fields)


@pytest.mark.parametrize(
    ("printed", "unit", "recorded", "same"),
    [
        ("109", "umol/L", "1.2345", True),  # 1.2345 mg/dL = 109.13 umol/L
        ("110", "umol/L", "1.2345", False),
        ("1.2", "mg/dL", "1.25", True),  # exactly half a unit: rounds either way
        ("1.2", "mg/dL", "1.26", False),
        ("1.20", "mg/dL", "1.204", True),
        ("1.20", "mg/dL", "1.206", False),  # two printed decimals: tighter
    ],
)
def test_value_must_agree_at_the_printed_precision(
    printed: str, unit: str, recorded: str, same: bool
) -> None:
    fhir = measurement("lab_result", recorded, Quantity(value=Decimal(recorded), unit="mg/dL"))
    assert (printed_value_match(row(printed, unit), fhir) is not None) is same


@pytest.mark.parametrize(
    "difference",
    [
        {"start": Timepoint(date=dt.date(2025, 3, 8), precision="day")},
        {"analyte": "hemoglobin"},
        {"comparator": "<"},
        {"usable": False, "status": "implausible"},
    ],
)
def test_other_day_analyte_comparator_or_unusable_value_is_not_the_same(
    difference: dict[str, Any],
) -> None:
    fhir = measurement("lab_result", "1.2345", **difference)
    assert printed_value_match(row("109"), fhir) is None


def test_egfr_equations_must_not_conflict() -> None:
    def egfr(method: str | None, kind: str = "report_row") -> NodeData:
        q = Quantity(value=Decimal(45), unit="mL/min/{1.73_m2}", code="mL/min/{1.73_m2}")
        return measurement(kind, "45", q, analyte="egfr", unit="mL/min/{1.73_m2}", method=method)

    mdrd = egfr("MDRD", "lab_result")
    assert printed_value_match(egfr("MDRD"), mdrd) is not None
    assert printed_value_match(egfr("unspecified"), mdrd) is not None
    assert printed_value_match(egfr("CKD-EPI 2021"), mdrd) is None
    assert printed_value_match(egfr("CKD-EPI 2021"), egfr("CKD-EPI 2021 (race-free)", "lab_result"))
