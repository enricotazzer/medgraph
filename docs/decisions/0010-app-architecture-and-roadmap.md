# 0010: App architecture and the roadmap after Phase 4 (Phases 5–7)

- Status: accepted (decided by the user, 2026-10-19)
- Date: 2026-10-19

## Context

ADR 0001 left two decisions for later: the frontend stack (Phase 5) and the source of drug–drug interactions (Phase 4). Phase 4 ended without an interaction source. Instead, the medication rules quote the drug labels: metformin flags at the label's eGFR thresholds, and one interaction shown as a note, an RAS inhibitor taken with an NSAID (ADR 0007). The README listed Phases 5–7 in one line each.

Earlier phases deferred these items to Phase 5:
- the Italian interface (ADR 0005);
- confirming ambiguous report dates (ADR 0004);
- confirming analyte names the LLM proposes (ADR 0003, open decision 3);
- serving `view_model` as JSON (ADR 0005);
- whether the personal view shows model text (ADR 0009).

**The user decided** (2026-10-19):
- **Order:** Phase 5 comes once Phase 4b is closed. Drug–drug interactions are out of scope for now.
- **Frontend:** the existing offline pages, served by FastAPI, with no separate frontend build.
- **Deployment:** local, one user, no accounts.
- **MIMIC-IV:** the user has credentialed access, so Phase 6 runs on it.

## Decision

### Frontend: grow the existing pages

The patient page and the concept map stay as they are built today (`graph/view.py`, `graph/static/`):
- **Data:** they read the `view_model` JSON embedded in the page.
- **Libraries:** Cytoscape.js and uPlot are vendored and pinned by SHA-256.
- **CSP:** `default-src 'none'`.

The server renders the same self-contained page for each request, so the read-only pages need no fetches and keep their CSP. Forms (5c) add `form-action 'self'`.

**The alternatives:**
- **React + Vite + Cytoscape.js:** an npm toolchain and its dependency tree to pin and audit, and a rewrite of `viewer.js`.
- **Streamlit:** the graph would need a custom component, its usage statistics must be switched off, and a strict CSP is harder to keep.

`frontend/` remains a placeholder that points here. The pages live in `src/medgraph/graph/static/`.

### Deployment: local, one user

- **Network:** the server binds to 127.0.0.1.
- **Host check:** it refuses any `Host` header other than `127.0.0.1:<port>` or `localhost:<port>`. Without that check, a website could use DNS rebinding to read the patient data.
- **Headers:** every response carries the pages' CSP, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`.
- **Forms:** POSTs need the same origin and a token. There are no accounts.
- **Data:** everything stays in `MEDGRAPH_DATA_DIR`, uploads included. Deleting a patient removes everything derived from their record.
- **Drug labels:** the app uses the committed label lock only. Fetching a label for a drug outside it sends the drug's RxNorm code to NLM, so it does not happen by default. Whether to offer it as an explicit action is decided at the start of 5c.
- **Docker:**
  - Compose publishes the port on 127.0.0.1 only.
  - Ollama runs on the host, because Docker on macOS has no Metal GPU.
  - The local-only guard (`check_local_llm`) counts the Docker host gateway as local through its own setting. It is never bypassed with `MEDGRAPH_ALLOW_REMOTE_LLM`, which would also allow Ollama cloud models.

### Roadmap

Each sub-phase opens with a short plan that waits for the user's go-ahead, and ends with the user's review (CLAUDE.md).

| Sub-phase | Scope | Decided at its start |
| --- | --- | --- |
| 5a | API (FastAPI, uvicorn) and the pages served from it, read-only; page assembly and the explanation cache move from `scripts/` into `src/` | — |
| 5b | Clinician and personal views in English and Italian; one string table for the interface | whether the personal view shows model text (ADR 0009) |
| 5c | Uploads of FHIR bundles and lab reports; background jobs for the model; confirmations of dates and analyte names; deletion | whether a confirmed analyte name stays local to the installation or becomes a proposed change to the committed table; label fetching |
| 5d | Docker image and compose | — |
| 6a | MIMIC-IV ingested into the record model; ICD-9/ICD-10 code lists; lab items mapped to analytes; rules on MIMIC | source format: the hosp module's CSV files, or MIMIC-IV on FHIR |
| 6b | One prediction task and its baselines | the task and its label |
| 6c | GNN against the same baselines | — |
| 6d | Note check on MIMIC-IV-Note | Note access (a separate credential) |
| 7 | Technical report, model cards, app quickstart, sources and licences | — |

### MIMIC-IV data rules (added to CLAUDE.md at the start of 6a)

- **Storage:** the files live under `$MEDGRAPH_DATA_DIR/mimic`, verified against PhysioNet's SHA256SUMS.
- **Outputs:** scripts print and write aggregates only. Counts under 10 are suppressed in anything written to the repository.
- **Tests:** those marked `mimic` run locally, never in CI.
- **Models:** local only.
- **Sessions:** no row and no note is ever shown in an assistant session. Anything that needs a person to read notes, such as reference labels for the note check, is done by the user, on a page the local app serves.

### Phase 6 evaluation, fixed before any model is trained

- **Task:** one primary task, written down before training.
- **Split:** by patient, with a held-out test set that is run once and reported whatever it shows. This is the same rule as for extraction.
- **Leakage controls:** inputs come strictly before the prediction time. The label's codes and their listed proxies are excluded.
- **Baselines first:**
  - prevalence;
  - logistic regression on code counts and the last lab values;
  - gradient boosting;
  - medgraph's own KDIGO and WHO criteria.
- **Metrics:** AUROC, AUPRC and Brier score, with bootstrap confidence intervals.

## Consequences

- **Drug–drug interactions** stay limited to notes that quote a label. A later 4c could build on the stored label sections "Contraindications" and "Drug Interactions", with its own source decision and ADR. The README no longer promises more than that.
- **Dependencies:** each is added when a sub-phase uses it. FastAPI and uvicorn come in 5a, scikit-learn in 6b, and torch and PyG in 6c.
- **Code moves:** in 5a, the scripts that now build pages and read the explanation cache (`view_patient.py`, `explain_flags.py`) start calling the moved modules in `src/`.
- **ADR 0001:** this ADR settles the frontend decision that ADR 0001 left open.
