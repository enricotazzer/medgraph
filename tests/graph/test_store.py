import sqlite3
from pathlib import Path

import pytest

from medgraph.graph.build import build_patient_graph
from medgraph.graph.check import check_graph
from medgraph.graph.schema import PatientGraph
from medgraph.graph.store import GraphStore, StoreError, cohort_digest, graph_digest
from medgraph.ingest.fhir import read_bundle_file


@pytest.fixture
def graph(fhir_fixture_dir: Path) -> PatientGraph:
    return build_patient_graph(read_bundle_file(fhir_fixture_dir / "patient-a.json"))


def test_round_trip_is_exact(tmp_path: Path, graph: PatientGraph) -> None:
    with GraphStore(tmp_path / "g.sqlite") as store:
        digest = store.save(graph)
        loaded = store.load("pat-a")
    assert digest == graph_digest(graph) == graph_digest(loaded)
    assert loaded.graph == graph.graph
    assert list(loaded.nodes(data=True)) == list(graph.nodes(data=True))
    assert list(loaded.edges(keys=True, data=True)) == list(graph.edges(keys=True, data=True))
    assert check_graph(loaded) == []


def test_save_replaces_the_patients_graph(tmp_path: Path, graph: PatientGraph) -> None:
    with GraphStore(tmp_path / "g.sqlite") as store:
        store.save(graph)
        smaller = graph.copy()
        smaller.remove_node("note:doc-a1")
        store.save(smaller)
        assert "note:doc-a1" not in store.load("pat-a")
        assert list(store.digests()) == ["pat-a"]
        (count,) = store.db.execute("SELECT count(*) FROM nodes").fetchone()
        assert count == smaller.number_of_nodes()


def test_database_refuses_an_edge_without_its_nodes(tmp_path: Path, graph: PatientGraph) -> None:
    with GraphStore(tmp_path / "g.sqlite") as store:
        store.save(graph)
        with pytest.raises(sqlite3.IntegrityError):
            store.db.execute("INSERT INTO edges VALUES (1, 'a', 'b', 'precedes', 0, '{}')")


def test_tampered_graph_fails_its_digest(tmp_path: Path, graph: PatientGraph) -> None:
    with GraphStore(tmp_path / "g.sqlite") as store:
        store.save(graph)
        store.db.execute(
            "UPDATE nodes SET attrs = replace(attrs, '1.4', '9.9') "
            "WHERE node_id = 'lab_result:obs-a1'"
        )
        with pytest.raises(StoreError, match="does not match its digest"):
            store.load("pat-a")


def test_missing_patient_and_other_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "g.sqlite"
    with GraphStore(path) as store:
        with pytest.raises(StoreError, match="no graph"):
            store.load("nobody")
        store.set_meta({"schema_version": "graph-v0"})
    with pytest.raises(StoreError, match="graph-v0"):
        GraphStore(path)


def test_cohort_digest_ignores_order() -> None:
    assert cohort_digest({"a": "1", "b": "2"}) == cohort_digest({"b": "2", "a": "1"})
    assert cohort_digest({"a": "1"}) != cohort_digest({"a": "2"})
