"""The evidence pack: everything an explanation of one flag may say, each item with an ID.

An explanation cites these IDs sentence by sentence, and the verifier
(:mod:`medgraph.agent.verify`) checks every number and date a sentence writes against the
items it cites. The pack is deterministic for a given flag, knowledge graph and index, and the
same for every language and register, so the four explanations of a flag rest on the same
material.

- ``F``: the flag's title and statement (from the rules; deterministic);
- ``E1``...: its evidence: date, record, value, and how the rule used it;
- ``L1``...: its limitations;
- ``S1``...: the statements its sources quote, as checked against the stored text
  (:mod:`medgraph.rules.sources`);
- ``P1``...: passages retrieved from the knowledge index. The query is English text built from
  the flag, whatever the language of the explanation. The search is limited to the guidelines
  and to the labels of drugs in the flag's own evidence: labels of the patient's other drugs
  would invite comments on medicines the flag is not about. Passages that hold an ``S``
  statement are left out; they would repeat it. So is a passage that nearly repeats a better
  ranked one (``NEAR_DUPLICATE`` of their words shared): a guideline's executive summary often
  repeats a table from its body.
"""

import datetime as dt
import hashlib
import json
import re
from collections.abc import Collection, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from medgraph.graph.knowledge import (
    KnowledgeGraph,
    concept_id,
    statement_of_source,
)
from medgraph.graph.schema import PatientGraph, node_data
from medgraph.rag.chunks import Chunk
from medgraph.rag.index import KnowledgeIndex
from medgraph.rules.model import Flag
from medgraph.rules.sources import SOURCES

ItemKind = Literal["flag", "evidence", "limitation", "statement", "passage"]
PASSAGES = 4
NEAR_DUPLICATE = 0.8  # Jaccard similarity of two passages' sets of words
# Part of the cache key of a stored pack (scripts/explain_flags.py): bump it when the code that
# builds packs changes. v2: near duplicates left out; a passage names its document's citation.
# v3: its title and citation (v2 named the citation alone, which drops "KDIGO").
PACK_VERSION = "pack-v3"

_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_WORD = re.compile(r"\w+")
_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*")


class PackItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # "F", "E1", "L1", "S1", "P1"
    kind: ItemKind
    text: str  # what the model reads, and what a sentence citing it is checked against
    date: dt.date | None = None  # evidence: the date of the value
    node: str | None = None  # evidence: the graph node
    ref: str | None = None  # statement: the source ID; passage: the passage ID
    where: str | None = None  # statement or passage: document and place in it


class EvidencePack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    patient: str
    rule: str
    flag_hash: str
    query: str
    searched: tuple[str, ...]  # index sources the passages were drawn from
    items: tuple[PackItem, ...]

    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

    def by_id(self) -> dict[str, PackItem]:
        return {item.id: item for item in self.items}


def flag_hash(flag: Flag) -> str:
    """Identifies a flag's content: an explanation is shown only for the flag it was made for."""
    text = json.dumps(flag.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def query_for(flag: Flag) -> str:
    """English search text from the flag: its title and statement, without dates and numbers,
    which would match passages only by coincidence."""
    text = _NUMBER.sub(" ", _DATE.sub(" ", f"{flag.title}. {flag.statement}"))
    return " ".join(text.split())


def searched_sources(flag: Flag, graph: PatientGraph, knowledge: KnowledgeGraph) -> tuple[str, ...]:
    """Index sources an explanation may draw on: every guideline document, and the label of
    each drug in the flag's evidence, as the cohort's lock chose it."""
    documents = set(document_citations(knowledge))
    labelled = {x.source: x.target for x in knowledge.links if x.kind == "labelled_by"}
    labels = set()
    for evidence in flag.evidence:
        if evidence.node not in graph:
            continue
        data = node_data(graph, evidence.node)
        if data.kind != "medication_request" or not data.code:
            continue
        system, _, code = data.code.rpartition("|")
        label = labelled.get(concept_id("medication_request", system, code))
        if label:
            labels.add(label.partition("@")[0])
    return tuple(sorted(documents | labels))


def quoted_passages(flag: Flag, knowledge: KnowledgeGraph) -> set[str]:
    """Passages holding the statements the flag's sources quote."""
    return {
        passage
        for source in flag.sources
        if (node := knowledge.nodes.get(statement_of_source(source))) is not None
        for passage in node.passages
    }


def retrieve(
    index: KnowledgeIndex,
    query: str,
    vector: Sequence[float] | None,
    model: str,
    *,
    sources: Collection[str],
    exclude: Collection[str] = (),
    limit: int = PASSAGES,
) -> list[Chunk]:
    """The best passages for ``query`` (hybrid ranking) among ``sources``, ``exclude`` and near
    duplicates of a better ranked passage left out."""
    hits = index.hybrid(query, vector, model, limit=3 * limit + len(exclude), sources=sources)
    chosen: list[tuple[Chunk, set[str]]] = []
    for hit in hits:
        if hit.chunk in exclude:
            continue
        chunk = index.chunk(hit.chunk)
        words = set(_WORD.findall(chunk.text.lower()))
        if any(similarity(words, other) >= NEAR_DUPLICATE for _, other in chosen):
            continue
        chosen.append((chunk, words))
        if len(chosen) == limit:
            break
    return [chunk for chunk, _ in chosen]


def similarity(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a or b else 1.0


def document_citations(knowledge: KnowledgeGraph) -> dict[str, str]:
    """Each guideline document's title and citation, by index source. A passage is named by
    them, so a sentence saying "the WHO guideline" can be checked against the passage it cites:
    the WHO's title does not name it, its citation does; KDIGO's citations do not name KDIGO,
    its titles do."""
    return {
        node.removeprefix("document:"): f"{data.title} ({data.citation})"
        for node, data in knowledge.nodes.items()
        if data.kind == "document"
    }


def build_pack(
    patient: str,
    flag: Flag,
    passages: Sequence[Chunk],
    *,
    query: str = "",
    searched: Sequence[str] = (),
    citations: Mapping[str, str] | None = None,
) -> EvidencePack:
    items = [PackItem(id="F", kind="flag", text=f"{flag.title}. {flag.statement}")]
    for i, e in enumerate(flag.evidence, 1):
        text = f"{e.date.isoformat()}: {e.label}"
        if e.value:
            text += f": {e.value}"
        if e.note:
            text += f" ({e.note})"
        items.append(PackItem(id=f"E{i}", kind="evidence", text=text, date=e.date, node=e.node))
    for i, limitation in enumerate(flag.limitations, 1):
        items.append(PackItem(id=f"L{i}", kind="limitation", text=limitation))
    for i, source_id in enumerate(flag.sources, 1):
        source = SOURCES[source_id]
        text = f'{source.citation}: "{source.quote}"'
        if source.table:
            text += f" Table, transcribed row by row: {source.table}"
        items.append(
            PackItem(
                id=f"S{i}",
                kind="statement",
                text=text,
                ref=source_id,
                where=source.citation,
            )
        )
    for i, chunk in enumerate(passages, 1):
        named = (citations or {}).get(chunk.source, chunk.title)
        items.append(
            PackItem(
                id=f"P{i}",
                kind="passage",
                text=f"{named}, {chunk.where}: {chunk.text}",
                ref=chunk.id,
                where=f"{chunk.title}, {chunk.where}",
            )
        )
    return EvidencePack(
        patient=patient,
        rule=flag.rule,
        flag_hash=flag_hash(flag),
        query=query,
        searched=tuple(searched),
        items=tuple(items),
    )
