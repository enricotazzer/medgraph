# 0007: Knowledge store, retrieval index and medication rules (Phase 4a)

- Status: proposed (awaiting the user's review)
- Date: 2026-10-09

## Context

Phase 4 explains flags in plain language and adds medication-monitoring flags. Both need sources medgraph can quote exactly: the guidelines Phase 3 cites, and drug labels. Phase 4a builds those sources and the medication rules, with no language model. Phase 4b (note check and explanations) uses them.

**The user decided** (2026-10-08):
- **Drug labels:** DailyMed (US FDA Structured Product Labeling).
- **Retrieval:** hybrid, BM25 plus bge-m3 embeddings.
- **Note check:** annotates a flag and never removes it (Phase 4b).

**Licences, read from each document's own text:**
- KDIGO 2024 guideline and its executive summary: CC BY-NC-ND 4.0.
- WHO 2024: CC BY-NC-SA 3.0 IGO.
- KDIGO 2012: "All rights reserved".
- NIDDK pages: US Government works.
- DailyMed labels: NLM "cannot guarantee the copyright status for any item".

## Decision

### Knowledge store (`medgraph.rag`, `scripts/fetch_knowledge.py`, `make knowledge`)

Files live in `$MEDGRAPH_DATA_DIR/knowledge/` and are never committed. The pre-commit guard now refuses `.xml` as well as `.pdf`.

**Documents** (`configs/knowledge/documents.yaml`):

| ID | Document | Licence |
| --- | --- | --- |
| `kdigo-2024-ckd` | KDIGO 2024 CKD guideline, 199 pages | CC BY-NC-ND 4.0 |
| `kdigo-2024-ckd-summary` | its executive summary, 18 pages | CC BY-NC-ND 4.0 |
| `kdigo-2012-anaemia` | KDIGO 2012 anaemia guideline, 64 pages | all rights reserved |
| `who-2024-haemoglobin` | WHO 2024 haemoglobin guideline, 79 pages | CC BY-NC-SA 3.0 IGO |
| `niddk-egfr-adults`, `niddk-egfr-previous` | NIDDK eGFR equation pages (CKD-EPI 2021, MDRD) | US Government work |

- Each file is pinned by SHA-256. A download that differs is refused, never stored, and stored files are re-verified on every run.
- Each document's licence statement must occur in its extracted text.
- Printed page numbers come from running footers or the PDF's page labels, so a citation names the page a reader sees (S149, not page 34 of the file).

**Labels** (`configs/knowledge/labels-dev-1000.lock.json`, committed):
- **Selection rule `labels-v1`:** for every RxNorm code the cohort prescribes, prefer the approved application's labelling (NDA or BLA), else an ANDA or authorized generic, else any label. Within that tier, the most recently published wins.
- **The lock** pins the set ID, version and SHA-256 of each label. A newer DailyMed version is reported, never taken silently. Each version is stored in its own file and never replaced.
- **Coverage:** 263 RxNorm codes, 201 distinct labels (41 MB); 19 codes have no label (withdrawn drugs, some packs, brand codes DailyMed does not map).
- **What leaves the machine:** public URLs and one drug code per request, never linked to a patient. The script refuses any cohort that is not synthetic, because choosing labels for credentialed data such as MIMIC-IV needs its data use agreement checked first.
- **Resilience:** after a DNS failure stopped the first run at 105 of 263 codes, the script was changed to continue past a failed code, retry with backoff, and resume from a progress file on the same day. The lock is written only when every code succeeded.

### Quotes are checked against the stored text (`rag/text.py`, `rag/quotes.py`)

- **Matching.** A quote is fragments separated by "[...]". Each fragment must occur in the stored text, verbatim up to case, spacing, punctuation and line breaks. Numbers keep their sign and comparators are kept: "-0.241" does not match "0.241".
- **Locators.** When a locator names pages, a fragment must be found on one of them. For a label it must be in the named section of the pinned version.
- **Tables.** Some tables read column by column in the extracted text. Their content is a separate *table transcription*, checked weakly: each row's words occur on the quote's page.
- **Coverage.** Every cited quote is checked: 11 guideline sources, 16 label statements and the 4 graph monitoring links. All pass (`pytest --run-knowledge`; `docs/data/knowledge-dev-1000.md`). Changing a threshold in a quote, citing the wrong section, or citing another label each fails the check.

**What the check found in earlier phases' quotes**, all now fixed:
- **WHO "objective" quote (Phase 2).** It dropped the words "locally adaptable," without marking the omission. The corrected quote changes the `monitored_by` edges, so the graphs were rebuilt: same 1,148 graphs and 0 problems, store digest `17bc4588…` → `8f7276bd…`.
- **CKD-EPI 2021 and MDRD (Phase 3).** These were my transcriptions (k and a for κ and α; units in my words). They now quote NIDDK's text. The MDRD quote is the conventional-units form, which is what the code uses.
- **KDIGO categories and WHO pregnancy cutoffs (Phase 3).** These are tables that extract column-wise; they are now marked as table transcriptions. Two units I had inserted in brackets ("[g/L]", "[m]") moved to the locators.
- **Locators** gained the pages where each quote was found.

### Passages and the retrieval index (`rag/chunks.py`, `rag/spl.py`, `rag/index.py`, `scripts/build_index.py`)

- **Guidelines** are cut by page, so a passage cites one page. Paragraphs are rebuilt from lines, running headers and footers are dropped, and reference lists are skipped (explicit page ranges).
- **Labels** are cut by LOINC-coded section; the Highlights excerpts, product data and package panels are left out.
- **Passages** have at most 200 words and stable IDs (`<source>@<version>/<page or section>.<n>`). A new document version gives new IDs, so an old citation never points at changed text.
- **Index:** 16,503 passages (1,306 guideline, 15,197 label) in SQLite.
  - BM25 runs over FTS5 with Porter stemming.
  - bge-m3 embeddings (local Ollama, MIT licence) are tied to the model's digest.
  - The two rankings are fused with reciprocal rank fusion (k = 60). Ties break by passage ID, so ranking is deterministic.
- **Source filter.** A search can be limited to sources: explanations will use the guidelines plus the labels of the patient's own drugs, so that 15,197 label passages do not drown out the guidelines.

### Medication rules (`rules/medications.py`) and dialysis; ruleset `rules-v2`

A census of all 201 labels (in the knowledge report) counted sentences stating eGFR thresholds, creatinine-clearance thresholds and lab monitoring with a time word. Rules were curated from those statements by one criterion: a flag only where the label states an eGFR threshold or a test interval that the record can check.

| Rule | Kind | When | Label |
| --- | --- | --- | --- |
| `metformin-egfr-below-30` | flag | metformin active; latest computed eGFR < 30 | contraindicated below 30; discontinue if it falls below 30 (§2.4) |
| `metformin-egfr-30-44` | flag | metformin active; latest eGFR 30-44 | initiation not recommended at 30-45; assess benefit and risk of continuing below 45 (§2.4) |
| `epoetin-haemoglobin-monthly` | flag | epoetin alfa active; no haemoglobin in the past month | weekly until stable, then at least monthly (Epogen §2.2) |
| `ras-inhibitor-nsaid` | note | ACE inhibitor, ARB or sacubitril/valsartan with an NSAID | "Monitor renal function periodically in patients receiving … and NSAID therapy" (12 labels) |
| `metformin-no-gfr-test` | note | metformin active; no GFR test at all | assess renal function before starting and periodically thereafter |

How the rules read the record:
- **Active.** The latest request for the drug, authored by the evaluation date, has status `active`.
- **eGFR.** Label thresholds are compared with the whole-number CKD-EPI 2021 eGFR, as the KDIGO categories are.
- **Code lists.** A test checks every code in the lists against the committed lock: each code must map to the label its statement comes from.
- **Notes** quote what a label says about a situation the record shows, with nothing to check. They are shown on the patient page and never counted as flags.

**Dialysis**, from procedures:
- **Definition.** A "Renal dialysis" session in the 30 days before the evaluation date takes a patient out of the CKD monitoring rules, like a kidney-failure code. A session during a CKD episode counts as recorded kidney disease.
- **The window is medgraph's definition, not a guideline's.** Every dialysis patient in dev-1000 had a last session within 7 days of the evaluation date or more than 250 days before it, so any window from 7 to 250 days gives the same result.

**Left out, on purpose:**
- **Creatinine-clearance thresholds**, which most renal dosing statements use. Computing creatinine clearance needs body weight and the Cockcroft-Gault equation, and comparing it with an eGFR would substitute one measure for another.
- **"Monitor renal function periodically"** where the label names no situation the record shows. For the ARBs, "these patients" includes CKD, which the KDIGO GFR follow-up flag already covers.
- These passages stay retrievable for explanations.

## Evidence (dev-1000, synthetic: a pipeline test)

From [the flags report](../data/synthea-dev-1000-flags.md) and [the knowledge report](../data/knowledge-dev-1000.md), as of 2026-01-01:

**Medication flags:**
- metformin with eGFR below 30: 9;
- metformin with eGFR 30-44: 2;
- epoetin without monthly haemoglobin: 0, because no living patient has epoetin active. The rule is tested, but it cannot fire in this cohort.
- Notes: an RAS inhibitor with an NSAID, 28; metformin with no GFR test, 0.

**Who the 11 metformin flags are:**
- In 3 of the 9 below-30 flags the CKD criteria are *not* met: the low eGFR has not yet persisted 90 days. The label speaks of the current eGFR, so the flag follows it, and it states that an acute change is not excluded.
- In Synthea the diabetes module keeps metformin while the kidney values worsen: the same generator inconsistency Phase 3 found, and the pattern this flag exists to catch.

**Dialysis:**
- 16 evaluated patients have a dialysis session; 15 also have a kidney-failure code (mostly "Awaiting transplantation of kidney").
- Recognizing dialysis changes one patient: one albuminuria and one haemoglobin follow-up flag fewer.
- Correction: during the work I said that the 7 patients on dialysis had no kidney-failure code. That came from checking the ESRD code alone, not the transplant-state codes.

**Totals:**
- 122 patients (12%) have at least one flag, against 117 under `rules-v1`.
- The Phase 3 flag counts are unchanged except for that one dialysis patient.

**Retrieval smoke checks** (10 questions, each in English and Italian; first passage containing the expected text; identical across two runs):

| top 5 of 10 | BM25 | dense (bge-m3) | hybrid (RRF) |
| --- | --- | --- | --- |
| English | 8 | 9 | 8 |
| Italian | 3 | 10 | 4 |

- bge-m3 finds the English passage for Italian questions.
- Plain RRF hurts in Italian: BM25 matches Italian words against English text, returns noise, and RRF weights it as much as the dense ranking. In English, hybrid is no better than dense.
- These are 10 questions I wrote: a signal, not a measurement. Nothing was tuned to them.
- For Phase 4b this suggests two things: build retrieval queries in English from the flag's own content (rule, analyte, drug), whatever the output language; and decide whether to keep BM25 in the fusion at all. That decision is the user's (they chose hybrid).

**Tests:**
- quote matching, locator pages and sections, table rows;
- the label selection rule and its tiers, the lock round trip, resuming after a failure, pinned-hash refusal;
- chunking, BM25, dense ranking, RRF ties and source filters;
- every medication and dialysis boundary (eGFR 29/30/44/45, a month to the day, 30 against 31 days).

## Limitations

- **Synthetic data and no clinical review.** Medication status, dialysis sessions and kidney values come from separate Synthea modules.
- **"Active" is the request status at export.** A drug started before the evaluation date and still active at export is taken as active on that date; an interruption in between would not be seen. The status is also not proof the patient takes the drug.
- **One US label per product.** Generic labels follow the reference product, but wording varies. Some chosen labels are old repackager copies: 14 are effective before 2016, 10 of them from Physicians Total Care, including the Micardis and Lotrel labels whose NSAID warnings the notes cite. Italian (AIFA) labels are not used.
- **Label eGFR** names no equation. CKD-EPI 2021 is used, from creatinine that Synthea partly records "in blood".
- **The table check is weaker** than the verbatim check.
- **Retrieval smoke checks** are 10 questions written by the developer, each in English and Italian. They show the right passage is reachable, not how good retrieval is. Proper evaluation needs questions written independently.
- **Code lists** cover dev-1000's codes only.

## Consequences

- Phase 4b uses the index with a source filter per patient, and cites passage IDs that the verifier can resolve to the exact stored text.
- Results name `rules-v2`. The graph rebuild removed the stored `rules-v1` results; `make flags` stores `rules-v2`.
