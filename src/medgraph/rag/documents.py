"""The guideline documents and reference pages in the knowledge store
(``configs/knowledge/documents.yaml``)."""

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from medgraph.rag.text import Page, html_pages, pdf_pages


class DocumentSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]+$")
    title: str
    citation: str
    url: str
    format: Literal["pdf", "html"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    licence: str
    licence_quote: str | None = None
    page_patterns: tuple[str, ...] = ()
    page_labels: Literal["pdf"] | None = None
    running: tuple[str, ...] = ()
    bullets: tuple[str, ...] = ()
    reference_pages: tuple[str, str] | None = None  # first and last printed page

    @property
    def filename(self) -> str:
        return f"{self.id}.{self.format}"

    def is_running(self, line: str) -> bool:
        """A running header or footer line, including the printed page number's line."""
        return any(re.match(p, line) for p in self.page_patterns) or any(
            re.search(p, line) for p in self.running
        )


def reference_pages(pages: tuple[Page, ...], spec: DocumentSpec) -> set[int]:
    """Positions (``Page.number``) of the reference list, from its first to its last page."""
    if spec.reference_pages is None:
        return set()
    first, last = spec.reference_pages
    numbers = {p.label: p.number for p in pages if p.label}
    if first not in numbers or last not in numbers:
        raise ValueError(f"{spec.id}: reference pages {first}-{last} not found")
    return set(range(numbers[first], numbers[last] + 1))


def load_documents(path: Path) -> tuple[DocumentSpec, ...]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    specs = tuple(DocumentSpec.model_validate(d) for d in raw["documents"])
    ids = [s.id for s in specs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate document IDs in {path}")
    return specs


def extract_pages(path: Path, spec: DocumentSpec) -> tuple[Page, ...]:
    if spec.format == "html":
        return html_pages(path)
    return pdf_pages(
        path,
        page_patterns=spec.page_patterns,
        pdf_labels=spec.page_labels == "pdf",
        bullets=spec.bullets,
    )
