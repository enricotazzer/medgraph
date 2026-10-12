"""The knowledge graph: the stored sources, joined to patient concepts by code.

It is built once per cohort from the knowledge store (``make knowledge``) by
``scripts/build_knowledge_graph.py`` and kept apart from the patient graphs, which hold only
what a record says. A page joins the two by concept ID (:func:`medgraph.graph.view.concept_of`)
when it is written. Every link comes from a table in the repository, never from text
similarity:

- ``labelled_by``: a medication's RxNorm code to the drug label the cohort's lock chose for it
  (``configs/knowledge/labels-<cohort>.lock.json``);
- ``guided_by``: a condition code to the guideline its monitoring links cite
  (:mod:`medgraph.rules.monitoring`);
- ``quotes``: a statement that a rule or a monitoring link quotes, to the document or label
  it is checked against (:mod:`medgraph.rules.sources`).

Each statement's quote is checked against the stored text when the graph is built, and the
statement keeps the IDs of the passages that hold it (cut as the retrieval index cuts them). A
graph that cannot be built that way is not built. Pages show a source's details, its section
titles and these quotes, never its full text: KDIGO 2012 has no open licence, and NLM asks for
short quotes with attribution.
"""

import dataclasses
import datetime as dt
import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from medgraph.rag.chunks import Chunk
from medgraph.rag.dailymed import RXNORM, LabelLock
from medgraph.rag.documents import DocumentSpec
from medgraph.rag.fetch import sha256_file, write_atomic
from medgraph.rag.quotes import check_quote, fragments, locator_pages
from medgraph.rag.spl import section_pages
from medgraph.rag.store import KnowledgeStore
from medgraph.rag.stored import document_pages, document_passages, label_passages, label_xml
from medgraph.rag.text import Page, find
from medgraph.rules.monitoring import MONITORING, SNOMED, GuidelineLink
from medgraph.rules.sources import SOURCES, Source

SCHEMA_VERSION = "knowledge-graph-v1"
KnowledgeKind = Literal["document", "label", "statement"]
KnowledgeLinkKind = Literal["labelled_by", "guided_by", "quotes"]


class KnowledgeError(RuntimeError):
    """The knowledge graph cannot be built, or a stored one cannot be used."""


class KnowledgeNode(BaseModel):
    """A guideline document, a drug label, or a statement quoted from one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: KnowledgeKind
    title: str  # a document's title, a label's product name, a statement's locator
    citation: str
    licence: str | None = None
    url: str | None = None  # where the stored file was retrieved from
    sha256: str | None = None  # of the stored file
    retrieved: dt.datetime | None = None
    # Labels only.
    labeler: str | None = None
    setid: str | None = None
    version: int | None = None
    effective: dt.date | None = None
    document_type: str | None = None
    sections: tuple[str, ...] = ()  # section titles that hold text, in document order
    # Statements only.
    locator: str | None = None
    quote: str | None = None
    table: str | None = None  # a table transcribed row by row: a weaker check (rag.quotes)
    passages: tuple[str, ...] = ()  # IDs of the passages that hold the quote


class KnowledgeLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str  # a patient concept ID, or a statement's node ID
    target: str  # a knowledge node ID
    kind: KnowledgeLinkKind
    basis: str
    detail: str | None = None


class KnowledgeGraph(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    cohort: str
    inputs: dict[str, str]  # SHA-256 of what it was built from (knowledge_inputs)
    nodes: dict[str, KnowledgeNode]
    links: tuple[KnowledgeLink, ...]
    unlabelled: dict[str, str]  # medication concept ID -> why it has no label

    def digest(self) -> str:
        text = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()


def concept_id(kind: str, system: str, code: str) -> str:
    """A coded record's concept ID, as :func:`medgraph.graph.view.concept_of` writes it."""
    return f"{kind}|{system}|{code}"


def document_node(doc_id: str) -> str:
    return f"document:{doc_id}"


def stored_node(stored: str) -> str:
    """The node a statement quotes: ``stored`` is a document ID or ``label:<set id>@v<n>``."""
    return stored if stored.startswith("label:") else document_node(stored)


def monitoring_statement(link: GuidelineLink) -> str:
    """The statement node of a monitoring link; links are keyed by analyte."""
    return monitoring_statement_for(link.analyte)


def monitoring_statement_for(analyte: str) -> str:
    return f"statement:monitoring-{analyte}"


def statement_of_source(source_id: str) -> str:
    return f"statement:{source_id}"


def sources_digest(
    sources: Mapping[str, Source] = SOURCES,
    monitoring: Mapping[str, tuple[str, tuple[GuidelineLink, ...]]] = MONITORING,
) -> str:
    """One digest of every quoted statement, so a graph built from older code is noticed."""
    data = {
        "sources": [dataclasses.asdict(sources[k]) for k in sorted(sources)],
        "monitoring": {
            code: [display, [dataclasses.asdict(link) for link in links]]
            for code, (display, links) in sorted(monitoring.items())
        },
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def knowledge_inputs(documents_config: Path, lock_file: Path) -> dict[str, str]:
    return {
        "schema": SCHEMA_VERSION,
        "documents": sha256_file(documents_config),
        "lock": sha256_file(lock_file),
        "sources": sources_digest(),
    }


def passage_ids(quote: str, locator: str, chunks: Sequence[Chunk]) -> tuple[str, ...]:
    """The passages holding each fragment of a quote, on the pages or in the section its
    locator names; empty if any fragment is in none of them."""
    if locator.startswith("section "):
        wanted = {locator}
    else:
        wanted = {f"p. {page}" for page in locator_pages(locator)}
    pool = [c for c in chunks if not wanted or c.where in wanted]
    pages = [Page(i, c.id, c.text) for i, c in enumerate(pool)]
    ids: list[str] = []
    for fragment in fragments(quote):
        hits = find(fragment, pages) if pages else []
        if not hits:
            return ()
        for hit in hits:
            ids += [pool[i].id for i in range(hit.first.number, hit.last.number + 1)]
    return tuple(dict.fromkeys(ids))


def build_knowledge_graph(
    *,
    cohort: str,
    specs: Sequence[DocumentSpec],
    lock: LabelLock,
    kstore: KnowledgeStore,
    inputs: dict[str, str],
    sources: Mapping[str, Source] = SOURCES,
    monitoring: Mapping[str, tuple[str, tuple[GuidelineLink, ...]]] = MONITORING,
) -> KnowledgeGraph:
    """Every stored document and pinned label, the links to concepts, and each quoted
    statement, checked. Raises :class:`KnowledgeError` listing every problem."""
    manifest = kstore.load_manifest()
    nodes: dict[str, KnowledgeNode] = {}
    pages: dict[str, tuple[Page, ...]] = {}
    passages: dict[str, list[Chunk]] = {}

    for spec in specs:
        node = document_node(spec.id)
        pages[node] = document_pages(kstore, spec)
        passages[node] = document_passages(spec, pages[node])
        entry = manifest.get(f"documents/{spec.filename}")
        nodes[node] = KnowledgeNode(
            kind="document",
            title=spec.title,
            citation=spec.citation,
            licence=spec.licence,
            url=spec.url,
            sha256=spec.sha256,
            retrieved=entry.retrieved if entry else None,
        )
    for setid, label in sorted(lock.labels().items()):
        node = f"label:{setid}@v{label.version}"
        xml = label_xml(kstore, label)
        pages[node] = section_pages(xml)
        passages[node] = label_passages(label, xml)
        name = passages[node][0].title if passages[node] else label.title
        entry = manifest.get(f"labels/{label.path}")
        nodes[node] = KnowledgeNode(
            kind="label",
            title=name,
            citation=(
                f"{name}, prescribing information ({label.labeler}). DailyMed set ID {setid}, "
                f"version {label.version}, effective {label.effective}"
            ),
            licence=entry.licence if entry else None,
            url=entry.url if entry else None,
            sha256=label.sha256,
            retrieved=entry.retrieved if entry else None,
            labeler=label.labeler,
            setid=setid,
            version=label.version,
            effective=label.effective,
            document_type=label.document_type,
            sections=tuple(dict.fromkeys(c.where.removeprefix("section ") for c in passages[node])),
        )

    links: list[KnowledgeLink] = []
    unlabelled: dict[str, str] = {}
    for drug in sorted(lock.drugs, key=lambda d: d.rxcui):
        concept = concept_id("medication_request", RXNORM, drug.rxcui)
        if drug.label is None:
            unlabelled[concept] = drug.reason or "the lock gives no label"
            continue
        links.append(
            KnowledgeLink(
                source=concept,
                target=f"label:{drug.label.setid}@v{drug.label.version}",
                kind="labelled_by",
                basis=f"label lock ({lock.rule}, chosen {lock.selected_on})",
                detail=f"{drug.tier}; {drug.candidates} label(s) listed in that tier",
            )
        )
    for code, (display, guideline_links) in sorted(monitoring.items()):
        for stored in sorted({link.stored for link in guideline_links}):
            links.append(
                KnowledgeLink(
                    source=concept_id("condition", SNOMED, code),
                    target=document_node(stored),
                    kind="guided_by",
                    basis="monitoring table (rules/monitoring.py)",
                    detail=display,
                )
            )

    statements: dict[str, tuple[str, str, str, str, str]] = {
        statement_of_source(s.id): (s.citation, s.locator, s.quote, s.table, s.stored)
        for s in sources.values()
    }
    for _, guideline_links in monitoring.values():
        for link in guideline_links:
            statements[monitoring_statement(link)] = (
                link.citation,
                link.locator,
                link.quote,
                "",
                link.stored,
            )
    problems: list[str] = []
    for statement, (citation, locator, quote, table, stored) in sorted(statements.items()):
        target = stored_node(stored)
        if target not in nodes:
            problems.append(f"{statement}: {stored} is not a stored document or a locked label")
            continue
        found = check_quote(quote, pages[target], locator=locator, table=table)
        problems += [f"{statement}: {p}" for p in found]
        held_by = passage_ids(quote, locator, passages[target])
        if not found and not held_by:
            problems.append(f"{statement}: the quote is in the text but in no single passage run")
        if found or not held_by:
            continue
        nodes[statement] = KnowledgeNode(
            kind="statement",
            title=locator,
            citation=citation,
            locator=locator,
            quote=quote,
            table=table or None,
            passages=held_by,
        )
        links.append(
            KnowledgeLink(
                source=statement,
                target=target,
                kind="quotes",
                basis="quote checked against the stored text",
                detail=locator,
            )
        )
    if problems:
        raise KnowledgeError("\n".join(problems))
    return KnowledgeGraph(
        cohort=cohort,
        inputs=inputs,
        nodes=dict(sorted(nodes.items())),
        links=tuple(links),
        unlabelled=unlabelled,
    )


def save_knowledge_graph(graph: KnowledgeGraph, path: Path) -> None:
    data = {"digest": graph.digest(), "graph": graph.model_dump(mode="json")}
    write_atomic(path, (json.dumps(data, sort_keys=True, indent=1) + "\n").encode())


def load_knowledge_graph(path: Path, inputs: Mapping[str, str] | None = None) -> KnowledgeGraph:
    """A stored graph, after checking its digest and, given ``inputs``, that it was built from
    the current documents, lock and statements."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    graph = KnowledgeGraph.model_validate(raw["graph"])
    if graph.digest() != raw["digest"]:
        raise KnowledgeError(f"{path} does not match its digest; run make knowledge-graph")
    if inputs is not None:
        keys = inputs.keys() | graph.inputs.keys()
        changed = sorted(k for k in keys if graph.inputs.get(k) != inputs.get(k))
        if changed:
            raise KnowledgeError(
                f"{path} was built from other inputs ({', '.join(changed)}); "
                "run make knowledge-graph"
            )
    return graph


def knowledge_for(
    graph: KnowledgeGraph, concepts: Iterable[str], statements: Iterable[str] = ()
) -> dict[str, Any]:
    """What a page shows of the knowledge graph: the labels and guidelines joined to
    ``concepts`` by code, the sources that the cited ``statements`` quote, and every
    statement quoting any of these sources, so a source lists all that medgraph quotes."""
    concepts = set(concepts)
    cited = set(statements)
    links = [x for x in graph.links if x.kind != "quotes" and x.source in concepts]
    quotes = {x.source: x.target for x in graph.links if x.kind == "quotes"}
    sources = {x.target for x in links} | {quotes[s] for s in cited if s in quotes}
    shown = {s: t for s, t in quotes.items() if t in sources}
    return {
        "digest": graph.digest(),
        "nodes": {
            n: graph.nodes[n].model_dump(mode="json", exclude_defaults=True)
            for n in sorted(sources | shown.keys())
        },
        "links": [x.model_dump(mode="json", exclude_none=True) for x in links],
        "quotes": dict(sorted(shown.items())),
        "cited": sorted(cited & shown.keys()),
        "unlabelled": {c: r for c, r in sorted(graph.unlabelled.items()) if c in concepts},
    }
