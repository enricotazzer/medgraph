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
from typing import Any, Literal

from medgraph.agent.cache import explanations_for
from medgraph.cohort import SYNTHETIC_BANNER, current_knowledge
from medgraph.graph.concepts import concept_map, with_knowledge
from medgraph.graph.schema import PatientGraph
from medgraph.graph.store import GraphStore
from medgraph.graph.timeline import build_timeline
from medgraph.graph.view import page_model, strings
from medgraph.rules.flags import evaluate
from medgraph.rules.model import RULESET
from medgraph.rules.monitoring import MONITORING, SNOMED
from medgraph.rules.plain import show_date
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


UPLOADED_BANNER = (
    "Added on this machine. medgraph is a research prototype, not a medical device: it does "
    "not diagnose."
)


def person_model(
    graph: PatientGraph, as_of: dt.date, *, label: str, source: str, added: dt.date
) -> dict[str, Any]:
    """The page of someone added in the app: as a cohort patient's, with the label given in the
    app and the uploaded banner. No knowledge graph is joined, and no explanations are shown
    (both are built per cohort)."""
    rules = evaluate(graph, as_of)
    model = page_model(graph, build_timeline(graph), UPLOADED_BANNER, rules.model_dump(mode="json"))
    model["person"] = {"label": label, "source": source, "added": added.isoformat()}
    return model


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
#
# Plain HTML, in English or Italian (``?lang=it``), from the patient page's string table. Every
# value from a record or a form is escaped; the texts themselves are escaped too, and the
# values put into them are HTML the caller has escaped.

_STYLE = """
:root {
  color-scheme: light; --bg: #f6f5f1; --panel: #ffffff; --ink: #1f1e1c; --muted: #77756f;
  --line: #e1e0d9; --accent: #2a78d6; --warn-bg: #fff4d6; --warn-ink: #6b4a00;
  --bad-bg: #fde8e6; --bad-ink: #8a1c12; --danger: #b3261e;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark; --bg: #161615; --panel: #1f1f1d; --ink: #ecebe6; --muted: #9a988f;
    --line: #34332f; --accent: #6aa6f0; --warn-bg: #3a2f12; --warn-ink: #f3d58a;
    --bad-bg: #3b1714; --bad-ink: #f4b8b0; --danger: #f2867c;
  }
}
body { margin: 0; padding: 24px 16px; background: var(--bg); color: var(--ink);
  font: 14px/1.5 var(--font); }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 24px 0 8px; }
a { color: var(--accent); }
.muted { color: var(--muted); }
.small { font-size: 12px; }
.banner { background: var(--warn-bg); color: var(--warn-ink); border-radius: 8px;
  padding: 8px 12px; margin: 12px 0; }
.error { background: var(--bad-bg); color: var(--bad-ink); border-radius: 8px;
  padding: 8px 12px; margin: 12px 0; }
table { border-collapse: collapse; width: 100%; background: var(--panel); }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line);
  vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
code { font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.top { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.brand { font-size: 12px; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--accent); margin: 0 0 2px; }
.langs { font-size: 13px; white-space: nowrap; }
.langs a[aria-current] { color: var(--ink); font-weight: 600; text-decoration: none; }
.crumbs { font-size: 13px; color: var(--muted); margin: 0 0 8px; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 12px 16px; margin: 12px 0; }
.people { list-style: none; padding: 0; margin: 0; }
.people li { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 12px;
  padding: 8px 0; border-bottom: 1px solid var(--line); }
.people li:last-child { border-bottom: 0; }
.people .name { font-weight: 600; font-size: 15px; }
.people .delete { margin-left: auto; font-size: 13px; color: var(--muted); }
form.stack { display: grid; gap: 10px; max-width: 560px; }
form.stack label { display: grid; gap: 4px; font-weight: 600; }
form.stack .hint { font-weight: 400; color: var(--muted); font-size: 12px; }
input[type="text"], input[type="date"], input[type="file"], select {
  font: inherit; color: inherit; background: var(--bg); border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 8px; max-width: 100%; }
button, .button { font: inherit; font-weight: 600; border-radius: 999px; padding: 6px 16px;
  border: 1px solid var(--accent); background: var(--accent); color: var(--panel);
  cursor: pointer; text-decoration: none; display: inline-block; }
button.secondary, .button.secondary { background: transparent; color: var(--accent); }
button.danger { border-color: var(--danger); background: var(--danger); }
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0; }
.filters { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 12px 0;
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  padding: 10px 12px; }
.filters input[type="search"] { flex: 1 1 24ch; padding: 6px 10px; border: 1px solid var(--line);
  border-radius: 999px; background: var(--bg); color: var(--ink); font: inherit; }
.filters select { font: inherit; color: inherit; background: var(--bg);
  border: 1px solid var(--line); border-radius: 6px; padding: 4px 6px; max-width: 32ch; }
.scroll { overflow-x: auto; }
tbody tr:hover td { background: var(--bg); }
"""
NOT_A_DEVICE = (
    "Research prototype, not a medical device: it surfaces evidence for discussion with a "
    "clinician and does not diagnose."
)
Lang = Literal["en", "it"]


def page_lang(value: str | None) -> Lang:
    return "it" if value == "it" else "en"


def tr(lang: Lang, key: str, **values: str) -> str:
    """The interface text ``key``, escaped, with ``values`` (HTML) in its ``{names}``."""
    text = escape(strings()[lang][key])
    for name, value in values.items():
        text = text.replace("{" + name + "}", value)
    return text


def _date(day: dt.date, lang: Lang) -> str:
    return escape(show_date(day, lang))


def _query(lang: Lang) -> str:
    return f"?lang={lang}"


def _page(
    lang: Lang,
    title: str,
    body: str,
    *,
    crumbs: str = "",
    refresh_s: int | None = None,
) -> str:
    """A page of the app. ``title`` is plain text; ``body`` and ``crumbs`` are HTML."""
    refresh = f'<meta http-equiv="refresh" content="{refresh_s}">\n' if refresh_s else ""
    switch = " · ".join(
        f'<a href="?lang={code}" lang="{code}"'
        + (' aria-current="true"' if code == lang else "")
        + f">{code.upper()}</a>"
        for code in ("en", "it")
    )
    nav = (
        f'<nav class="crumbs" aria-label="{tr(lang, "crumbs.label")}">{crumbs}</nav>\n'
        if crumbs
        else ""
    )
    return (
        "<!doctype html>\n"
        f'<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="referrer" content="same-origin">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{refresh}<title>{escape(title)}</title>\n<style>{_STYLE}</style>\n</head>\n"
        f'<body>\n<main>\n<div class="top"><div>{nav}<p class="brand">medgraph</p></div>'
        f'<div class="langs">{switch}</div></div>\n{body}\n</main>\n</body>\n</html>\n'
    )


@dataclass(frozen=True)
class PersonRow:
    id: str
    label: str
    source: str  # "upload" or "entered"
    added: dt.date
    flags: int
    unchecked: int  # checks held back by unread records


def home_page(lang: Lang, rows: list[PersonRow], cohorts: list[tuple[str, int]]) -> str:
    """The people added on this machine, then the synthetic cohorts."""
    q = _query(lang)
    if rows:
        items = "\n".join(
            "<li>"
            f'<a class="name" href="people/{r.id}.html#lang={lang}">{escape(r.label)}</a> '
            f'<span class="muted">{tr(lang, f"app.people.{r.source}", date=_date(r.added, lang))}'
            f" · {tr(lang, 'app.people.flags', n=str(r.flags))}"
            + (f" · {tr(lang, 'app.people.unchecked', n=str(r.unchecked))}" if r.unchecked else "")
            + "</span>"
            f'<a class="delete" href="people/{r.id}/delete{q}">{tr(lang, "app.people.delete")}</a>'
            "</li>"
            for r in rows
        )
        people_list = f'<ul class="people">\n{items}\n</ul>'
    else:
        people_list = f'<p class="muted">{tr(lang, "app.people.none")}</p>'
    if cohorts:
        listing = (
            "<ul>\n"
            + "\n".join(
                f'<li><a href="cohorts/{escape(name, quote=True)}/{q}">{escape(name)}</a> '
                f'<span class="muted">{tr(lang, "app.cohorts.patients", n=f"{n:,}")}</span></li>'
                for name, n in cohorts
            )
            + "\n</ul>"
        )
    else:
        none = tr(lang, "app.cohorts.none", command="<code>make graphs</code>")
        listing = f'<p class="muted">{none}</p>'
    return _page(
        lang,
        "medgraph",
        f'<h1>{tr(lang, "app.title")}</h1>\n<p class="banner">{tr(lang, "app.notadevice")}</p>\n'
        f'<h2>{tr(lang, "app.people")}</h2>\n<div class="card">\n'
        f'<p class="muted">{tr(lang, "app.people.intro")}</p>\n{people_list}\n'
        f'<div class="actions"><a class="button" href="people/add{q}">'
        f"{tr(lang, 'app.people.add')}</a></div>\n</div>\n"
        f"<h2>{tr(lang, 'app.cohorts')}</h2>\n{listing}",
    )


def _home_crumbs(lang: Lang, up: str, here: str) -> str:
    return (
        f'<a href="{up}{_query(lang)}">{tr(lang, "crumbs.home")}</a> &rsaquo; '
        f'<span aria-current="page">{here}</span>'
    )


def add_page(
    lang: Lang, form_token: str, folder: str, max_mb: int, today: dt.date, error: str | None = None
) -> str:
    """The two ways to add a person: upload a FHIR bundle, or enter a birth date and sex."""
    q = _query(lang)
    token = f'<input type="hidden" name="form_token" value="{escape(form_token, quote=True)}">'
    label = (
        f'<label>{tr(lang, "app.add.label")}<input type="text" name="label" required '
        f'maxlength="60" autocomplete="off"><span class="hint">{tr(lang, "app.add.label.hint")}'
        "</span></label>"
    )
    genders = "".join(
        f'<option value="{g}">{tr(lang, f"gender.{g}")}</option>'
        for g in ("female", "male", "other", "unknown")
    )
    return _page(
        lang,
        f"medgraph: {strings()[lang]['app.add.title']}",
        f"<h1>{tr(lang, 'app.add.title')}</h1>\n"
        + (f'<p class="error" role="alert">{escape(error)}</p>\n' if error else "")
        + f"<p>{tr(lang, 'app.add.intro', folder=f'<code>{escape(folder)}</code>')}</p>\n"
        f'<p class="banner">{tr(lang, "app.notadevice")}</p>\n'
        f'<section class="card"><h2>{tr(lang, "app.add.upload")}</h2>\n'
        f'<form class="stack" method="post" action="upload{q}" enctype="multipart/form-data">'
        f"{token}{label}"
        f'<label>{tr(lang, "app.add.file", mb=str(max_mb))}<input type="file" name="record" '
        'required accept=".json,application/json,application/fhir+json"></label>'
        f'<div class="actions"><button type="submit">{tr(lang, "app.add.submit.upload")}</button>'
        "</div></form></section>\n"
        f'<section class="card"><h2>{tr(lang, "app.add.enter")}</h2>\n'
        f'<p class="muted">{tr(lang, "app.add.enter.intro")}</p>\n'
        f'<form class="stack" method="post" action="enter{q}">{token}{label}'
        f'<label>{tr(lang, "app.add.birth")}<input type="date" name="birth_date" required '
        f'min="1900-01-01" max="{today.isoformat()}"></label>'
        f'<label>{tr(lang, "app.add.sex")}<select name="gender" required>{genders}</select>'
        f'<span class="hint">{tr(lang, "app.add.sex.hint")}</span></label>'
        f'<div class="actions"><button type="submit">{tr(lang, "app.add.submit.enter")}</button>'
        "</div></form></section>",
        crumbs=_home_crumbs(lang, "../", tr(lang, "app.add.title")),
    )


def added_page(
    lang: Lang, person_id: str, label: str, entries: int, issues: dict[str, int], replaced: bool
) -> str:
    """What was added: the graph's size and what the reader set aside."""
    title = tr(lang, "app.replaced.title" if replaced else "app.added.title", label=escape(label))
    if issues:
        listed = "".join(
            f"<li><code>{escape(code)}</code>: {n:,}</li>" for code, n in sorted(issues.items())
        )
        read = f"<p>{tr(lang, 'app.added.issues')}</p>\n<ul>{listed}</ul>"
    else:
        read = f"<p>{tr(lang, 'app.added.noissues')}</p>"
    return _page(
        lang,
        f"medgraph: {label}",
        f"<h1>{title}</h1>\n<p>{tr(lang, 'app.added.entries', n=f'{entries:,}')}</p>\n{read}\n"
        f'<div class="actions"><a class="button" href="{person_id}.html#lang={lang}">'
        f"{tr(lang, 'app.added.open')}</a>"
        f'<a class="button secondary" href="../{_query(lang)}">{tr(lang, "crumbs.home")}</a></div>',
        crumbs=_home_crumbs(lang, "../", escape(label)),
    )


def message_page(
    lang: Lang, title_key: str, text: str, *, up: str, values: dict[str, str] | None = None
) -> str:
    """A short page with one message (HTML in ``text``) and a way home."""
    return _page(
        lang,
        f"medgraph: {strings()[lang][title_key]}",
        f"<h1>{tr(lang, title_key, **(values or {}))}</h1>\n<p>{text}</p>\n"
        f'<div class="actions"><a class="button secondary" href="{up}{_query(lang)}">'
        f"{tr(lang, 'crumbs.home')}</a></div>",
    )


def error_page(lang: Lang, message: str, *, back: str) -> str:
    """A form that could not be used, and why (``message`` is HTML)."""
    return _page(
        lang,
        f"medgraph: {strings()[lang]['app.error.title']}",
        f"<h1>{tr(lang, 'app.error.title')}</h1>\n"
        f'<p class="error" role="alert">{message}</p>\n'
        f'<div class="actions"><a class="button secondary" href="{back}{_query(lang)}">'
        f"{tr(lang, 'app.error.back')}</a></div>",
    )


def replace_page(lang: Lang, form_token: str, key: str, label: str, added: dt.date) -> str:
    """An upload whose patient is already here: replace that record, or keep it."""
    hidden = (
        f'<input type="hidden" name="form_token" value="{escape(form_token, quote=True)}">'
        f'<input type="hidden" name="key" value="{escape(key, quote=True)}">'
    )
    return _page(
        lang,
        f"medgraph: {strings()[lang]['app.replace.title']}",
        f"<h1>{tr(lang, 'app.replace.title')}</h1>\n"
        f"<p>{tr(lang, 'app.replace.text', label=escape(label), date=_date(added, lang))}</p>\n"
        f'<form method="post" action="upload/choice{_query(lang)}" class="actions">{hidden}'
        f'<button type="submit" name="choice" value="replace">{tr(lang, "app.replace.yes")}'
        "</button>"
        f'<button type="submit" name="choice" value="keep" class="secondary">'
        f"{tr(lang, 'app.replace.no')}</button></form>",
        crumbs=_home_crumbs(lang, "../", tr(lang, "app.add.title")),
    )


def delete_page(lang: Lang, form_token: str, label: str, folder: str) -> str:
    """What deleting a person removes, and the button that does it."""
    return _page(
        lang,
        f"medgraph: {strings()[lang]['app.delete.title'].replace('{label}', label)}",
        f"<h1>{tr(lang, 'app.delete.title', label=escape(label))}</h1>\n"
        f"<p>{tr(lang, 'app.delete.text', folder=f'<code>{escape(folder)}</code>')}</p>\n"
        f'<p class="muted">{tr(lang, "app.delete.disk")}</p>\n'
        f'<form method="post" action="delete{_query(lang)}" class="actions">'
        f'<input type="hidden" name="form_token" value="{escape(form_token, quote=True)}">'
        f'<button type="submit" class="danger">{tr(lang, "app.delete.yes")}</button>'
        f'<a class="button secondary" href="../../{_query(lang)}">{tr(lang, "app.delete.no")}</a>'
        "</form>",
        crumbs=_home_crumbs(lang, "../../", escape(label)),
    )


def locked_page(lang: Lang) -> str:
    return _page(
        lang,
        f"medgraph: {strings()[lang]['app.locked.title']}",
        f"<h1>{tr(lang, 'app.locked.title')}</h1>\n"
        f"<p>{tr(lang, 'app.locked.text', command='<code>make app</code>')}</p>",
    )


# Filters the patient table in the page; values reach the page only as escaped attributes.
_FILTER_SCRIPT = """
(function () {
  const rows = Array.from(document.querySelectorAll("#patients tbody tr"));
  const q = document.getElementById("q");
  const withFlags = document.getElementById("with-flags");
  const withReports = document.getElementById("with-reports");
  const condition = document.getElementById("condition");
  const count = document.getElementById("count");
  const lang = document.documentElement.lang;
  function apply() {
    const text = q.value.trim().toLowerCase();
    let shown = 0;
    for (const row of rows) {
      const d = row.dataset;
      const ok = (!text || d.search.includes(text))
        && (!withFlags.checked || Number(d.flags) > 0)
        && (!withReports.checked || Number(d.reports) > 0)
        && (!condition.value || d.conditions.split("|").includes(condition.value));
      row.hidden = !ok;
      if (ok) shown += 1;
    }
    count.textContent = count.dataset.text
      .replace("{shown}", shown.toLocaleString(lang))
      .replace("{total}", rows.length.toLocaleString(lang));
  }
  for (const el of [q, withFlags, withReports, condition]) el.addEventListener("input", apply);
  apply();
})();
"""


def patients_page(cohort: str, rows: list[PatientRow], lang: Lang = "en") -> str:
    """A cohort's patients, with links to their pages and to the concept map, and a search
    by ID or condition with filters for flags and lab reports."""
    body = "\n".join(
        f'<tr data-search="{escape(" ".join([r.patient, *r.conditions]).lower(), quote=True)}"'
        f' data-flags="{-1 if r.flags is None else r.flags}" data-reports="{r.reports}"'
        f' data-conditions="{escape("|".join(r.conditions), quote=True)}">'
        f'<td><a href="{escape(r.patient, quote=True)}.html#lang={lang}">'
        f"<code>{escape(r.patient)}</code></a></td>"
        f'<td class="num">{r.records:,}</td>'
        f'<td class="num">{r.reports}</td>'
        f'<td class="num">{"?" if r.flags is None else r.flags}</td>'
        f"<td>{escape('; '.join(r.conditions))}</td>"
        "</tr>"
        for r in rows
    )
    conditions = sorted({c for r in rows for c in r.conditions})
    options = "".join(
        f'<option value="{escape(c, quote=True)}">{escape(c)}</option>' for c in conditions
    )
    looked = sum(r.worth_a_look for r in rows)
    intro = tr(
        lang,
        "app.cohort.intro",
        n=f"{len(rows):,}",
        m=f"{looked:,}",
        command="<code>make flags</code>",
        ruleset=f"<code>{escape(RULESET)}</code>",
    )
    count = escape(strings()[lang]["app.cohort.count"], quote=True)
    headers = "".join(
        f"<th>{tr(lang, f'app.cohort.col.{c}')}</th>"
        for c in ("patient", "records", "reports", "flags", "conditions")
    )
    return _page(
        lang,
        f"medgraph: {cohort}",
        f"<h1>{escape(cohort)}</h1>\n"
        f'<p class="banner">{tr(lang, "banner.synthetic")}</p>\n'
        f'<p><a href="concept-map.html">{tr(lang, "app.cohort.map")}</a></p>\n'
        f'<p class="muted">{intro}</p>\n'
        '<div class="filters" role="search">\n'
        f'<input id="q" type="search" placeholder="{tr(lang, "app.cohort.search")}" '
        f'aria-label="{tr(lang, "app.cohort.search.aria")}">\n'
        f'<label><input id="with-flags" type="checkbox"> {tr(lang, "app.cohort.with.flags")}'
        "</label>\n"
        f'<label><input id="with-reports" type="checkbox"> '
        f"{tr(lang, 'app.cohort.with.reports')}</label>\n"
        f'<select id="condition" aria-label="{tr(lang, "app.cohort.condition")}"><option value="">'
        f"{tr(lang, 'app.cohort.any')}</option>{options}</select>\n"
        f'<span id="count" class="muted" aria-live="polite" data-text="{count}"></span>\n</div>\n'
        f'<div class="scroll"><table id="patients">\n<thead><tr>{headers}</tr></thead>\n'
        f"<tbody>\n{body}\n</tbody>\n</table></div>\n<script>{_FILTER_SCRIPT}</script>",
        crumbs=_home_crumbs(lang, "../../", escape(cohort)),
    )


def building_page(cohort: str, patients: int, lang: Lang = "en") -> str:
    """Shown while the concept map is built; it reloads itself."""
    return _page(
        lang,
        f"medgraph: {cohort}",
        f"<h1>{tr(lang, 'app.map.title')}</h1>\n"
        f"<p>{tr(lang, 'app.map.building', n=f'{patients:,}', cohort=escape(cohort))}</p>",
        crumbs=f'<a href="./{_query(lang)}">{escape(cohort)}</a>',
        refresh_s=5,
    )


def failed_page(cohort: str, lang: Lang = "en") -> str:
    return _page(
        lang,
        f"medgraph: {cohort}",
        f"<h1>{tr(lang, 'app.map.title')}</h1>\n<p>{tr(lang, 'app.map.failed')}</p>",
        crumbs=f'<a href="./{_query(lang)}">{escape(cohort)}</a>',
    )
