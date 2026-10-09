# 0005: Patient graph, timeline, store and viewer (Phase 2)

- Status: proposed (awaiting the user's review)
- Date: 2026-10-07

## Context

Phase 2 turns one patient's record, plus any lab reports filed in it, into a knowledge graph and a timeline that a person can inspect. Phase 3 rules will read their inputs from it. Two decisions were the user's: **SQLite** for persistence and a **local HTML viewer** (both chosen 2026-10-07).

Facts from dev-1000 that shaped the design:
- **Size.** A median patient has about 380 records and the largest about 19,000, of which 14,700 are observations.
- **Reason links.** FHIR `reasonReference` names a condition on 77% of medication requests and 27% of procedures.
- **Same value in two sources.** A lab report prints values converted and rounded: creatinine recorded as 1.2345 mg/dL may be printed as `109 µmol/L`.

## Decision

### The graph (`graph/schema.py`, `graph/build.py`)

One `networkx.MultiDiGraph` per patient. Edge keys are edge kinds, and node IDs are deterministic: `<kind>:<source id>`. Attributes are the JSON form of pydantic models (`NodeData`, `EdgeData`, `GraphInfo`). Values travel as decimal strings with their digits, so a stored graph loads back identical.

**Every record becomes a node; nothing is dropped.**

| node kind | from |
| --- | --- |
| `encounter`, `condition`, `medication_request`, `procedure`, `note` | the FHIR record |
| `lab_result` | an observation of an in-scope analyte, normalized (ADR 0002) |
| `observation` | every other observation: vitals, surveys, out-of-scope labs |
| `lab_report`, `report_row` | a filed lab report and each row read from it (ADR 0003, 0004) |
| `analyte` | concept: an in-scope analyte from the registry |

A measurement (lab result or report row) is **usable** only if its status is `ok` and it has a date. Only usable values join a series. Everything else stays a node with the reason in `detail`, for review: rejected, unmapped and implausible rows, values with unknown units, and rows of a report whose date was refused.

**Every edge says what it rests on** (`basis`).

| edge kind | joins | basis |
| --- | --- | --- |
| `occurred_during` | record → encounter | the record's encounter reference |
| `measures` | lab result or report row → analyte | LOINC code; the analyte name table for a report row |
| `part_of` | report row → lab report | printed in the report |
| `treated_by` | condition → medication request or procedure | FHIR `reasonReference`, only; nothing is inferred |
| `monitored_by` | condition → analyte | a cited guideline passage, quoted verbatim (below) |
| `precedes` | consecutive encounters; consecutive values of one analyte's series | time order |
| `same_measurement` | report row → FHIR lab result | same analyte, day and value at the printed precision (below) |

**Monitoring links** (`rules/monitoring.py`). Each link cites its source and quotes the passage, and both are checked against the guideline text:

| Condition | Analytes | Source |
| --- | --- | --- |
| CKD stages 1–4 | eGFR and urine ACR | KDIGO 2024 Practice Point 2.1.1 ("Assess albuminuria … and GFR at least annually in people with CKD"), with Practice Point 1.3.1.1 for ACR as the preferred albuminuria test (executive summary, Kidney Int 2024;105:684–701) |
| CKD stages 1–4 | creatinine | KDIGO 2024 Practice Point 1.2.2.1 (serum creatinine and an estimating equation for initial assessment of GFR) |
| Anaemia | haemoglobin | WHO 2024 guideline on haemoglobin cutoffs, whose stated objective is "the use of haemoglobin concentrations to assess anaemia" |

The link says only that a guideline assesses the condition with the analyte. **How often, and whether a test is overdue, is Phase 3**, which adds frequencies to this table and raises any flag.
- **Codes are matched exactly**, because medgraph has no SNOMED CT hierarchy.
- **Deliberately not linked yet:** end-stage renal disease, renal transplant and diabetic kidney disease codes. Phase 3 decides with its own citation whether the same tests apply.

**Same measurement in two sources.** A report row repeats a FHIR result when all of these hold:
- the analyte, the day and the comparator agree;
- any eGFR equation the report names is compatible with the LOINC code;
- both values are usable;
- the recorded value, converted to the printed unit, lies **within half a unit of the last printed digit**. For example, `109 µmol/L` repeats 1.2345 mg/dL, which is 109.13 µmol/L.

A repeating row is not a second point. It corroborates the FHIR point, which then lists both sources. A row that repeats several results gets an edge to each.

**Series order** has one definition, `timeline.series_members`, which both the timeline and the `precedes` edges use. A report gives only a date, so its values sort before timed values on the same day.

### Timeline (`graph/timeline.py`)

The timeline is read off the graph alone, so a stored graph is enough. It has three parts:
- **Series:** one per analyte, built from usable values, each point with its sources.
- **Events:** conditions (onset to abatement), medication requests, procedures and encounters.
- **Data notes.** These state facts about the data:
  - a series with only one value;
  - values set aside, and why;
  - reports with no collection date;
  - rows rejected by the extraction checks;
  - rows whose name is not in the name table;
  - rows counted once because they repeat a recorded result.

  Whether any of this matters clinically is for the rules.

### Store (`graph/store.py`)

One SQLite file per cohort, at `$MEDGRAPH_DATA_DIR/graphs/<cohort>.sqlite`.
- **Tables:** `graphs`, `nodes` and `edges` hold attributes as JSON; `meta` holds the schema version, config, digests and git state.
- **Writes:** a patient's graph is replaced in one transaction.
- **Integrity:**
  - Foreign keys refuse an edge whose endpoint isn't a node.
  - Each graph's SHA-256 digest is stored and checked on load, which catches a tampered or corrupted row.
  - A store from another schema version is refused.

### Viewer (`graph/view.py`, `graph/static/`)

`make view PATIENT=<id prefix>` writes one self-contained HTML page per patient to `$MEDGRAPH_DATA_DIR/views/`.
- **Graph:** concepts, meaning records grouped by code (a condition code, a medication, an analyte, a report). A concept opens a list of its records; a record opens its values, source references and links. By default the concepts sit in columns, left to right: lab reports, analytes, conditions, medications, procedures. Conditions are ordered by first date, and every other concept sits near the concepts it links to. A force-directed layout is available as an option.
- **Timeline:** event lanes and one chart per lab series, with zoom kept in sync. It opens on the span of the lab values, and "Full range" shows everything.
- **Offline and private:**
  - Cytoscape.js 3.34.3 and uPlot 1.6.32 (MIT) are vendored and inlined. Their SHA-256 is pinned in code and checked before rendering. On download, they were also checked against the hashes cdnjs and jsDelivr publish.
  - A Content Security Policy forbids every network request.
  - Record text reaches the page only as JSON data, escaped for a script block, and is written with `textContent`. A test fails if the script ever uses `innerHTML`, `eval`, `fetch` and the like.
- **Banner:** a synthetic-data banner, set by `view_patient.py`.
- **Language:** English only for now. Italian comes with the Phase 5 interface.

### Cohort build (`scripts/build_graphs.py`, `configs/graphs/dev-1000.yaml`)

The script builds every patient and checks the invariants in `graph/check.py`:
- valid attributes and a source on every node;
- allowed endpoint kinds for every edge;
- a citation and quote on every `monitored_by` edge;
- each usable lab result in exactly one series;
- each usable report row either in a series or repeating a value that is;
- no unusable value in a series;
- `precedes` edges matching the series order;
- a node for every record and report row.

It then writes the store and an aggregate report, `docs/data/synthea-dev-1000-graphs.md`. The report holds no per-patient rows and no timings, so a rebuild reproduces it.

**Synthetic reports are filed** in the record of the patient whose tag they print. Their transcriptions come from the saved LLM runs on the text reports. No model is called. Each transcription is checked against the report's text hash and the current prompt version, then interpreted again with the current code.

### Deviations from the Phase 2 plan

- **Patient data sits on the graph** (`GraphInfo`), not on a patient node. Each graph holds one patient.
- **No separate `measured_at` edge.** Lab results use `occurred_during` like every other record.
- **Added** the `lab_report` node and the `part_of` edge. Report-level facts belong to the report: the collection date or why it was refused, conventions, issues.
- **`same_measurement` compares values at the printed precision**, not by equal canonical values. Reports print converted, rounded values, so exact equality would almost never hold.
- **DiagnosticReports are not nodes.** Their notes are note nodes, which list both resources as sources. Lab-panel grouping is not in the graph yet.

## Evidence (dev-1000; synthetic data, a pipeline test)

From the [graph report](../data/synthea-dev-1000-graphs.md):
- **All 1,148 patients built, with 0 invariant violations and 0 build issues.**
- **Size:** 977,475 nodes and 1,152,231 edges. Nodes per patient: median 384, maximum 18,855. The store is 1.3 GB, and the build took about 10 minutes on an Apple M3.
- **`treated_by`:** 41,953 of 54,181 medication requests (77%) and 38,466 of 144,713 procedures (27%) name the condition they treat.
- **`monitored_by`:** 266 CKD condition records (stages 1–4) and 383 anaemia records.
- **Reports filed:** 156 reports in 156 patients' records. Rows by status: 279 ok, 915 unmapped (mostly out-of-scope tests), 13 eGFR in mL/min, and 2 misplaced.
  - 18 reports have no collection date: the 11 reports-v1 and 7 reports-v2 reports that Phase 1e refused. Their rows are off the timeline.
- **All 243 usable report rows repeat exactly one FHIR result:** none matches no result, and none matches more than one. Every synthetic report was printed from FHIR values. So this checks extraction and matching together: no wrong value or date got through. It agrees independently with the extraction evaluation (ADR 0004), on reports-v1 and reports-v2 together.
- **Determinism:** the first build used a random string-hash seed and the rebuild used `PYTHONHASHSEED=1`. Both gave the same store digest, `17bc4588…`. (Phase 4a corrected a quote on the `monitored_by` edges, ADR 0007; the rebuilt store's digest is `8f7276bd…`.) A unit test also checks the digest under two hash seeds.
- **Viewer** (headless Chromium, driven by Playwright):
  - On a CKD and anaemia patient with a filed report, the script tapped every visible concept and link, opened a record and every data note, used search, clicked a chart value and zoomed. There were no script errors and no network requests.
  - The page for the patient with 6,900 records is 7 MB and renders in about 2 s.
- **Tests:** 426 pass. One opt-in Synthea test builds every 50th dev-1000 patient, checks it and round-trips it through the store.

## Limitations

- **Synthetic links.** Synthea fills `reasonReference` from its modules. Real records often leave it empty, and then `treated_by` is missing. No link is ever inferred, so a missing link is not evidence of an untreated condition.
- **No SNOMED CT hierarchy.** `monitored_by` matches exact codes, so a subtype such as iron-deficiency anaemia is not linked until it is listed.
- **Two measurements can be merged.** Two different measurements with the same analyte, day and value at the printed precision would count as one. This never changes a value, but it can undercount repeats on the same day.
- **The same report filed twice** as two different files (say text and PDF) gives two report nodes. Rows that repeat a FHIR result are still counted once, but rows found only on the report would appear twice. Document-level deduplication is for Phase 5, where people file reports.
- **Filing by the printed patient tag is a test harness** for synthetic reports. In the app, a person files a report into a record.
- **Store size.** Every observation is a node and notes are stored in full, hence 1.3 GB for 1,148 patients. Source reference ranges are not copied into the graph; they stay in the source record.
- **Viewer.**
  - English only.
  - Undated (day-precision) values are drawn at noon UTC.
  - Unlinked concepts are hidden by default. A checkbox shows them, and search finds them; all of them stay on the timeline.
  - In the column layout, the column says what a concept is; the row only places it near its links. Graph labels drop SNOMED CT's semantic tag ("(disorder)"); the side panel shows the full name.

## Consequences

- **Phase 3 rules read usable series from `build_timeline` and conditions from the graph.** Every flag can then cite the nodes, and through them the source resources and values. The monitoring table gains frequencies there, with citations.
- **The Phase 5 API can serve `view_model` as JSON;** the page already consumes exactly that.
- **`.gitignore` fix.** Its `data/` pattern also matched `docs/data/`, so the aggregate data reports and the data README had never been committed. The patterns are now anchored to the repository root (`/data/`, `/runs/`).
