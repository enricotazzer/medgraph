"""The cohort's concept map: every patient graph merged by concept.

The store holds one graph per patient, and patients share no nodes. The map merges them the
way the patient page groups records (:func:`medgraph.graph.view.concept_of`): one concept per
condition code, medication, procedure, observation code, encounter type, note type and
analyte. A patient's filed lab reports have no code, so all of them form one concept.

A link counts the patients whose own graph has at least one edge of that kind between records
of the two concepts. The map adds nothing that no patient graph contains: no co-occurrence, no
statistics, no edge between patients. A link rests on the basis of the edges it groups
(``LINK_BASIS``); in Synthea data, ``treated_by`` comes from reasons the simulator wrote.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from medgraph.graph.knowledge import KnowledgeGraph, knowledge_for
from medgraph.graph.schema import NodeData, PatientGraph, graph_info, node_data
from medgraph.graph.view import CONCEPT_LINKS, LINK_BASIS, concept_of

LAB_REPORTS = "lab_report"
LAB_REPORTS_LABEL = "Filed lab reports"


def cohort_concept(node: str, data: NodeData) -> str:
    """The concept a record belongs to on the cohort map."""
    if data.kind in ("lab_report", "report_row"):
        return LAB_REPORTS
    return concept_of(node, data)


def concept_kind(concept: str) -> str:
    """The kind a concept is shown as, named as on the patient page."""
    if concept.startswith("analyte:"):
        return "analyte"
    kind = concept.partition("|")[0]
    return "medication" if kind == "medication_request" else kind


def concept_map(graphs: Iterable[PatientGraph]) -> dict[str, Any]:
    """Concepts and links over all ``graphs``, as JSON-compatible data.

    Patients are listed once, sorted by ID; concepts and links refer to them by index. A
    concept's patients are those with at least one of its records; an analyte's own node,
    present when the monitoring table links it to a condition, is not a record. The result
    does not depend on the order of ``graphs``.
    """
    codes: dict[str, str | None] = {}
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    members: dict[str, set[str]] = defaultdict(set)
    records: Counter[str] = Counter()
    linked: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    edges: Counter[tuple[str, str, str]] = Counter()
    citations: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    patients: list[str] = []
    deceased = 0

    for graph in graphs:
        patient = graph_info(graph).patient
        patients.append(patient.id)
        deceased += patient.deceased is not None
        concept_for: dict[str, str] = {}
        for node in graph.nodes:
            data = node_data(graph, node)
            concept = concept_for[node] = cohort_concept(node, data)
            if data.kind == "analyte":
                labels[concept][data.label] += 1
                continue
            members[concept].add(patient.id)
            if data.kind == "lab_report":
                continue
            records[concept] += 1
            if concept != LAB_REPORTS and not concept.startswith("analyte:"):
                codes.setdefault(concept, data.code)
                labels[concept][data.label] += 1
        for u, v, kind, attrs in graph.edges(keys=True, data=True):
            if kind not in CONCEPT_LINKS:
                continue
            key = (concept_for[u], concept_for[v], kind)
            if key[0] == key[1]:
                continue
            linked[key].add(patient.id)
            edges[key] += 1
            if attrs.get("citation"):
                citations[key].add(attrs["citation"])

    order = sorted(patients)
    if len(set(order)) != len(order):
        raise ValueError("a patient appears in more than one graph")
    index = {p: i for i, p in enumerate(order)}

    def indices(group: set[str]) -> list[int]:
        return sorted(index[p] for p in group)

    def label(concept: str) -> str:
        if concept == LAB_REPORTS:
            return LAB_REPORTS_LABEL
        counts = labels[concept]
        if not counts:  # an analyte measured without its own node: not built that way
            return concept.removeprefix("analyte:")
        return min(counts, key=lambda text: (-counts[text], text))

    return {
        "patients": order,
        "deceased": deceased,
        "concepts": [
            {
                "id": concept,
                "kind": concept_kind(concept),
                "label": label(concept),
                "code": codes.get(concept),
                "patients": indices(members[concept]),
                "records": records[concept],
            }
            for concept in sorted(labels.keys() | members.keys())
        ],
        "links": [
            {
                "source": source,
                "target": target,
                "kind": kind,
                "basis": LINK_BASIS[kind],
                "citations": sorted(citations[source, target, kind]),
                "patients": indices(group),
                "edges": edges[source, target, kind],
            }
            for (source, target, kind), group in sorted(linked.items())
        ],
    }


def with_knowledge(model: dict[str, Any], knowledge: KnowledgeGraph) -> dict[str, Any]:
    """The map with its sources joined by code (:mod:`medgraph.graph.knowledge`): a drug's
    label and a condition's guideline become "source" concepts. A source counts the patients
    of the concepts linked to it, and each link the patients of its concept; a drug with no
    label says why."""
    concepts = {c["id"]: c for c in model["concepts"]}
    extra = knowledge_for(knowledge, concepts)
    members: dict[str, set[int]] = defaultdict(set)
    links = []
    for link in extra["links"]:
        patients = concepts[link["source"]]["patients"]
        members[link["target"]].update(patients)
        links.append(
            {
                "source": link["source"],
                "target": link["target"],
                "kind": link["kind"],
                "basis": link["basis"],
                "detail": link.get("detail"),
                "citations": [],
                "patients": patients,
                "edges": None,
            }
        )
    sources = [
        {
            "id": node,
            "kind": "source",
            "label": extra["nodes"][node]["title"],
            "code": None,
            "patients": sorted(members[node]),
            "records": 0,
        }
        for node in sorted(members)
    ]
    marked = [
        {**c, "no_label": extra["unlabelled"][c["id"]]} if c["id"] in extra["unlabelled"] else c
        for c in model["concepts"]
    ]
    return {
        **model,
        "concepts": sorted(marked + sources, key=lambda c: c["id"]),
        "links": model["links"] + links,
        "knowledge": {k: extra[k] for k in ("digest", "nodes", "quotes")},
    }
