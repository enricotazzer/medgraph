import datetime as dt
import json
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from medgraph.graph.build import build_patient_graph
from medgraph.graph.concepts import concept_map, with_knowledge
from medgraph.graph.knowledge import (
    KnowledgeError,
    KnowledgeGraph,
    build_knowledge_graph,
    concept_id,
    knowledge_for,
    load_knowledge_graph,
    passage_ids,
    save_knowledge_graph,
)
from medgraph.graph.schema import NodeData, PatientGraph
from medgraph.graph.timeline import build_timeline
from medgraph.graph.view import concept_of, render_html
from medgraph.ingest.fhir import read_bundle_file
from medgraph.rag.chunks import Chunk
from medgraph.rag.dailymed import RXNORM, LabelLock, LockedDrug, LockedLabel
from medgraph.rag.documents import DocumentSpec
from medgraph.rag.fetch import sha256_file
from medgraph.rag.store import KnowledgeStore, ManifestEntry
from medgraph.rag.stored import PinError
from medgraph.rules.monitoring import SNOMED, GuidelineLink
from medgraph.rules.sources import Source

SPL = Path(__file__).parents[1] / "fixtures" / "spl" / "minimal.xml"
SETID = "11111111-2222-3333-4444-555555555555"
LABEL = f"label:{SETID}@v7"
LISINOPRIL = concept_id("medication_request", RXNORM, "314076")  # pat-a
FERROUS = concept_id("medication_request", RXNORM, "310325")  # pat-b
CKD3 = concept_id("condition", SNOMED, "433144002")  # pat-a
SECTION = "section 2 DOSAGE AND ADMINISTRATION > 2.2 Dose in Patients with Renal Impairment"
GUIDE = """<!doctype html><html><body><nav>Menu</nav><main>
<h1>A small guide</h1>
<p>Assess eGFR at least annually in people with CKD.</p>
<p>Measure haemoglobin when anaemia is suspected.</p>
</main></body></html>"""
DATA = re.compile(r'<script type="application/json" id="medgraph-data">(.*?)</script>', re.DOTALL)


class Store:
    """A knowledge store with one guideline page and one label, and what refers to them."""

    def __init__(self, root: Path) -> None:
        self.kstore = KnowledgeStore(root)
        self.kstore.documents_dir.mkdir(parents=True)
        guide = self.kstore.document_path("guide.html")
        guide.write_text(GUIDE, encoding="utf-8")
        label_path = self.kstore.label_path(f"{SETID}/v7.xml")
        label_path.parent.mkdir(parents=True)
        shutil.copy(SPL, label_path)
        self.kstore.save_manifest(
            {
                "documents/guide.html": ManifestEntry(
                    kind="document",
                    url="https://example.org/guide",
                    sha256=sha256_file(guide),
                    bytes=guide.stat().st_size,
                    licence="CC BY 4.0",
                    retrieved=dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC),
                )
            }
        )
        self.specs = (
            DocumentSpec(
                id="guide",
                title="A small guide",
                citation="Guide 2025",
                url="https://example.org/guide",
                format="html",
                sha256=sha256_file(guide),
                licence="CC BY 4.0",
            ),
        )
        label = LockedLabel(
            setid=SETID,
            version=7,
            published=dt.date(2025, 1, 3),
            effective=dt.date(2025, 1, 2),
            title="EXAMPLINE tablets",
            labeler="Example Pharma, Inc.",
            document_type="HUMAN PRESCRIPTION DRUG LABEL",
            approvals=("NDA000001",),
            sha256=sha256_file(label_path),
            bytes=label_path.stat().st_size,
        )
        drug: dict[str, Any] = {"patients": 1, "rxnorm_status": "Active", "tty": "SCD"}
        self.lock = LabelLock(
            rule="labels-v1",
            selected_on=dt.date(2026, 10, 8),
            cohort="tiny",
            drugs=(
                LockedDrug(
                    rxcui="314076",
                    name="lisinopril 10 MG Oral Tablet",
                    ingredients=("lisinopril",),
                    label=label,
                    tier="NDA or BLA",
                    candidates=1,
                    **drug,
                ),
                LockedDrug(
                    rxcui="310325",
                    name="ferrous sulfate 325 MG Oral Tablet",
                    ingredients=("ferrous sulfate",),
                    label=None,
                    tier=None,
                    candidates=0,
                    reason="DailyMed maps no label to this RxCUI",
                    **drug,
                ),
            ),
        )
        self.sources = {
            "guide-egfr": Source(
                id="guide-egfr",
                document="Guide 2025",
                locator="Monitoring",
                quote="Assess eGFR at least annually in people with CKD.",
                stored="guide",
            ),
            "label-egfr": Source(
                id="label-egfr",
                document="Examplix label",
                locator=SECTION,
                quote="Assess eGFR before starting EXAMPLINE [...] every 12 months thereafter",
                stored=LABEL,
            ),
        }
        self.monitoring = {
            "433144002": (
                "Chronic kidney disease stage 3 (disorder)",
                (
                    GuidelineLink(
                        analyte="egfr",
                        source="Guide 2025",
                        locator="Monitoring",
                        quote="Assess eGFR at least annually",
                        stored="guide",
                    ),
                ),
            )
        }

    def build(self, **changes: Any) -> KnowledgeGraph:
        arguments: dict[str, Any] = {
            "cohort": "tiny",
            "specs": self.specs,
            "lock": self.lock,
            "kstore": self.kstore,
            "inputs": {"lock": "a"},
            "sources": self.sources,
            "monitoring": self.monitoring,
        }
        return build_knowledge_graph(**{**arguments, **changes})


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "knowledge")


@pytest.fixture
def patients(fhir_fixture_dir: Path) -> list[PatientGraph]:
    return [
        build_patient_graph(read_bundle_file(fhir_fixture_dir / f"patient-{x}.json"))
        for x in ("a", "b")
    ]


def test_drugs_conditions_and_checked_statements_are_linked(store: Store) -> None:
    graph = store.build()
    assert {n: graph.nodes[n].kind for n in graph.nodes} == {
        "document:guide": "document",
        LABEL: "label",
        "statement:guide-egfr": "statement",
        "statement:label-egfr": "statement",
        "statement:monitoring-egfr": "statement",
    }
    links = {(x.source, x.target, x.kind) for x in graph.links}
    assert (LISINOPRIL, LABEL, "labelled_by") in links
    assert (CKD3, "document:guide", "guided_by") in links
    assert ("statement:label-egfr", LABEL, "quotes") in links
    assert graph.unlabelled == {FERROUS: "DailyMed maps no label to this RxCUI"}
    label = graph.nodes[LABEL]
    assert label.title == "Examplix (exampline)"
    assert label.sections[-1] == "4 CONTRAINDICATIONS"
    statement = graph.nodes["statement:label-egfr"]
    assert statement.passages
    assert all(p.startswith(f"{LABEL}/2.") for p in statement.passages)
    guide = graph.nodes["document:guide"]
    assert (guide.licence, guide.retrieved) == (
        "CC BY 4.0",
        dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC),
    )
    assert graph.digest() == store.build().digest()


def test_a_quote_not_in_the_stored_text_stops_the_build(store: Store) -> None:
    altered = replace(store.sources["guide-egfr"], quote="Assess eGFR at least monthly")
    with pytest.raises(KnowledgeError, match=r"statement:guide-egfr: not in the document"):
        store.build(sources={**store.sources, "guide-egfr": altered})


def test_a_label_version_outside_the_lock_stops_the_build(store: Store) -> None:
    other = replace(store.sources["label-egfr"], stored=f"label:{SETID}@v6")
    with pytest.raises(KnowledgeError, match="not a stored document or a locked label"):
        store.build(sources={**store.sources, "label-egfr": other})


def test_a_changed_stored_file_stops_the_build(store: Store) -> None:
    path = store.kstore.document_path("guide.html")
    path.write_text(GUIDE.replace("annually", "yearly"), encoding="utf-8")
    with pytest.raises(PinError, match=r"guide\.html"):
        store.build()


def test_a_saved_graph_is_checked_when_loaded(store: Store, tmp_path: Path) -> None:
    graph = store.build()
    path = tmp_path / "graph.json"
    save_knowledge_graph(graph, path)
    assert load_knowledge_graph(path, {"lock": "a"}) == graph
    with pytest.raises(KnowledgeError, match=r"other inputs \(lock\)"):
        load_knowledge_graph(path, {"lock": "b"})
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["graph"]["nodes"]["document:guide"]["title"] = "Another guide"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="digest"):
        load_knowledge_graph(path)


def test_passages_follow_the_locator_and_may_span_two() -> None:
    def chunk(id_: str, where: str, text: str) -> Chunk:
        return Chunk(id=id_, source="d", version="v", title="t", where=where, text=text)

    chunks = [
        chunk("d@v/1.1", "p. 1", "alpha beta"),
        chunk("d@v/1.2", "p. 1", "gamma delta"),
        chunk("d@v/2.1", "p. 2", "alpha beta gamma"),
    ]
    assert passage_ids("beta gamma", "p. 1", chunks) == ("d@v/1.1", "d@v/1.2")
    assert passage_ids("alpha beta", "p. 2", chunks) == ("d@v/2.1",)
    assert passage_ids("alpha beta [...] delta", "", chunks) == ("d@v/1.1", "d@v/2.1", "d@v/1.2")
    assert passage_ids("alpha zeta", "", chunks) == ()


def test_concept_ids_are_the_patient_page_ones() -> None:
    drug = NodeData(kind="medication_request", label="x", code=f"{RXNORM}|314076")
    condition = NodeData(kind="condition", label="y", code=f"{SNOMED}|433144002")
    assert concept_of("medication_request:m1", drug) == LISINOPRIL
    assert concept_of("condition:c1", condition) == CKD3


def test_a_page_gets_the_joined_sources_and_the_cited_ones(store: Store) -> None:
    graph = store.build()
    joined = knowledge_for(graph, [LISINOPRIL, FERROUS])
    assert set(joined["nodes"]) == {LABEL, "statement:label-egfr"}
    assert [x["kind"] for x in joined["links"]] == ["labelled_by"]
    assert joined["unlabelled"] == {FERROUS: "DailyMed maps no label to this RxCUI"}
    assert joined["cited"] == []
    cited = knowledge_for(graph, [], ["statement:guide-egfr"])
    assert set(cited["nodes"]) == {
        "document:guide",
        "statement:guide-egfr",
        "statement:monitoring-egfr",
    }
    assert cited["links"] == []
    assert cited["cited"] == ["statement:guide-egfr"]


def test_patient_page_links_drug_and_condition_to_their_sources(
    store: Store, patients: list[PatientGraph]
) -> None:
    graph = patients[0]
    html = render_html(graph, build_timeline(graph), "", knowledge=store.build())
    assert not re.search(r"<(?:script|link|img|iframe)[^>]*\s(?:src|href)=", html, re.IGNORECASE)
    (data,) = DATA.findall(html)
    model = json.loads(data)
    concepts = {c["id"]: c for c in model["concepts"]}
    assert concepts[LABEL]["kind"] == concepts["document:guide"]["kind"] == "source"
    links = {(x["source"], x["target"], x["kind"]): x for x in model["links"]}
    assert (LISINOPRIL, LABEL, "labelled_by") in links
    assert (CKD3, "document:guide", "guided_by") in links
    # The monitoring link names the checked statement it rests on, when there is one.
    assert links[(CKD3, "analyte:egfr", "monitored_by")]["statement"] == "statement:monitoring-egfr"
    assert "statement" not in links[(CKD3, "analyte:creatinine", "monitored_by")]
    assert model["knowledge"]["quotes"]["statement:monitoring-egfr"] == "document:guide"


def test_concept_map_counts_the_patients_behind_each_source(
    store: Store, patients: list[PatientGraph]
) -> None:
    model = with_knowledge(concept_map(patients), store.build())
    concepts = {c["id"]: c for c in model["concepts"]}
    assert (concepts[LABEL]["kind"], concepts[LABEL]["patients"]) == ("source", [0])
    assert concepts["document:guide"]["patients"] == [0]
    assert concepts[FERROUS]["no_label"] == "DailyMed maps no label to this RxCUI"
    (link,) = (x for x in model["links"] if x["kind"] == "labelled_by")
    assert (link["source"], link["patients"], link["edges"]) == (LISINOPRIL, [0], None)
    assert set(model["knowledge"]["nodes"]) >= {LABEL, "document:guide"}
