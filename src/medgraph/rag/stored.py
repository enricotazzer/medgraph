"""Reading the knowledge store's pinned files.

Every reader checks the file against its pin first (``documents.yaml`` for guidelines, the
cohort's lock for labels) and refuses a file that differs. Passages are cut the way the
retrieval index cuts them (``medgraph.rag.chunks``), so a passage ID means the same thing
everywhere.
"""

from medgraph.rag.chunks import Chunk, document_chunks, label_chunks
from medgraph.rag.dailymed import LockedLabel, spl_meta
from medgraph.rag.documents import DocumentSpec, extract_pages, reference_pages
from medgraph.rag.fetch import sha256_file
from medgraph.rag.spl import sections
from medgraph.rag.store import KnowledgeStore
from medgraph.rag.text import Page


class PinError(RuntimeError):
    """A stored file does not match its pin."""


def document_pages(kstore: KnowledgeStore, spec: DocumentSpec) -> tuple[Page, ...]:
    """Every page of a pinned guideline document, reference pages included."""
    path = kstore.document_path(spec.filename)
    if sha256_file(path) != spec.sha256:
        raise PinError(f"{path} does not match its pinned SHA-256; run make knowledge")
    return extract_pages(path, spec)


def document_passages(spec: DocumentSpec, pages: tuple[Page, ...]) -> list[Chunk]:
    """The passages of a document's pages; its reference list is left out."""
    skipped = reference_pages(pages, spec)
    kept = tuple(p for p in pages if p.number not in skipped)
    return document_chunks(spec.id, spec.sha256[:12], spec.title, kept, running=spec.is_running)


def label_xml(kstore: KnowledgeStore, label: LockedLabel) -> bytes:
    """A pinned drug label's SPL XML."""
    path = kstore.label_path(label.path)
    if sha256_file(path) != label.sha256:
        raise PinError(f"{path} does not match the lock; run make knowledge")
    return path.read_bytes()


def label_passages(label: LockedLabel, xml: bytes) -> list[Chunk]:
    name = spl_meta(xml).name or label.title
    return label_chunks(label.setid, label.version, name, sections(xml))
