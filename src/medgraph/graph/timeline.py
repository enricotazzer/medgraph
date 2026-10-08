"""A patient's timeline, read from their graph: lab series, dated events and data notes.

A series holds only usable values (status ``ok`` and dated), one point per measurement: a
report row that repeats a recorded FHIR result is a corroborating source of that point, not a
second point. Everything else stays visible as a data note instead of being dropped: values
set aside and why, reports without a collection date, rows the extraction checks rejected.
Notes state facts about the data only; whether a gap matters clinically is for the rules.
"""

from collections import Counter, defaultdict
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from medgraph.graph.schema import (
    MEASUREMENTS,
    NodeData,
    NodeKind,
    PatientGraph,
    node_data,
    time_key,
)
from medgraph.normalize.analytes import BY_KEY
from medgraph.records import Comparator, SourceRef, Timepoint

EVENT_KINDS: tuple[NodeKind, ...] = ("condition", "medication_request", "procedure", "encounter")
REJECTED = ("ungrounded", "misplaced")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SeriesPoint(_Model):
    node: str
    time: Timepoint
    value: Decimal  # canonical unit
    comparator: Comparator | None = None
    method: str | None = None
    corroborated_by: tuple[str, ...] = ()  # report rows that repeat this value
    sources: tuple[SourceRef, ...]  # the point's own, then its corroborating rows'


class Series(_Model):
    analyte: str
    label: str
    unit: str
    points: tuple[SeriesPoint, ...]


class Event(_Model):
    node: str
    kind: NodeKind
    label: str
    code: str | None = None
    start: Timepoint
    end: Timepoint | None = None
    status: str | None = None


class DataNote(_Model):
    topic: str  # an analyte key, or a lab report's node
    text: str
    nodes: tuple[str, ...] = ()


class Timeline(_Model):
    series: tuple[Series, ...]
    events: tuple[Event, ...]
    notes: tuple[DataNote, ...]


def _sort_key(item: tuple[str, NodeData]) -> tuple[tuple[str, str], str]:
    return time_key(item[1].start), item[0]


def series_members(graph: PatientGraph) -> dict[str, list[str]]:
    """Per analyte, the nodes that are points of its series, in time order.

    The one definition of a series point, shared by the timeline and the ``precedes`` edges:
    a usable measurement that does not repeat another one (``same_measurement``).
    """
    repeats = {u for u, _, kind in graph.edges(keys=True) if kind == "same_measurement"}
    members: dict[str, list[tuple[str, NodeData]]] = defaultdict(list)
    for node in graph.nodes:
        data = node_data(graph, node)
        if data.kind in MEASUREMENTS and data.usable and node not in repeats:
            assert data.analyte is not None
            members[data.analyte].append((node, data))
    return {
        analyte: [node for node, _ in sorted(items, key=_sort_key)]
        for analyte, items in sorted(members.items())
    }


def build_timeline(graph: PatientGraph) -> Timeline:
    return Timeline(series=_series(graph), events=_events(graph), notes=_notes(graph))


def _series(graph: PatientGraph) -> tuple[Series, ...]:
    series = []
    for analyte, nodes in series_members(graph).items():
        points = []
        for node in nodes:
            data = node_data(graph, node)
            assert data.start is not None
            assert data.value is not None
            rows = sorted(
                u for u, _, kind in graph.in_edges(node, keys=True) if kind == "same_measurement"
            )
            points.append(
                SeriesPoint(
                    node=node,
                    time=data.start,
                    value=data.value,
                    comparator=data.comparator,
                    method=data.method,
                    corroborated_by=tuple(rows),
                    sources=data.sources
                    + tuple(s for r in rows for s in node_data(graph, r).sources),
                )
            )
        registry = BY_KEY[analyte]
        series.append(
            Series(
                analyte=analyte,
                label=registry.name,
                unit=registry.canonical_unit,
                points=tuple(points),
            )
        )
    return tuple(series)


def _events(graph: PatientGraph) -> tuple[Event, ...]:
    events = []
    for node in graph.nodes:
        data = node_data(graph, node)
        if data.kind in EVENT_KINDS and data.start is not None:
            events.append(
                Event(
                    node=node,
                    kind=data.kind,
                    label=data.label,
                    code=data.code,
                    start=data.start,
                    end=data.end,
                    status=data.status,
                )
            )
    return tuple(sorted(events, key=lambda e: (time_key(e.start), e.node)))


def _date(tp: Timepoint | None) -> str:
    return tp.date.isoformat() if tp else "undated"


def _counts(counter: Counter[str]) -> str:
    return ", ".join(f"{key} {n}" for key, n in sorted(counter.items()))


def _notes(graph: PatientGraph) -> tuple[DataNote, ...]:
    notes: list[DataNote] = []
    members = series_members(graph)
    for analyte, nodes in members.items():
        if len(nodes) == 1:
            only = node_data(graph, nodes[0])
            notes.append(
                DataNote(
                    topic=analyte,
                    text=f"{BY_KEY[analyte].name}: only one usable value ({_date(only.start)}).",
                    nodes=tuple(nodes),
                )
            )

    # Measurements that are not series points, by analyte and reason.
    set_aside: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for node in graph.nodes:
        data = node_data(graph, node)
        if data.kind not in MEASUREMENTS or data.usable or data.analyte is None:
            continue
        reason = data.status if data.status != "ok" else "no date"
        set_aside[data.analyte][reason or "unknown"].append(node)
    for analyte in sorted(set_aside):
        reasons = set_aside[analyte]
        name = BY_KEY[analyte].name
        qualitative = reasons.pop("non_numeric", [])
        if qualitative:
            notes.append(
                DataNote(
                    topic=analyte,
                    text=f"{name}: {len(qualitative)} qualitative result(s), listed but not "
                    "plotted.",
                    nodes=tuple(qualitative),
                )
            )
        if reasons:
            counts = Counter({reason: len(nodes) for reason, nodes in reasons.items()})
            notes.append(
                DataNote(
                    topic=analyte,
                    text=f"{name}: {counts.total()} value(s) set aside ({_counts(counts)}); "
                    "kept for review, never used by rules.",
                    nodes=tuple(n for reason in sorted(reasons) for n in reasons[reason]),
                )
            )

    for report in graph.nodes:
        data = node_data(graph, report)
        if data.kind != "lab_report":
            continue
        rows = sorted(u for u, _, kind in graph.in_edges(report, keys=True) if kind == "part_of")
        row_data = {r: node_data(graph, r) for r in rows}
        if data.start is None:
            notes.append(
                DataNote(
                    topic=report,
                    text=f"{data.label}: no collection date ({data.detail or 'not read'}); its "
                    f"{len(rows)} rows are off the timeline until a person confirms the date.",
                    nodes=(report,),
                )
            )
        rejected = [r for r in rows if row_data[r].status in REJECTED]
        if rejected:
            counts = Counter(str(row_data[r].status) for r in rejected)
            notes.append(
                DataNote(
                    topic=report,
                    text=f"{data.label}: {len(rejected)} row(s) rejected by the extraction "
                    f"checks ({_counts(counts)}).",
                    nodes=tuple(rejected),
                )
            )
        unmapped = [r for r in rows if row_data[r].status == "unmapped"]
        if unmapped:
            notes.append(
                DataNote(
                    topic=report,
                    text=f"{data.label}: {len(unmapped)} row(s) with a test name not in the "
                    "analyte name table (out of scope, or a spelling the table lacks).",
                    nodes=tuple(unmapped),
                )
            )
        usable = [r for r in rows if row_data[r].usable]
        repeats = [
            r
            for r in usable
            if any(k == "same_measurement" for *_, k in graph.out_edges(r, keys=True))
        ]
        if repeats:
            notes.append(
                DataNote(
                    topic=report,
                    text=f"{data.label}: {len(repeats)} of {len(usable)} usable row(s) repeat a "
                    "recorded result (same analyte, date and printed value) and are counted "
                    "once.",
                    nodes=tuple(repeats),
                )
            )
    return tuple(notes)
