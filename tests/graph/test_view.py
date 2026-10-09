import json
import re
from pathlib import Path

import pytest

from medgraph.graph import view
from medgraph.graph.build import build_patient_graph
from medgraph.graph.schema import PatientGraph
from medgraph.graph.timeline import build_timeline
from medgraph.graph.view import VENDOR, VendorFileError, render_html, vendor_file, view_model
from medgraph.ingest.fhir import read_bundle_file

DATA = re.compile(r'<script type="application/json" id="medgraph-data">(.*?)</script>', re.DOTALL)


@pytest.fixture
def graph(fhir_fixture_dir: Path) -> PatientGraph:
    return build_patient_graph(read_bundle_file(fhir_fixture_dir / "patient-a.json"))


def test_vendored_libraries_match_their_pinned_hashes() -> None:
    for name in VENDOR:
        assert vendor_file(name)


def test_a_changed_library_is_refused(monkeypatch: pytest.MonkeyPatch, graph: PatientGraph) -> None:
    monkeypatch.setitem(view.VENDOR, "uPlot.min.css", "0" * 64)
    with pytest.raises(VendorFileError, match=r"uPlot\.min\.css"):
        render_html(graph, build_timeline(graph), "")


def test_page_is_self_contained_and_forbids_network_requests(graph: PatientGraph) -> None:
    html = render_html(graph, build_timeline(graph), "Synthetic data")
    assert "default-src 'none'" in html
    assert not re.search(r"<(?:script|link|img|iframe)[^>]*\s(?:src|href)=", html, re.IGNORECASE)
    assert "@import" not in html
    assert "/*DATA*/" not in html
    assert "/*VIEWER_JS*/" not in html


def test_record_text_cannot_break_out_of_the_data_block(graph: PatientGraph) -> None:
    hostile = '</script><script>alert("x")</script>\u2028<!--'
    graph.nodes["note:doc-a1"]["text"] = hostile
    html = render_html(graph, build_timeline(graph), "")
    assert hostile not in html
    assert html.count("</script>") == 3  # data, libraries, viewer
    (data,) = DATA.findall(html)
    assert json.loads(data)["records"]["note:doc-a1"]["text"] == hostile


def test_viewer_script_never_writes_html_or_reaches_the_network() -> None:
    script = (Path(view.__file__).parent / "static" / "viewer.js").read_text(encoding="utf-8")
    for forbidden in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "sendBeacon",
    ):
        assert forbidden not in script, forbidden


def test_concepts_group_records_and_links_keep_their_basis(graph: PatientGraph) -> None:
    model = view_model(graph, build_timeline(graph))
    concepts = {c["id"]: c for c in model["concepts"]}
    creatinine = concepts["analyte:creatinine"]
    assert creatinine["records"] == ["lab_result:obs-a1", "lab_result:obs-a2", "lab_result:obs-a3"]
    assert (creatinine["first"], creatinine["last"]) == ("2025-01-10", "2025-04-25")
    assert concepts["analyte:urine_acr"]["count"] == 0  # linked by the guideline, never measured
    links = {(link["source"], link["target"], link["kind"]): link for link in model["links"]}
    ckd = "condition|http://snomed.info/sct|433144002"
    monitored = links[(ckd, "analyte:egfr", "monitored_by")]
    assert "KDIGO 2024" in monitored["citation"]
    treated = links[
        (
            "condition|http://snomed.info/sct|38341003",
            "medication_request|http://www.nlm.nih.gov/research/umls/rxnorm|314076",
            "treated_by",
        )
    ]
    assert treated["count"] == 1
    assert treated["basis"] == "reasonReference in the record"  # true of every edge it groups
    (edge,) = (e for e in model["edges"] if e[2] == "treated_by")
    assert edge[3] == "reasonReference of MedicationRequest/med-a1"
    assert not any(e[2] == "precedes" for e in model["edges"])
    (series,) = (s for s in model["series"] if s["analyte"] == "creatinine")
    assert [p["value"] for p in series["points"]] == ["1.4", "1.5", "1.6"]


def test_page_carries_flags_their_sources_and_the_computed_series(graph: PatientGraph) -> None:
    import datetime as dt

    from medgraph.rules.flags import evaluate

    rules = evaluate(graph, dt.date(2026, 1, 1)).model_dump(mode="json")
    html = render_html(graph, build_timeline(graph), "", rules)
    (data,) = DATA.findall(html)
    model = json.loads(data)
    assert model["rules"]["as_of"] == "2026-01-01"
    cited = {s for a in model["rules"]["assessments"] for s in a["sources"]}
    assert cited
    assert cited <= set(model["rule_sources"])
    assert all(v["quote"] for v in model["rule_sources"].values())
    (computed,) = [s for s in model["series"] if s.get("computed")]
    assert computed["label"].startswith("eGFR, CKD-EPI 2021 (computed")
    # One computed value per usable creatinine; each links back to its creatinine record.
    assert [p["node"] for p in computed["points"]] == [
        "lab_result:obs-a1",
        "lab_result:obs-a2",
        "lab_result:obs-a3",
    ]


def test_notes_and_their_sources_reach_the_page(graph: PatientGraph) -> None:
    import datetime as dt

    from medgraph.rules.flags import evaluate
    from medgraph.rules.model import Note

    result = evaluate(graph, dt.date(2026, 1, 1))
    note = Note(
        rule="ras-inhibitor-nsaid", title="t", statement="s", sources=("label-zestril-nsaid",)
    )
    rules = result.model_copy(update={"notes": (note,)}).model_dump(mode="json")
    (data,) = DATA.findall(render_html(graph, build_timeline(graph), "", rules))
    model = json.loads(data)
    assert model["rules"]["notes"][0]["rule"] == "ras-inhibitor-nsaid"
    assert "lisinopril" in model["rule_sources"]["label-zestril-nsaid"]["quote"]
