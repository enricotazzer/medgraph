# 0002: Record model, FHIR ingestion and lab normalization

- Status: accepted
- Date: 2026-10-01

## Context

Phase 1 turns FHIR bundles and, later, lab reports into data that the graph and rules can trust. Every flag must cite the exact value, unit, date and source resource behind it. Synthea data (see `docs/data/`) shows the problems real data will have too: the same LOINC code in two units, impossible values, events dated after death, and condition stages that pile up.

## Decision

**Scope (user decisions)**
- Besides Patient, Encounter, Condition, MedicationRequest, Observation and Procedure, the parser reads **DiagnosticReport**, for lab panels, and **DocumentReference**, for notes.
- Free-text extraction (Phase 1d) will use **qwen3.5:9b** via local Ollama, compared against a rule-based baseline. Only **PDFs with a text layer** are supported; scanned reports are rejected with a clear message.

**Record model** (`medgraph/records.py`)
- Every record carries a `SourceRef`: a hash of the source file, plus the resource type and ID.
- Values are `Decimal`, as written, and are never overwritten.
- Times are a `Timepoint`: the calendar date as written, its precision (year, month, day or instant), and an instant only when the source gives a timezone offset.
- Data problems become `IngestIssue`s on the record, not exceptions. A broken resource is skipped with an error issue, and the rest of the bundle is still read.
- Identical note text in the same encounter becomes one note that lists every resource carrying it. Synthea writes each note twice.

**Normalization** (`medgraph/normalize/`)
- **Nothing is guessed.**
  - Unknown unit spellings are reported.
  - A number such as `1,234` without a locale is rejected as ambiguous.
  - A FHIR time of day without a timezone offset is rejected.
  - A numeric report date is read day-first or month-first only according to the stated locale.
- **Analyte registry:** creatinine, eGFR (as reported, tagged with its equation from the LOINC code), urine albumin/creatinine ratio, urine albumin, urine protein, hemoglobin, hematocrit, MCV and ferritin. Each has its LOINC codes, a canonical UCUM unit and its conversion factors. Non-trivial factors carry a cited source, and a test enforces this. Other analytes stay unmapped.
- **Incompatible units are not converted.** eGFR in `mL/min` cannot become `mL/min/1.73m²` without body surface area.
- **Sanity bounds** are wide engineering limits for catching unit mix-ups and corrupt values, not clinical criteria. Values outside them are kept and shown but marked `implausible`.
- **Only `LabResult`s with status `ok` may feed rules.** Every other status names its reason, so a flag can say what it could not use.
- **Reference ranges** come only from the source. medgraph never supplies its own.

## Evidence

- **Unit tests:** 240, including property-based round trips for every unit conversion and for numbers formatted in Italian and English.
- **Full cohort:** all 1,148 dev-1000 patients parse with every in-scope resource accounted for, and the only issues are the 136 known encounters after death (`make test-synthea`).
- **Normalization over dev-1000** (`docs/data/synthea-dev-1000-normalization.md`):
  - 17,376 creatinine values are usable; 46 between 40.4 and 97.3 mg/dL are marked implausible.
  - 8,051 eGFR values are usable; 3,490 in `mL/min` are marked incompatible.

## Consequences

- Rules (Phase 3) compute CKD-EPI 2021 eGFR themselves from usable creatinine values. Reported eGFR is shown, labelled with its equation, and is never the sole basis of a flag.
- `gender` is FHIR administrative gender. Sex-specific rules (CKD-EPI, WHO hemoglobin thresholds) use it as a proxy and must say so.
- MedicationAdministration (in-hospital administrations; 16,960 in dev-1000) is not read. Monitoring rules work from prescriptions (MedicationRequest). Revisit if a rule needs administrations.
