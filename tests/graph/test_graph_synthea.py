"""Graphs of real Synthea patients pass every invariant and survive the store.

Opt-in (``--run-synthea``): needs the generated cohort in ``MEDGRAPH_DATA_DIR``. The whole
cohort is checked by ``make graphs``; this samples every 50th patient.
"""

from itertools import islice
from pathlib import Path

import pytest

from medgraph.graph.build import build_patient_graph
from medgraph.graph.check import check_coverage, check_graph
from medgraph.graph.store import GraphStore, graph_digest
from medgraph.ingest.fhir import BundleError, read_bundle_file
from medgraph.ingest.files import iter_data_files
from medgraph.settings import Settings


@pytest.mark.synthea
def test_sampled_patients_build_check_and_round_trip(tmp_path: Path) -> None:
    fhir_dir = Settings().synthea_dir / "dev-1000" / "fhir"
    if not fhir_dir.is_dir():
        pytest.fail(f"cohort not found at {fhir_dir}; run `make synthea-dev`")
    built = 0
    with GraphStore(tmp_path / "sample.sqlite") as store:
        for path in islice(iter_data_files(fhir_dir, "*.json"), 0, None, 50):
            try:
                record = read_bundle_file(path)
            except BundleError:
                continue
            graph = build_patient_graph(record)
            assert check_graph(graph) + check_coverage(graph, record) == [], path.name
            digest = store.save(graph)
            assert graph_digest(store.load(record.patient.id)) == digest
            built += 1
    assert built >= 20
