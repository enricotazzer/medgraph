# medgraph

Turns a person's medical records (conditions, medications, labs, procedures, encounters and free-text lab reports) into a patient knowledge graph with a timeline, and raises **evidence-grounded follow-up flags**: findings that look unaddressed, medications missing the monitoring they need, and medications their label restricts at the recorded kidney function. Every flag links to the exact values, dates and cited sources behind it.

> **Research prototype, not a medical device.** medgraph surfaces evidence for discussion with a clinician. It does not diagnose and must not be used to make medical decisions. In the EU, software intended to inform diagnostic or therapeutic decisions is regulated as a medical device (Regulation (EU) 2017/745, Annex VIII, Rule 11). This project is not certified and makes no such claim.

## What it will do

1. **Knowledge graph and timeline** showing how conditions, treatments, medications and lab results relate over time.
2. **Follow-up flags**, starting with two conditions: chronic kidney disease (KDIGO: eGFR and albuminuria persisting for more than 3 months) and anemia (WHO hemoglobin thresholds). Types of flag:
   - criteria met with no matching diagnosis;
   - a known condition without the follow-up labs its guideline expects;
   - a medication without its required monitoring;
   - a medication its label restricts at the recorded eGFR (metformin).

   Drug–drug interactions appear only as notes that quote a label (an RAS inhibitor with an NSAID). There is no interaction database (ADR 0010).
3. **Two views of the same data.**
   - A clinician view: dense evidence tables and guideline references.
   - A personal view in English and Italian: plain language, with flags framed as things worth discussing with your doctor.
4. **Research extension:** graph neural networks that predict future diagnoses, evaluated against honest baselines on MIMIC-IV.

## Design principles

- **LLMs handle language; code handles medical logic.** Guideline criteria, unit conversions and eGFR are deterministic, unit-tested code. LLMs extract, normalize wording, check notes and explain.
- **Every claim is traceable** to source data (resource, value, date) or to a cited, versioned document.
- **Decision support, not diagnosis.**
- **Local-first privacy.** Health data is a special category under GDPR. The default LLM backend is local (Ollama), there is no telemetry, and data leaves the machine only if you configure a remote backend yourself.
- **Honest evaluation.** Results on synthetic data are labeled as pipeline tests. Baselines come first, and limitations are reported.

## Status

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Project setup, reproducible Synthea cohort, data profile | done |
| 1 | FHIR ingestion, lab normalization, lab-report extraction and its evaluation | done (ADR 0003, 0004; results in `docs/results/`) |
| 2 | Patient knowledge graph and visualization | done (ADR 0005; `make graphs`, `make view`) |
| 3 | Guideline rules and follow-up flags | done (ADR 0006; `make flags`) |
| 4 | Retrieval-augmented explanations and drug knowledge | 4a done: knowledge store, index, medication rules (ADR 0007; `make knowledge`); knowledge graph joined to the pages (ADR 0008; `make knowledge-graph`); 4b built, awaiting review: explanations checked sentence by sentence, and the note check (ADR 0009; `make explain`) |
| 5 | Local app for one user (ADR 0010): 5a API and the pages served from it; 5b clinician and personal views in English and Italian; 5c uploads, confirmations and deletion; 5d Docker | planned |
| 6 | Research on MIMIC-IV (ADR 0010): 6a ingest and rules; 6b one prediction task and its baselines; 6c GNN; 6d note check on MIMIC-IV-Note | planned |
| 7 | Documentation and results: technical report, model cards, app quickstart, sources and licences | planned |

## Quickstart

Requires [uv](https://docs.astral.sh/uv/) and a JDK 17 or newer (to run Synthea).

```sh
cp .env.example .env    # set MEDGRAPH_DATA_DIR to a folder outside the repository
make setup              # install dependencies and pre-commit hooks
make check              # lint, type-check, test
make synthea-pilot      # 10-patient pilot cohort (about 10 s)
make synthea-dev        # 1,000-patient development cohort (about 1 min, about 4 GB)
make profile            # aggregate profile -> docs/data/synthea-dev-1000-profile.md
make graphs             # every patient's graph, checked and stored in SQLite (about 10 min)
make flags              # CKD, anaemia and medication rules for every patient -> docs/data/synthea-dev-1000-flags.md
make knowledge          # pinned guidelines and the cohort's DailyMed labels -> $MEDGRAPH_DATA_DIR/knowledge
make index              # retrieval index (BM25); ARGS=--embed adds embeddings (local Ollama, background)
make knowledge-report   # quotes, labels, index, retrieval checks -> docs/data/knowledge-dev-1000.md
make knowledge-graph    # drug labels and guidelines joined to patient concepts by code, for the pages
make explain           # checked explanations and note check for a sample of flags (local LLM, background)
make view PATIENT=...   # one patient's graph, timeline and flags as an offline HTML page
make concept-map       # every patient graph merged by concept, as one offline HTML page
make review             # run the review notebook (notebooks/review.ipynb)
```

Cohort generation is reproducible. The Synthea release is pinned by SHA-256, and the seeds, simulation dates, JVM timezone and locale are all fixed. Each cohort's `MANIFEST.json` records a content digest: regenerating the pilot in two different timezones gave byte-identical output. See [`docs/data/`](docs/data/README.md).

## Repository layout

```text
src/medgraph/   ingest · normalize · graph · rules · rag · agent · gnn · api
scripts/        data tooling (cohort generation, profiling)
configs/        one YAML per cohort or experiment
tests/          unit tests and small synthetic fixtures
docs/           architecture, decisions (ADRs), data notes
frontend/       placeholder: the pages live in src/medgraph/graph/static (ADR 0010)
```

## Data and licensing

- **Synthea** (Apache-2.0) generates the synthetic patients used for development, tests and the public demo. Its diseases follow hand-written modules, so nothing learned from it is clinically meaningful. Citation: Walonoski J, et al. *Synthea: An approach, method, and software mechanism for generating synthetic patients and the synthetic electronic health care record.* JAMIA 2018;25(3):230–238. doi:10.1093/jamia/ocx079
- **MIMIC-IV** will be used only in the research phase, under PhysioNet credentialing and its data use agreement. It is processed locally with local models only and never committed or exposed in the app.
- Knowledge sources (guideline summaries, drug labels) will be listed here with their versions and licenses as they are added.

Data never lives in this repository. It goes in `MEDGRAPH_DATA_DIR`, and a pre-commit hook refuses data files.

## License

Code: MIT (see `LICENSE`). Data sources keep their own licenses.
