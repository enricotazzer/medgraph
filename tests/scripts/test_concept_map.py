import json
import re
from pathlib import Path

import pytest

import concept_map as cm
from medgraph.graph.build import build_patient_graph
from medgraph.graph.store import GraphStore, cohort_digest
from medgraph.ingest.fhir import read_bundle_file

DATA = re.compile(r'<script type="application/json" id="medgraph-data">(.*?)</script>', re.DOTALL)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fhir_fixture_dir: Path) -> Path:
    data = tmp_path / "data"
    (data / "graphs").mkdir(parents=True)
    with GraphStore(data / "graphs" / "tiny.sqlite") as store:
        for x in ("a", "b"):
            record = read_bundle_file(fhir_fixture_dir / f"patient-{x}.json")
            store.save(build_patient_graph(record))
        store.set_meta({"store_digest": cohort_digest(store.digests())})
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", str(data))
    monkeypatch.chdir(tmp_path)  # no .env from the repository
    return data


def test_writes_the_map_next_to_the_patient_pages_and_links_them(data_dir: Path) -> None:
    views = data_dir / "views" / "tiny"
    views.mkdir(parents=True)
    (views / "pat-b.html").write_text("<!doctype html>", encoding="utf-8")
    (views / "._pat-a.html").write_text("", encoding="utf-8")  # exFAT metadata, not a page
    assert cm.main(["tiny"]) == 0
    html = (views / "concept-map.html").read_text(encoding="utf-8")
    (data,) = DATA.findall(html)
    model = json.loads(data)
    assert model["patients"] == ["pat-a", "pat-b"]
    assert model["pages"] == [1]
    assert model["store_digest"]
    assert model["banner"].startswith("Synthetic data")


def test_a_missing_store_is_an_error(data_dir: Path) -> None:
    with pytest.raises(SystemExit):
        cm.main(["other"])
