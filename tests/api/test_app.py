import hashlib
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import view_patient as vp
from medgraph.api.app import HEADERS, NO_TELEMETRY, create_app
from medgraph.graph.build import build_patient_graph
from medgraph.graph.store import GraphStore, cohort_digest
from medgraph.ingest.fhir import read_bundle_file
from medgraph.settings import Settings

DATA = re.compile(r'<script type="application/json" id="medgraph-data">(.*?)</script>', re.DOTALL)
MANIFEST = {
    "cohort": "tiny",
    "config": {
        "synthea": {"version": "v4.0.0"},
        "seed": 1,
        "clinician_seed": 2,
        "reference_date": "2026-01-01",
    },
    "outputs": {"content_digest": "abc"},
}


def save_store(path: Path, fhir_fixture_dir: Path) -> None:
    with GraphStore(path) as store:
        for x in ("a", "b"):
            record = read_bundle_file(fhir_fixture_dir / f"patient-{x}.json")
            store.save(build_patient_graph(record))
        store.set_meta({"store_digest": cohort_digest(store.digests())})


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fhir_fixture_dir: Path) -> Path:
    """Two stores: ``tiny``, a Synthea cohort, and ``other``, which has no Synthea manifest."""
    data = tmp_path / "data"
    (data / "synthea" / "tiny").mkdir(parents=True)
    (data / "synthea" / "tiny" / "MANIFEST.json").write_text(json.dumps(MANIFEST))
    save_store(data / "graphs" / "tiny.sqlite", fhir_fixture_dir)
    save_store(data / "graphs" / "other.sqlite", fhir_fixture_dir)
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", str(data))
    monkeypatch.chdir(tmp_path)  # no .env from the repository
    return data


@pytest.fixture
def client(data_dir: Path) -> TestClient:
    return TestClient(create_app(Settings()), base_url="http://127.0.0.1:8000")


def test_the_index_lists_synthea_cohorts_only(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="cohorts/tiny/"' in response.text
    assert "2 patients" in response.text
    assert "other" not in response.text
    assert client.get("/cohorts/other/").status_code == 404
    assert client.get("/cohorts/other/pat-a.html").status_code == 404


def test_a_cohort_lists_its_patients_and_links_their_pages(client: TestClient) -> None:
    text = client.get("/cohorts/tiny/").text
    assert 'href="pat-a.html"' in text
    assert 'href="pat-b.html"' in text
    assert 'href="concept-map.html"' in text
    assert "Synthetic data (Synthea)" in text
    # Search and filters work on escaped attributes; the script writes no HTML.
    assert '<input id="q" type="search"' in text
    assert 'data-search="pat-a' in text
    assert "innerHTML" not in text


def test_the_patient_list_escapes_what_it_puts_in_attributes() -> None:
    from medgraph.api.pages import PatientRow, patients_page

    row = PatientRow('p"><script>x</script>', 3, 0, None, ('Anemia "a" <b>',))
    page = patients_page("tiny", [row])
    assert "<script>x</script>" not in page
    assert "<b>" not in page
    assert 'data-flags="-1"' in page


def test_a_served_page_is_the_page_make_view_writes(client: TestClient, tmp_path: Path) -> None:
    out = tmp_path / "pat-a.html"
    assert vp.main(["tiny", "pat-a", "--out", str(out)]) == 0
    response = client.get("/cohorts/tiny/pat-a.html")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.text == out.read_text(encoding="utf-8")


def test_view_json_is_the_data_the_page_embeds(client: TestClient) -> None:
    (embedded,) = DATA.findall(client.get("/cohorts/tiny/pat-a.html").text)
    response = client.get("/api/cohorts/tiny/patients/pat-a/view.json")
    assert response.headers["content-type"] == "application/json"
    assert response.json() == json.loads(embedded)
    assert response.json()["rules"]["as_of"] == "2026-01-01"


@pytest.mark.parametrize(
    "path",
    [
        "/cohorts/none/",
        "/cohorts/tiny/nobody.html",
        "/cohorts/tiny/pat-a",
        "/cohorts/%2E%2E/",
        "/cohorts/tiny/%2E%2E%2Fother.html",
        "/api/cohorts/tiny/patients/nobody/view.json",
        "/docs",
        "/openapi.json",
    ],
)
def test_anything_else_is_not_found(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example"])
def test_a_foreign_host_is_refused(client: TestClient, host: str) -> None:
    """A website whose name resolves to 127.0.0.1 (DNS rebinding) sends its own name."""
    response = client.get("/cohorts/tiny/pat-a.html", headers={"host": host})
    assert response.status_code == 400
    assert "pat-a" not in response.text
    assert client.get("/", headers={"host": "localhost:8000"}).status_code == 200


@pytest.mark.parametrize(
    ("path", "host"),
    [
        ("/", "127.0.0.1"),
        ("/cohorts/tiny/pat-a.html", "127.0.0.1"),
        ("/x", "127.0.0.1"),
        ("/", "evil.example"),
    ],
)
def test_every_response_carries_the_security_headers(
    client: TestClient, path: str, host: str
) -> None:
    response = client.get(path, headers={"host": host})
    for name, value in HEADERS.items():
        assert response.headers[name] == value
    assert "connect-src" not in HEADERS["content-security-policy"]  # default-src 'none' holds
    assert "server" not in response.headers


def test_the_concept_map_is_built_off_the_request(client: TestClient) -> None:
    first = client.get("/cohorts/tiny/concept-map.html")
    assert first.status_code == 200
    assert '<meta http-equiv="refresh" content="5">' in first.text
    maps = client.app.state.concept_maps  # type: ignore[attr-defined]
    maps.wait("tiny")
    (data,) = DATA.findall(client.get("/cohorts/tiny/concept-map.html").text)
    model = json.loads(data)
    assert model["patients"] == ["pat-a", "pat-b"]
    assert model["pages"] == [0, 1]  # every patient has a page here
    assert model["banner"].startswith("Synthetic data")
    assert maps._building == {}  # served from memory, not built again


def test_a_concept_map_that_fails_says_so(data_dir: Path, client: TestClient) -> None:
    with GraphStore(data_dir / "graphs" / "tiny.sqlite") as store:
        store.db.execute("UPDATE nodes SET attrs = replace(attrs, '1.4', '9.9')")
        store.db.commit()
    client.get("/cohorts/tiny/concept-map.html")
    client.app.state.concept_maps.wait("tiny")  # type: ignore[attr-defined]
    assert "could not be built" in client.get("/cohorts/tiny/concept-map.html").text


def test_serving_writes_nothing(data_dir: Path, client: TestClient) -> None:
    def snapshot() -> dict[str, str]:
        return {
            str(p.relative_to(data_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(data_dir.rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    for path in ("/", "/cohorts/tiny/", "/cohorts/tiny/pat-a.html", "/cohorts/tiny/pat-b.html"):
        assert client.get(path).status_code == 200
    client.get("/cohorts/tiny/concept-map.html")
    client.app.state.concept_maps.wait("tiny")  # type: ignore[attr-defined]
    assert snapshot() == before


def test_fastapi_telemetry_is_off_even_if_the_environment_asks(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    app = create_app(Settings())
    # FastAPI keeps its settings in a private attribute; this fails loudly if that changes.
    config = app._telemetry  # type: ignore[attr-defined]
    assert {k: config[k] for k in NO_TELEMETRY} == NO_TELEMETRY
    assert not app._native_telemetry.enabled()  # type: ignore[attr-defined]
