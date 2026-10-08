"""Invariants every patient graph must satisfy. Each function returns the violations it finds.

:func:`check_graph` needs only the graph, so it applies to a graph loaded from the store as
well as to a fresh one. :func:`check_coverage` also needs the record the graph was built from,
and checks that nothing was dropped on the way.
"""

from collections import Counter
from itertools import pairwise

from pydantic import ValidationError

from medgraph.graph.build import AttachedReport, printed_value_match
from medgraph.graph.schema import (
    EDGE_ENDPOINTS,
    MEASUREMENTS,
    EdgeData,
    NodeData,
    PatientGraph,
    graph_info,
    node_id,
)
from medgraph.graph.timeline import series_members
from medgraph.normalize.labs import normalize_labs
from medgraph.records import PatientRecord


def check_graph(graph: PatientGraph) -> list[str]:
    problems: list[str] = []
    try:
        graph_info(graph)
    except ValidationError as exc:
        problems.append(f"graph attributes invalid: {exc.error_count()} error(s)")
    data: dict[str, NodeData] = {}
    for node, attrs in graph.nodes(data=True):
        try:
            data[node] = NodeData.model_validate(attrs)
        except ValidationError:
            problems.append(f"{node}: attributes invalid (e.g. created by an edge)")
            continue
        d = data[node]
        if not node.startswith(f"{d.kind}:"):
            problems.append(f"{node}: ID does not start with its kind {d.kind}")
        if d.kind != "analyte" and not d.sources:
            problems.append(f"{node}: no source")
        if d.kind in MEASUREMENTS and d.usable != (d.status == "ok" and d.start is not None):
            problems.append(f"{node}: usable={d.usable} with status {d.status}")
        if d.kind == "report_row" and d.usable and d.analyte is None:
            problems.append(f"{node}: usable row without an analyte")
    for u, v, kind, attrs in graph.edges(keys=True, data=True):
        try:
            edge = EdgeData.model_validate(attrs)
        except ValidationError:
            problems.append(f"{u} -> {v}: edge attributes invalid")
            continue
        if edge.kind != kind:
            problems.append(f"{u} -> {v}: key {kind} but kind {edge.kind}")
        if u in data and v in data and (data[u].kind, data[v].kind) not in EDGE_ENDPOINTS[kind]:
            problems.append(f"{u} -> {v}: {kind} cannot join {data[u].kind} to {data[v].kind}")
        if kind == "monitored_by" and not (edge.citation and edge.detail):
            problems.append(f"{u} -> {v}: monitored_by without a citation and quote")
        if (
            kind == "same_measurement"
            and u in data
            and v in data
            and printed_value_match(data[u], data[v]) is None
        ):
            problems.append(f"{u} -> {v}: same_measurement but the values don't agree")
    if problems:
        return problems  # the series checks below assume valid attributes
    return _series_problems(graph, data)


def _series_problems(graph: PatientGraph, data: dict[str, NodeData]) -> list[str]:
    problems: list[str] = []
    members = series_members(graph)
    points = Counter(node for nodes in members.values() for node in nodes)
    problems += [f"{node}: in {n} series" for node, n in points.items() if n > 1]
    for node, d in data.items():
        if d.kind not in MEASUREMENTS:
            continue
        if not d.usable:
            if node in points:
                problems.append(f"{node}: not usable but in a series")
            continue
        if d.kind == "lab_result" and node not in points:
            problems.append(f"{node}: usable lab result missing from its series")
        if d.kind == "report_row" and node not in points:
            repeated = [
                v for _, v, kind in graph.out_edges(node, keys=True) if kind == "same_measurement"
            ]
            if not any(v in points for v in repeated):
                problems.append(f"{node}: usable report row neither in a series nor a repeat")
    for analyte, nodes in members.items():
        if any(data[n].analyte != analyte for n in nodes):
            problems.append(f"series {analyte}: holds another analyte's value")
    expected = {pair for nodes in members.values() for pair in pairwise(nodes)}
    actual = {
        (u, v)
        for u, v, kind in graph.edges(keys=True)
        if kind == "precedes" and data[u].kind in MEASUREMENTS
    }
    if expected != actual:
        problems.append(
            f"precedes edges between values: {len(actual - expected)} unexpected, "
            f"{len(expected - actual)} missing"
        )
    return problems


def check_coverage(
    graph: PatientGraph, record: PatientRecord, reports: tuple[AttachedReport, ...] = ()
) -> list[str]:
    """Every record and report row of the source has its node."""
    labs = {lab.observation_id for lab in normalize_labs(record)}
    expected = [
        *(node_id("encounter", r.id) for r in record.encounters),
        *(node_id("condition", r.id) for r in record.conditions),
        *(node_id("medication_request", r.id) for r in record.medication_requests),
        *(node_id("procedure", r.id) for r in record.procedures),
        *(
            node_id("lab_result" if o.id in labs else "observation", o.id)
            for o in record.observations
        ),
        *(node_id("note", n.id) for n in record.notes),
        *(node_id("lab_report", r.result.source) for r in reports),
        *(
            node_id("report_row", f"{r.result.source}:{row.index}")
            for r in reports
            for row in r.result.rows
        ),
    ]
    missing = [n for n in expected if n not in graph]
    return [f"{len(missing)} record(s) without a node, e.g. {missing[0]}"] if missing else []
