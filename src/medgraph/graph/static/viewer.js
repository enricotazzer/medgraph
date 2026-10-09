"use strict";
// medgraph patient view. Record text reaches the page only through textContent.
(function () {
  const D = JSON.parse(document.getElementById("medgraph-data").textContent);
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

  const KINDS = [
    ["condition", "Conditions", true],
    ["medication", "Medications", true],
    ["procedure", "Procedures", true],
    ["analyte", "Lab analytes", true],
    ["lab_report", "Lab reports", true],
    ["observation", "Other observations", false],
    ["encounter", "Encounters", false],
    ["note", "Notes", false],
  ];
  const KIND_OF_RECORD = { medication_request: "medication", lab_result: "analyte", report_row: "lab_report" };
  const color = (kind) => cssVar(`--k-${KIND_OF_RECORD[kind] || kind}`) || "#888";
  const shown = Object.fromEntries(KINDS.map(([k, , on]) => [k, on]));
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
  const isoDate = (t) => new Date(t * 1000).toISOString().slice(0, 10);
  const shorten = (text, n) => (text.length > n ? text.slice(0, n - 1) + "…" : text);
  // SNOMED CT's semantic tag, e.g. "(disorder)": dropped from graph labels only.
  const SEMANTIC_TAG = /\s*\((?:disorder|finding|procedure|situation|physical object|regime\/therapy|observable entity)\)$/;

  // --- header ---------------------------------------------------------------------------
  const p = D.patient;
  byId("patient-line").textContent = [
    `Patient ${p.id.slice(0, 8)}`,
    p.gender,
    p.birth_date ? `born ${p.birth_date.date}` : null,
    p.deceased ? `died ${p.deceased.date}` : null,
    `${Object.keys(D.records).length.toLocaleString("en")} records from ${D.sources.length} source document(s)`,
  ].filter(Boolean).join(" · ");
  byId("banner").textContent = D.banner || "";

  // --- record summaries -----------------------------------------------------------------
  function valueText(r) {
    if (r.value !== undefined) return `${r.comparator || ""}${r.value} ${r.unit || ""}`.trim();
    if (r.text) return shorten(r.text, 90);
    return "";
  }
  function when(r) {
    if (!r.start) return "undated";
    const start = r.start.date;
    const end = r.end && r.end.date !== start ? ` to ${r.end.date}` : "";
    return start + end;
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
      h("thead", {}, h("tr", {}, h("th", {}, "date"), h("th", {}, "value or text"), h("th", {}, "status"))),
      h("tbody", {}, body));
    const more = nodes.length > limit ? h("p", { class: "muted" }, `and ${nodes.length - limit} more`) : null;
    return [table, more];
  }

  // --- details panel --------------------------------------------------------------------
  const details = byId("details");
  let lastConcept = null;
  function show(...children) {
    details.replaceChildren(...children.flat(Infinity).filter(Boolean));
    details.scrollTop = 0;
  }
  function intro() {
    show(h("h2", {}, "Details"),
      h("p", { class: "muted" }, "Select a node or a link in the graph, a value on a chart or an event on the timeline."),
      h("p", { class: "muted" }, "Links in the graph come from the data (an encounter or reason reference in the record) or from a cited guideline; each one says which."));
  }
  function linkItem(l, from) {
    const other = concepts.get(l.source === from ? l.target : l.source);
    const arrow = l.source === from ? `${l.kind} →` : `← ${l.kind}`;
    return h("li", {},
      h("span", { class: "kind" }, arrow), " ",
      h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); selectConcept(other.id); } }, other.label),
      l.count > 1 ? ` (${l.count} records)` : "",
      h("div", { class: "muted" }, `basis: ${l.basis}`),
      l.citation ? h("div", { class: "muted" }, l.citation) : null,
      l.detail && l.kind === "monitored_by" ? h("div", { class: "quote" }, `“${l.detail}”`) : null);
  }
  function showConcept(id) {
    const c = concepts.get(id);
    lastConcept = id;
    const links = D.links.filter((l) => l.source === id || l.target === id)
      .filter((l) => l.kind !== "occurred_during" || c.kind === "encounter");
    const series = D.series.find((s) => `analyte:${s.analyte}` === id);
    const head = D.records[id];
    show(
      h("span", { class: "kind" }, c.kind.replace("_", " ")),
      h("h2", {}, c.label),
      h("dl", {},
        c.code ? [h("dt", {}, "code"), h("dd", { class: "mono" }, c.code)] : null,
        [h("dt", {}, "records"), h("dd", {}, String(c.count))],
        c.first ? [h("dt", {}, "first / last"), h("dd", {}, `${c.first} / ${c.last}`)] : null,
        c.kind === "condition" ? [h("dt", {}, "clinical status"), h("dd", {}, c.active ? "active in at least one record" : "not active")] : null,
        series ? [h("dt", {}, "usable values"), h("dd", {}, `${series.points.length} in the series (${series.unit})`)] : null,
        head && head.text && c.kind === "lab_report" ? [h("dt", {}, "conventions"), h("dd", {}, head.text)] : null,
        head && head.detail && c.kind !== "analyte" ? [h("dt", {}, "issues"), h("dd", {}, head.detail)] : null,
        head && head.kind === "lab_report" ? [h("dt", {}, "collection date"), h("dd", {}, head.start ? head.start.date : "not read")] : null),
      links.length ? [h("h3", {}, "Links"), h("ul", { class: "links" }, links.map((l) => linkItem(l, id)))] : null,
      c.records.length ? [h("h3", {}, "Records"), recordsTable(c.records)] : null);
  }
  function sourceItem(s) {
    return h("li", { class: "mono", title: s.source }, `${s.resource_type}/${s.resource_id} in ${s.source.slice(0, 19)}…`);
  }
  function showRecord(n, fromConcept) {
    const r = D.records[n];
    const o = r.original;
    const rows = [
      ["date", when(r)],
      ["status", r.status],
      ["usable by rules", r.usable === undefined ? null : r.usable ? "yes" : "no"],
      ["value", r.value !== undefined ? `${r.comparator || ""}${r.value} ${r.unit || ""}` : null],
      ["as recorded", o ? `${o.comparator || ""}${o.value} ${o.unit || o.code || ""}` : null],
      ["method", r.method],
      ["analyte", r.analyte],
      ["category", r.category],
      ["code", r.code],
      ["why not usable", r.usable === false ? r.detail : null],
      ["issues", r.usable !== false ? r.detail : null],
    ].filter(([, v]) => v !== null && v !== undefined && v !== "");
    const links = (edgesOf.get(n) || []).map((e) => {
      const outgoing = e[0] === n;
      const other = outgoing ? e[1] : e[0];
      const target = D.records[other];
      return h("li", {},
        h("span", { class: "kind" }, outgoing ? `${e[2]} →` : `← ${e[2]}`), " ",
        h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); showRecord(other, false); } },
          `${target.label}${target.start ? ` (${target.start.date})` : ""}`),
        h("div", { class: "muted" }, `basis: ${e[3]}`),
        e[4] ? h("div", { class: "muted" }, e[4]) : null,
        e[5] ? h("div", { class: e[2] === "monitored_by" ? "quote" : "muted" }, e[5]) : null);
    });
    const concept = conceptOf.get(n) || (concepts.has(n) ? n : null);
    show(
      fromConcept && lastConcept ? h("button", { class: "back", type: "button", onclick: () => showConcept(lastConcept) }, "← back to the concept") : null,
      h("span", { class: "kind" }, r.kind.replace("_", " ")),
      h("h2", {}, r.label),
      h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", { class: k === "code" ? "mono" : (k === "why not usable" ? "unusable" : null) }, v)])),
      r.text && r.kind !== "lab_report" ? [h("h3", {}, r.kind === "report_row" ? "As printed" : r.kind === "note" ? "Note text" : "Value"), h("div", { class: "note-text" }, r.text)] : null,
      [h("h3", {}, "Sources"), h("ul", { class: "links" }, (r.sources || []).map(sourceItem))],
      links.length ? [h("h3", {}, "Links"), h("ul", { class: "links" }, links)] : null,
      concept && concept !== n ? h("p", {}, h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); selectConcept(concept); } }, "Show its concept in the graph")) : null);
  }
  function showList(title, nodes) {
    lastConcept = null;
    show(h("h2", {}, title), recordsTable(nodes));
  }

  // --- graph ----------------------------------------------------------------------------
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
  const cy = cytoscape({
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
      { selector: ":selected", style: { "border-width": 3, "border-color": cssVar("--accent"), "line-color": cssVar("--accent"), "target-arrow-color": cssVar("--accent") } },
      { selector: ".dim", style: { opacity: 0.15 } },
    ],
    layout: { name: "preset" },
  });
  function visible(n) {
    if (!shown[n.data("kind")]) return false;
    if (showUnlinked || n.hasClass("found")) return true;
    return n.connectedEdges().some((e) => shown[e.source().data("kind")] && shown[e.target().data("kind")]);
  }
  // Columns read left to right: what was measured, what is wrong, what treats it. Within a
  // column, nodes sit near the concepts they link to (mean row of their neighbours).
  const COLUMNS = ["lab_report", "analyte", "condition", "medication", "procedure", "observation", "encounter", "note"];
  const COLUMN_WIDTH = 250;
  let layoutName = "columns";
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
    const order = ["condition", "medication", "procedure", "analyte", "lab_report", "observation", "encounter", "note"];
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
    const row = Math.max(26, Math.min(64, ((box.height / box.width) * width) / longest));
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
  cy.on("tap", "node", (ev) => { showConcept(ev.target.id()); });
  cy.on("tap", "edge", (ev) => {
    const d = ev.target.data();
    const link = D.links.find((l) => l.kind === d.kind && l.source === d.source && l.target === d.target);
    const nodes = D.edges.filter((e) => e[2] === d.kind && (conceptOf.get(e[0]) || e[0]) === d.source && (conceptOf.get(e[1]) || e[1]) === d.target).map((e) => e[0]);
    lastConcept = null;
    show(h("span", { class: "kind" }, d.kind), h("h2", {}, `${concepts.get(d.source).label} → ${concepts.get(d.target).label}`),
      h("ul", { class: "links" }, linkItem(link, d.source)), h("h3", {}, "Records behind this link"), recordsTable([...new Set(nodes)]));
  });

  const toggles = byId("kind-toggles");
  const present = new Set(D.concepts.map((c) => c.kind));
  for (const [kind, label] of KINDS) {
    if (!present.has(kind)) continue;
    const box = h("input", { type: "checkbox", value: kind, checked: shown[kind] });
    box.addEventListener("change", () => { shown[kind] = box.checked; relayout(); });
    toggles.append(h("label", {}, box, h("span", { class: "swatch", style: `background:${color(kind)}` }), label));
  }
  const unlinked = h("input", { type: "checkbox" });
  unlinked.addEventListener("change", () => { showUnlinked = unlinked.checked; relayout(); });
  toggles.append(h("label", { title: "Concepts with no link to another shown concept; all of them are on the timeline" }, unlinked, "Show unlinked concepts"));
  const layoutSwitch = h("select", { "aria-label": "Graph layout" },
    h("option", { value: "columns" }, "Columns: measured · condition · treatment"),
    h("option", { value: "network" }, "Network (force-directed)"));
  layoutSwitch.addEventListener("change", () => { layoutName = layoutSwitch.value; relayout(); });
  toggles.append(h("label", {}, "Layout ", layoutSwitch));
  byId("search").addEventListener("input", (ev) => {
    const q = ev.target.value.trim().toLowerCase();
    cy.batch(() => {
      cy.elements().removeClass("dim");
      if (!q) return;
      const hits = cy.nodes().filter((n) => concepts.get(n.id()).label.toLowerCase().includes(q));
      cy.elements().not(hits).addClass("dim");
      if (hits.length === 1) selectConcept(hits[0].id());
      else if (!hits.length) show(h("h2", {}, "Details"), h("p", { class: "muted" }, `No concept matches “${q}”.`));
    });
  });
  relayout();
  intro();

  // --- timeline -------------------------------------------------------------------------
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
  const tooltip = byId("tooltip");
  let plot = { left: LABEL_W + 56, width: 400 };

  function setRange(min, max) {
    range.min = min;
    range.max = max;
    for (const u of charts) u.setScale("x", { min, max });
    const whole = min <= fullRange.min && max >= fullRange.max;
    byId("range-label").textContent = `${isoDate(min)} to ${isoDate(max)}` +
      (whole ? " (all records)" : ` (records span ${isoDate(fullRange.min)} to ${isoDate(fullRange.max)})`);
    drawLanes();
  }
  byId("reset-zoom").addEventListener("click", () => setRange(fullRange.min, fullRange.max));

  function readout(u, s) {
    const i = u.cursor.idx;
    const out = u.root.parentElement.previousSibling.querySelector(".readout");
    if (i === null || i === undefined) { out.textContent = ""; return; }
    const pt = s.points[i];
    const fromReport = !s.computed && pt.node.startsWith("report_row:");
    const note = s.computed ? `  (${pt.detail})` : fromReport ? "  (report only)" : pt.rows.length ? "  (also on a report)" : "";
    out.textContent = `${isoDate(pt.t)}  ${pt.cmp || ""}${pt.value}${note}`;
  }
  function makeChart(s) {
    const label = h("div", { class: "chart-label" },
      h("div", {}, s.label),
      h("div", { class: "unit" }, `${s.unit} · ${s.points.length} value(s)${s.computed ? " · one per usable creatinine" : ""}`),
      h("div", { class: "readout" }));
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
  if (D.series.length) D.series.forEach(makeChart);
  else byId("charts").append(h("p", { class: "muted" }, "No usable lab values in this record."));
  if (charts.length) {
    const u = charts[0];
    const holderLeft = u.root.getBoundingClientRect().left - byId("lanes").getBoundingClientRect().left;
    plot = { left: holderLeft + u.bbox.left / devicePixelRatio, width: u.bbox.width / devicePixelRatio };
  }

  // Event lanes: one per concept, grouped by kind; a collapsed group shares one lane.
  const LANE_KINDS = [["condition", "Conditions", true], ["medication", "Medications", true], ["procedure", "Procedures", false], ["encounter", "Encounters", false]];
  const expanded = Object.fromEntries(LANE_KINDS.map(([k, , open]) => [k, open]));
  const eventsBy = new Map();
  for (const e of D.events) {
    if (!eventsBy.has(e.concept)) eventsBy.set(e.concept, []);
    eventsBy.get(e.concept).push(e);
  }
  let hits = [];
  let groupHeaders = [];
  function lanes() {
    const rows = [];
    for (const [kind, title] of LANE_KINDS) {
      const ids = [...eventsBy.keys()].filter((id) => concepts.get(id).kind === kind);
      if (!ids.length) continue;
      rows.push({ header: true, kind, label: `${expanded[kind] ? "▾" : "▸"} ${title} (${ids.length})` });
      if (expanded[kind]) for (const id of ids) rows.push({ kind, label: concepts.get(id).label, events: eventsBy.get(id) });
      else rows.push({ kind, label: `all ${title.toLowerCase()}`, events: ids.flatMap((id) => eventsBy.get(id)) });
    }
    return rows;
  }
  function drawLanes() {
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
    const x = (t) => left + ((t - range.min) / (range.max - range.min)) * plotWidth;
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

  new ResizeObserver(() => {
    for (const u of charts) {
      const holder = u.root.parentElement;
      u.setSize({ width: Math.max(holder.clientWidth, 200), height: 130 });
    }
    if (charts.length) {
      const u = charts[0];
      const holderLeft = u.root.getBoundingClientRect().left - byId("lanes").getBoundingClientRect().left;
      plot = { left: holderLeft + u.bbox.left / devicePixelRatio, width: u.bbox.width / devicePixelRatio };
    }
    drawLanes();
  }).observe(document.querySelector(".timeline-panel"));
  setRange(range.min, range.max);

  // --- notes ----------------------------------------------------------------------------
  const notes = byId("notes");
  if (!D.notes.length) notes.append(h("li", { class: "muted" }, "None: every value is usable and dated."));
  for (const note of D.notes) {
    notes.append(h("li", {}, note.text,
      note.nodes.length ? h("button", { type: "button", onclick: () => showList(note.text, note.nodes) }, "show") : null));
  }
  // --- follow-up flags (medgraph.rules) -------------------------------------------------
  const RULE_NAMES = {
    "ckd-criteria": "CKD criteria (KDIGO 2024)",
    "anaemia-criteria": "Anaemia, most recent haemoglobin (WHO 2024)",
    "ckd-gfr-follow-up": "GFR test within 12 months (KDIGO 2024)",
    "ckd-albuminuria-follow-up": "Albuminuria test within 12 months (KDIGO 2024)",
    "ckd-haemoglobin-follow-up": "Haemoglobin test in CKD (KDIGO 2012)",
    "metformin-egfr": "Metformin and eGFR (drug label)",
    "epoetin-haemoglobin-monthly": "Haemoglobin test on epoetin alfa (drug label)",
  };
  const STATUS_TEXT = { met: "met", not_met: "not met", not_assessable: "not assessable" };
  function sourceDetails(ids) {
    return ids.map((id) => {
      const src = (D.rule_sources || {})[id];
      return src ? h("details", {}, h("summary", {}, src.citation), h("div", { class: "quote" }, `“${src.quote}”`)) : null;
    });
  }
  function evidenceList(evidence) {
    return h("ul", {}, evidence.map((e) => h("li", {},
      h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); showRecord(e.node, false); } }, `${e.date}: ${e.label}`),
      e.value ? ` · ${e.value}` : "",
      e.note ? h("div", { class: "muted" }, e.note) : null)));
  }
  if (D.rules) {
    const r = D.rules;
    const panel = byId("flags-panel");
    panel.hidden = false;
    const flagsList = r.flags || [];
    const head = [
      h("h2", {}, flagsList.length ? `Follow-up flags (${flagsList.length})` : "Follow-up flags"),
      h("p", { class: "muted" }, `Evaluated as of ${r.as_of} (${r.ruleset}). A flag says what the record shows and cites its source; it is not a diagnosis.`),
    ];
    if (!r.evaluated) head.push(h("p", {}, r.note || "Not evaluated."));
    else if (!flagsList.length) head.push(h("p", {}, "No follow-up flags."));
    const cards = flagsList.map((f) => h("div", { class: "flag" },
      h("h3", {}, f.title),
      h("p", {}, f.statement),
      f.evidence.length ? [h("div", { class: "muted" }, "Evidence"), evidenceList(f.evidence)] : null,
      sourceDetails(f.sources),
      f.limitations.length ? h("p", { class: "lim" }, "Limitations: " + f.limitations.join(" ")) : null));
    const rows = (r.assessments || []).map((a) => h("tr", {},
      h("td", {}, RULE_NAMES[a.rule] || a.rule),
      h("td", { class: `status-${a.status}` }, STATUS_TEXT[a.status] || a.status),
      h("td", {}, a.summary,
        a.reason ? h("div", { class: "muted" }, a.reason) : null,
        a.evidence.length ? h("details", {}, h("summary", {}, `evidence (${a.evidence.length})`), evidenceList(a.evidence)) : null,
        a.sources.length ? h("details", {}, h("summary", {}, "sources"), sourceDetails(a.sources)) : null,
        a.limitations.length ? h("div", { class: "lim" }, "Limitations: " + a.limitations.join(" ")) : null)));
    const notesList = r.notes || [];
    const notes = notesList.length ? h("details", { class: "notes" },
      h("summary", {}, `Label notes (${notesList.length})`),
      h("p", { class: "muted" }, "What a drug label says about a situation this record shows. A note has no threshold or interval to check: it is not a flag."),
      notesList.map((n) => h("div", { class: "flag note" },
        h("h3", {}, n.title),
        h("p", {}, n.statement),
        n.evidence.length ? [h("div", { class: "muted" }, "Evidence"), evidenceList(n.evidence)] : null,
        sourceDetails(n.sources),
        n.limitations.length ? h("p", { class: "lim" }, "Limitations: " + n.limitations.join(" ")) : null))) : null;
    const checked = r.evaluated ? h("details", {},
      h("summary", {}, "What medgraph checked"),
      h("p", { class: "muted" }, "For the follow-up checks, met means a test is overdue."),
      h("table", { class: "checks" }, h("tbody", {}, rows))) : null;
    panel.replaceChildren(...[...head, ...cards, notes, checked].filter(Boolean));
  }

  const issues = byId("issues");
  if (!D.issues.length) issues.append(h("li", { class: "muted" }, "None."));
  for (const issue of D.issues) issues.append(h("li", {}, issue));
})();
