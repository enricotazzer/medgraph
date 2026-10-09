"""One self-contained HTML page per patient: the graph at concept level, and the timeline.

The page inlines its data and two vendored libraries (Cytoscape.js for the graph, uPlot for the
lab series). Their hashes are pinned below and checked before rendering. A Content Security
Policy forbids every network request, so the page works offline and can't send the record
anywhere. Record text only ever reaches the page as data: the script writes it with
``textContent``, never as HTML.

The graph shows **concepts**: one node per condition code, medication, procedure, observation
code, analyte or lab report, grouped from the individual records, which open in the side
panel. A median Synthea patient has about 340 records and the largest about 18,000, too many to
draw one by one.
"""

import hashlib
import json
import re
from collections import defaultdict
from importlib.resources import files
from typing import Any

from medgraph.graph.schema import (
    EdgeKind,
    NodeData,
    PatientGraph,
    display_time,
    graph_info,
    node_data,
    time_key,
)
from medgraph.graph.timeline import Timeline
from medgraph.rules.sources import SOURCES

STATIC = files("medgraph.graph") / "static"
VENDOR: dict[str, str] = {
    "cytoscape.min.js": "5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91",
    "uPlot.iife.min.js": "19c8d4c6ad88929a79f4ae49d6f7161566dfd0ba3d15cc495e974f787eb78f1f",
    "uPlot.min.css": "df630c6a8d6f8eeaff264b50f73ce5b114f646ffd9a0bb74f049b0a00135fa04",
}
CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; base-uri 'none'; form-action 'none'"
)
# Record-level edges drawn between concepts. The rest (precedes, part_of, same_measurement and
# a lab result's measures edge) join records inside one concept.
CONCEPT_LINKS: tuple[EdgeKind, ...] = ("treated_by", "monitored_by", "measures", "occurred_during")
# What a concept link rests on, true of every record edge it groups; each record edge keeps
# its own, more specific basis.
LINK_BASIS: dict[str, str] = {
    "treated_by": "reasonReference in the record",
    "monitored_by": "cited guideline (rules/monitoring.py)",
    "measures": "the analyte name table, for report rows",
    "occurred_during": "encounter reference in the record",
}


_MARKER = re.compile(r"/\*(?:CSP|VENDOR_CSS|VIEWER_CSS|VENDOR_JS|VIEWER_JS|DATA)\*/")


class VendorFileError(RuntimeError):
    """A vendored library differs from its pinned hash."""


def vendor_file(name: str) -> str:
    data = (STATIC / "vendor" / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != VENDOR[name]:
        raise VendorFileError(f"{name} does not match its pinned SHA-256; see vendor/README.md")
    return data.decode("utf-8")


def concept_of(node: str, data: NodeData) -> str:
    """The concept a record belongs to in the view."""
    if data.kind == "lab_result" and data.analyte:
        return f"analyte:{data.analyte}"
    if data.kind in ("analyte", "lab_report"):
        return node
    return f"{data.kind}|{data.code or data.label}"


def view_model(graph: PatientGraph, timeline: Timeline) -> dict[str, Any]:
    """Everything the page draws, as JSON-compatible data."""
    info = graph_info(graph)
    data = {node: node_data(graph, node) for node in graph.nodes}
    rows_of: dict[str, str] = {}  # report row -> its report
    for u, v, kind in graph.edges(keys=True):
        if kind == "part_of":
            rows_of[u] = v

    records: dict[str, dict[str, Any]] = {}
    members: dict[str, list[str]] = defaultdict(list)
    for node, d in data.items():
        record = d.attrs()
        record["t0"] = display_time(d.start) if d.start else None
        record["t1"] = display_time(d.end) if d.end else None
        records[node] = record
        concept = rows_of.get(node) or concept_of(node, d)
        if d.kind != "analyte" and d.kind != "lab_report":
            members[concept].append(node)
        elif concept not in members:
            members[concept] = []

    concepts = []
    for concept, nodes in members.items():
        head = data.get(concept) or data[nodes[0]]
        kind = "medication" if head.kind == "medication_request" else head.kind
        ordered = sorted(nodes, key=lambda n: (time_key(data[n].start), n))
        dated = [tp for n in ordered if (tp := data[n].start) is not None]
        statuses = {data[n].status for n in ordered}
        concepts.append(
            {
                "id": concept,
                "kind": kind,
                "label": head.label,
                "code": head.code,
                "count": len(nodes),
                "first": dated[0].date.isoformat() if dated else None,
                "last": dated[-1].date.isoformat() if dated else None,
                "active": "active" in statuses,
                "records": ordered,
            }
        )

    concept_for = {n: c for c, nodes in members.items() for n in nodes}
    concept_for.update({c: c for c in members})
    links: dict[tuple[str, str, str], dict[str, Any]] = {}
    for u, v, kind, attrs in graph.edges(keys=True, data=True):
        if kind not in CONCEPT_LINKS:
            continue
        cu, cv = concept_for[u], concept_for[v]
        if cu == cv:
            continue
        link = links.setdefault(
            (cu, cv, kind),
            {
                "source": cu,
                "target": cv,
                "kind": kind,
                "count": 0,
                "basis": LINK_BASIS[kind],
                "citation": attrs.get("citation"),
                "detail": attrs.get("detail"),
            },
        )
        link["count"] += 1

    return {
        "patient": info.patient.model_dump(mode="json", exclude_none=True),
        "sources": list(info.sources),
        "issues": [f"{i.code} ({i.severity}): {i.message}" for i in info.ingest_issues]
        + list(info.build_issues),
        "concepts": concepts,
        "links": list(links.values()),
        # Record-level edges for the side panel; time order is shown by the timeline instead.
        "edges": [
            [u, v, kind, attrs.get("basis"), attrs.get("citation"), attrs.get("detail")]
            for u, v, kind, attrs in graph.edges(keys=True, data=True)
            if kind != "precedes"
        ],
        "records": records,
        "series": [
            {
                "analyte": s.analyte,
                "label": s.label,
                "unit": s.unit,
                "points": [
                    {
                        "t": display_time(p.time),
                        "v": float(p.value),
                        "value": str(p.value),
                        "cmp": p.comparator,
                        "node": p.node,
                        "rows": list(p.corroborated_by),
                    }
                    for p in s.points
                ],
            }
            for s in timeline.series
        ],
        "events": [
            {
                "node": e.node,
                "concept": concept_for[e.node],
                "t0": display_time(e.start),
                "t1": display_time(e.end) if e.end else None,
            }
            for e in timeline.events
        ],
        "notes": [n.model_dump(mode="json") for n in timeline.notes],
    }


def _script_json(value: Any) -> str:
    """JSON safe to place inside a ``<script>`` element."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return text.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def rules_model(graph: PatientGraph, rules: dict[str, Any]) -> dict[str, Any]:
    """Rule results for the page: flags, notes, assessments, the computed eGFR series, and the
    text of every source they cite. ``rules`` is ``PatientRules`` as JSON (``medgraph.rules``)."""
    cited = {
        s
        for kind in ("assessments", "flags", "notes")
        for item in rules.get(kind, ())
        for s in item.get("sources", ())
    }
    computed = rules.get("computed_egfr", ())
    points = []
    for c in computed:
        start = node_data(graph, c["node"]).start
        assert start is not None
        points.append(
            {
                "t": display_time(start),
                "v": float(c["value"]),
                "value": str(c["whole"]),
                "cmp": None,
                "node": c["node"],
                "rows": [],
                "detail": f"from creatinine {c['creatinine']} mg/dL, age {c['age']}, {c['sex']}",
            }
        )
    return {
        "rules": rules,
        "sources": {
            k: {"citation": SOURCES[k].citation, "quote": SOURCES[k].quote}
            for k in sorted(cited)
            if k in SOURCES
        },
        "computed_series": {
            "analyte": "egfr-computed",
            "label": "eGFR, CKD-EPI 2021 (computed by medgraph)",
            "unit": "mL/min/{1.73_m2}",
            "computed": True,
            "points": points,
        }
        if points
        else None,
    }


def render_html(
    graph: PatientGraph, timeline: Timeline, banner: str, rules: dict[str, Any] | None = None
) -> str:
    """The patient's page. ``banner`` says where the data came from (e.g. synthetic);
    ``rules`` adds follow-up flags and the computed eGFR series."""
    model = view_model(graph, timeline)
    model["banner"] = banner
    if rules is not None:
        extra = rules_model(graph, rules)
        model["rules"] = extra["rules"]
        model["rule_sources"] = extra["sources"]
        if extra["computed_series"]:
            model["series"].append(extra["computed_series"])
    template = (STATIC / "viewer.html").read_text(encoding="utf-8")
    parts = {
        "/*CSP*/": CSP,
        "/*VENDOR_CSS*/": vendor_file("uPlot.min.css"),
        "/*VIEWER_CSS*/": (STATIC / "viewer.css").read_text(encoding="utf-8"),
        "/*VENDOR_JS*/": vendor_file("cytoscape.min.js") + "\n" + vendor_file("uPlot.iife.min.js"),
        "/*VIEWER_JS*/": (STATIC / "viewer.js").read_text(encoding="utf-8"),
        "/*DATA*/": _script_json(model),
    }
    for marker in parts:
        if template.count(marker) != 1:
            raise ValueError(f"template must contain {marker} exactly once")
    # One pass, so text inserted for one marker is never searched for the others.
    return _MARKER.sub(lambda m: parts[m[0]], template)
