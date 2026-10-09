"""The knowledge-store fetch: pinned documents, the label lock, and verification."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

import fetch_knowledge as fk
from medgraph.rag.dailymed import TIERS, LabelLock
from medgraph.rag.documents import DocumentSpec, load_documents
from medgraph.rag.fetch import Fetcher, sha256_bytes
from medgraph.rag.store import KnowledgeStore

SPL = (Path(__file__).parents[1] / "fixtures" / "spl" / "minimal.xml").read_bytes()
SETID = "11111111-2222-3333-4444-555555555555"
PAGE = (
    b"<html><body><main><p>Estimate GFR in individuals ages 18 and older.</p>"
    b"<p>This page is free to use.</p></main></body></html>"
)


def spec(**changes: Any) -> DocumentSpec:
    fields: dict[str, Any] = {
        "id": "page",
        "title": "A page",
        "citation": "A page, web",
        "url": "https://example.org/page",
        "format": "html",
        "sha256": sha256_bytes(PAGE),
        "licence": "free",
        "licence_quote": "This page is free to use.",
    }
    return DocumentSpec(**(fields | changes))


def transport(spl: bytes = SPL, listed: bool = True) -> httpx.MockTransport:
    nda = TIERS[0][1][0]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/page":
            return httpx.Response(200, content=PAGE)
        if path.endswith("historystatus.json"):
            return httpx.Response(
                200, json={"rxcuiStatusHistory": {"metaData": {"status": "Active"}}}
            )
        if path.endswith("spls.json"):
            code = request.url.params.get("marketing_category_code")
            items = [
                {"setid": SETID, "spl_version": 7, "published_date": "Jan 02, 2025", "title": "X"}
            ]
            return httpx.Response(200, json={"data": items if listed and code == nda else []})
        if path.endswith(f"{SETID}.xml"):
            return httpx.Response(200, content=spl)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_the_committed_document_config_is_valid() -> None:
    specs = load_documents(fk.CONFIG_DIR / "documents.yaml")
    assert {s.id for s in specs} >= {"kdigo-2024-ckd", "kdigo-2012-anaemia", "who-2024-haemoglobin"}
    for s in specs:
        assert s.url.startswith("https://")
        assert s.licence_quote or "U.S. Government" in s.licence


def test_documents_are_pinned_and_their_licence_checked(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    manifest: dict[str, Any] = {}
    fetcher = Fetcher(transport=transport(), delay_s=0)
    assert fk.fetch_documents((spec(),), store, manifest, fetcher) == []
    assert store.document_path("page.html").read_bytes() == PAGE
    assert manifest["documents/page.html"].sha256 == sha256_bytes(PAGE)
    # Stored files are verified offline.
    assert fk.fetch_documents((spec(),), store, manifest, None) == []

    wrong = spec(id="other", sha256="0" * 64)
    assert "does not match the pinned" in fk.fetch_documents((wrong,), store, manifest, fetcher)[0]
    assert not store.document_path("other.html").exists()
    missing = spec(id="third", licence_quote="All rights reserved.")
    assert fk.fetch_documents((missing,), store, manifest, fetcher) == [
        "third: licence statement not found in the text"
    ]
    assert fk.fetch_documents((spec(id="fourth"),), store, manifest, None) == [
        "fourth: not stored (offline)"
    ]


def test_lock_then_verify(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    manifest: dict[str, Any] = {}
    drugs = {"314076": ("lisinopril 10 MG Oral Tablet", 3), "197378": ("astemizole", 1)}
    lock, problems = fk.lock_labels(
        "c", drugs, Fetcher(transport=transport(), delay_s=0), store, manifest
    )
    assert problems == []
    assert lock is not None
    assert not list(tmp_path.glob("*.progress.jsonl"))  # removed once the lock is complete
    by_rxcui = {d.rxcui: d for d in lock.drugs}
    assert [d.rxcui for d in lock.drugs] == ["197378", "314076"]  # numeric order
    label = by_rxcui["314076"].label
    assert label is not None
    assert (label.setid, label.version, label.sha256) == (SETID, 7, sha256_bytes(SPL))
    assert by_rxcui["314076"].tier == "NDA or BLA"
    assert store.label_path(label.path).read_bytes() == SPL
    assert manifest[f"labels/{SETID}/v7.xml"].kind == "label"
    # Both RxCUIs get the same label: it is downloaded once.
    assert list(lock.labels()) == [SETID]
    again = LabelLock.model_validate_json(json.dumps(lock.model_dump(mode="json")))
    assert fk.verify_labels(again, store, manifest, None) == []

    # A missing file is fetched again only if DailyMed still serves the pinned version.
    store.label_path(label.path).unlink()
    newer = SPL.replace(b'<versionNumber value="7"/>', b'<versionNumber value="8"/>')
    problems = fk.verify_labels(
        lock, store, manifest, Fetcher(transport=transport(newer), delay_s=0)
    )
    assert problems == [
        f"label {SETID} v7: DailyMed now serves version 8, the lock pins 7; re-run with --lock "
        "to choose again"
    ]
    assert fk.verify_labels(lock, store, manifest, Fetcher(transport=transport(), delay_s=0)) == []

    store.label_path(label.path).write_bytes(b"tampered")
    assert fk.verify_labels(lock, store, manifest, None) == [
        f"label {SETID} v7: stored file differs from the lock"
    ]


def test_drugs_without_a_label_are_kept_with_a_reason(tmp_path: Path) -> None:
    fetcher = Fetcher(transport=transport(listed=False), delay_s=0)
    lock, _ = fk.lock_labels(
        "c", {"197378": ("astemizole", 1)}, fetcher, KnowledgeStore(tmp_path), {}
    )
    assert lock is not None
    (drug,) = lock.drugs
    assert (drug.label, drug.tier, drug.candidates) == (None, None, 0)
    assert drug.reason == "DailyMed maps no label to this RxCUI"
    assert "no label: 197378 astemizole (1 patients)" in fk.summarize_lock(lock)


def test_a_failed_drug_stops_the_lock_and_a_rerun_resumes(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path)
    drugs = {"314076": ("lisinopril", 3), "999": ("broken", 1)}
    healthy = transport()
    calls: list[str] = []
    down = [True]

    def flaky(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "rxcui/999/" in request.url.path and down[0]:
            return httpx.Response(503)
        return healthy.handle_request(request)

    fetcher = Fetcher(
        transport=httpx.MockTransport(flaky), delay_s=0, retries=1, sleep=lambda s: None
    )
    lock, problems = fk.lock_labels("c", drugs, fetcher, store, {})
    assert lock is None
    assert len(problems) == 1
    assert problems[0].startswith("RxCUI 999: ")
    first_round = len(calls)
    down[0] = False
    lock, problems = fk.lock_labels("c", drugs, fetcher, store, {})
    assert problems == []
    assert lock is not None
    assert [d.rxcui for d in lock.drugs] == ["999", "314076"]
    # The rerun asked only about the drug that had failed.
    assert all("314076" not in url for url in calls[first_round:])


def test_only_synthea_cohorts_are_allowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", str(tmp_path))
    with pytest.raises(SystemExit):
        fk.main(["mimic-iv"])
