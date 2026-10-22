"use strict";
// medgraph patient view. Record text reaches the page only through textContent.
// Two views (personal, clinician) in two languages; interface text comes from strings.json,
// record text and source quotes are shown as written. The graph and the timeline are built
// the first time they are shown.
(function () {
  const D = JSON.parse(document.getElementById("medgraph-data").textContent);
  const S = JSON.parse(document.getElementById("medgraph-strings").textContent);
  const byId = (id) => document.getElementById(id);
  const root = document.documentElement;
  const cssVar = (name) => getComputedStyle(root).getPropertyValue(name).trim();

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : String(child));
    }
    return node;
  }
  // replaceChildren with h()'s rules: lists are flattened, empty children dropped.
  function fill(node, ...children) {
    node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  }

  // --- view, language and interface text ---------------------------------------------------
  const VIEWS = ["personal", "clinician"];
  const STORE_KEY = "medgraph.page";
  const state = { view: "personal", lang: "en", explore: false };
  try { Object.assign(state, JSON.parse(localStorage.getItem(STORE_KEY) || "{}")); } catch (e) { /* storage blocked */ }
  const fromHash = new URLSearchParams(location.hash.slice(1));
  if (fromHash.get("view")) state.view = fromHash.get("view");
  if (fromHash.get("lang")) state.lang = fromHash.get("lang");
  if (!VIEWS.includes(state.view)) state.view = "personal";
  if (!S[state.lang]) state.lang = "en";
  state.explore = false;
  function saveState() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify({ view: state.view, lang: state.lang })); } catch (e) { /* storage blocked */ }
    try { history.replaceState(null, "", `#view=${state.view}&lang=${state.lang}`); } catch (e) { /* file pages */ }
  }
  function t(key, vars) {
    let text = S[state.lang][key];
    if (text === undefined) text = S.en[key];
    if (text === undefined) return key;
    for (const [name, value] of Object.entries(vars || {})) text = text.split(`{${name}}`).join(String(value));
    return text;
  }
  const locale = () => (state.lang === "it" ? "it-IT" : "en");
  const num = (n) => n.toLocaleString(locale());
  const list = (key) => t(key).split(",");
  // Dates: ISO in the English clinician view, day/month/year in Italian; "30 December 2024"
  // in the English personal view.
  function fd(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-");
    return state.lang === "it" ? `${d}/${m}/${y}` : iso;
  }
  function pd(iso) {
    if (!iso) return "";
    if (state.lang === "it") return fd(iso);
    const [y, m, d] = iso.split("-").map(Number);
    return `${d} ${list("months")[m - 1]} ${y}`;
  }
  const isoDate = (t0) => new Date(t0 * 1000).toISOString().slice(0, 10);
  const shorten = (text, n) => (text.length > n ? text.slice(0, n - 1) + "…" : text);
  // SNOMED CT's semantic tag, e.g. "(disorder)": dropped from labels for display only.
  const SEMANTIC_TAG = /\s*\((?:disorder|finding|procedure|situation|physical object|regime\/therapy|observable entity)\)$/;
  const UNIT_SHOWN = { "mL/min/{1.73_m2}": "mL/min/1.73 m²" };
  const unitShown = (unit) => UNIT_SHOWN[unit] || unit;
  const BANNER = ["banner.synthetic", "banner.uploaded"].find((k) => D.banner === S.en[k]) || null;

  const KINDS = [
    ["condition", true], ["medication", true], ["procedure", true], ["analyte", true], ["lab_report", true],
    ["source", true], ["observation", false], ["encounter", false], ["note", false],
  ];
  const KIND_OF_RECORD = { medication_request: "medication", lab_result: "analyte", report_row: "lab_report" };
  const color = (kind) => cssVar(`--k-${KIND_OF_RECORD[kind] || kind}`) || "#888";
  const shown = Object.fromEntries(KINDS.map(([k, on]) => [k, on]));
  let showUnlinked = false;

  const concepts = new Map(D.concepts.map((c) => [c.id, c]));
  const conceptOf = new Map();
  for (const c of D.concepts) for (const n of c.records) conceptOf.set(n, c.id);
  const edgesOf = new Map();
  for (const e of D.edges) {
    for (const end of [e[0], e[1]]) {
      if (!edgesOf.has(end)) edgesOf.set(end, []);
      edgesOf.get(end).push(e);
    }
  }
  const K = D.knowledge || null;
  const X = D.explanations || {};
  const R = D.rules || null;
  const P = D.personal || { flags: [], notes: [], lines: [] };

  // --- header ---------------------------------------------------------------------------
  function renderHeader() {
    const p = D.patient;
    document.title = t("page.title");
    byId("page-heading").textContent = t(state.view === "personal" ? "h1.personal" : "h1.clinician");
    const gender = p.gender ? (S.en[`gender.${p.gender}`] ? t(`gender.${p.gender}`) : p.gender) : null;
    // Someone added in the app is shown by the label given there, not by the record's ID.
    byId("patient-line").textContent = [
      D.person ? D.person.label : t("patient.id", { id: p.id.slice(0, 8) }),
      gender,
      p.birth_date ? t("patient.born", { date: fd(p.birth_date.date) }) : null,
      p.deceased ? t("patient.died", { date: fd(p.deceased.date) }) : null,
      t("patient.records", { n: num(Object.keys(D.records).length), m: D.sources.length }),
    ].filter(Boolean).join(" · ");
    byId("banner").textContent = BANNER ? t(BANNER) : D.banner || "";
    // Breadcrumbs only when the app serves the page: /cohorts/<cohort>/<patient>.html, or
    // /people/<id>.html for someone added in the app.
    const crumbs = byId("crumbs");
    const parts = location.pathname.split("/");
    const http = location.protocol.startsWith("http");
    const inCohort = http && parts.length >= 4 && parts[1] === "cohorts";
    const person = http && parts.length === 3 && parts[1] === "people" && D.person;
    crumbs.hidden = !(inCohort || person);
    const sep = () => h("span", { "aria-hidden": "true" }, " › ");
    const q = `?lang=${state.lang}`;
    if (inCohort) {
      crumbs.replaceChildren(
        h("a", { href: `../../${q}` }, t("crumbs.home")), sep(),
        h("a", { href: `./${q}` }, decodeURIComponent(parts[2])), sep(),
        h("span", { "aria-current": "page" }, t("crumbs.patient", { id: p.id.slice(0, 8) })));
    } else if (person) {
      crumbs.replaceChildren(
        h("a", { href: `../${q}` }, t("crumbs.home")), sep(),
        h("span", { "aria-current": "page" }, D.person.label));
    }
  }
  function applyStatic() {
    root.lang = state.lang;
    for (const el of document.querySelectorAll("[data-t]")) el.textContent = t(el.dataset.t);
    for (const el of document.querySelectorAll("[data-t-aria]")) el.setAttribute("aria-label", t(el.dataset.tAria));
    for (const el of document.querySelectorAll("[data-t-placeholder]")) el.setAttribute("placeholder", t(el.dataset.tPlaceholder));
    for (const b of document.querySelectorAll("[data-view]")) b.setAttribute("aria-pressed", String(b.dataset.view === state.view));
    for (const b of document.querySelectorAll("[data-lang]")) b.setAttribute("aria-pressed", String(b.dataset.lang === state.lang));
    const explore = byId("explore");
    explore.textContent = t(state.explore ? "p.explore.hide" : "p.explore");
    explore.setAttribute("aria-expanded", String(state.explore));
  }

  // --- record summaries -----------------------------------------------------------------
  function valueText(r) {
    if (r.value !== undefined) return `${r.comparator || ""}${r.value} ${r.unit || ""}`.trim();
    if (r.text) return shorten(r.text, 90);
    return "";
  }
  function when(r) {
    if (!r.start) return t("r.undated");
    const start = r.start.date;
    if (r.end && r.end.date !== start) return t("r.span", { start: fd(start), end: fd(r.end.date) });
    return fd(start);
  }
  function recordsTable(nodes, limit = 300) {
    const body = nodes.slice(0, limit).map((n) => {
      const r = D.records[n];
      const unusable = r.usable === false;
      return h("tr", { class: "row", tabindex: 0, onclick: () => showRecord(n, true),
        onkeydown: (ev) => { if (ev.key === "Enter") showRecord(n, true); } },
        h("td", {}, when(r)),
        h("td", { class: unusable ? "unusable" : null },
          r.kind === "report_row" ? `${r.label}: ${valueText(r)}` : valueText(r) || r.label),
        h("td", {}, r.status ? h("span", { class: unusable ? "badge unusable" : "badge" }, r.status) : ""));
    });
    const table = h("table", { class: "records" },
      h("thead", {}, h("tr", {}, h("th", {}, t("t.date")), h("th", {}, t("t.value")), h("th", {}, t("t.status")))),
      h("tbody", {}, body));
    const more = nodes.length > limit ? h("p", { class: "muted" }, t("t.more", { n: nodes.length - limit })) : null;
    return [table, more];
  }

  // --- details panel --------------------------------------------------------------------
  const details = byId("details");
  let lastConcept = null;
  let redo = () => intro();
  function show(...children) {
    details.replaceChildren(...children.flat(Infinity).filter(Boolean));
    details.scrollTop = 0;
  }
  function intro() {
    redo = intro;
    show(h("h2", {}, t("details.title")),
      h("p", { class: "muted" }, t("details.intro1")),
      h("p", { class: "muted" }, t("details.intro2")));
  }
  function linkItem(l, from) {
    const other = concepts.get(l.source === from ? l.target : l.source);
    const arrow = l.source === from ? `${l.kind} →` : `← ${l.kind}`;
    return h("li", {},
      h("span", { class: "kind" }, arrow), " ",
      h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); selectConcept(other.id); } }, other.label),
      l.count > 1 ? t("link.records", { n: l.count }) : "",
      h("div", { class: "muted" }, t("link.basis", { basis: l.basis })),
      l.citation ? h("div", { class: "muted" }, l.citation) : null,
      l.detail && l.kind === "monitored_by" ? h("div", { class: "quote" }, `“${l.detail}”`) : null,
      l.detail && KNOWLEDGE_LINKS.has(l.kind) ? h("div", { class: "muted" }, l.detail) : null,
      l.statement ? showSourceLink(l.statement) : null);
  }
  function showConcept(id) {
    const c = concepts.get(id);
    if (c.kind === "source") return showSource(id);
    redo = () => showConcept(id);
    lastConcept = id;
    const links = D.links.filter((l) => l.source === id || l.target === id)
      .filter((l) => l.kind !== "occurred_during" || c.kind === "encounter");
    const series = D.series.find((s) => `analyte:${s.analyte}` === id);
    const head = D.records[id];
    show(
      h("span", { class: "kind" }, c.kind.replace("_", " ")),
      h("h2", {}, c.label),
      h("dl", {},
        c.code ? [h("dt", {}, t("f.code")), h("dd", { class: "mono" }, c.code)] : null,
        [h("dt", {}, t("f.records")), h("dd", {}, String(c.count))],
        c.first ? [h("dt", {}, t("f.firstlast")), h("dd", {}, `${fd(c.first)} / ${fd(c.last)}`)] : null,
        c.kind === "condition" ? [h("dt", {}, t("f.clinical")), h("dd", {}, t(c.active ? "f.clinical.active" : "f.clinical.inactive"))] : null,
        series ? [h("dt", {}, t("f.usable")), h("dd", {}, t("f.usable.n", { n: series.points.length, unit: series.unit }))] : null,
        head && head.text && c.kind === "lab_report" ? [h("dt", {}, t("f.conventions")), h("dd", {}, head.text)] : null,
        head && head.detail && c.kind !== "analyte" ? [h("dt", {}, t("f.issues")), h("dd", {}, head.detail)] : null,
        head && head.kind === "lab_report" ? [h("dt", {}, t("f.collection")), h("dd", {}, head.start ? fd(head.start.date) : t("f.collection.none"))] : null,
        K && K.unlabelled[id] ? [h("dt", {}, t("f.label")), h("dd", {}, t("f.label.none", { why: K.unlabelled[id] }))] : null),
      links.length ? [h("h3", {}, t("h.links")), h("ul", { class: "links" }, links.map((l) => linkItem(l, id)))] : null,
      c.records.length ? [h("h3", {}, t("h.records")), recordsTable(c.records)] : null);
  }
  // --- sources (the knowledge graph) ------------------------------------------------------
  const KNOWLEDGE_LINKS = new Set(["labelled_by", "guided_by"]);
  const SOURCE_KIND = { document: "source.document", label: "source.label" };
  function showSourceLink(statement) {
    const target = K && K.quotes[statement];
    if (!target) return null;
    return h("div", {}, h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); selectConcept(target); } }, t("link.source")));
  }
  function statementItem(id) {
    const s = K.nodes[id];
    return h("li", {},
      h("div", { class: "muted" }, s.locator, K.cited.includes(id) ? t("source.cited") : ""),
      h("div", { class: "quote" }, `“${s.quote}”`),
      s.table ? h("div", { class: "muted" }, t("source.table", { table: s.table })) : null,
      h("div", { class: "mono muted" }, t("source.passage", { ids: s.passages.join(", ") })));
  }
  function showSource(id) {
    redo = () => showSource(id);
    const n = K.nodes[id];
    lastConcept = id;
    const statements = Object.keys(K.quotes).filter((s) => K.quotes[s] === id);
    const links = D.links.filter((l) => l.target === id);
    const rows = [
      ["source.citation", n.citation],
      ["source.labeler", n.labeler],
      ["source.setid", n.setid ? `${n.setid}, v${n.version}` : null],
      ["source.effective", n.effective ? fd(n.effective) : null],
      ["source.type", n.document_type],
      ["source.licence", n.licence],
      ["source.file", n.sha256 ? `SHA-256 ${n.sha256.slice(0, 12)}…` : null],
      ["source.retrieved", n.retrieved ? fd(n.retrieved.slice(0, 10)) : null],
      ["source.from", n.url],
    ].filter(([, v]) => v);
    show(
      h("span", { class: "kind" }, SOURCE_KIND[n.kind] ? t(SOURCE_KIND[n.kind]) : n.kind),
      h("h2", {}, n.title),
      h("dl", {}, rows.map(([k, v]) => [h("dt", {}, t(k)), h("dd", { class: k === "source.from" ? "mono" : null }, v)])),
      links.length ? [h("h3", {}, t("source.linked")), h("ul", { class: "links" }, links.map((l) => linkItem(l, id)))] : null,
      h("h3", {}, t("source.quotes", { n: statements.length })),
      statements.length ? h("ul", { class: "links" }, statements.map(statementItem)) : h("p", { class: "muted" }, t("source.nothing")),
      n.sections && n.sections.length ? h("details", {}, h("summary", {}, t("source.sections", { n: n.sections.length })), h("ul", {}, n.sections.map((s) => h("li", {}, s)))) : null,
      h("p", { class: "muted" }, t("source.note")));
  }
  function sourceItem(s) {
    return h("li", { class: "mono", title: s.source }, `${s.resource_type}/${s.resource_id} in ${s.source.slice(0, 19)}…`);
  }
  function showRecord(n, fromConcept) {
    redo = () => showRecord(n, fromConcept);
    const r = D.records[n];
    const o = r.original;
    const rows = [
      ["r.date", when(r)],
      ["r.status", r.status],
      ["r.usable", r.usable === undefined ? null : t(r.usable ? "r.yes" : "r.no")],
      ["r.value", r.value !== undefined ? `${r.comparator || ""}${r.value} ${r.unit || ""}` : null],
      ["r.asrecorded", o ? `${o.comparator || ""}${o.value} ${o.unit || o.code || ""}` : null],
      ["r.method", r.method],
      ["r.analyte", r.analyte],
      ["r.category", r.category],
      ["r.code", r.code],
      ["r.whynot", r.usable === false ? r.detail : null],
      ["r.issues", r.usable !== false ? r.detail : null],
    ].filter(([, v]) => v !== null && v !== undefined && v !== "");
    const links = (edgesOf.get(n) || []).map((e) => {
      const outgoing = e[0] === n;
      const other = outgoing ? e[1] : e[0];
      const target = D.records[other];
      return h("li", {},
        h("span", { class: "kind" }, outgoing ? `${e[2]} →` : `← ${e[2]}`), " ",
        h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); showRecord(other, false); } },
          `${target.label}${target.start ? ` (${fd(target.start.date)})` : ""}`),
        h("div", { class: "muted" }, t("link.basis", { basis: e[3] })),
        e[4] ? h("div", { class: "muted" }, e[4]) : null,
        e[5] ? h("div", { class: e[2] === "monitored_by" ? "quote" : "muted" }, e[5]) : null);
    });
    const concept = conceptOf.get(n) || (concepts.has(n) ? n : null);
    show(
      fromConcept && lastConcept ? h("button", { class: "back", type: "button", onclick: () => showConcept(lastConcept) }, t("r.back")) : null,
      h("span", { class: "kind" }, r.kind.replace("_", " ")),
      h("h2", {}, r.label),
      h("dl", {}, rows.map(([k, v]) => [h("dt", {}, t(k)), h("dd", { class: k === "r.code" ? "mono" : (k === "r.whynot" ? "unusable" : null) }, v)])),
      r.text && r.kind !== "lab_report" ? [h("h3", {}, t(r.kind === "report_row" ? "r.asprinted" : r.kind === "note" ? "r.notetext" : "r.valuetext")), h("div", { class: "note-text" }, r.text)] : null,
      [h("h3", {}, t("r.sources")), h("ul", { class: "links" }, (r.sources || []).map(sourceItem))],
      links.length ? [h("h3", {}, t("r.links")), h("ul", { class: "links" }, links)] : null,
      concept && concept !== n ? h("p", {}, h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); selectConcept(concept); } }, t("r.concept"))) : null);
  }
  function showList(key, nodes) {
    redo = () => showList(key, nodes);
    lastConcept = null;
    show(h("h2", {}, key), recordsTable(nodes));
  }
  function showPassage(item) {
    redo = () => showPassage(item);
    lastConcept = null;
    show(h("span", { class: "kind" }, t("x.passage")), h("h2", {}, item.where),
      h("dl", {}, h("dt", {}, t("x.passage.id")), h("dd", { class: "mono" }, item.ref)),
      h("p", { class: "muted" }, t("x.passage.note")));
  }
  // A record, a concept or a source opened from the personal view shows the connections.
  function openRecord(node) {
    if (state.view === "personal" && !state.explore) setExplore(true);
    showRecord(node, false);
    details.scrollIntoView({ block: "nearest" });
  }

  // --- graph (built the first time it is shown) -----------------------------------------
  let cy = null;
  let layoutName = "columns";
  function visible(n) {
    if (!shown[n.data("kind")]) return false;
    if (showUnlinked || n.hasClass("found")) return true;
    return n.connectedEdges().some((e) => shown[e.source().data("kind")] && shown[e.target().data("kind")]);
  }
  // Columns read left to right: what was measured, what is wrong, what treats it. Within a
  // column, nodes sit near the concepts they link to (mean row of their neighbours).
  const COLUMNS = ["lab_report", "analyte", "condition", "medication", "procedure", "source", "observation", "encounter", "note"];
  const COLUMN_WIDTH = 250;
  function columnPositions(nodes) {
    const rows = new Map();
    const byKind = new Map(COLUMNS.map((k) => [k, []]));
    nodes.forEach((n) => byKind.get(n.data("kind")).push(n));
    const firstDate = (n) => concepts.get(n.id()).first || "9999";
    const meanRow = (n) => {
      const placed = n.neighborhood("node:visible").filter((m) => rows.has(m.id()));
      if (!placed.length) return Infinity;
      return placed.reduce((sum, m) => sum + rows.get(m.id()), 0) / placed.length;
    };
    // Conditions first, by first date; then the columns that link to them; then the rest.
    const order = ["condition", "medication", "procedure", "source", "analyte", "lab_report", "observation", "encounter", "note"];
    for (const kind of order) {
      const column = byKind.get(kind);
      const key = kind === "condition" ? (n) => firstDate(n) : meanRow;
      const keyed = column.map((n) => [key(n), concepts.get(n.id()).label, n]);
      keyed.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1].localeCompare(b[1])));
      keyed.forEach(([, , n], i) => rows.set(n.id(), i));
    }
    const used = COLUMNS.filter((k) => byKind.get(k).length);
    // Row spacing that fills the panel's shape: tall columns get tight rows, short ones air.
    const box = byId("cy").getBoundingClientRect();
    const longest = Math.max(1, ...used.map((k) => byKind.get(k).length));
    const width = (used.length - 1) * COLUMN_WIDTH + 200;
    const row = Math.max(26, Math.min(64, ((box.height / Math.max(box.width, 1)) * width) / longest));
    const positions = {};
    nodes.forEach((n) => {
      positions[n.id()] = { x: used.indexOf(n.data("kind")) * COLUMN_WIDTH, y: rows.get(n.id()) * row };
    });
    return positions;
  }
  function relayout() {
    cy.batch(() => cy.nodes().forEach((n) => n.style("display", visible(n) ? "element" : "none")));
    const shownNodes = cy.nodes(":visible");
    if (layoutName === "columns") {
      cy.batch(() => cy.nodes().removeClass("network"));
      const positions = columnPositions(shownNodes);
      shownNodes.layout({ name: "preset", positions: (n) => positions[n.id()], fit: true, padding: 20 }).run();
    } else {
      cy.batch(() => cy.nodes().addClass("network"));
      cy.elements(":visible").layout({
        name: "cose", animate: false, randomize: false, fit: true, padding: 24,
        nodeDimensionsIncludeLabels: true, componentSpacing: 30,
        nodeRepulsion: () => 4500, idealEdgeLength: () => 60, nodeOverlap: 8, numIter: 1500,
      }).run();
    }
    cy.zoom(Math.min(cy.zoom(), 1.4));
  }
  function selectConcept(id) {
    if (state.view === "personal" && !state.explore) setExplore(true);
    ensureGraph();
    const node = cy.getElementById(id);
    const kind = concepts.get(id).kind;
    if (!shown[kind] || !visible(node)) {
      shown[kind] = true;
      node.addClass("found");
      const box = document.querySelector(`#kind-toggles input[value="${kind}"]`);
      if (box) box.checked = true;
      relayout();
    }
    cy.elements().unselect();
    node.select();
    cy.animate({ center: { eles: node }, duration: 200 });
    showConcept(id);
  }
  function renderToggles() {
    const toggles = byId("kind-toggles");
    const present = new Set(D.concepts.map((c) => c.kind));
    toggles.replaceChildren();
    for (const [kind] of KINDS) {
      if (!present.has(kind)) continue;
      const box = h("input", { type: "checkbox", value: kind, checked: shown[kind] });
      box.addEventListener("change", () => { shown[kind] = box.checked; relayout(); });
      toggles.append(h("label", {}, box, h("span", { class: "swatch", style: `background:${color(kind)}` }), t(`kind.${kind}`)));
    }
    const unlinked = h("input", { type: "checkbox", checked: showUnlinked });
    unlinked.addEventListener("change", () => { showUnlinked = unlinked.checked; relayout(); });
    toggles.append(h("label", { title: t("graph.unlinked.title") }, unlinked, t("graph.unlinked")));
    const layoutSwitch = h("select", { "aria-label": t("graph.layout") },
      h("option", { value: "columns" }, t("graph.layout.columns")),
      h("option", { value: "network" }, t("graph.layout.network")));
    layoutSwitch.value = layoutName;
    layoutSwitch.addEventListener("change", () => { layoutName = layoutSwitch.value; relayout(); });
    toggles.append(h("label", {}, `${t("graph.layout")} `, layoutSwitch));
  }
  function ensureGraph() {
    if (cy) { cy.resize(); return; }
    const elements = [];
    D.concepts.forEach((c, i) => {
      const angle = (2 * Math.PI * i) / Math.max(D.concepts.length, 1);
      elements.push({
        group: "nodes",
        data: {
          id: c.id, label: shorten(c.label.replace(SEMANTIC_TAG, ""), 44), kind: c.kind, color: color(c.kind),
          size: Math.min(10 + 4 * Math.log2(1 + c.count), 24), active: c.active ? 1 : 0, empty: c.count === 0 && c.kind === "analyte" ? 1 : 0,
        },
        position: { x: 400 * Math.cos(angle), y: 400 * Math.sin(angle) },
      });
    });
    for (const l of D.links) {
      elements.push({ group: "edges", data: { id: `${l.kind}|${l.source}|${l.target}`, source: l.source, target: l.target, kind: l.kind, width: 1 + Math.log2(l.count) } });
    }
    cy = cytoscape({
      container: byId("cy"),
      elements,
      minZoom: 0.1,
      maxZoom: 3,
      style: [
        { selector: "node", style: {
          width: "data(size)", height: "data(size)", label: "data(label)",
          "background-color": "data(color)", "border-color": "data(color)", "border-width": 0,
          "font-size": 12, "font-family": cssVar("--font") || "sans-serif", color: cssVar("--ink"),
          "text-valign": "center", "text-halign": "right", "text-margin-x": 5, "text-wrap": "ellipsis", "text-max-width": 205,
          "text-background-color": cssVar("--panel"), "text-background-opacity": 0.85, "text-background-padding": 1,
          "min-zoomed-font-size": 6,
        } },
        { selector: "node.network", style: { "text-valign": "bottom", "text-halign": "center", "text-margin-x": 0, "text-margin-y": 3, "text-max-width": 150 } },
        // An analyte the guideline table links to but the record never measured: hollow.
        { selector: "node[empty = 1]", style: { "background-opacity": 0.12, "border-width": 2 } },
        { selector: "node[kind = 'condition'][active = 1]", style: { "border-width": 2, "border-color": cssVar("--k-condition"), "border-opacity": 0.5 } },
        { selector: "edge", style: { width: "data(width)", "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.7, opacity: 0.75, "line-color": cssVar("--muted"), "target-arrow-color": cssVar("--muted") } },
        { selector: "edge[kind = 'treated_by']", style: { "line-color": color("medication"), "target-arrow-color": color("medication") } },
        { selector: "edge[kind = 'monitored_by']", style: { "line-style": "dashed", "line-color": color("analyte"), "target-arrow-color": color("analyte") } },
        { selector: "edge[kind = 'measures']", style: { "line-style": "dotted", "line-color": color("lab_report"), "target-arrow-color": color("lab_report") } },
        { selector: "edge[kind = 'occurred_during']", style: { width: 0.6, opacity: 0.35 } },
        { selector: "edge[kind = 'labelled_by'], edge[kind = 'guided_by']", style: { "line-style": "dashed", "line-color": color("source"), "target-arrow-color": color("source") } },
        { selector: ":selected", style: { "border-width": 3, "border-color": cssVar("--accent"), "line-color": cssVar("--accent"), "target-arrow-color": cssVar("--accent") } },
        { selector: ".dim", style: { opacity: 0.15 } },
      ],
      layout: { name: "preset" },
    });
    cy.on("tap", "node", (ev) => { showConcept(ev.target.id()); });
    cy.on("tap", "edge", (ev) => showEdge(ev.target.data()));
    renderToggles();
    byId("search").addEventListener("input", (ev) => {
      const q = ev.target.value.trim().toLowerCase();
      cy.batch(() => {
        cy.elements().removeClass("dim");
        if (!q) return;
        const hits = cy.nodes().filter((n) => concepts.get(n.id()).label.toLowerCase().includes(q));
        cy.elements().not(hits).addClass("dim");
        if (hits.length === 1) selectConcept(hits[0].id());
        else if (!hits.length) show(h("h2", {}, t("details.title")), h("p", { class: "muted" }, t("graph.nomatch", { q })));
      });
    });
    relayout();
  }
  function showEdge(d) {
    redo = () => showEdge(d);
    const link = D.links.find((l) => l.kind === d.kind && l.source === d.source && l.target === d.target);
    const nodes = D.edges.filter((e) => e[2] === d.kind && (conceptOf.get(e[0]) || e[0]) === d.source && (conceptOf.get(e[1]) || e[1]) === d.target).map((e) => e[0]);
    lastConcept = null;
    show(h("span", { class: "kind" }, d.kind), h("h2", {}, `${concepts.get(d.source).label} → ${concepts.get(d.target).label}`),
      h("ul", { class: "links" }, linkItem(link, d.source)), h("h3", {}, t("edge.records")), recordsTable([...new Set(nodes)]));
  }

  // --- timeline (built the first time it is shown) --------------------------------------
  const LABEL_W = 220;
  root.style.setProperty("--label-width", `${LABEL_W}px`);
  const times = [];
  for (const e of D.events) { times.push(e.t0); if (e.t1) times.push(e.t1); }
  for (const s of D.series) for (const pt of s.points) times.push(pt.t);
  const full = times.length ? [Math.min(...times), Math.max(...times)] : [0, 1];
  const padOf = (a, b) => Math.max((b - a) * 0.02, 86400);
  const fullRange = { min: full[0] - padOf(...full), max: full[1] + padOf(...full) };
  // Open on the span of the lab values, where the charts are readable; records can start
  // decades earlier. "Full range" shows everything, and events outside the shown range are
  // clipped at its edge, never dropped.
  const focus = [];
  for (const s of D.series) for (const pt of s.points) focus.push(pt.t);
  const start = focus.length ? Math.min(...focus) : full[0];
  const range = { min: start - padOf(start, full[1]), max: fullRange.max };
  const charts = [];
  const chartLabels = [];
  const tooltip = byId("tooltip");
  let plot = { left: LABEL_W + 56, width: 400 };
  let timelineReady = false;
  const uplotNames = () => ({ MMMM: list("months"), MMM: list("months.short"), WWWW: list("weekdays"), WWW: list("weekdays.short") });

  function rangeLabel() {
    const whole = range.min <= fullRange.min && range.max >= fullRange.max;
    byId("range-label").textContent = t("tl.range", { start: fd(isoDate(range.min)), end: fd(isoDate(range.max)) }) +
      (whole ? t("tl.all") : t("tl.span", { start: fd(isoDate(fullRange.min)), end: fd(isoDate(fullRange.max)) }));
  }
  function setRange(min, max) {
    range.min = min;
    range.max = max;
    for (const u of charts) u.setScale("x", { min, max });
    rangeLabel();
    drawLanes();
  }
  function readout(u, s) {
    const i = u.cursor.idx;
    const out = u.root.parentElement.previousSibling.querySelector(".readout");
    if (i === null || i === undefined) { out.textContent = ""; return; }
    const pt = s.points[i];
    const fromReport = !s.computed && pt.node.startsWith("report_row:");
    const note = s.computed ? `  (${pt.detail})` : fromReport ? `  (${t("tl.reportonly")})` : pt.rows.length ? `  (${t("tl.alsoreport")})` : "";
    out.textContent = `${fd(isoDate(pt.t))}  ${pt.cmp || ""}${pt.value}${note}`;
  }
  function chartUnit(s) {
    return t("tl.unit", { unit: s.unit, n: s.points.length }) + (s.computed ? t("tl.computed") : "");
  }
  function makeChart(s) {
    const unit = h("div", { class: "unit" }, chartUnit(s));
    chartLabels.push([unit, s]);
    const label = h("div", { class: "chart-label" }, h("div", {}, s.label), unit, h("div", { class: "readout" }));
    const holder = h("div", { class: "chart" });
    byId("charts").append(h("div", { class: "chart-row" }, label, holder));
    const xs = s.points.map((pt) => pt.t);
    const recorded = s.points.map((pt) => (!s.computed && pt.node.startsWith("report_row:") ? null : pt.v));
    const reportOnly = s.points.map((pt) => (!s.computed && pt.node.startsWith("report_row:") ? pt.v : null));
    const lineColor = s.computed ? cssVar("--computed") : color("analyte");
    const ink = cssVar("--ink-2");
    const grid = { stroke: cssVar("--line"), width: 1 };
    const u = new uPlot({
      width: Math.max(holder.clientWidth, 200),
      height: 130,
      padding: [10, 12, 0, 0],
      legend: { show: false },
      fmtDate: (tpl) => uPlot.fmtDate(tpl, uplotNames()),
      cursor: { drag: { x: true, y: false, setScale: false }, points: { size: 8 } },
      scales: { x: { time: true, range: () => [range.min, range.max] } },
      axes: [
        { stroke: ink, grid, ticks: grid, size: 28, font: `11px ${cssVar("--font")}` },
        { stroke: ink, grid, ticks: grid, size: 56, font: `11px ${cssVar("--font")}` },
      ],
      series: [
        {},
        { label: "value", stroke: lineColor, width: 1.5, spanGaps: true, points: { show: true, size: 5, fill: lineColor } },
        { label: "report only", stroke: color("lab_report"), width: 0, paths: () => null, points: { show: true, size: 7, fill: color("lab_report") } },
      ],
      hooks: {
        setCursor: [(uu) => readout(uu, s)],
        setSelect: [(uu) => {
          if (uu.select.width > 4) {
            const a = uu.posToVal(uu.select.left, "x");
            const b = uu.posToVal(uu.select.left + uu.select.width, "x");
            uu.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
            setRange(a, b);
          }
        }],
      },
    }, [xs, recorded, reportOnly], holder);
    u.over.addEventListener("click", () => {
      const i = u.cursor.idx;
      if (i !== null && i !== undefined && u.select.width < 2) showRecord(s.points[i].node, false);
    });
    u.over.addEventListener("dblclick", () => setRange(fullRange.min, fullRange.max));
    charts.push(u);
  }
  function measurePlot() {
    if (!charts.length) return;
    const u = charts[0];
    const holderLeft = u.root.getBoundingClientRect().left - byId("lanes").getBoundingClientRect().left;
    plot = { left: holderLeft + u.bbox.left / devicePixelRatio, width: u.bbox.width / devicePixelRatio };
  }
  function ensureTimeline() {
    if (timelineReady) { resizeTimeline(); return; }
    timelineReady = true;
    if (D.series.length) D.series.forEach(makeChart);
    else byId("charts").append(h("p", { class: "muted", "data-t": "tl.none" }, t("tl.none")));
    measurePlot();
    new ResizeObserver(resizeTimeline).observe(document.querySelector(".timeline-panel"));
    setRange(range.min, range.max);
  }
  function resizeTimeline() {
    for (const u of charts) {
      const holder = u.root.parentElement;
      u.setSize({ width: Math.max(holder.clientWidth, 200), height: 130 });
    }
    measurePlot();
    drawLanes();
  }

  // Event lanes: one per concept, grouped by kind; a collapsed group shares one lane.
  const LANE_KINDS = [["condition", true], ["medication", true], ["procedure", false], ["encounter", false]];
  const expanded = Object.fromEntries(LANE_KINDS.map(([k, open]) => [k, open]));
  const eventsBy = new Map();
  for (const e of D.events) {
    if (!eventsBy.has(e.concept)) eventsBy.set(e.concept, []);
    eventsBy.get(e.concept).push(e);
  }
  let hits = [];
  let groupHeaders = [];
  function lanes() {
    const rows = [];
    for (const [kind] of LANE_KINDS) {
      const ids = [...eventsBy.keys()].filter((id) => concepts.get(id).kind === kind);
      if (!ids.length) continue;
      const title = t(`kind.${kind}`);
      rows.push({ header: true, kind, label: `${expanded[kind] ? "▾" : "▸"} ${title} (${ids.length})` });
      if (expanded[kind]) for (const id of ids) rows.push({ kind, label: concepts.get(id).label, events: eventsBy.get(id) });
      else rows.push({ kind, label: t("tl.lane.all", { what: title.toLowerCase() }), events: ids.flatMap((id) => eventsBy.get(id)) });
    }
    return rows;
  }
  function drawLanes() {
    if (!timelineReady) return;
    const canvas = byId("lanes");
    const rows = lanes();
    const ROW = 16;
    const width = canvas.clientWidth || canvas.parentElement.clientWidth;
    const height = rows.length * ROW + 4;
    const ratio = devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.height = `${height}px`;
    const g = canvas.getContext("2d");
    g.scale(ratio, ratio);
    g.clearRect(0, 0, width, height);
    const left = charts.length ? plot.left : LABEL_W + 56;
    const plotWidth = charts.length ? plot.width : Math.max(width - left - 12, 50);
    const x = (t0) => left + ((t0 - range.min) / (range.max - range.min)) * plotWidth;
    hits = [];
    groupHeaders = [];
    g.font = `12px ${cssVar("--font")}`;
    g.textBaseline = "middle";
    rows.forEach((row, i) => {
      const y = i * ROW + ROW / 2 + 2;
      if (row.header) {
        g.fillStyle = cssVar("--ink");
        g.fillText(row.label, 0, y);
        groupHeaders.push({ y0: y - ROW / 2, y1: y + ROW / 2, kind: row.kind });
        g.strokeStyle = cssVar("--line");
        g.beginPath(); g.moveTo(left, y + ROW / 2 - 1); g.lineTo(left + plotWidth, y + ROW / 2 - 1); g.stroke();
        return;
      }
      g.fillStyle = cssVar("--ink-2");
      let text = row.label;
      while (text.length > 4 && g.measureText(text).width > LABEL_W - 16) text = text.slice(0, -2);
      g.fillText(text === row.label ? text : text + "…", 12, y);
      const c = color(row.kind);
      g.save();
      g.beginPath(); g.rect(left, y - ROW / 2, plotWidth, ROW); g.clip();
      for (const e of row.events) {
        const x0 = x(e.t0);
        const open = e.t1 === null && row.kind === "condition";
        const x1 = e.t1 ? x(e.t1) : open ? x(fullRange.max) : x0;
        g.fillStyle = c;
        g.globalAlpha = open ? 0.45 : 0.9;
        if (x1 - x0 > 3) {
          g.fillRect(x0, y - 4, x1 - x0, 8);
        } else {
          g.beginPath(); g.arc(x0, y, 3, 0, 2 * Math.PI); g.fill();
        }
        hits.push({ x0: x0 - 3, x1: Math.max(x1, x0) + 3, y0: y - 6, y1: y + 6, node: e.node });
      }
      g.restore();
      g.globalAlpha = 1;
    });
  }
  function hitAt(ev) {
    const box = byId("lanes").getBoundingClientRect();
    const px = ev.clientX - box.left;
    const py = ev.clientY - box.top;
    const header = groupHeaders.find((r) => py >= r.y0 && py < r.y1 && px < LABEL_W);
    if (header) return { header };
    let best = null;
    for (const hit of hits) {
      if (px >= hit.x0 && px <= hit.x1 && py >= hit.y0 && py <= hit.y1) {
        const mid = (hit.x0 + hit.x1) / 2;
        if (!best || Math.abs(mid - px) < Math.abs((best.x0 + best.x1) / 2 - px)) best = hit;
      }
    }
    return best ? { node: best.node } : null;
  }
  const lanesCanvas = byId("lanes");
  lanesCanvas.addEventListener("mousemove", (ev) => {
    const hit = hitAt(ev);
    lanesCanvas.style.cursor = hit ? "pointer" : "default";
    if (hit && hit.node) {
      const r = D.records[hit.node];
      tooltip.textContent = `${r.label} · ${when(r)}${r.status ? ` · ${r.status}` : ""}`;
      tooltip.style.left = `${ev.clientX + 12}px`;
      tooltip.style.top = `${ev.clientY + 12}px`;
      tooltip.hidden = false;
    } else {
      tooltip.hidden = true;
    }
  });
  lanesCanvas.addEventListener("mouseleave", () => { tooltip.hidden = true; });
  lanesCanvas.addEventListener("click", (ev) => {
    const hit = hitAt(ev);
    if (hit && hit.header) { expanded[hit.header.kind] = !expanded[hit.header.kind]; drawLanes(); }
    else if (hit && hit.node) showRecord(hit.node, false);
  });
  byId("reset-zoom").addEventListener("click", () => setRange(fullRange.min, fullRange.max));

  // --- data notes -----------------------------------------------------------------------
  function renderNotes() {
    const notes = byId("notes");
    notes.replaceChildren();
    if (!D.notes.length) notes.append(h("li", { class: "muted" }, t("notes.none")));
    for (const note of D.notes) {
      notes.append(h("li", {}, note.text,
        note.nodes.length ? h("button", { type: "button", onclick: () => showList(note.text, note.nodes) }, t("notes.show")) : null));
    }
    const issues = byId("issues");
    issues.replaceChildren();
    if (!D.issues.length) issues.append(h("li", { class: "muted" }, t("issues.none")));
    for (const issue of D.issues) issues.append(h("li", {}, issue));
  }

  // --- follow-up flags, clinician view (medgraph.rules) ---------------------------------
  function sourceDetails(ids) {
    return ids.map((id) => {
      const src = (D.rule_sources || {})[id];
      return src ? h("details", {}, h("summary", {}, src.citation), h("div", { class: "quote" }, `“${src.quote}”`),
        src.statement ? showSourceLink(src.statement) : null) : null;
    });
  }
  function evidenceList(evidence, open = (n) => showRecord(n, false)) {
    return h("ul", {}, evidence.map((e) => h("li", {},
      h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); open(e.node); } }, `${fd(e.date)}: ${e.label}`),
      e.value ? ` · ${e.value}` : "",
      e.note ? h("div", { class: "muted" }, e.note) : null)));
  }
  // Explanations written once by scripts/explain_flags.py and checked sentence by sentence.
  const VARIANT_NAMES = {
    "clinician-en": "Clinician · English",
    "personal-en": "For the patient · English",
    "clinician-it": "Clinico · Italiano",
    "personal-it": "Per il paziente · Italiano",
  };
  function citeChip(x, id) {
    const item = x.items[id];
    if (!item) return null;
    let title = item.text || id;
    let action = null;
    if (item.kind === "evidence" && item.node && D.records[item.node]) action = () => showRecord(item.node, false);
    if (item.kind === "statement") {
      title = item.where || title;
      const target = K && K.quotes[`statement:${item.ref}`];
      if (target) action = () => selectConcept(target);
    }
    if (item.kind === "passage") {
      title = `${item.where} (passage ${item.ref})`;
      action = () => showPassage(item);
    }
    return h("button", { type: "button", class: "cite", title, disabled: !action, onclick: action || null }, id);
  }
  const variantChoice = {};
  function explanationBlock(rule) {
    const x = X[rule];
    if (!x) return null;
    const keys = Object.keys(VARIANT_NAMES).filter((k) => x.variants[k]);
    if (!keys.length) return null;
    const select = h("select", { "aria-label": t("x.select") }, keys.map((k) => h("option", { value: k, lang: k.endsWith("-it") ? "it" : "en" }, VARIANT_NAMES[k])));
    const preferred = variantChoice[rule] || `clinician-${state.lang}`;
    if (keys.includes(preferred)) select.value = preferred;
    const body = h("div", {});
    function render() {
      variantChoice[rule] = select.value;
      const v = x.variants[select.value];
      body.replaceChildren(
        h("p", { class: "muted" }, v.origin === "model"
          ? t("x.model", { model: v.model, prompt: v.prompt_version })
          : v.fallback ? t("x.template.why", { why: v.fallback }) : t("x.template")),
        h("ol", { class: "explanation", lang: select.value.endsWith("-it") ? "it" : "en" },
          v.sentences.map((s) => h("li", {}, s.text, " ", s.cites.map((c) => citeChip(x, c))))));
    }
    select.addEventListener("change", render);
    render();
    let note = null;
    if (x.note_checked) {
      note = h("p", { class: "muted" }, x.note
        ? t("x.note.found", { date: fd(x.note.date), quote: x.note.quote, stance: x.note.stance })
        : x.notes_read ? t("x.note.none", { n: x.notes_read }) : t("x.note.empty"));
    }
    return h("details", { class: "explain" }, h("summary", {}, t("x.title")), h("div", { class: "toolbar" }, select), body, note);
  }
  function flagCard(f, plain, idPrefix, i) {
    const it = state.lang === "it" && plain;
    return h("div", { class: "flag", id: `${idPrefix}-${i}` },
      h("h3", {}, it ? plain.it.title : f.title),
      it ? [h("p", { lang: "it" }, plain.it.shows), h("p", { lang: "it" }, plain.it.why)] : null,
      it ? h("div", { class: "muted" }, t("fl.rule")) : null,
      h("p", { lang: "en" }, f.statement),
      idPrefix === "flag" ? explanationBlock(f.rule) : null,
      f.evidence.length ? [h("div", { class: "muted" }, t("fl.evidence")), evidenceList(f.evidence)] : null,
      sourceDetails(f.sources),
      f.limitations.length ? h("p", { class: "lim" }, t("fl.limits", { text: f.limitations.join(" ") })) : null);
  }
  function renderFlagsPanel() {
    const panel = byId("flags-panel");
    if (!R) { panel.hidden = true; return; }
    panel.hidden = false;
    const flagsList = R.flags || [];
    const head = [
      h("h2", {}, flagsList.length ? t("fl.title.n", { n: flagsList.length }) : t("fl.title")),
      h("p", { class: "muted" }, t("fl.asof", { date: fd(R.as_of), ruleset: R.ruleset })),
    ];
    if (!R.evaluated) head.push(h("p", {}, R.note || t("fl.notevaluated")));
    else if (!flagsList.length) head.push(h("p", {}, t("fl.none")));
    const summary = flagsList.length ? h("table", { class: "checks summary" },
      h("thead", {}, h("tr", {}, h("th", {}, t("fl.table.flag")), h("th", {}, t("fl.table.evidence")), h("th", {}, t("fl.table.sources")))),
      h("tbody", {}, flagsList.map((f, i) => h("tr", {},
        h("td", {}, h("a", { href: `#flag-${i}`, onclick: (ev) => { ev.preventDefault(); byId(`flag-${i}`).scrollIntoView({ block: "start" }); } },
          state.lang === "it" && P.flags[i] ? P.flags[i].it.title : f.title)),
        h("td", {}, f.evidence.map((e) => `${fd(e.date)}: ${e.label}${e.value ? ` ${e.value}` : ""}`).join("; ")),
        h("td", {}, f.sources.map((s) => h("div", { class: "mono", title: ((D.rule_sources || {})[s] || {}).citation || s }, s))))))) : null;
    const cards = flagsList.map((f, i) => flagCard(f, P.flags[i], "flag", i));
    const heldBack = R.unchecked || [];
    const unreadRecords = R.unread || [];
    const unchecked = heldBack.length ? h("div", { class: "flag unchecked" },
      h("h3", {}, t("fl.unchecked.title", { n: heldBack.length })),
      h("p", { class: "muted" }, t("fl.unchecked.intro")),
      h("ul", {}, heldBack.map((u) => h("li", {}, h("span", { class: "mono" }, u.rule), ` · ${u.topics.join(", ")}`))),
      h("table", { class: "checks" },
        h("thead", {}, h("tr", {}, h("th", {}, t("fl.unchecked.record")), h("th", {}, t("fl.unchecked.code")), h("th", {}, t("fl.unchecked.why")))),
        h("tbody", {}, unreadRecords.map((r, i) => h("tr", {},
          h("td", { lang: "en" }, D.records[r.node]
            ? h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); showRecord(r.node, false); } }, r.date ? `${fd(r.date)}: ${r.label}` : r.label)
            : r.label),
          h("td", { class: "mono" }, r.code || ""),
          h("td", { lang: state.lang }, state.lang === "it" && P.unchecked ? P.unchecked.records[i].why.it : r.why)))))) : null;
    const notesList = R.notes || [];
    const notes = notesList.length ? h("details", { class: "notes" },
      h("summary", {}, t("fl.labelnotes", { n: notesList.length })),
      h("p", { class: "muted" }, t("fl.labelnotes.intro")),
      notesList.map((n, i) => {
        const card = flagCard(n, P.notes[i], "note", i);
        card.classList.add("note");
        return card;
      })) : null;
    const rows = (R.assessments || []).map((a) => h("tr", {},
      h("td", {}, S.en[`rule.${a.rule}`] ? t(`rule.${a.rule}`) : a.rule),
      h("td", { class: `status-${a.status}` }, t(`st.${a.status}`)),
      h("td", { lang: "en" }, a.summary,
        a.reason ? h("div", { class: "muted" }, a.reason) : null,
        a.evidence.length ? h("details", {}, h("summary", {}, t("fl.evidence.n", { n: a.evidence.length })), evidenceList(a.evidence)) : null,
        a.sources.length ? h("details", {}, h("summary", {}, t("fl.sources")), sourceDetails(a.sources)) : null,
        a.limitations.length ? h("div", { class: "lim" }, t("fl.limits", { text: a.limitations.join(" ") })) : null)));
    const checked = R.evaluated ? h("details", {},
      h("summary", {}, t("fl.checked")),
      h("p", { class: "muted" }, t("fl.checked.intro")),
      h("table", { class: "checks" }, h("tbody", {}, rows))) : null;
    panel.replaceChildren(...[...head, unchecked, summary, ...cards, notes, checked].filter(Boolean));
  }

  // --- personal view --------------------------------------------------------------------
  function basisBlock(f, text) {
    const src = (D.rule_sources || {})[text.source];
    return h("details", { class: "basis" },
      h("summary", {}, t("p.basis")),
      f.evidence.length ? [h("h4", {}, t("p.basis.values")), h("ul", { class: "values" }, f.evidence.map((e) => h("li", {},
        h("span", { class: "v-label", lang: "en" }, e.label), e.value ? h("span", { class: "v-value" }, e.value) : null,
        h("span", { class: "v-date" }, pd(e.date)),
        D.records[e.node] ? h("a", { href: "#", class: "v-open", onclick: (ev) => { ev.preventDefault(); openRecord(e.node); } }, t("p.basis.open")) : null)))] : null,
      src ? [h("h4", {}, t("p.basis.source")), h("p", { class: "citation", lang: "en" }, src.citation), h("blockquote", { lang: "en" }, src.quote),
        src.statement ? showSourceLink(src.statement) : null] : null,
      h("h4", {}, t("p.basis.limit")), h("p", {}, text.limit),
      state.lang === "it" ? h("p", { class: "muted" }, t("p.basis.english")) : null,
      h("p", {}, h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); setView("clinician"); } }, t("p.basis.more"))));
  }
  // A fallback item (no plain texts for its rule) shows the rule's own English text.
  function personalCard(f, texts, kind) {
    const text = texts[state.lang];
    return h("article", { class: `p-card finding ${kind}`, lang: texts.fallback ? "en" : null },
      h("h3", {}, text.title),
      h("p", { class: "shows" }, text.shows),
      text.why ? h("p", { class: "why" }, h("span", { class: "why-label" }, `${t("p.why")}: `), text.why) : null,
      text.closing ? h("p", { class: "closing" }, text.closing) : null,
      basisBlock(f, text));
  }
  // Checks held back by entries the rules cannot read (rules/unread.py): no result for them.
  function uncheckedCard(u) {
    const text = u[state.lang];
    return h("article", { class: "p-card unchecked" },
      h("h3", {}, text.title),
      h("p", { class: "shows" }, text.shows),
      h("ul", { class: "plain-list" }, u.checks.map((c) => h("li", {}, c[state.lang]))),
      h("details", { class: "basis" },
        h("summary", {}, t("p.unchecked.records")),
        h("ul", { class: "values" }, u.records.map((r) => h("li", {},
          h("span", { class: "v-label", lang: "en" }, r.label),
          r.date ? h("span", { class: "v-date" }, pd(r.date)) : null,
          D.records[r.node] ? h("a", { href: "#", class: "v-open", onclick: (ev) => { ev.preventDefault(); openRecord(r.node); } }, t("p.basis.open")) : null,
          h("div", { class: "muted small" }, r.why[state.lang])))),
        h("h4", {}, t("p.basis.limit")), h("p", {}, text.limit),
        state.lang === "it" ? h("p", { class: "muted" }, t("p.basis.english")) : null),
      h("p", { class: "closing" }, text.closing));
  }
  function renderPersonalFlags() {
    const box = byId("p-flags");
    const flags = R && R.evaluated ? R.flags || [] : [];
    const notes = R && R.evaluated ? R.notes || [] : [];
    const U = R && R.evaluated ? P.unchecked : null;
    byId("p-flags-title").textContent = flags.length ? `${t("p.flags.title")} (${flags.length})` : t("p.flags.title");
    if (!R) { box.replaceChildren(h("p", { class: "muted" }, t("p.flags.notrun"))); return; }
    if (!R.evaluated) { box.replaceChildren(h("p", {}, t("p.flags.notevaluated", { why: R.note || "" }))); return; }
    fill(box,
      U ? uncheckedCard(U) : null,
      flags.length ? h("p", { class: "muted" }, t("p.flags.intro")) : h("p", { class: "all-clear" }, t(U ? "p.flags.none.partial" : "p.flags.none")),
      flags.map((f, i) => personalCard(f, P.flags[i], "flag")),
      notes.length ? [h("h3", { class: "p-sub" }, `${t("p.notes.title")} (${notes.length})`), h("p", { class: "muted" }, t("p.notes.intro")),
        notes.map((n, i) => personalCard(n, P.notes[i], "note"))] : null);
  }
  // Results over time: the eGFR medgraph computes (the one the rules use) or, without it, the
  // recorded one; haemoglobin; urine albumin. Dashed lines: thresholds the rules applied.
  const personalCharts = [];
  function personalSeries() {
    const by = Object.fromEntries(D.series.map((s) => [s.analyte, s]));
    return [
      [by["egfr-computed"] || by.egfr, "egfr"],
      [by.hemoglobin, "hemoglobin"],
      [by.urine_acr, "urine_acr"],
    ].filter(([s]) => s && s.points.length);
  }
  function renderPersonalCharts() {
    for (const u of personalCharts.splice(0)) u.destroy();
    const box = byId("p-results");
    box.replaceChildren();
    const series = personalSeries();
    if (!series.length) { box.append(h("p", { class: "muted" }, t("p.results.none"))); return; }
    for (const [s, key] of series) {
      const last = s.points[s.points.length - 1];
      const lines = P.lines.filter((l) => l.analyte === key && l.unit === s.unit);
      const sub = key === "egfr" ? t(s.computed ? "chart.egfr.computed" : "chart.egfr.recorded") : null;
      const holder = h("div", { class: "p-chart" });
      box.append(h("article", { class: "p-card result" },
        h("div", { class: "result-head" },
          h("div", {}, h("h3", {}, t(`chart.${key}`)), sub ? h("div", { class: "muted small" }, sub) : null),
          h("div", { class: "latest" }, h("span", { class: "big" }, `${last.cmp || ""}${last.value}`), " ", h("span", { class: "unit" }, unitShown(s.unit)),
            h("div", { class: "muted small" }, pd(isoDate(last.t))))),
        holder,
        lines.length ? h("ul", { class: "line-key" }, lines.map((l) => h("li", {}, h("span", { class: "dash", "aria-hidden": "true" }), l.label[state.lang]))) : null,
        h("div", { class: "muted small" }, t("p.results.count", { n: s.points.length }))));
      const xs = s.points.map((pt) => pt.t);
      const ys = s.points.map((pt) => pt.v);
      const accent = s.computed ? cssVar("--computed") : cssVar("--accent");
      const ink = cssVar("--ink-2");
      const grid = { stroke: cssVar("--line"), width: 1 };
      const u = new uPlot({
        width: Math.max(holder.clientWidth, 240),
        height: 150,
        padding: [10, 10, 0, 0],
        legend: { show: false },
        cursor: { show: false },
        fmtDate: (tpl) => uPlot.fmtDate(tpl, uplotNames()),
        scales: { x: { time: true } },
        axes: [
          { stroke: ink, grid: { show: false }, ticks: grid, size: 26, font: `11px ${cssVar("--font")}` },
          { stroke: ink, grid, ticks: { show: false }, size: 44, font: `11px ${cssVar("--font")}` },
        ],
        series: [
          {},
          { stroke: accent, width: 2, spanGaps: true, points: { show: true, size: 6, fill: accent } },
          ...lines.map(() => ({ stroke: cssVar("--muted"), width: 1.5, dash: [5, 5], points: { show: false } })),
        ],
      }, [xs, ys, ...lines.map((l) => xs.map(() => l.value))], holder);
      personalCharts.push(u);
    }
  }
  function conceptList(kind, key) {
    const items = D.concepts.filter((c) => c.kind === kind);
    const current = items.filter((c) => c.active);
    const past = items.filter((c) => !c.active).sort((a, b) => (b.last || "").localeCompare(a.last || ""));
    const label = (c) => c.label.replace(SEMANTIC_TAG, "");
    return [
      current.length ? h("ul", { class: "plain-list", lang: "en" }, current.map((c) => h("li", {}, label(c),
        c.first ? h("span", { class: "muted small", lang: state.lang }, ` · ${t("p.since", { date: pd(c.first) })}`) : null)))
        : h("p", { class: "muted" }, t(`${key}.none`)),
      past.length ? h("details", {}, h("summary", {}, t(`${key}.past`, { n: past.length })),
        h("ul", { class: "plain-list", lang: "en" }, past.map((c) => h("li", {}, label(c),
          c.last ? h("span", { class: "muted small", lang: state.lang }, ` · ${t("p.last", { date: pd(c.last) })}`) : null)))) : null,
    ];
  }
  function renderPersonalLists() {
    fill(byId("p-meds"), conceptList("medication", "p.meds"));
    fill(byId("p-conditions"), conceptList("condition", "p.conditions"));
  }
  function renderPersonal() {
    renderPersonalFlags();
    renderPersonalLists();
    if (state.view === "personal") renderPersonalCharts();
  }

  // --- print for my doctor --------------------------------------------------------------
  function printSheet() {
    const sheet = byId("print-sheet");
    const flags = R && R.evaluated ? R.flags || [] : [];
    const notes = R && R.evaluated ? R.notes || [] : [];
    const U = R && R.evaluated ? P.unchecked : null;
    const item = (f, texts) => {
      const text = texts[state.lang];
      const src = (D.rule_sources || {})[text.source];
      return h("section", { class: "print-item" },
        h("h2", {}, text.title),
        h("p", {}, text.shows),
        text.why ? h("p", {}, h("strong", {}, `${t("p.why")}: `), text.why) : null,
        src ? h("p", { class: "citation", lang: "en" }, src.citation) : null,
        f.evidence.length ? h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, t("print.col.what")), h("th", {}, t("print.col.value")), h("th", {}, t("print.col.date")))),
          h("tbody", {}, f.evidence.map((e) => h("tr", {}, h("td", { lang: "en" }, e.label), h("td", {}, e.value || ""), h("td", {}, pd(e.date)))))) : null,
        text.limit ? h("p", { class: "small" }, h("strong", {}, `${t("p.basis.limit")}: `), text.limit) : null,
        text.closing ? h("p", {}, h("em", {}, text.closing)) : null);
    };
    const today = new Date().toISOString().slice(0, 10);
    fill(sheet,
      h("p", { class: "brand" }, "medgraph"),
      h("h1", {}, t("print.title")),
      h("p", {}, byId("patient-line").textContent),
      h("p", { class: "banner" }, byId("banner").textContent),
      h("p", {}, t("print.intro")),
      R ? h("p", { class: "small" }, t("print.asof", { date: pd(R.as_of), ruleset: R.ruleset, printed: pd(today) })) : null,
      U ? h("section", { class: "print-unchecked" },
        h("h2", {}, U[state.lang].title),
        h("p", {}, U[state.lang].shows),
        h("ul", {}, U.checks.map((c) => h("li", {}, c[state.lang]))),
        h("table", {},
          h("thead", {}, h("tr", {}, h("th", {}, t("print.col.what")), h("th", {}, t("print.col.date")), h("th", {}, t("fl.unchecked.why")))),
          h("tbody", {}, U.records.map((r) => h("tr", {}, h("td", { lang: "en" }, r.label), h("td", {}, r.date ? pd(r.date) : ""), h("td", {}, r.why[state.lang]))))),
        h("p", { class: "small" }, h("strong", {}, `${t("p.basis.limit")}: `), U[state.lang].limit),
        h("p", {}, h("em", {}, U[state.lang].closing))) : null,
      flags.length ? flags.map((f, i) => item(f, P.flags[i])) : h("p", {}, t(U ? "print.none.partial" : "print.none")),
      notes.length ? [h("h2", { class: "print-sub" }, t("p.notes.title")), notes.map((n, i) => item(n, P.notes[i]))] : null,
      h("p", { class: "small" }, t("footer")));
    if (typeof window.print === "function") {
      try { window.print(); } catch (e) { /* no printing here */ }
    }
  }

  // --- switching ------------------------------------------------------------------------
  function setExplore(on) {
    state.explore = on;
    document.body.classList.toggle("explore", on);
    applyStatic();
    if (on) {
      ensureGraph();
      relayout();
    }
  }
  function setView(view) {
    if (!VIEWS.includes(view)) return;
    state.view = view;
    saveState();
    applyView();
    window.scrollTo(0, 0);
  }
  function applyView() {
    document.body.classList.toggle("view-personal", state.view === "personal");
    document.body.classList.toggle("view-clinician", state.view === "clinician");
    document.body.classList.toggle("explore", state.explore);
    applyStatic();
    renderHeader();
    if (state.view === "clinician") {
      ensureGraph();
      relayout();
      ensureTimeline();
    } else {
      renderPersonalCharts();
      if (state.explore) { ensureGraph(); relayout(); }
    }
  }
  function setLang(lang) {
    if (!S[lang]) return;
    state.lang = lang;
    saveState();
    applyStatic();
    renderHeader();
    renderPersonal();
    renderFlagsPanel();
    renderNotes();
    redo();
    if (cy) renderToggles();
    if (timelineReady) rebuildCharts();
  }
  // uPlot formats axis dates with the month names it was built with: rebuild on a switch.
  function rebuildCharts() {
    for (const u of charts.splice(0)) u.destroy();
    chartLabels.length = 0;
    byId("charts").replaceChildren();
    if (D.series.length) D.series.forEach(makeChart);
    else byId("charts").append(h("p", { class: "muted" }, t("tl.none")));
    measurePlot();
    setRange(range.min, range.max);
  }
  for (const b of document.querySelectorAll("[data-view]")) b.addEventListener("click", () => setView(b.dataset.view));
  for (const b of document.querySelectorAll("[data-lang]")) b.addEventListener("click", () => setLang(b.dataset.lang));
  byId("explore").addEventListener("click", () => setExplore(!state.explore));
  byId("print").addEventListener("click", printSheet);
  let resizeTimer = null;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => { if (state.view === "personal") renderPersonalCharts(); }, 150);
  });

  applyStatic();
  renderHeader();
  renderNotes();
  renderFlagsPanel();
  renderPersonalFlags();
  renderPersonalLists();
  intro();
  applyView();
})();
