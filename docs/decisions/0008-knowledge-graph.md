# 0008: Knowledge graph, joined to patient concepts by code

- Status: proposed (awaiting the user's review)
- Date: 2026-10-09

## Context

Phase 4a stored the sources medgraph quotes (ADR 0007): six guideline documents and the cohort's 201 DailyMed labels, with every quote checked against the stored text. The patient pages and the concept map did not show them: a drug did not lead to its label, and a flag's source was a citation string.

**The user decided** (2026-10-09):
- **Where the knowledge lives:** a separate knowledge graph, joined to patients by code when a page is written. Patient graphs stay exactly what the record says.
- **What pages show of a source:** its details, its section titles and the quotes medgraph checks, never its full text.

## Decision

### The knowledge graph (`graph/knowledge.py`, `make knowledge-graph`)

One graph per cohort, at `$MEDGRAPH_DATA_DIR/knowledge/graph-<cohort>.json`, built from the knowledge store with no language model.

**Nodes:**
- **documents** (6): title, citation, licence, retrieval URL and date, SHA-256 of the stored file;
- **labels** (201): product name, labeler, set ID, version, effective date, document type, the titles of its sections that hold text, retrieval URL and date, SHA-256;
- **statements** (31): each quote a rule (27) or a monitoring link (4) cites, with its locator and the IDs of the passages that hold it.

**Links.** Each comes from a table in the repository, never from text similarity.

| Kind | From → to | Basis | Count |
|---|---|---|---|
| `labelled_by` | RxNorm drug concept → label | the cohort's label lock (`labels-v1`) | 244 |
| `guided_by` | condition concept → guideline | the monitoring table (`rules/monitoring.py`) | 5 |
| `quotes` | statement → document or label | the quote, checked against the stored text | 31 |

The 19 RxNorm codes without a label keep the lock's reason, such as "DailyMed maps no label to this RxCUI".

**Join keys.** Links start at the patient page's concept IDs (`view.concept_of`): `medication_request|<RxNorm system>|<code>` and `condition|<SNOMED system>|<code>`. A test checks that the two agree.

**Checked when built.** Building re-reads every file against its pin and re-checks every quote, on the locator's pages or section. It then finds the passages holding each fragment, cut exactly as the retrieval index cuts them (`rag/stored.py`, shared with `build_index.py`). A quote that fails, a quote that no passage run holds, or a statement on a label version outside the lock stops the build. The dev-1000 graph builds in about 7 s, and all 31 statements pass.

**Kept in step.** The file stores its digest, plus SHA-256s of what it was built from:
- `documents.yaml`;
- the label lock;
- every quoted statement's text and locator;
- the schema.

A page script uses the graph only if both match. Otherwise it writes the page without sources and says why: "run make knowledge-graph".

### Pages

- **Patient page** (`make view`):
  - **Sources column.** It sits to the right of procedures. A drug links to its label (`labelled_by`) and a condition to its guideline (`guided_by`).
  - **Source panel.** Clicking a source shows:
    - its details and licence, and its stored file's hash and retrieval date;
    - every statement medgraph quotes from it, marking those cited for this record, with their passage IDs;
    - its section titles.
  - **Show the source.** Each flag's sources and each `monitored_by` link get this link. A source cited only by a rule is not joined by code, so it opens from there.
  - **Drugs without a label** say why.
- **Concept map** (`make concept-map`): the same join. A source counts the patients of the concepts linked to it. On dev-1000 that gives 203 sources and 249 links; the largest is a DailyMed acetaminophen label with 460 patients.
- **Never full text.** KDIGO 2012 has no open licence, and NLM asks for short quotes with attribution.

### Fixed along the way

- **Product names.** SPL writes some names as a name plus a `<suffix>`, and the reader joined them without a space: "INDERALLA", "AleveCaplets", "EnvarsusXR". This affected 9 of the 201 labels, and the names are fixed.
- **Index title changes.** The index kept a passage whose text was unchanged even if its title had changed. It now also compares the title and location, which are part of what is embedded.
- **Index refreshed** (2026-10-12). `make index` re-inserted the 9 labels' 636 passages under their corrected names and dropped their embeddings. `make index ARGS=--embed` re-embedded just those, with the same bge-m3 version as the other 15,867, in about a minute. The knowledge report's retrieval checks gave exactly the same results as before.

## Consequences

- Pages can show where each drug's knowledge comes from, at the exact label version the rules use, without copying sources into 1,148 patient graphs.
- **Rebuilding.**
  - A new lock, a changed quote or a changed document needs `make knowledge-graph` before the pages show sources again; the scripts say so.
  - Rebuilding the patient graphs is never needed for this.
- **Limits.**
  - The `guided_by` links cover only the five condition codes in the monitoring table. Other conditions get no guideline, because medgraph has no SNOMED CT hierarchy and cites nothing for them.
  - A label link means "the label the lock chose for this RxNorm code". It does not mean the one the patient's pharmacy dispensed.
  - Both page scripts were run in Node, with headless Cytoscape and the real pages (2026-10-12), with no errors. The checks clicked a drug and its label, a guideline, a flag's and a monitoring link's "Show the source", a drug without a label, and the concept map's sources. Nobody has looked at the pages in a browser yet.
- **Retrieval-suggested passages** remain Phase 4b, after the decision on BM25.
