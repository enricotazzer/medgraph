"""Every quote a rule or a graph link cites occurs in the stored document, on the cited page.

Needs the knowledge store (``make knowledge``); run with ``pytest --run-knowledge``. The
documents' licences forbid committing them, so this cannot run without the local store.
"""

from pathlib import Path

import pytest

from medgraph.rag.dailymed import LabelLock
from medgraph.rag.documents import DocumentSpec, extract_pages, load_documents, reference_pages
from medgraph.rag.fetch import sha256_file
from medgraph.rag.quotes import check_quote
from medgraph.rag.spl import section_pages
from medgraph.rag.text import Page
from medgraph.rules.monitoring import MONITORING
from medgraph.rules.sources import SOURCES
from medgraph.settings import Settings

pytestmark = pytest.mark.knowledge

CONFIG = Path(__file__).parents[2] / "configs" / "knowledge" / "documents.yaml"
LOCK = CONFIG.with_name("labels-dev-1000.lock.json")
LINKS = {
    f"{link.analyte}: {link.locator}": link for _, links in MONITORING.values() for link in links
}


@pytest.fixture(scope="module")
def documents() -> dict[str, tuple[Page, ...]]:
    root = Settings().knowledge_dir / "documents"
    pages = {}
    for spec in load_documents(CONFIG):
        path = root / spec.filename
        if not path.exists():
            pytest.fail(f"{path} is missing; run make knowledge")
        assert sha256_file(path) == spec.sha256, f"{spec.id} is not the pinned file"
        pages[spec.id] = extract_pages(path, spec)
    return pages


def specs() -> dict[str, DocumentSpec]:
    return {s.id: s for s in load_documents(CONFIG)}


def label_sections(stored: str) -> tuple[Page, ...]:
    """The sections of a pinned label, ``label:<set id>@v<version>``, after checking its hash."""
    setid, _, version = stored.removeprefix("label:").partition("@v")
    lock = LabelLock.model_validate_json(LOCK.read_text(encoding="utf-8"))
    label = lock.labels()[setid]
    assert label.version == int(version)
    path = Settings().knowledge_dir / "labels" / label.path
    assert sha256_file(path) == label.sha256, f"{path} is not the pinned file"
    return section_pages(path.read_bytes())


@pytest.mark.parametrize("source_id", sorted(SOURCES))
def test_rule_source_quotes(source_id: str, documents: dict[str, tuple[Page, ...]]) -> None:
    s = SOURCES[source_id]
    pages = label_sections(s.stored) if s.stored.startswith("label:") else documents[s.stored]
    assert check_quote(s.quote, pages, locator=s.locator, table=s.table) == []


@pytest.mark.parametrize("name", sorted(LINKS))
def test_monitoring_link_quotes(name: str, documents: dict[str, tuple[Page, ...]]) -> None:
    link = LINKS[name]
    assert check_quote(link.quote, documents[link.stored], locator=link.locator) == []


@pytest.mark.parametrize("doc_id", sorted(specs()))
def test_reference_pages_exist(doc_id: str, documents: dict[str, tuple[Page, ...]]) -> None:
    spec = specs()[doc_id]
    skipped = reference_pages(documents[doc_id], spec)
    assert bool(skipped) == (spec.reference_pages is not None)


@pytest.mark.parametrize("doc_id", sorted(specs()))
def test_licence_statements(doc_id: str, documents: dict[str, tuple[Page, ...]]) -> None:
    spec = specs()[doc_id]
    if spec.licence_quote:
        assert check_quote(spec.licence_quote, documents[doc_id]) == []
