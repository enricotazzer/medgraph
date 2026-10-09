# 0006: Guideline rules and follow-up flags for CKD and anaemia (Phase 3)

- Status: proposed (awaiting the user's review)
- Date: 2026-10-08
- Revised by [ADR 0007](0007-knowledge-store-and-medication-rules.md) (Phase 4a): every quote is now checked against the stored document, and several were rewritten to match it; dialysis is recognized from procedures; medication rules added; ruleset `rules-v2`.

## Context

medgraph's purpose is evidence-grounded follow-up flags. Phase 3 adds the first rules, for chronic kidney disease (CKD) and anaemia, on top of the patient graph (ADR 0005).

**The user decided** (2026-10-08):
- **CKD persistence** is strict: two abnormal values at least 90 days apart, with no normal value between.
- **For anaemia**, the most recent usable haemoglobin decides.
- **Medication-monitoring flags** wait for Phase 4, with the drug labels they must cite.

**Already fixed by the project spec:** eGFR is computed with CKD-EPI 2021.

## Decision

### Sources first (`rules/sources.py`)

Every threshold, equation and interval cites an entry that names the document and the place in it, and quotes the passage verbatim. Each quote was checked on 2026-10-08 against the primary text:

| ID | Source | Used for |
| --- | --- | --- |
| `ckd-epi-2021` | Inker et al., N Engl J Med 2021;385:1737-49; coefficients as published by NIDDK and the NKF, which agree | computed eGFR |
| `kdigo-2024-criteria` | KDIGO 2024, Figure 3 (p. S139); Practice Points 1.1.3.1-2 (p. S149) | eGFR < 60 or ACR >= 30 mg/g; chronicity |
| `kdigo-2024-categories` | KDIGO 2024, CKD nomenclature (p. S126) and albuminuria categories (p. S127) | G1-G5, A1-A3 |
| `kdigo-2024-monitoring` | KDIGO 2024, Practice Point 2.1.1 | GFR and albuminuria at least annually |
| `kdigo-2012-anaemia-testing` | KDIGO 2012 anaemia guideline, Recommendation 1.1.1 | Hb at least annually (CKD 3) or twice yearly (CKD 4-5 not on dialysis) |
| `kdigo-2012-anaemia-intervals` | KDIGO 2012, Recommendations 1.1.2 and 3.12.1-3 | with anaemia or on ESA, intervals are shorter |
| `who-2024-cutoffs` | WHO 2024 haemoglobin guideline, Table 2 (p. xi) | cutoffs by age, sex and pregnancy |
| `who-2024-smoking` / `who-2024-elevation` | WHO 2024, normative statements 2.a.2 and 2.a.1, Tables 5 and 4 | adjustments |
| `who-2024-age-gap` | WHO 2024, research gaps | no cutoff over 65 years |
| `mdrd-2006` | Levey et al., Ann Intern Med 2006;145:247-54, as published by NIDDK | a data check only, never a rule |

A test fails if a constant in the code does not appear in the quote of the source it cites.

### eGFR (`rules/egfr.py`)

- **Computation:** CKD-EPI 2021 for each usable creatinine, in `Decimal`, using the age in completed years on the sample date.
- **Sex:** taken from the FHIR administrative gender as a proxy, and stated on every result.
- **Not computed:** under 18 (the equation is for adults), for gender `other` or `unknown`, and for a creatinine reported with a comparator.
- **Rounding:** categories use the value rounded to a whole number (half up); the unrounded value is kept.
- **Display:** the computed series is shown apart from any reported eGFR and labelled as computed.

### CKD criteria (`rules/ckd.py`)

- **Markers:** computed eGFR < 60, or urine ACR >= 30 mg/g.
- **Current and persistent:** the run of consecutive abnormal values that ends at the most recent value must span at least 90 days. A normal value breaks the run, so a finding that has since normalized is not "currently met".
- **Categories:** G and A are reported for the latest values.
- **Comparators:** an ACR whose comparator leaves its category open is not used.
- **Evidence:** the three values that establish persistence (the first abnormal value, the first at least 90 days later, and the latest). The whole run is on the timeline.

### Anaemia (`rules/anaemia.py`)

- **Cutoff:** the most recent usable haemoglobin is compared with the WHO 2024 cutoff for the patient's age and sex on that date.
- **Pregnancy:** a pregnancy recorded over the sample date uses the pregnancy cutoffs. The trimester is not recorded, so only a value between 105 and 110 g/L is not assessable; below 105 or from 110, every trimester gives the same answer.
- **Smokers:** a current smoker, by the latest smoking status on or before the sample date, gets WHO's +3 g/L for "smoker, quantity unknown".
- **No elevation adjustment:** residence elevation is not recorded, which amounts to WHO's adjustment of 0 for 1-499 m. Every result says so.
- **Not assessable:**
  - under 6 months and over 65 years (WHO 2024 sets no cutoff, and lists people over 65 as a research gap);
  - from age 15 when the gender is other or unknown.

### Matching diagnoses (`rules/diagnoses.py`)

Explicit code lists, built from the cohort's codes, each with its display:
- **Kidney diagnoses:** CKD stages 1-4, end-stage renal disease, kidney transplant states, diabetic kidney disease, albuminuria or proteinuria due to diabetes, renal dysplasia.
- **Anaemia diagnoses:** SNOMED 271737000, and ICD-10 D46.4.
- **Pregnancy.**
- **Smoking-status texts.**

A diagnosis covers a finding if it is recorded by the evaluation date and not abated before the finding's episode began. This is data matching, not guideline content.

### Flags (`rules/flags.py`)

| Rule | Flags when | Source |
| --- | --- | --- |
| `ckd-criteria-no-diagnosis` | CKD criteria met; no kidney diagnosis covers the episode | KDIGO 2024 criteria, categories; CKD-EPI 2021 |
| `anaemia-criteria-no-diagnosis` | latest Hb below the WHO cutoff; no anaemia diagnosis covers it | WHO 2024 |
| `ckd-gfr-follow-up` | CKD recorded (stage code ongoing) or criteria met; no GFR test (creatinine or eGFR) in the 12 months before the evaluation date | KDIGO 2024 PP 2.1.1 |
| `ckd-albuminuria-follow-up` | as above; no urine ACR (for a child, also urine protein) in 12 months | KDIGO 2024 PP 2.1.1 |
| `ckd-haemoglobin-follow-up` | CKD stage 3 (12 months) or 4-5 not on dialysis (6 months), from the latest computed eGFR category, else the recorded stage; no haemoglobin in that window | KDIGO 2012 Rec 1.1.1; Recs 1.1.2 and 3.12 show that the intervals with anaemia or ESA are shorter, so a test overdue under 1.1.1 is overdue in every case |

How the flags work:
- **Tests:** a test counts as done when it is recorded, even if its value could not be used.
- **Exclusions:** kidney failure, dialysis and transplant are outside the monitoring rules, which report them as not assessable.
- **Deceased patients** are not evaluated.
- **Evaluation date:** for Synthea, the simulation end date (2026-01-01). Data dated after it is ignored.
- **Wording:** a flag states what the record shows, cites its sources, lists its limitations, and ends by suggesting a doctor. It never states a diagnosis.
- **Results:** every result names the ruleset (`rules-v1`).

### Storage and viewer

- **Storage:** results are derived data, in the store's `rules` table per patient, ruleset and evaluation date (`make flags`). Recomputing gives the same result; the notebook checks this.
- **Viewer:** the patient page gains a flags panel with evidence linking to records, sources with their quotes, limitations, and every check medgraph ran. The computed eGFR appears as its own series.

### Deviations from the approved plan

- **Smoking adjustment.** The plan said it could not be applied, for lack of cigarettes per day. WHO's Table 5 has a "smoker, quantity unknown" row (+3 g/L), and Synthea records smoking status, so it is applied.
- **Pregnancy** is assessed wherever the trimester cannot change the answer, instead of always being not assessable.
- **The haemoglobin follow-up rule** was conditional on verifying KDIGO 2012; it was verified and is included. It also covers patients with anaemia, through the shorter-interval argument above.
- **"Currently met".** The plan's persistence rule is applied to the run ending at the latest value.
- **Added a data check** with MDRD (below). No rule uses MDRD.

## Evidence (dev-1000; synthetic data, a pipeline test)

From [the flags report](../data/synthea-dev-1000-flags.md):
- **Population:** 1,148 patients; 1,000 evaluated, plus 148 deceased (not evaluated).
- **Flags:**
  - CKD criteria with no kidney diagnosis: 39;
  - anaemia criteria with no anaemia diagnosis: 42;
  - GFR follow-up: 7;
  - albuminuria follow-up: 68;
  - haemoglobin follow-up: 65.
  - In all, 117 patients (12%) have at least one flag. The counts were identical across two full runs.
- **Not assessable:**
  - CKD: 581 patients, all for lack of usable creatinine or ACR except one child.
  - Anaemia: 171 (119 over 65, 52 under 6 months).
- **Computed eGFR:** 11,120 values; 9 not computed, all under 18.
- **Synthea's reported eGFR does not follow from its own creatinine.** Applying MDRD to the same-day creatinine differs from the reported MDRD value by a median of +23.7 mL/min/1.73 m², and the gap ranges widely (5th to 95th percentile: −9.6 to +64.6). The generator models eGFR and creatinine separately. medgraph's criteria use eGFR computed from creatinine, so they cannot be expected to agree with diagnoses Synthea assigns from its own eGFR.
- **Who the 39 CKD-criteria flags are** (checked, not assumed):
  - 37 of them have no reported eGFR at all; Synthea's kidney module records eGFR for only a quarter of patients.
  - Their creatinine is markedly high and has stayed high for at least 90 days: latest median 2.8 mg/dL, computed eGFR median 21, category G4 in 26 of 39. Yet no kidney diagnosis is coded.
  - In Synthea, these creatinine values and the CKD codes come from different modules. On synthetic data this flag therefore surfaces a generator inconsistency, which is exactly the pattern it exists to catch in a real record.
  - The 21 patients with a kidney diagnosis whose criteria are not met are reported as observed, with no explanation claimed.
- **Tests:**
  - every boundary: eGFR whole-number rounding; ACR 30 and 300; 89 against 90 days; 12 months to the day; each WHO age band; pregnancy 105, 107 and 110 g/L; smoker +3;
  - CKD-EPI against hand-computed values and an independent floating-point implementation over random inputs (Hypothesis), plus monotonicity and continuity at κ;
  - every flag end to end on small FHIR records.

## Limitations

- **Synthetic data, no clinical validation.** Agreement with Synthea's codes is consistency with the generator, and the generator's eGFR is inconsistent with its creatinine. No clinician has reviewed the flags. That review, on real or realistic data, belongs to Phase 7.
- **CKD-EPI 2021 reference values.** No independent table of reference values was found. The coefficients come from two official transcriptions of the paper (NIDDK and NKF), which agree, and the implementation was checked by hand and against an independent implementation of the same published formula.
- **Creatinine in blood.** CKD-EPI 2021 is defined for serum creatinine. Synthea records much of its creatinine as "in blood" (LOINC 38483-4), which is used as is and stated on the result.
- **Acute kidney injury is not excluded.** The strict persistence rule reduces the risk but cannot remove it. Markers other than eGFR and ACR (imaging, histology, urine sediment) are not assessed.
- **Anaemia over 65 is not assessed** (119 evaluated patients), because WHO 2024 gives no cutoff there.
- **Sex** comes from administrative gender.
- **Smoking** is matched by the value's display text, because the graph stores no value code.
- **Elevation of residence** is not recorded, so no elevation adjustment is made.
- **Code lists were built from Synthea's codes.** Real data will use other codes, and the lists must be extended with tests; an unlisted code is never matched.
- **Follow-up flags see only this record.** A test done elsewhere is invisible. Dialysis and ESA treatment are not recognized from medications yet (Phase 4), so kidney failure and transplant are excluded through their condition codes. (Phase 4a recognizes dialysis from procedures and ESA through the Epogen label rule; ADR 0007.)
- **Evaluation date.** It is Synthea's simulation end date; real use evaluates at the current date.
- **Notes are not checked.** Whether a note already acknowledges a finding is a Phase 4 language task.
- **English only.** Flag texts are English. Italian and plain-language explanations come with Phase 4 and 5.

## Consequences

- Phase 4 explains each flag in two registers and two languages, grounded only in its sources and evidence. It also adds medication-monitoring flags from drug labels and recognizes dialysis and ESA treatment from medications.
- A flag's meaning changes only with a new ruleset version; stored results name theirs.
