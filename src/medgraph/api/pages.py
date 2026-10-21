"""What each page shows, for the scripts (``make view``, ``make concept-map``) and the local app.

The patient page and the concept map are built here from the stores, so a page the app serves
is the page the scripts write. The app's own pages (the cohorts, a cohort's patients, a concept
map still being built) are plain HTML with no script; every value in them is escaped.
"""

import datetime as dt
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from html import escape
from typing import Any

from medgraph.agent.cache import explanations_for
from medgraph.cohort import SYNTHETIC_BANNER, current_knowledge
from medgraph.graph.concepts import concept_map, with_knowledge
from medgraph.graph.schema import PatientGraph
from medgraph.graph.store import GraphStore
from medgraph.graph.timeline import build_timeline
from medgraph.graph.view import page_model
from medgraph.rules.flags import evaluate
from medgraph.rules.model import RULESET
from medgraph.rules.monitoring import MONITORING, SNOMED
from medgraph.settings import Settings


def patient_model(
    settings: Settings, cohort: str, graph: PatientGraph, as_of: dt.date
) -> tuple[dict[str, Any], str | None]:
    """The data of the patient's page: the graph and timeline, the flags as of ``as_of``, the
    sources joined by code if the cohort's knowledge graph is current, and the cached
    explanations of unchanged flags. Also returns why sources are left out, if they are."""
    rules = evaluate(graph, as_of)
    knowledge, why = current_knowledge(settings, cohort)
    patient = str(graph.graph["patient"]["id"])
    model = page_model(
        graph,
        build_timeline(graph),
        SYNTHETIC_BANNER,
        rules.model_dump(mode="json"),
        knowledge=knowledge,
        explanations=explanations_for(settings, cohort, patient, rules),
    )
    return model, why


def concept_model(
    settings: Settings, cohort: str, graphs: Iterable[PatientGraph]
) -> tuple[dict[str, Any], str | None]:
    """The cohort's concept map, joined to its knowledge graph if current; and why not."""
    model = concept_map(graphs)
    knowledge, why = current_knowledge(settings, cohort)
    return (model if knowledge is None else with_knowledge(model, knowledge)), why


@dataclass(frozen=True)
class PatientRow:
    patient: str
    records: int
    reports: int
    flags: int | None  # stored by make flags; None if it has not run for this ruleset
    conditions: tuple[str, ...]  # conditions in the monitoring table

    @property
    def worth_a_look(self) -> bool:
        return bool(self.conditions or self.reports)


def patient_rows(store: GraphStore) -> list[PatientRow]:
    """Every patient in the store, those worth a look first: a condition in the monitoring
    table and a filed lab report, then more such conditions, then smaller graphs."""
    conditions: dict[str, set[str]] = defaultdict(set)
    for patient, _, attrs in store.nodes_of_kind("condition"):
        code = str(attrs.get("code", "")).removeprefix(f"{SNOMED}|")
        if code in MONITORING:
            conditions[patient].add(MONITORING[code][0].removesuffix(" (disorder)"))
    reports = Counter(patient for patient, _, _ in store.nodes_of_kind("lab_report"))
    sizes: dict[str, int] = dict(
        store.db.execute("SELECT patient_id, n_nodes FROM graphs").fetchall()
    )
    flags = {
        patient: len(json.loads(payload)["flags"])
        for patient, payload in store.db.execute(
            "SELECT patient_id, payload FROM rules WHERE ruleset = ?", (RULESET,)
        )
    }
    rows = [
        PatientRow(p, n, reports[p], flags.get(p), tuple(sorted(conditions.get(p, ()))))
        for p, n in sizes.items()
    ]
    return sorted(
        rows,
        key=lambda r: (
            not r.worth_a_look,
            -(bool(r.reports) and bool(r.conditions)),
            -len(r.conditions),
            r.records,
            r.patient,
        ),
    )


# --- the app's own pages ------------------------------------------------------------------

_STYLE = """
:root {
  color-scheme: light; --bg: #f6f5f1; --panel: #ffffff; --ink: #1f1e1c; --muted: #77756f;
  --line: #e1e0d9; --accent: #2a78d6; --warn-bg: #fff4d6; --warn-ink: #6b4a00;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark; --bg: #161615; --panel: #1f1f1d; --ink: #ecebe6; --muted: #9a988f;
    --line: #34332f; --accent: #6aa6f0; --warn-bg: #3a2f12; --warn-ink: #f3d58a;
  }
}
body { margin: 0; padding: 24px; background: var(--bg); color: var(--ink);
  font: 14px/1.5 var(--font); }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
a { color: var(--accent); }
.muted { color: var(--muted); }
.banner { background: var(--warn-bg); color: var(--warn-ink); border-radius: 8px;
  padding: 8px 12px; margin: 12px 0; }
table { border-collapse: collapse; width: 100%; background: var(--panel); }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
code { font-family: var(--mono); font-size: 12px; }
"""
NOT_A_DEVICE = (
    "Research prototype, not a medical device: it surfaces evidence for discussion with a "
    "clinician and does not diagnose."
)


def _page(title: str, body: str, *, refresh_s: int | None = None) -> str:
    refresh = f'<meta http-equiv="refresh" content="{refresh_s}">\n' if refresh_s else ""
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="referrer" content="no-referrer">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{refresh}<title>{escape(title)}</title>\n<style>{_STYLE}</style>\n</head>\n"
        f"<body>\n<main>\n{body}\n</main>\n</body>\n</html>\n"
    )


def cohorts_page(cohorts: list[tuple[str, int]]) -> str:
    """The cohorts with a graph store, and how many patients each holds."""
    if cohorts:
        items = "\n".join(
            f'<li><a href="cohorts/{escape(name, quote=True)}/">{escape(name)}</a> '
            f'<span class="muted">{n:,} patients</span></li>'
            for name, n in cohorts
        )
        listing = f"<ul>\n{items}\n</ul>"
    else:
        listing = (
            '<p class="muted">No cohort has a graph store yet: run <code>make graphs</code>.</p>'
        )
    return _page(
        "medgraph",
        f'<h1>medgraph</h1>\n<p class="banner">{escape(NOT_A_DEVICE)}</p>\n'
        f"<h2>Cohorts</h2>\n{listing}",
    )


def patients_page(cohort: str, rows: list[PatientRow]) -> str:
    """A cohort's patients, with links to their pages and to the concept map."""
    body = "\n".join(
        "<tr>"
        f'<td><a href="{escape(r.patient, quote=True)}.html"><code>{escape(r.patient)}</code>'
        "</a></td>"
        f'<td class="num">{r.records:,}</td>'
        f'<td class="num">{r.reports}</td>'
        f'<td class="num">{"?" if r.flags is None else r.flags}</td>'
        f"<td>{escape('; '.join(r.conditions))}</td>"
        "</tr>"
        for r in rows
    )
    looked = sum(r.worth_a_look for r in rows)
    return _page(
        f"medgraph: {cohort}",
        f'<p><a href="../../">Cohorts</a></p>\n<h1>{escape(cohort)}</h1>\n'
        f'<p class="banner">{escape(SYNTHETIC_BANNER)}</p>\n'
        f'<p><a href="concept-map.html">Concept map</a> of the whole cohort.</p>\n'
        f'<p class="muted">{len(rows):,} patients. The first {looked:,} have a condition '
        "in the monitoring table or a filed lab report. Flags are counted from the stored "
        f"results of <code>make flags</code> ({escape(RULESET)}; ? if not run); "
        "each page evaluates them again.</p>\n"
        "<table>\n<thead><tr><th>patient</th><th>records</th><th>lab reports</th>"
        "<th>flags</th><th>conditions in the monitoring table</th></tr></thead>\n"
        f"<tbody>\n{body}\n</tbody>\n</table>",
    )


def building_page(cohort: str, patients: int) -> str:
    """Shown while the concept map is built; it reloads itself."""
    return _page(
        f"medgraph: {cohort} concept map",
        f'<p><a href="./">{escape(cohort)}</a></p>\n<h1>Concept map</h1>\n'
        f"<p>Merging the {patients:,} patient graphs of {escape(cohort)} by concept "
        "(about a minute for a thousand patients). This page reloads itself.</p>",
        refresh_s=5,
    )


def failed_page(cohort: str) -> str:
    return _page(
        f"medgraph: {cohort} concept map",
        f'<p><a href="./">{escape(cohort)}</a></p>\n<h1>Concept map</h1>\n'
        "<p>The concept map could not be built; the server's log says why. It is built "
        "again once the store or the knowledge graph changes, or the server restarts.</p>",
    )
