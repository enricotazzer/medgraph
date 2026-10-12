"use strict";
// medgraph cohort concept map. Record text reaches the page only through textContent.
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
    ["lab_report", "Filed lab reports", true],
    ["source", "Sources", true],
    ["observation", "Other observations", false],
    ["encounter", "Encounter types", false],
    ["note", "Notes", false],
  ];
  const KIND_LABEL = Object.fromEntries(KINDS.map(([k, label]) => [k, label]));
  const color = (kind) => cssVar(`--k-${kind}`) || "#888";
  const shown = Object.fromEntries(KINDS.map(([k, , on]) => [k, on]));
  const THRESHOLDS = [1, 2, 5, 10, 20, 50, 100, 200, 500];
  let minPatients = 10;
  let showUnlinked = false;

  const N = D.patients.length;
  const pages = new Set(D.pages);
  const concepts = new Map(D.concepts.map((c) => [c.id, c]));
  const linkId = (l) => `${l.kind}|${l.source}|${l.target}`;
  const links = new Map(D.links.map((l) => [linkId(l), l]));
  const linksOf = new Map();
  for (const l of D.links) {
    for (const end of [l.source, l.target]) {
      if (!linksOf.has(end)) linksOf.set(end, []);
      linksOf.get(end).push(l);
    }
  }
  const fmt = (n) => n.toLocaleString("en");
  const share = (n) => {
    const pct = (100 * n) / N;
    return `${fmt(n)} of ${fmt(N)} patients (${pct > 0 && pct < 1 ? pct.toFixed(1) : pct.toFixed(0)}%)`;
  };
  const shorten = (text, n) => (text.length > n ? text.slice(0, n - 1) + "…" : text);
  const kindName = (kind) => kind.replaceAll("_", " ");
  // SNOMED CT's semantic tag, e.g. "(disorder)": dropped from graph labels only.
  const SEMANTIC_TAG = /\s*\((?:disorder|finding|procedure|situation|physical object|regime\/therapy|observable entity)\)$/;

  // --- header ---------------------------------------------------------------------------
  byId("cohort-line").textContent = [
    `Cohort ${D.cohort}`,
    `${fmt(N)} patients (${fmt(D.deceased)} deceased)`,
    `${fmt(D.concepts.length)} concepts, ${fmt(D.links.length)} links`,
    `store digest ${D.store_digest.slice(0, 12)}…`,
  ].join(" · ");
  byId("banner").textContent = D.banner || "";

  // --- details panel --------------------------------------------------------------------
  const details = byId("details");
  function show(...children) {
    details.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
    details.scrollTop = 0;
  }

  function intro() {
    const bases = new Map(D.links.map((l) => [l.kind, l.basis]));
    show(
      h("h2", {}, "About this map"),
      h("p", {}, `Every stored patient graph of cohort ${D.cohort}, merged by concept. A concept groups records with the same code, as on a patient's page; filed lab reports have no code and form one concept.`),
      h("p", {}, "A link from A to B counts the patients whose own graph has at least one edge of that kind from a record of A to a record of B. The map adds no link that no patient graph contains, and computes no co-occurrence."),
      h("h3", {}, "What each kind of link rests on"),
      h("ul", { class: "links" }, [...bases].sort().map(([kind, basis]) => h("li", {}, h("span", { class: "kind" }, kindName(kind)), " ", basis))),
      D.knowledge ? h("p", {}, `Sources (drug labels and guidelines) are joined to drugs and conditions by code, from the knowledge graph (digest ${D.knowledge.digest.slice(0, 12)}…). A source counts the patients of the concepts linked to it.`) : null,
      h("p", { class: "muted" }, "Counts describe this cohort only. For Synthea data they follow the simulator's hand-written disease modules, not any real population."),
      h("p", { class: "muted" }, "Click a node or a link for its patients."),
    );
  }

  const SAFE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
  const LIST_LIMIT = 240;
  function patientItem(i) {
    const id = D.patients[i];
    if (pages.has(i) && SAFE_NAME.test(id)) return h("li", {}, h("a", { href: `${id}.html`, title: id }, id.slice(0, 8)));
    return h("li", { title: id }, id.slice(0, 8));
  }
  function patientList(indices) {
    const box = h("div", {});
    const withPage = indices.filter((i) => pages.has(i)).length;
    const note = h("p", { class: "hint" },
      withPage ? `${fmt(withPage)} of them have a page; their IDs are links. ` : "None of them has a page yet. ",
      `To write one: make view COHORT=${D.cohort} PATIENT=<ID or its first characters>.`);
    function render(limit) {
      const list = h("ul", { class: "patients" }, indices.slice(0, limit).map(patientItem));
      const more = indices.length > limit
        ? h("button", { type: "button", onclick: () => render(Infinity) }, `Show all ${fmt(indices.length)}`)
        : null;
      box.replaceChildren(...[note, list, more].filter(Boolean));
    }
    render(LIST_LIMIT);
    return box;
  }

  function linkItem(l, from) {
    const out = l.source === from;
    const other = concepts.get(out ? l.target : l.source);
    return h("li", {},
      h("span", { class: "kind" }, kindName(l.kind)), " ", out ? "→ " : "← ",
      h("button", { type: "button", class: "link", onclick: () => selectConcept(other.id) }, other.label),
      " · ",
      h("button", { type: "button", class: "link", onclick: () => showLink(l) }, `${fmt(l.patients.length)} patients`));
  }

  // --- sources (the knowledge graph) ------------------------------------------------------
  const K = D.knowledge || null;
  const SOURCE_KIND = { document: "Guideline document", label: "Drug label" };
  function showSource(id) {
    const c = concepts.get(id);
    const n = K.nodes[id];
    const statements = Object.keys(K.quotes).filter((s) => K.quotes[s] === id);
    const mine = (linksOf.get(id) || []).slice().sort((a, b) => b.patients.length - a.patients.length || linkId(a).localeCompare(linkId(b)));
    const rows = [
      ["Citation", n.citation],
      ["Set ID, version", n.setid ? `${n.setid}, v${n.version}` : null],
      ["Effective", n.effective],
      ["Licence", n.licence],
      ["Stored file", n.sha256 ? `SHA-256 ${n.sha256.slice(0, 12)}…` : null],
      ["Retrieved", n.retrieved ? n.retrieved.slice(0, 10) : null],
      ["From", n.url],
    ].filter(([, v]) => v);
    show(
      h("span", { class: "kind" }, SOURCE_KIND[n.kind] || n.kind),
      h("h2", {}, n.title),
      h("dl", {}, rows.map(([k, v]) => [h("dt", {}, k), h("dd", { class: k === "From" ? "mono" : null }, v)]),
        h("dt", {}, "Patients"), h("dd", {}, share(c.patients.length))),
      h("h3", {}, `Linked concepts (${mine.length})`),
      h("ul", { class: "links" }, mine.map((l) => linkItem(l, id))),
      h("h3", {}, `What medgraph quotes from it (${statements.length})`),
      statements.length ? h("ul", { class: "links" }, statements.map((sid) => {
        const st = K.nodes[sid];
        return h("li", {}, h("div", { class: "muted" }, st.locator), h("div", { class: "quote" }, `“${st.quote}”`),
          h("div", { class: "mono muted" }, `passage ${st.passages.join(", ")}`));
      })) : h("p", { class: "muted" }, "Nothing yet."),
      n.sections && n.sections.length ? h("details", {}, h("summary", {}, `Sections (${n.sections.length})`), h("ul", {}, n.sections.map((t) => h("li", {}, t)))) : null,
      h("p", { class: "muted" }, "Quotes were checked against the stored file when the knowledge graph was built. The page shows details and quotes, not the full text."),
      h("h3", {}, "Patients"),
      patientList(c.patients),
    );
  }

  function showConcept(id) {
    const c = concepts.get(id);
    if (c.kind === "source" && K && K.nodes[id]) return showSource(id);
    const mine = (linksOf.get(id) || []).slice().sort((a, b) => b.patients.length - a.patients.length || linkId(a).localeCompare(linkId(b)));
    show(
      h("span", { class: "kind" }, KIND_LABEL[c.kind] || c.kind),
      h("h2", {}, c.label),
      h("dl", {},
        c.code ? [h("dt", {}, "Code"), h("dd", { class: "mono" }, c.code)] : null,
        h("dt", {}, "Patients"), h("dd", {}, share(c.patients.length)),
        h("dt", {}, "Records"), h("dd", {}, fmt(c.records)),
        c.no_label ? [h("dt", {}, "Drug label"), h("dd", {}, `none: ${c.no_label}`)] : null),
      h("h3", {}, `Links (${mine.length})`),
      mine.length ? h("ul", { class: "links" }, mine.map((l) => linkItem(l, id))) : h("p", { class: "muted" }, "No link to another concept."),
      h("h3", {}, "Patients"),
      patientList(c.patients),
    );
  }

  function showLink(l) {
    const a = concepts.get(l.source);
    const b = concepts.get(l.target);
    show(
      h("span", { class: "kind" }, kindName(l.kind)),
      h("h2", {}, `${a.label} → ${b.label}`),
      h("dl", {},
        h("dt", {}, "Basis"), h("dd", {}, l.basis),
        l.citations.length ? [h("dt", {}, "Cited"), h("dd", {}, l.citations.join("; "))] : null,
        l.detail ? [h("dt", {}, "Detail"), h("dd", {}, l.detail)] : null,
        h("dt", {}, "Patients"), h("dd", {}, share(l.patients.length)),
        l.edges !== null ? [h("dt", {}, "Record links"), h("dd", {}, fmt(l.edges))] : null),
      h("p", {},
        h("button", { type: "button", onclick: () => selectConcept(a.id) }, shorten(a.label, 40)), " ",
        h("button", { type: "button", onclick: () => selectConcept(b.id) }, shorten(b.label, 40))),
      h("h3", {}, "Patients"),
      patientList(l.patients),
    );
  }

  // --- graph ----------------------------------------------------------------------------
  const elements = [];
  for (const c of D.concepts) {
    const n = c.patients.length;
    elements.push({ group: "nodes", data: {
      id: c.id, kind: c.kind, n, color: color(c.kind), size: 10 + 4.5 * Math.log2(1 + n),
      label: `${shorten(c.label.replace(SEMANTIC_TAG, ""), 48)} (${fmt(n)})`,
    } });
  }
  for (const l of D.links) {
    const n = l.patients.length;
    elements.push({ group: "edges", data: {
      id: linkId(l), source: l.source, target: l.target, kind: l.kind, n, width: 0.6 + 0.55 * Math.log2(1 + n),
    } });
  }
  const cy = cytoscape({
    container: byId("cy"),
    elements,
    minZoom: 0.05,
    maxZoom: 3,
    style: [
      { selector: "node", style: {
        width: "data(size)", height: "data(size)", label: "data(label)", "background-color": "data(color)",
        "font-size": 12, "font-family": cssVar("--font") || "sans-serif", color: cssVar("--ink"),
        "text-valign": "center", "text-halign": "right", "text-margin-x": 5, "text-wrap": "ellipsis", "text-max-width": 230,
        "text-background-color": cssVar("--panel"), "text-background-opacity": 0.85, "text-background-padding": 1,
        "min-zoomed-font-size": 6,
      } },
      { selector: "node.network", style: { "text-valign": "bottom", "text-halign": "center", "text-margin-x": 0, "text-margin-y": 3, "text-max-width": 150 } },
      { selector: "edge", style: { width: "data(width)", "curve-style": "bezier", "target-arrow-shape": "triangle", "arrow-scale": 0.7, opacity: 0.6, "line-color": cssVar("--muted"), "target-arrow-color": cssVar("--muted") } },
      { selector: "edge[kind = 'treated_by']", style: { "line-color": color("medication"), "target-arrow-color": color("medication") } },
      { selector: "edge[kind = 'monitored_by']", style: { "line-style": "dashed", "line-color": color("analyte"), "target-arrow-color": color("analyte") } },
      { selector: "edge[kind = 'measures']", style: { "line-style": "dotted", "line-color": color("lab_report"), "target-arrow-color": color("lab_report") } },
      { selector: "edge[kind = 'occurred_during']", style: { opacity: 0.3 } },
      { selector: "edge[kind = 'labelled_by'], edge[kind = 'guided_by']", style: { "line-style": "dashed", "line-color": color("source"), "target-arrow-color": color("source") } },
      { selector: ":selected", style: { "border-width": 3, "border-color": cssVar("--accent"), "line-color": cssVar("--accent"), "target-arrow-color": cssVar("--accent"), opacity: 1 } },
      { selector: ".dim", style: { opacity: 0.12 } },
    ],
    layout: { name: "preset" },
  });

  const nodeOk = (n) => n.hasClass("found") || (shown[n.data("kind")] && n.data("n") >= minPatients);
  const edgeOk = (e) => e.data("n") >= minPatients && nodeOk(e.source()) && nodeOk(e.target());

  // Columns read left to right: what was measured, what is wrong, what treats it. Conditions
  // are ordered by how many patients have them; every other column puts each node near the
  // concepts it links to (mean row of its placed neighbours).
  const COLUMNS = ["lab_report", "analyte", "condition", "medication", "procedure", "source", "observation", "encounter", "note"];
  const PLACING = ["condition", "medication", "procedure", "source", "analyte", "lab_report", "observation", "encounter", "note"];
  const COLUMN_WIDTH = 270;
  let layoutName = "columns";
  function columnPositions(nodes) {
    const rows = new Map();
    const byKind = new Map(COLUMNS.map((k) => [k, []]));
    nodes.forEach((n) => byKind.get(n.data("kind")).push(n));
    const meanRow = (n) => {
      const placed = n.neighborhood("node:visible").filter((m) => rows.has(m.id()));
      if (!placed.length) return Infinity;
      return placed.reduce((sum, m) => sum + rows.get(m.id()), 0) / placed.length;
    };
    for (const kind of PLACING) {
      const key = kind === "condition" ? (n) => -n.data("n") : meanRow;
      const keyed = byKind.get(kind).map((n) => [key(n), concepts.get(n.id()).label, n]);
      keyed.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1].localeCompare(b[1])));
      keyed.forEach(([, , n], i) => rows.set(n.id(), i));
    }
    const used = COLUMNS.filter((k) => byKind.get(k).length);
    const box = byId("cy").getBoundingClientRect();
    const longest = Math.max(1, ...used.map((k) => byKind.get(k).length));
    const width = (used.length - 1) * COLUMN_WIDTH + 240;
    const row = Math.max(24, Math.min(64, ((box.height / box.width) * width) / longest));
    const positions = {};
    nodes.forEach((n) => {
      positions[n.id()] = { x: used.indexOf(n.data("kind")) * COLUMN_WIDTH, y: rows.get(n.id()) * row };
    });
    return positions;
  }

  function relayout() {
    cy.batch(() => {
      const ok = new Set(cy.edges().filter(edgeOk).map((e) => e.id()));
      cy.edges().forEach((e) => e.style("display", ok.has(e.id()) ? "element" : "none"));
      cy.nodes().forEach((n) => {
        const on = nodeOk(n) && (showUnlinked || n.hasClass("found") || n.connectedEdges().some((e) => ok.has(e.id())));
        n.style("display", on ? "element" : "none");
      });
    });
    const shownNodes = cy.nodes(":visible");
    if (layoutName === "columns") {
      cy.batch(() => cy.nodes().removeClass("network"));
      const positions = columnPositions(shownNodes);
      shownNodes.layout({ name: "preset", positions: (n) => positions[n.id()], fit: true, padding: 20 }).run();
    } else {
      cy.batch(() => cy.nodes().addClass("network"));
      cy.elements(":visible").layout({
        name: "cose", animate: false, randomize: false, fit: true, padding: 24,
        nodeDimensionsIncludeLabels: true, componentSpacing: 40,
        nodeRepulsion: () => 6000, idealEdgeLength: () => 80, nodeOverlap: 8, numIter: 1500,
      }).run();
    }
    cy.zoom(Math.min(cy.zoom(), 1.4));
    byId("status").textContent =
      `Showing ${fmt(shownNodes.length)} of ${fmt(D.concepts.length)} concepts and ` +
      `${fmt(cy.edges(":visible").length)} of ${fmt(D.links.length)} links.`;
  }

  function selectConcept(id) {
    const node = cy.getElementById(id);
    if (!node.visible()) {
      node.addClass("found");
      relayout();
    }
    cy.elements().unselect();
    node.select();
    cy.animate({ center: { eles: node }, duration: 200 });
    showConcept(id);
  }
  cy.on("tap", "node", (ev) => showConcept(ev.target.id()));
  cy.on("tap", "edge", (ev) => showLink(links.get(ev.target.id())));

  // --- controls -------------------------------------------------------------------------
  const toggles = byId("kind-toggles");
  const perKind = new Map();
  for (const c of D.concepts) perKind.set(c.kind, (perKind.get(c.kind) || 0) + 1);
  for (const [kind, label] of KINDS) {
    if (!perKind.has(kind)) continue;
    const box = h("input", { type: "checkbox", value: kind, checked: shown[kind] });
    box.addEventListener("change", () => { shown[kind] = box.checked; relayout(); });
    toggles.append(h("label", {}, box, h("span", { class: "swatch", style: `background:${color(kind)}` }), `${label} (${fmt(perKind.get(kind))})`));
  }
  const filters = byId("filters");
  const threshold = h("select", { "aria-label": "Minimum number of patients" },
    THRESHOLDS.filter((t) => t <= Math.max(1, N)).map((t) => h("option", { value: t, selected: t === minPatients }, fmt(t))));
  threshold.addEventListener("change", () => { minPatients = Number(threshold.value); relayout(); });
  filters.append(h("label", { title: "Hides concepts and links with fewer patients" }, "At least ", threshold, " patients"));
  const unlinked = h("input", { type: "checkbox" });
  unlinked.addEventListener("change", () => { showUnlinked = unlinked.checked; relayout(); });
  filters.append(h("label", { title: "Concepts with no shown link to another shown concept" }, unlinked, "Show unlinked concepts"));
  const layoutSwitch = h("select", { "aria-label": "Graph layout" },
    h("option", { value: "columns" }, "Columns: measured · condition · treatment"),
    h("option", { value: "network" }, "Network (force-directed)"));
  layoutSwitch.addEventListener("change", () => { layoutName = layoutSwitch.value; relayout(); });
  filters.append(h("label", {}, "Layout ", layoutSwitch));

  byId("search").addEventListener("input", (ev) => {
    const q = ev.target.value.trim().toLowerCase();
    cy.batch(() => cy.elements().removeClass("dim"));
    if (!q) {
      if (cy.nodes(".found").length) {
        cy.nodes().removeClass("found");
        relayout();
      }
      return;
    }
    const hits = cy.nodes().filter((n) => {
      const c = concepts.get(n.id());
      return c.label.toLowerCase().includes(q) || (c.code || "").toLowerCase().includes(q);
    });
    if (hits.length === 1) {
      selectConcept(hits[0].id());
    } else if (!hits.length) {
      show(h("h2", {}, "Details"), h("p", { class: "muted" }, `No concept matches “${q}”.`));
    } else {
      cy.batch(() => cy.elements(":visible").not(hits).addClass("dim"));
      show(h("h2", {}, `${fmt(hits.length)} concepts match “${q}”`),
        h("ul", { class: "links" }, hits.sort((a, b) => b.data("n") - a.data("n")).slice(0, 100).map((n) =>
          h("li", {}, h("button", { type: "button", class: "link", onclick: () => selectConcept(n.id()) }, concepts.get(n.id()).label),
            ` (${fmt(n.data("n"))})`))));
    }
  });

  relayout();
  intro();
})();
