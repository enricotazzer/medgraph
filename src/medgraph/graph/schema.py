"""The patient graph's schema: node and edge kinds, their attributes, and the kinds an edge joins.

One graph per patient, a ``networkx.MultiDiGraph`` whose edge keys are edge kinds. A node's
attributes are the JSON form of :class:`NodeData`, and an edge's of :class:`EdgeData`. Values
travel as decimal strings, digits included, so a graph saved to SQLite and loaded back is
identical. Node IDs are deterministic: ``<kind>:<source id>``.

Every node except an analyte concept carries the source records it came from. Every edge says
why it exists (``basis``): a reference in the data, a time order, a value comparison, or a
cited guideline passage.
"""

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, get_args

import networkx as nx
from pydantic import BaseModel, ConfigDict

from medgraph.records import Comparator, IngestIssue, Patient, Quantity, SourceRef, Timepoint

SCHEMA_VERSION = "graph-v1"

if TYPE_CHECKING:
    PatientGraph = nx.MultiDiGraph[str]
else:  # networkx graphs are generic only to type checkers
    PatientGraph = nx.MultiDiGraph

NodeKind = Literal[
    "encounter",
    "condition",
    "medication_request",
    "procedure",
    "observation",  # any observation outside the analyte registry (vitals, surveys, other labs)
    "lab_result",  # an observation of an in-scope analyte, normalized
    "lab_report",  # an uploaded lab report (text or PDF)
    "report_row",  # one result row read from a lab report
    "note",
    "analyte",  # concept: an in-scope analyte from the registry
]
EdgeKind = Literal[
    "occurred_during",  # record -> its encounter
    "measures",  # lab result or report row -> analyte
    "part_of",  # report row -> its lab report
    "treated_by",  # condition -> medication request or procedure (FHIR reasonReference)
    "monitored_by",  # condition -> analyte (cited guideline, rules/monitoring.py)
    "precedes",  # consecutive encounters; consecutive values of one analyte's series
    "same_measurement",  # report row -> the FHIR lab result it repeats
]
NODE_KINDS: tuple[NodeKind, ...] = get_args(NodeKind)
EDGE_KINDS: tuple[EdgeKind, ...] = get_args(EdgeKind)
MEASUREMENTS: tuple[NodeKind, ...] = ("lab_result", "report_row")

_RECORDS: tuple[NodeKind, ...] = (
    "condition",
    "medication_request",
    "procedure",
    "observation",
    "lab_result",
    "note",
)
EDGE_ENDPOINTS: dict[EdgeKind, frozenset[tuple[NodeKind, NodeKind]]] = {
    "occurred_during": frozenset((kind, "encounter") for kind in _RECORDS),
    "measures": frozenset({("lab_result", "analyte"), ("report_row", "analyte")}),
    "part_of": frozenset({("report_row", "lab_report")}),
    "treated_by": frozenset({("condition", "medication_request"), ("condition", "procedure")}),
    "monitored_by": frozenset({("condition", "analyte")}),
    "precedes": frozenset(
        [("encounter", "encounter"), *((a, b) for a in MEASUREMENTS for b in MEASUREMENTS)]
    ),
    "same_measurement": frozenset({("report_row", "lab_result")}),
}


class NodeData(BaseModel):
    """One node's attributes. Fields that don't apply to a kind stay ``None``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: NodeKind
    label: str
    code: str | None = None  # "system|code" of the primary coding
    category: str | None = None  # encounter class; observation category
    start: Timepoint | None = None  # when it happened or began
    end: Timepoint | None = None  # abatement, end of a procedure or encounter
    status: str | None = None  # clinical status; lab or row status
    # Measurements only: whether a rule may use the value (status "ok" and dated).
    usable: bool | None = None
    analyte: str | None = None  # registry key
    method: str | None = None  # LOINC method, or the eGFR equation a report names
    value: Decimal | None = None  # canonical unit for measurements; as recorded otherwise
    unit: str | None = None
    comparator: Comparator | None = None
    original: Quantity | None = None  # measurements: the value as recorded or printed
    text: str | None = None  # note text; qualitative value; a report row as printed
    detail: str | None = None  # why a value is not usable; report issues
    sources: tuple[SourceRef, ...] = ()

    def attrs(self) -> dict[str, Any]:
        """JSON-compatible attributes for networkx and the store."""
        return self.model_dump(mode="json", exclude_none=True)


class EdgeData(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: EdgeKind
    basis: str  # what the edge rests on
    citation: str | None = None  # guideline passage, for monitored_by
    detail: str | None = None  # quote, or the values compared

    def attrs(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class GraphInfo(BaseModel):
    """Graph-level attributes: whose graph it is, what it was built from, what went wrong."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SCHEMA_VERSION
    patient: Patient
    sources: tuple[str, ...]  # the FHIR bundle's hash, then each attached report's
    ingest_issues: tuple[IngestIssue, ...] = ()
    build_issues: tuple[str, ...] = ()  # e.g. a reason naming a condition that failed to parse

    def attrs(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


def graph_info(graph: PatientGraph) -> GraphInfo:
    return GraphInfo.model_validate(graph.graph)


def node_id(kind: NodeKind, source_id: str) -> str:
    return f"{kind}:{source_id}"


def node_data(graph: PatientGraph, node: str) -> NodeData:
    return NodeData.model_validate(graph.nodes[node])


def edge_data(graph: PatientGraph, u: str, v: str, kind: EdgeKind) -> EdgeData:
    return EdgeData.model_validate(graph.edges[u, v, kind])


def time_key(tp: Timepoint | None) -> tuple[str, str]:
    """Sort key: calendar date, then instant in UTC; undated sorts first."""
    if tp is None:
        return ("", "")
    instant = tp.instant.astimezone(dt.UTC).isoformat() if tp.instant else ""
    return (tp.date.isoformat(), instant)


def display_time(tp: Timepoint) -> float:
    """Seconds since the epoch, for drawing only. A date without a time is drawn at noon UTC."""
    if tp.instant is not None:
        return tp.instant.timestamp()
    return dt.datetime.combine(tp.date, dt.time(12), tzinfo=dt.UTC).timestamp()
