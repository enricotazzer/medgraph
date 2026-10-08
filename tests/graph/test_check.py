"""Each invariant catches the corruption it exists for."""

from pathlib import Path

import pytest

from medgraph.graph.build import build_patient_graph
from medgraph.graph.check import check_coverage, check_graph
from medgraph.graph.schema import PatientGraph
from medgraph.ingest.fhir import read_bundle_file
from medgraph.records import PatientRecord


@pytest.fixture
def record(fhir_fixture_dir: Path) -> PatientRecord:
    return read_bundle_file(fhir_fixture_dir / "patient-a.json")


@pytest.fixture
def graph(record: PatientRecord) -> PatientGraph:
    built = build_patient_graph(record)
    assert check_graph(built) == []
    return built


def test_node_created_by_an_edge(graph: PatientGraph) -> None:
    graph.add_edge(
        "condition:cond-a1", "procedure:ghost", key="treated_by", kind="treated_by", basis="x"
    )
    assert check_graph(graph) == ["procedure:ghost: attributes invalid (e.g. created by an edge)"]


def test_edge_between_the_wrong_kinds(graph: PatientGraph) -> None:
    graph.add_edge(
        "lab_result:obs-a1", "condition:cond-a1", key="treated_by", kind="treated_by", basis="x"
    )
    (problem,) = check_graph(graph)
    assert "treated_by cannot join lab_result to condition" in problem


def test_monitoring_edge_without_citation(graph: PatientGraph) -> None:
    attrs = graph.edges["condition:cond-a1", "analyte:egfr", "monitored_by"]
    del attrs["citation"]
    (problem,) = check_graph(graph)
    assert "monitored_by without a citation" in problem


def test_usable_flag_must_match_status(graph: PatientGraph) -> None:
    graph.nodes["lab_result:obs-a1"]["status"] = "implausible"
    (problem,) = check_graph(graph)
    assert "usable=True with status implausible" in problem


def test_series_order_must_match_precedes_edges(graph: PatientGraph) -> None:
    graph.remove_edge("lab_result:obs-a1", "lab_result:obs-a2", key="precedes")
    assert check_graph(graph) == ["precedes edges between values: 0 unexpected, 1 missing"]


def test_record_without_a_node(graph: PatientGraph, record: PatientRecord) -> None:
    graph.remove_node("note:doc-a1")
    (problem,) = check_coverage(graph, record)
    assert problem.startswith("1 record(s) without a node")
