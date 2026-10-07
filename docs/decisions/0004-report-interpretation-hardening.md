# 0004: Hardening lab-report interpretation (Phase 1e)

- Status: proposed (awaiting the user's review; evidence complete)
- Date: 2026-10-07

## Context

The Phase 1 review ([ADR 0003](0003-lab-report-extraction.md), technical report) left open decisions. The user asked whether to handle them before or after Phase 2. Three of them change what a report row is when it reaches the patient graph: whether it is accepted at all, whether its date is certain, and which eGFR equation it carries. Phase 2 stores rows, so these come first. The fourth, LLM-proposed names that a person confirms, needs a review screen and moves to Phase 5.

1. **A grounded value in the wrong field.** On the reports-v1 test split the model copied two one-sided ranges as values (`< 200` from `214 mg/dL (rif. < 200)`). The grounding check can't see this, because the text is on the page.
2. **Guessed conventions.** English reports were read as day-first or month-first by whether they printed SI units. The decimal separator came from the language. On the synthetic data both were right by construction. On real reports a wrong guess swaps day and month (`03/04/2025`), or reads `1.200` as 1.2 instead of 1200.
4. **eGFR equation.** FHIR results carry it through their LOINC code; report rows carried none.

## Decision

**Row-order check: the new status `misplaced`** (`misplacement` in `ingest/lab_report.py`). In every layout a result row reads name, value, unit, range. After grounding, a row must satisfy three conditions in the whitespace-squashed report text:
- its value occurs after its analyte name, within 100 characters, with **no reference-range marker** in between: `ref`, `rif`, `riferimento`, `reference`, `range`, `intervallo`, or a bracket opening onto a number or comparator, such as `(4,0` or `[< 200`;
- a non-empty unit follows the value within 60 characters;
- a non-empty range follows that, within another 60.

A row that fails is rejected as `misplaced`: kept for review and never used. The check runs before name mapping, so out-of-scope rows are checked as well.

**Conventions from evidence, not guesses** (`report_conventions`):
- **Language:** from vocabulary, as before.
- **Decimal separator** (`numbers.decimal_separator_evidence`): only a number with one separator followed by 1, 2 or 4+ digits proves which character is the separator (`13,5`, `0.70`). Three digits (`1.200`) prove nothing. Dates, and clock times after a time word (`ore 08.30`), are skipped. With no proof, a value such as `1.200` is refused as ambiguous, and every other value reads the same either way. Proof that contradicts the language, or both separators in one report, is recorded as an issue.
- **Date order** (`time.date_order_evidence`): only a date with one field above 12 proves the order. Equal fields (`09/09/2024`) prove nothing about the other dates. Italian reports are day-first by convention.
- **An ambiguous English date with no proof is refused (user decision).** No collection date is stored, and the issue reads "date order unclear". A date whose own digits settle it (`19/02/2024`) is always read.

**eGFR equation:** `ReportRow.method`, taken from the printed name (`analytes.egfr_equation`): `MDRD`, `CKD-EPI 2021`, `CKD-EPI (year not stated)` or `unspecified`. A name that doesn't state the equation is not assumed to use one.

**Metrics** added to `scripts/evaluate_extraction.py`:
- misplaced rows (rejected);
- values refused as ambiguous;
- collection date **not stored** and **wrong**, which replace "locale detection";
- language detection;
- decimal separator **wrong** and date order **wrong**.

The safety numbers remain "wrong values accepted", plus now "collection date wrong".

**A fresh test set, reports-v2** (`configs/lab_reports/reports-v2.yaml`). reports-v1, test split included, was examined while these checks were developed, so it now counts as development data.
- **Size:** 84 test-only reports, 6 per (family, language) cell, as text and PDF.
- **Patients:** none from reports-v1. They are excluded by the patient tags in its headers, so reports-v1 itself is unchanged; regenerating it gives the same digest, `e47d7ca5…`.
- **Layouts:** one new held-out family, `vertical` (one labelled field per line). Only that family counts as unseen.
- **Names:** held-out names at a rate of 0.25 (0.5 in reports-v1). That gives more accepted rows and therefore a tighter bound on wrong values; refusal of unknown names was already measured.
- **Digest:** `dda930bb…`; 84 reports, 619 rows, 147 in-scope rows under known names, 26 in-scope rows with a one-sided range.
- **Pipeline:** the prompt is unchanged (`transcribe-v3`).

## Evidence

**reports-v1, re-scored from the cached transcriptions** (no new LLM calls; development data from now on):
- **Row-order check:**
  - It rejects exactly the two range-as-value rows found in Phase 1, and both were genuinely wrong.
  - **It rejected no correct row:** none of the LLM's 186 development rows or 404 test rows (text and PDF), and none of the rules rows.
  - Value metrics are unchanged for both methods: end-to-end, seen names 100% (LLM) and 69.7% (rules) on test; wrong values accepted 0.
- **Dates:**
  - **No wrong date.**
  - No date stored for 4 of 24 development reports and 7 of 48 test reports. Each is an English report whose only dates have both fields ≤ 12 (for example `06/02/2020` with report date `06/03/2020`). Before this change those dates were read correctly, but only because the SI-unit guess matched how the generator works.
  - No decimal-separator error, no value refused as ambiguous, and language detection 100%.
- **A bug caught by the new "date wrong" metric.** The first version counted a date with equal fields (`09/09/2024`) as proof of day-first. Two en-US reports then had their collection date swapped (`09/08/2024` read as 9 August). Equal fields now prove nothing, and a test pins the case.

**reports-v2 (fresh, run once, prompt v3 unchanged).** 84 reports, 619 rows per format. Results: [LLM](../results/extraction-qwen35-9b-r2-test.md) and [rules](../results/extraction-rules-r2-test.md). Intervals: bootstrap over reports, or exact binomial (`*`) where the observed rate is 0% or 100%.

| | LLM, text | LLM, PDF | rules, text |
| --- | --- | --- | --- |
| end-to-end, seen names | **147/147** (97–100\*) | 147/147 | 56.5% (43–69) |
| **wrong values accepted** | **0 of 150** (0–3\*) | 0 of 150 | 0 of 84 (0–5\*) |
| rows rejected as misplaced | 0 of 619 (0–1\*) | 0 of 618 | 0 |
| **collection date wrong** | **0 of 84** (0–5\*) | 0 of 84 | 0 of 84 |
| collection date not stored (refused) | 7 of 84 | 7 of 84 | 7 of 84 |
| decimal separator / date order wrong | 0 / 0 | 0 / 0 | 0 / 0 |
| row recall, unseen `vertical` layout | 100% | 100% | 0% |

- **Safety.** No wrong value and no wrong date were accepted, in text or in PDF. The bound on wrong values accepted tightened from 6% (reports-v1) to 3%.
- **No false rejections on fresh data.** The row-order check rejected none of the 619 rows, including every row in the unseen `vertical` layout.
- **The catch rate is not measured on fresh data.** The model never copied a range as a value on reports-v2, although 26 in-scope rows had one-sided ranges and all 18 of them under known names were accepted correctly. Evidence that the check *catches* this error remains the two reports-v1 rows and the unit tests.
- **Generalization held.** The LLM delivered every in-scope value printed under a known name, the 17 on the unseen `vertical` layout included. Held-out names mapped 3 of 54, again only through the parenthetical rule.
- **Remaining transcription errors** (text):
  - 43 units copied into `value`: en-US and en-GB colon and two-column layouts, plus one Italian sections row. Code splits them off; end-to-end on known names is 100%.
  - 5 flag or range slips in the narrative and two-column layouts: a flag dropped, `alto` returned as `H`, a flag added, and a flag copied into a range.
  - PDF only: one missed row.
- **Cost:** median 62 s per report (maximum 185 s); 168 transcriptions took about 3 h.

## Limitations

- **The row-order check assumes name, value, unit, range.** A layout that prints the value first, or the range before the value, would have its rows rejected. That failure is safe (lost recall, no wrong value), and reports-v2's new layout checks it on unseen data. The window sizes are judgement, chosen on reports-v1.
- **The check catches the failures seen so far, not every misplacement.** A value copied from a range without brackets or a label, in a layout where the next row's unit happens to fall inside the window, could still pass.
- **Clock times are skipped only after a time word.** A bare `08.30` in an Italian report counts as proof of a decimal point. A contradiction then surfaces as an issue rather than a wrong value only if the report also contains a decimal comma.
- **Italian reports are taken as day-first by convention.** Evidence still overrides the convention, and a contradiction is recorded.
- **Grounding is weak for one-character fields.** A flag such as `H` occurs somewhere in almost any report, so grounding barely constrains it: on reports-v2 the model returned `H` for a printed `alto`. Flags don't feed rules; values and dates do.

## Consequences

- Phase 2 stores report rows with these statuses. `misplaced`, `ungrounded`, `unmapped` and refused values or dates become review items with their provenance. They are never dropped, and never placed on the timeline by guessing.
- English reports with an ambiguous date have no collection date until a person confirms one. That workflow belongs to the Phase 5 interface, together with confirming LLM-proposed analyte names (open decision 3).
