# medgraph

Patient knowledge graph with evidence-grounded follow-up flags: a research-grade portfolio project and a deployable local app. Research prototype, not a medical device. Full goals and phase plan: `README.md`; architecture: `docs/architecture.md`; decisions: `docs/decisions/`.

## Working agreement

- Work phase by phase (roadmap in `README.md`). Before writing code in a phase, show a short plan and wait for the user's go-ahead; stop again when the phase is done.
- Ask when a decision is genuinely the user's (e.g. persistence layer, DDI data source, frontend stack, LLM model). Record accepted decisions as ADRs in `docs/decisions/`.
- **Never commit.** The user commits; leave changes uncommitted for review.
- Flag any shortcut that could inflate results, and any statement the system makes that is not traceable to data or a cited source.

## Commands

```sh
make setup        # uv sync + install pre-commit hooks
make check        # ruff lint + format check, mypy --strict, pytest
make test         # pytest only; add --run-llm / --run-synthea / --run-knowledge / --run-mimic for opt-in tests
make synthea-dev  # regenerate the dev cohort (ARGS=--force to replace it)
make test-synthea # opt-in: parse all of dev-1000 and check nothing in scope is dropped
make profile      # rewrite docs/data/synthea-dev-1000-profile.md
make normalization  # rewrite docs/data/synthea-dev-1000-normalization.md
make extract-rules SPLIT=dev|test [RULES=rules-r2]  # rule-based extraction baseline -> docs/results/
make extract-llm SPLIT=dev|test [LLM=qwen35-9b-r2] OLLAMA_MODELS=/Volumes/T7/ollama-models  # hours, in the background
make graphs       # build, check and store every dev-1000 patient graph (~10 min) -> docs/data/synthea-dev-1000-graphs.md
make flags        # CKD and anaemia rules for every stored patient (~8 min) -> rules table + docs/data/synthea-dev-1000-flags.md
make knowledge   # fetch/verify pinned guideline documents and the cohort's DailyMed labels (ARGS=--lock re-chooses labels, ~1 h; --offline verifies)
make index       # passages + BM25 index (seconds); ARGS=--embed OLLAMA_MODELS=/Volumes/T7/ollama-models adds bge-m3 embeddings in the background (~45 min)
make knowledge-report OLLAMA_MODELS=/Volumes/T7/ollama-models  # background -> docs/data/knowledge-dev-1000.md
make view PATIENT=<id prefix>  # offline HTML page of one patient's graph, timeline and flags ($MEDGRAPH_DATA_DIR/views/); no PATIENT lists candidates
make review       # re-run notebooks/review.ipynb in place (the user's review notebook)
make report       # figures from saved metrics + LaTeX report -> docs/reports/build/ (lualatex)
uv run pre-commit run --files <paths>   # hooks without committing
```

## Layout

- `src/medgraph/records.py`: the shared typed record model (`PatientRecord`, `Observation`, `Timepoint`, `SourceRef`, `IngestIssue`…). Every later module consumes these.
- `src/medgraph/`: `ingest` (`fhir.py` bundle parser, `files.py` exFAT-safe file helpers, `lab_report.py` LLM/rules transcription + deterministic interpretation, `pdf.py` text-layer PDFs), `normalize` (`time`, `numbers`, `units`, `ranges`, `analytes` registry, `analyte_names` EN/IT name table, `labs`), `graph` (`schema`, `build`, `check` invariants, `timeline`, `store` SQLite, `view` + `static/` offline HTML viewer with vendored, hash-pinned Cytoscape.js and uPlot), `rules` (`sources.py` verified citations with verbatim quotes, `egfr.py` CKD-EPI 2021, `ckd.py`, `anaemia.py`, `flags.py` the follow-up rules, `medications.py` drug-label rules and notes, `diagnoses.py` code lists incl. dialysis, `monitoring.py` cited condition-to-analyte links), `rag` (`fetch.py` pinned downloads, `documents.py` + `text.py` PDF/HTML text and quote tokens, `quotes.py` quote verification, `dailymed.py` label choice + lock, `spl.py` label sections, `chunks.py` passages, `index.py` BM25 + embeddings + RRF), `agent` (LLM language tasks; `llm.py` local-only Ollama client), `gnn` (research), `api`; `settings.py` reads `MEDGRAPH_*` env vars and `.env`.
- `scripts/`: data tooling (`generate_synthea.py`, `profile_cohort.py`, `check_normalization.py`, `generate_lab_reports.py` + `lab_report_catalog.py`, `evaluate_extraction.py`, `build_graphs.py`, `evaluate_flags.py`, `view_patient.py`, `fetch_knowledge.py`, `build_index.py`, `knowledge_report.py`); `scripts/dev/` holds repo hooks and `llm_run.sh` (long LLM jobs in their own session, against a private Ollama server). Tests import scripts by module name (pytest `pythonpath`).
- `configs/`: one YAML per cohort or experiment, validated by pydantic.
- `notebooks/review.ipynb`: the user's guided review of each phase's work, on synthetic data only. Extend it with a section per phase. Outputs are stripped on commit (nbstripout).
- `tests/fixtures/`: small hand-written synthetic fixtures only.
- Data lives in `MEDGRAPH_DATA_DIR` (`/Volumes/T7/datasets/medgraph`, an exFAT volume), never in the repo.

## Non-negotiables, as working rules

- Medical logic (thresholds, unit conversions, eGFR and other calculations, guideline criteria) lives only in deterministic, unit-tested code in `normalize/` and `rules/`. LLMs extract, normalize wording, check notes and explain; LLM output never decides whether a flag is raised.
- Every derived fact carries provenance (source resource ID, value, unit, date) or a citation (source, version, chunk ID). No uncited medical statements anywhere, including UI text and explanations.
- The system never states that a person has a disease. Flags say criteria are met, or that something is worth discussing with a doctor.
- Local-first: the default LLM backend is local Ollama; remote backends only by explicit opt-in. No telemetry: disable it in any library that has it.
- Synthea patients follow hand-written modules. Label every result computed on Synthea as a pipeline test, never as a finding.

## Data handling in assistant sessions

- **Never open, print or paste MIMIC data or real personal health records in a Claude session.** A Claude session is an external service: the PhysioNet data use agreement forbids sending MIMIC to one, and GDPR covers personal records. Run code on them locally and look only at aggregate metrics; debug with Synthea or synthetic fixtures.
- Synthea output is synthetic and may be inspected.
- Always scan data folders with `medgraph.ingest.files.iter_data_files`. The data volume is exFAT: macOS creates a binary `._*` companion next to every file, and `pathlib.glob` matches them. Delete trees with `remove_tree` (`shutil.rmtree` fails on exFAT).

## Conventions

- Python 3.12, pydantic v2 models, `mypy --strict`, ruff (line length 100).
- Datetimes are timezone-aware (ruff `DTZ` enforces this). No `print()` in `src/` (ruff `T20`); log resource IDs, never record values.
- Units as UCUM strings; LOINC, SNOMED CT and RxNorm codes as strings.
- Source values are `Decimal` (digits as written) and are never overwritten; canonical values sit next to the originals. Only `LabResult`s with status `ok` may feed rules.
- Data problems become `IngestIssue`s on the record, not exceptions. Never guess: an unknown unit, an ambiguous number (`1,234` without a locale) or a time without a timezone offset is reported, not interpreted.
- Analytes in scope live in `normalize/analytes.py`; a non-trivial conversion factor needs a cited source (a test enforces this).
- Tests come before building on units, calculations, rules and parsing. Opt-in markers: `llm`, `synthea`, `knowledge`, `mimic`.
- Experiments are config-driven: a YAML under `configs/`, outputs under `$MEDGRAPH_DATA_DIR/runs/`, with the config snapshot, seed and versions recorded next to the results.
- Extraction evaluation: tune prompts and interpretation only on development data, which since Phase 1e means all of reports-v1. A held-out test set (now reports-v2) is run once per pipeline version and reported whatever it shows; never select among several runs. A test set that has been examined becomes development data, and the next estimate needs a fresh set. Bump `PROMPT_VERSION` whenever the prompt or schema changes.
- Patient graphs: every record becomes a node; only measurements with status `ok` and a date are `usable` and join a series. Every edge carries its `basis` (a reference in the data, time order, a value comparison, or a cited, quoted guideline passage); never infer a link the data doesn't state. Bump `SCHEMA_VERSION` when the graph's shape changes, and rebuild with `make graphs` (it must report 0 invariant violations).
- Rules: every threshold, equation or interval lives in `rules/` and cites a `rules/sources.py` entry whose verbatim quote contains it (a test checks this). Every source names the stored document it is checked against (`stored`: a `documents.yaml` ID, or `label:<set id>@v<version>` pinned in the label lock); `pytest --run-knowledge` checks each quote fragment against the stored text, on the page or label section its locator names. Never add a quote without running it. Tables that extract column-wise go in `table`, never in `quote`. Flags state what the record shows and end by suggesting a doctor; never "has <disease>". Rule results are derived data: recompute with `make flags` after changing rules, and bump `RULESET` when their meaning changes.
- Knowledge store: guideline PDFs and DailyMed labels stay in `$MEDGRAPH_DATA_DIR/knowledge`, never in the repo (licences; `.pdf` and `.xml` are blocked by the pre-commit hook). Only their pins are committed (`configs/knowledge/`). Label selection sends drug codes to NLM, so `fetch_knowledge.py` accepts synthetic cohorts only. Medication code lists must map, in the committed lock, to the label their statement cites (a test checks this).
- Viewer pages stay offline: no external URLs, CSP `default-src 'none'`, record text only via `textContent`. Files under `graph/static/vendor/` are pinned by SHA-256 in `view.py`; never edit them.
- Local LLM runs: start them with `make extract-llm`, and run nothing heavy (pytest, notebooks, other models) while one is going. On 16 GB this has crashed the session before. Stop Ollama servers by PID, never by name (`pkill ollama` also stops the Ollama app's server and any the user started). Check progress from the log and the count of prediction files.
