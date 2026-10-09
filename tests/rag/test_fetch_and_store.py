"""Verified downloads and the knowledge store's manifest."""

import datetime as dt
from pathlib import Path

import httpx
import pytest

from medgraph.rag.fetch import USER_AGENT, Fetcher, FetchError, sha256_bytes, sha256_file
from medgraph.rag.store import KnowledgeStore, ManifestEntry

BODY = b"%PDF-1.7 a guideline"


def serving(*statuses: int, body: bytes = BODY) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """Answer with these status codes in turn, then repeat the last one."""
    seen: list[httpx.Request] = []
    queue = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        status = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, content=body if status == 200 else b"")

    return httpx.MockTransport(handler), seen


def no_sleep(_: float) -> None:
    pass


def test_download_stores_a_matching_file(tmp_path: Path) -> None:
    transport, seen = serving(200)
    dest = tmp_path / "documents" / "g.pdf"
    digest = Fetcher(transport=transport, delay_s=0).download(
        "https://x/g.pdf", dest, sha256_bytes(BODY)
    )
    assert digest == sha256_bytes(BODY) == sha256_file(dest)
    assert dest.read_bytes() == BODY
    assert [p.name for p in dest.parent.iterdir()] == ["g.pdf"]  # no temporary file left
    assert seen[0].headers["user-agent"] == USER_AGENT


def test_download_refuses_a_changed_document(tmp_path: Path) -> None:
    transport, _ = serving(200, body=b"something else")
    dest = tmp_path / "g.pdf"
    with pytest.raises(FetchError, match="does not match the pinned"):
        Fetcher(transport=transport, delay_s=0).download(
            "https://x/g.pdf", dest, sha256_bytes(BODY)
        )
    assert not dest.exists()


def test_transient_errors_are_retried_and_others_are_not() -> None:
    waits: list[float] = []
    transport, seen = serving(503, 429, 200)
    fetcher = Fetcher(transport=transport, delay_s=0, sleep=waits.append)
    assert fetcher.get("https://x/a").content == BODY
    assert len(seen) == 3
    assert waits == [1, 2]  # exponential backoff

    transport, seen = serving(404)
    with pytest.raises(FetchError, match="HTTP 404"):
        Fetcher(transport=transport, delay_s=0, sleep=no_sleep).get("https://x/missing")
    assert len(seen) == 1

    transport, seen = serving(502)
    with pytest.raises(FetchError, match="HTTP 502 after 3 attempts"):
        Fetcher(transport=transport, delay_s=0, retries=2, sleep=no_sleep).get("https://x/down")
    assert len(seen) == 3


def test_requests_are_spaced() -> None:
    waits: list[float] = []
    transport, _ = serving(200)
    fetcher = Fetcher(transport=transport, delay_s=5, sleep=waits.append)
    fetcher.get("https://x/a")
    fetcher.get("https://x/b")
    assert len(waits) == 1
    assert 4 < waits[0] <= 5


def entry(sha: str, retrieved: dt.datetime) -> ManifestEntry:
    return ManifestEntry(
        kind="document", url="https://x", sha256=sha, bytes=3, licence="L", retrieved=retrieved
    )


def test_manifest_round_trips_and_keeps_the_first_retrieval_time(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge")
    first = dt.datetime(2026, 10, 8, 9, tzinfo=dt.UTC)
    later = dt.datetime(2026, 10, 9, 9, tzinfo=dt.UTC)
    manifest: dict[str, ManifestEntry] = {}
    store.record(manifest, "documents/g.pdf", entry("a" * 64, first))
    store.record(manifest, "documents/g.pdf", entry("a" * 64, later))
    assert manifest["documents/g.pdf"].retrieved == first
    store.record(manifest, "documents/g.pdf", entry("b" * 64, later))
    assert manifest["documents/g.pdf"].retrieved == later
    store.save_manifest(manifest)
    assert store.load_manifest() == manifest
    assert store.relative(store.label_path("s/v2.xml")) == "labels/s/v2.xml"
    assert KnowledgeStore(tmp_path / "empty").load_manifest() == {}
