import json
import re
from pathlib import Path
from typing import Any

import pytest

from medgraph.graph.build import AttachedReport, build_patient_graph
from medgraph.graph.concepts import LAB_REPORTS, concept_map
from medgraph.graph.schema import PatientGraph
from medgraph.graph.view import render_concept_map
from medgraph.ingest.fhir import read_bundle_file
from medgraph.ingest.lab_report import TranscribedRow, Transcription, interpret

DATA = re.compile(r'<script type="application/json" id="medgraph-data">(.*?)</script>', re.DOTALL)
CKD = "condition|http://snomed.info/sct|433144002"
HYPERTENSION = "condition|http://snomed.info/sct|38341003"
LISINOPRIL = "medication_request|http://www.nlm.nih.gov/research/umls/rxnorm|314076"
REPORT = """\
LABORATORIO SINTETICO
Data prelievo: 07/03/2025
Emoglobina         13,5       g/dL     12,0 - 15,5
Glucosio           92         mg/dL    70 - 99
"""


def attached(sha: str) -> AttachedReport:
    transcription = Transcription(
        collection_date="07/03/2025",
        rows=(
            TranscribedRow(analyte="Emoglobina", value="13,5", unit="g/dL", reference_range=""),
            TranscribedRow(analyte="Glucosio", value="92", unit="mg/dL", reference_range=""),
        ),
    )
    return AttachedReport(name=f"{sha}.txt", result=interpret(transcription, REPORT, sha))


@pytest.fixture
def graphs(fhir_fixture_dir: Path) -> list[PatientGraph]:
    """pat-a (CKD, hypertension) and pat-b (anaemia, deceased): indices 0 and 1."""
    return [
        build_patient_graph(read_bundle_file(fhir_fixture_dir / f"patient-{x}.json"))
        for x in ("a", "b")
    ]


def by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in items}


def links_of(model: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {(x["source"], x["target"], x["kind"]): x for x in model["links"]}


def test_a_concept_counts_patients_once_and_all_their_records(graphs: list[PatientGraph]) -> None:
    model = concept_map(graphs)
    assert model["patients"] == ["pat-a", "pat-b"]
    assert model["deceased"] == 1
    concepts = by_id(model["concepts"])
    haemoglobin = concepts["analyte:hemoglobin"]
    # One result for pat-a and two for pat-b; labelled by the analyte, not by a LOINC name.
    assert (haemoglobin["patients"], haemoglobin["records"]) == ([0, 1], 3)
    assert (haemoglobin["kind"], haemoglobin["label"]) == ("analyte", "Hemoglobin, blood")
    assert concepts["analyte:creatinine"]["patients"] == [0]
    assert concepts["analyte:creatinine"]["records"] == 3
    lisinopril = concepts[LISINOPRIL]
    assert (lisinopril["kind"], lisinopril["code"]) == (
        "medication",
        "http://www.nlm.nih.gov/research/umls/rxnorm|314076",
    )


def test_an_analyte_the_table_links_but_nobody_measured_has_no_patients(
    graphs: list[PatientGraph],
) -> None:
    model = concept_map(graphs)
    acr = by_id(model["concepts"])["analyte:urine_acr"]
    assert (acr["patients"], acr["records"]) == ([], 0)
    link = links_of(model)[(CKD, "analyte:urine_acr", "monitored_by")]
    assert link["patients"] == [0]
    assert link["basis"] == "cited guideline (rules/monitoring.py)"
    assert any("KDIGO 2024" in c for c in link["citations"])


def test_links_count_patients_and_record_edges_and_keep_their_basis(
    graphs: list[PatientGraph],
) -> None:
    model = concept_map(graphs)
    links = links_of(model)
    treated = links[(HYPERTENSION, LISINOPRIL, "treated_by")]
    assert (treated["patients"], treated["edges"]) == ([0], 1)
    assert treated["basis"] == "reasonReference in the record"
    assert treated["citations"] == []
    kinds = {kind for _, _, kind in links}
    assert "precedes" not in kinds  # joins records inside one concept
    assert all(source != target for source, target, _ in links)


def test_the_map_does_not_depend_on_the_order_of_the_graphs(graphs: list[PatientGraph]) -> None:
    assert concept_map(graphs) == concept_map(reversed(graphs))


def test_a_patient_given_twice_is_refused(graphs: list[PatientGraph]) -> None:
    with pytest.raises(ValueError, match="more than one graph"):
        concept_map([graphs[0], graphs[0]])


def test_filed_lab_reports_form_one_concept_linked_to_what_their_rows_measure(
    fhir_fixture_dir: Path,
) -> None:
    graphs = [
        build_patient_graph(
            read_bundle_file(fhir_fixture_dir / f"patient-{x}.json"), [attached(f"sha256:{x}")]
        )
        for x in ("a", "b")
    ]
    model = concept_map(graphs)
    concepts = by_id(model["concepts"])
    reports = concepts[LAB_REPORTS]
    assert (reports["label"], reports["kind"]) == ("Filed lab reports", "lab_report")
    assert (reports["patients"], reports["records"]) == ([0, 1], 4)  # two rows each
    assert not any(c.startswith("report_row") for c in concepts)
    measures = links_of(model)[(LAB_REPORTS, "analyte:hemoglobin", "measures")]
    assert measures["patients"] == [0, 1]


# --- the page ---------------------------------------------------------------------------------


def page_data(html: str) -> dict[str, Any]:
    (data,) = DATA.findall(html)
    result: dict[str, Any] = json.loads(data)
    return result


def test_page_is_self_contained_and_forbids_network_requests(graphs: list[PatientGraph]) -> None:
    html = render_concept_map(
        concept_map(graphs), cohort="tiny", store_digest="0" * 64, banner="Synthetic data"
    )
    assert "default-src 'none'" in html
    assert not re.search(r"<(?:script|link|img|iframe)[^>]*\s(?:src|href)=", html, re.IGNORECASE)
    assert "@import" not in html
    assert not re.search(r"/\*(?:CSP|VIEWER_CSS|VENDOR_JS|VIEWER_JS|DATA)\*/", html)
    data = page_data(html)
    assert (data["cohort"], data["banner"], data["pages"]) == ("tiny", "Synthetic data", [])


def test_only_existing_pages_with_plain_names_are_linked(graphs: list[PatientGraph]) -> None:
    model = concept_map(graphs)
    pages = {"pat-b", "pat-c", "../pat-a"}
    data = page_data(render_concept_map(model, cohort="t", store_digest="", banner="", pages=pages))
    assert data["pages"] == [1]
    model["patients"] = ["../pat-a", "pat-b"]
    data = page_data(render_concept_map(model, cohort="t", store_digest="", banner="", pages=pages))
    assert data["pages"] == [1]


def test_record_text_cannot_break_out_of_the_data_block(graphs: list[PatientGraph]) -> None:
    model = concept_map(graphs)
    hostile = '</script><script>alert("x")</script>\u2028<!--'
    model["concepts"][0]["label"] = hostile
    html = render_concept_map(model, cohort="t", store_digest="", banner="")
    assert hostile not in html
    assert html.count("</script>") == 3  # data, library, page script
    assert page_data(html)["concepts"][0]["label"] == hostile
