"""Passages for retrieval, each with a stable ID and the place it came from.

- **Guidelines** are cut by page, never across one, so every passage cites a single page.
  Within a page, paragraphs are rebuilt from lines: a short line ending a sentence closes a
  paragraph, and a bullet, recommendation, practice point, table or figure opens one. Running
  headers and footers are dropped.
- **Drug labels** are cut by section (``medgraph.rag.spl``); a passage cites its section.

Paragraphs are packed into passages of at most ``MAX_WORDS`` words. A longer paragraph is split
at sentence ends, and a longer sentence at a word boundary.

IDs: ``<source>@<version>/<page or section>.<n>``, for example
``kdigo-2024-ckd@b18db280f7dc/34.2`` (the file's SHA-256 prefix) or
``label:838c2d78-d2d8-4981-9ec9-e50ef9e1a5d8@v2/8.1``. The same file always gives the same
passages and IDs; a new version gives new IDs, so an old citation never points at changed text.
"""

import re
import statistics
from collections.abc import Callable, Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from medgraph.rag.spl import Section
from medgraph.rag.text import Page

MAX_WORDS = 200
OPENS_PARAGRAPH = re.compile(
    r"^(•|Recommendation \d|Practice Point \d|Research recommendation|Table \d|Figure \d"
    r"|Chapter \d|Normative statement|\d+(\.\d+)+:? )"
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(•\[])")


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    source: str  # document ID, or "label:<set id>"
    version: str  # SHA-256 prefix of a document, or "v<n>" of a label
    title: str  # the document's or label's title
    where: str  # "p. S149", or "section 2 DOSAGE AND ADMINISTRATION > 2.4 ..."
    code: str | None = None  # LOINC section code of a label passage
    text: str


def words(text: str) -> int:
    return len(text.split())


def paragraphs(lines: Sequence[str]) -> list[str]:
    """Rebuild paragraphs from wrapped lines."""
    lines = [line.strip() for line in lines if line.strip()]
    if not lines:
        return []
    typical = statistics.median(len(line) for line in lines)
    result: list[list[str]] = [[]]
    for line in lines:
        if result[-1] and OPENS_PARAGRAPH.match(line):
            result.append([])
        result[-1].append(line)
        if line.endswith((".", ":", "?", "!")) and len(line) < 0.75 * typical:
            result.append([])
    return [" ".join(p) for p in result if p]


def _pieces(paragraph: str) -> list[str]:
    """A paragraph cut so that no piece exceeds MAX_WORDS."""
    if words(paragraph) <= MAX_WORDS:
        return [paragraph]
    pieces: list[str] = []
    for sentence in SENTENCE_END.split(paragraph):
        tokens = sentence.split()
        for start in range(0, len(tokens), MAX_WORDS):
            pieces.append(" ".join(tokens[start : start + MAX_WORDS]))
    return pieces


def pack(units: Iterable[str]) -> list[str]:
    """Consecutive paragraphs joined into passages of at most MAX_WORDS words."""
    passages: list[str] = []
    current: list[str] = []
    for piece in (p for unit in units for p in _pieces(unit)):
        if current and words(" ".join([*current, piece])) > MAX_WORDS:
            passages.append(" ".join(current))
            current = []
        current.append(piece)
    if current:
        passages.append(" ".join(current))
    return passages


def _chunks(
    source: str,
    version: str,
    title: str,
    number: int,
    where: str,
    code: str | None,
    units: Iterable[str],
) -> list[Chunk]:
    return [
        Chunk(
            id=f"{source}@{version}/{number}.{n}",
            source=source,
            version=version,
            title=title,
            where=where,
            code=code,
            text=text,
        )
        for n, text in enumerate(pack(units), start=1)
    ]


def document_chunks(
    source: str,
    version: str,
    title: str,
    pages: Sequence[Page],
    *,
    running: Callable[[str], bool] = lambda line: False,
) -> list[Chunk]:
    """Passages of a guideline or web page. ``running`` tells a running header or footer; it
    is applied to the first and last two lines of a page only."""
    chunks = []
    for page in pages:
        lines = [line.strip() for line in page.text.splitlines() if line.strip()]
        edges = {0, 1, len(lines) - 2, len(lines) - 1}
        lines = [line for i, line in enumerate(lines) if i not in edges or not running(line)]
        chunks += _chunks(source, version, title, page.number, page.cite, None, paragraphs(lines))
    return chunks


def label_chunks(setid: str, version: int, title: str, sections: Sequence[Section]) -> list[Chunk]:
    """Passages of a drug label: its lines (paragraphs, list items, table rows) by section."""
    chunks = []
    for section in sections:
        chunks += _chunks(
            f"label:{setid}",
            f"v{version}",
            title,
            section.index,
            f"section {section.title}",
            section.code,
            section.text.splitlines(),
        )
    return chunks
