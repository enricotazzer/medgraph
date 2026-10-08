# Data

Data files never live in this repository. They go in `MEDGRAPH_DATA_DIR`:

```text
$MEDGRAPH_DATA_DIR/
  tools/synthea/v4.0.0/synthea-with-dependencies.jar   # pinned by SHA-256
  synthea/<cohort>/
    fhir/            # one FHIR R4 transaction bundle per patient, plus hospital and practitioner bundles
    metadata/, x12/  # other Synthea exports (not covered by the content digest)
    synthea.log
    MANIFEST.json    # config snapshot, command, versions, per-file SHA-256, content digest
  lab_reports/<set>/
    dev/, test/      # <id>.txt, <id>.pdf (text layer) and <id>.truth.json (exact printed fields)
    MANIFEST.json    # config snapshot, per-file SHA-256, content digest
  runs/
    extraction/<config>/<split>/
      predictions/<format>/<id>.json  # LLM transcription + cache key (text hash, model, prompt)
      metrics.json   # scores per report, provenance (report set, git state, code and model digests)
    logs/            # background LLM runs (make extract-llm), cohort graph builds
  graphs/<cohort>.sqlite   # patient graphs (make graphs): tables graphs, nodes, edges, meta
  views/<cohort>/<patient>.html  # one patient's offline page (make view PATIENT=...)
```

This folder holds only aggregate, synthetic reports:
- the cohort profile ([`synthea-dev-1000-profile.md`](synthea-dev-1000-profile.md));
- the outcome of ingestion and normalization over the whole cohort ([`synthea-dev-1000-normalization.md`](synthea-dev-1000-normalization.md));
- the patient graphs built from it, with their invariant checks ([`synthea-dev-1000-graphs.md`](synthea-dev-1000-graphs.md), Phase 2, ADR 0005).

## Cohorts

| Cohort | Config | Patients (deceased) | Size | Generation time | Content digest |
| --- | --- | --- | --- | --- | --- |
| pilot-10 | `configs/synthea/pilot-10.yaml` | 12 (2) | 38 MB | 9 s | `0eaba7ec…` |
| dev-1000 | `configs/synthea/dev-1000.yaml` | 1,148 (148) | 4.2 GB | 52 s | `921e427d…` |

Synthea exports patients who die during the simulation in addition to the requested number of living ones. Timings are from an Apple M3.

## Lab-report sets

| Set | Config | Reports (dev / test) | Rows per format | Content digest |
| --- | --- | --- | --- | --- |
| reports-v1 | `configs/lab_reports/reports-v1.yaml` | 24 / 48 | 591 | `e47d7ca5…` |
| reports-v2 | `configs/lab_reports/reports-v2.yaml` | 0 / 84 | 619 | `dda930bb…` |

These are synthetic reports for the extraction evaluation (Phase 1d), printed by `scripts/generate_lab_reports.py` from dev-1000 lab values using code templates, never an LLM. Each report has a ground-truth file holding the exact printed string of every field.

- **Splits.** Patients don't overlap between splits. The test split adds two layout families and held-out analyte names that development never shows.
- **reports-v2** is test-only and fresh (Phase 1e, ADR 0004). None of its patients appear in reports-v1, and it adds a held-out layout, `vertical`. Since Phase 1e, reports-v1 counts as development data, because its test split was examined.
- **Reference ranges** printed on these reports are illustrative test data, not clinical ranges.
- **Every report's header** names a synthetic laboratory and marks the report as synthetic, for testing only (all 72 checked).

Results: [`docs/results/`](../results/). Design and limitations: [ADR 0003](../decisions/0003-lab-report-extraction.md).

## Patient graphs

| Store | Config | Patients | Nodes / edges | Size | Build time | Store digest |
| --- | --- | --- | --- | --- | --- | --- |
| dev-1000 | `configs/graphs/dev-1000.yaml` | 1,148 | 977,475 / 1,152,231 | 1.3 GB | about 10 min | `17bc4588…` |

Built by `scripts/build_graphs.py` (ADR 0005). The 156 synthetic lab reports of reports-v1 and reports-v2 are filed in their patients' records, from the saved LLM transcriptions of their text versions. Every graph is checked against the invariants in `graph/check.py` before it is stored. The build time is from an Apple M3.

## What the dev-1000 profile shows

These are properties of the Synthea generator, not of any population. They matter for design; they are not findings.

**Kidney function**
- eGFR is present only as LOINC `33914-3`, the **MDRD** equation, in 25% of patients. There are no CKD-EPI codes. The rules must compute CKD-EPI 2021 from creatinine, age and sex, and report how that compares with the stored value.
- The same eGFR code is recorded in two units, `mL/min/{1.73_m2}` (8,051 values) and `mL/min` (3,490 values), with different distributions (medians 53.6 vs 22.3). These units are not interconvertible without body surface area, so normalization must flag the mismatch rather than convert.
- Creatinine comes under two codes: `38483-4` (blood, 44% of patients) and `2160-0` (serum/plasma, 15%). Both are needed.
- Some creatinine values are physiologically implausible (maximum 97.3 mg/dL). Plausibility checks belong in normalization.

**Albuminuria**
- The urine albumin/creatinine ratio exists only as `14959-1` (mg/g), in 6% of patients. There is no `9318-7`. Albuminuria criteria can be tested only in this small subgroup.

**Hemoglobin**
- Hemoglobin (`718-7`, g/dL) exists for every patient, but the median is 2 values per patient. 936 patients have two or more values at least 90 days apart.
- "Anemia (disorder)" is coded for 33% of patients. Whether the coded anemia matches the hemoglobin values is a Phase 3 question, to be answered with the tested rules.

**HbA1c**
- The HbA1c distribution is implausible (median 3.94%, minimum 2.32%). This matters if prediabetes and diabetes rules are added later.

**Conditions**
- CKD is coded in stages. Earlier stages stay listed as the disease progresses: one patient's note lists stages 1 to 4 together. Ingestion must handle clinical status and abatement.

**Notes**
- Every encounter has a templated note (median 1,333 characters). It appears both as a DocumentReference and as a DiagnosticReport's `presentedForm`.
- Notes are generated from the coded record and list every condition and medication. Any coded finding is therefore trivially "acknowledged", and an uncoded one never is. On Synthea, the notes-acknowledgement check can only be a pipeline test; its real evaluation needs the hand-annotated MIMIC-IV-Note set.

**Data quality**
- 136 of the 148 deceased patients have an encounter dated after their death. Ingestion must tolerate this rather than reject such records.
- Several codes mix unit spellings: `{presence}` numeric values alongside coded values for urine dipsticks; `pH` vs `[pH]`; `U/L` vs `[iU]/L`; `pg/mL` vs `ng/L`; and RDW in both `fL` and `%` (two different measures under one code).
