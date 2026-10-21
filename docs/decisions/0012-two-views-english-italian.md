# 0012: Two views of the patient page, in English and Italian (Phase 5b)

- Status: proposed (awaiting the user's review)
- Date: 2026-10-21

## Context

ADR 0010 planned Phase 5b: a clinician view and a personal view of the same patient page, each in English and Italian. Until now the page had one dense, English-only layout (ADR 0005), with the follow-up flags in the rules' own technical wording (ADR 0006, 0007).

**The user decided** (2026-10-21):
- **Look:** a calm, health-app style for the personal view. Cards come first, with large readable numbers and charts that draw the thresholds.
- **Model text:** the personal view shows no model text, only fixed texts. This settles ADR 0009's open decision for that view.
- **Scope:** "Explore connections" (the graph, on request), results charts, search on the patient list, and "Print for my doctor".

## Decision

### One page, two views, two languages

- **Switches in the header:** a Personal/Clinician switch and an EN/IT switch. The choice is kept in the page's address (`#view=…&lang=…`) and remembered by the browser where storage is allowed.
- **Default:** the page opens on the personal view, in English.
- **Breadcrumbs** (Cohorts › cohort › patient) appear only when the app serves the page, since in a file written by `make view` they would point nowhere.
- **Lazy panels:** the graph and the timeline are built the first time they are shown.
- **Same page everywhere:** `make view` writes, and the app serves, the same page (ADR 0011).

### The personal view

- **"Worth discussing with your doctor":** one card per flag, and one card per label note under "Also noted". Each card has:
  - a title;
  - what the record shows;
  - why it may matter, paraphrasing the cited source;
  - the flag's closing doctor sentence;
  - "What this is based on", opened on request: the values with their dates (each opens its record), the source's citation and quote, and the limit of the check.
- **When nothing is flagged,** the page says the checks found nothing to raise, and that they cover only a few criteria, tests and medicines, so they are not a health check.
- **Results over time:** eGFR, haemoglobin and urine albumin, each with its latest value in large type.
  - **Which eGFR:** the one medgraph computes from creatinine (CKD-EPI 2021), which is what the rules use; the recorded eGFR only when there is no creatinine.
  - **Threshold lines:** dashed, each labelled with its value and source (for example "60 mL/min/1.73 m2 · KDIGO 2024").
- **Medicines and conditions:** the active ones, then the earlier ones, opened on request. Names are as recorded.
- **"Explore connections"** opens the graph and the record details. A record opened from a card does the same.
- **"Print for my doctor"** prints a sheet with:
  - the patient line, the synthetic-data banner and the date of the checks;
  - each flag and note with what it shows, why, the citation, a table of its values, its limit and its closing sentence;
  - the not-a-medical-device statement.

  It has no graph and no model text.

### The plain-language texts (`rules/plain.py`)

Each rule that raises a flag or a note has a fixed English and Italian text, written once, never by a model.

**Content:**
- **"What the record shows"** is filled in from the flag's evidence and its rule's facts: values, dates, the follow-up interval, a recorded CKD stage. It never uses numbers typed into the text.
- **"Why it may matter"** paraphrases one source the flag cites. Every number it uses appears in that source's quote or citation (a test).

**Tests,** run on flags and notes the real rules raise:
- every flag and note rule has texts;
- every text passes the explanations' wording checks (no diagnosis, no treatment instruction) and language check;
- each flag's text ends with the flag's own closing sentence.

**On dev-1000:** the texts were built for all 140 patients with a flag or a note, 259 flags and notes in all, as each page evaluates them now. None failed a check.

**Fallback:** a flag whose text cannot be built shows the rule's own English text, marked as such, so a page never fails on it. For example, a future rule without texts yet. The tests check that no real rule falls back.

**Choices in the wording, for the user's review:**
- **Impersonal Italian:** "nel record …" rather than "tu" or "Lei".
- **Terms:** US drug labels are "scheda tecnica statunitense"; NSAIDs are "FANS".
- **Hedged attribution:** "KDIGO 2024 advises …" and "The US metformin label lists …".
- **The record's own diagnosis:** a recorded stage reads "A diagnosis of chronic kidney disease stage N is recorded". My first wording, "Your record lists chronic kidney disease stage N", failed the diagnosis-wording check, and the check was right.

### Facts the rules now record (`rules/model.py`)

`Assessment` gains `thresholds`, `interval_months` and `recorded_stage`. These are values the rules already computed but kept only inside their summary sentences.

- **Thresholds:**
  - the CKD markers (eGFR 60, ACR 30 mg/g);
  - the WHO cutoff the anaemia rule applied, converted to the series' g/dL, and only when there is a single cutoff (in pregnancy there are three);
  - the metformin label's 30 and 45.
- **Hashes and the ruleset are unchanged:** `Flag` is unchanged, so flag hashes, and with them the cached explanations, still match. `RULESET` stays `rules-v2`, because no result changes.
- **Stored results:** `make flags` was re-run so the stored results carry the new fields. The review notebook checks that re-evaluating gives exactly the stored result. The flags report changed only in its provenance line: every count is the same.
- **Metformin constants:** the label thresholds were bare numbers. They are now named constants, and a test checks them against the label quotes, closing a gap in the "every threshold is in its quote" rule.

### The clinician view

- **Layout:** the earlier page, with a summary table of the flags at the top.
- **Italian:**
  - each flag shows its plain Italian title and texts, then the rule's English text, labelled as such;
  - model explanations keep their own language switch;
  - limitations, assessment summaries and record text stay in English.

### Interface text (`graph/static/strings.json`)

- **One table:** all interface text is in one table, 213 keys per language, embedded in the page like its data.
- **Tests:**
  - both languages have the same keys, with the same `{variables}`;
  - every key the page uses exists;
  - the synthetic banner is recognised, so it can be translated.
- **Dates:**
  - Italian: day/month/year;
  - the English personal view: "30 December 2024";
  - the English clinician view: ISO.

### Patient list (the app)

- **Search and filters:** search by patient ID or condition, filters for "with flags" and "with a lab report", and a choice of condition.
- **How it works:** a small inline script reads escaped data attributes and writes no HTML.

### Fixed along the way

The Italian template of the explanations (`agent/explain.py`) said "negli ultimi 12 mesi" for every haemoglobin follow-up. It now takes the interval from the flag: 6 months in CKD 4–5. Explanations cached before the fix keep the old sentence.

## Checks

- `make check`: 824 passed, 46 skipped (opt-in).
- **Page check in Node** (jsdom, headless Cytoscape) on eight dev-1000 pages, run as the app serves them. The pages cover:
  - CKD criteria;
  - CKD with follow-ups and cached explanations;
  - metformin below 30;
  - anaemia with an RAS-inhibitor note;
  - a note only;
  - haemoglobin every 6 months;
  - no flags;
  - a deceased patient.

  Each check walks:
  - the personal view in English and Italian: card count and titles, charts, threshold lines, the all-clear text;
  - "Explore connections", a record opened from a card, and the print sheet;
  - the clinician view in both languages: summary rows, timeline charts, a rebuild after a language switch.

  All passed with no script error. The Phase 4b explanation check also still passes on three of them.
- **Not checked: the look.** Headless Chrome-based browsers cannot render in the assistant's session. jsdom checks behaviour, not layout. The look rests on the user's visual check.

## Limitations

- **The texts and the Italian are the assistant's.** They need the user's review, as does the plain-language register.
- **Record text stays in English:** condition and medicine names, evidence labels, source quotes, and the rules' summaries and limitations. The Italian page says so.
- **Number format:** numbers keep the decimal point in Italian (for example "1.4 mg/dL").
- **English only:** the cohort and patient lists and the concept map.
- **Screen readers** were not tested. The page has `lang` attributes, labelled switches and keyboard focus styles.

## Consequences

- **5c** (uploads, confirmations, deletion) adds its screens in this shell. The personal view is where an uploaded record will first be seen.
- **A new rule** needs its plain texts in `rules/plain.py`, or its flags show the rule's English text. The coverage test fails until they are written.
