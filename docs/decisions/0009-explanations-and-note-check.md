# 0009: Checked explanations and the note check (Phase 4b)

- Status: proposed (awaiting the user's review)
- Date: 2026-10-19

## Context

Phases 3 and 4a raise follow-up flags from deterministic rules, each with its evidence and a verified, quoted source (ADR 0006, 0007). Phase 4b adds the two language tasks the project gives a language model:

- **explanations** of each flag, for a clinician or for the person whose record it is, in English or Italian;
- a **note check**: does a recent clinical note mention what the flag is about?

The non-negotiables set the frame. The model never decides whether a flag is raised. Every statement must be traceable to the record or a cited source. The system never says a person has a disease.

**The user decided** (2026-10-08): a note that mentions the finding annotates the flag; it never removes or reorders it.

**Model:** `qwen3.5:9b` through local Ollama, the extraction model of ADR 0003, as named in the Phase 4 plan the user approved. Thinking is off, with temperature 0 and a fixed seed.

## Decision

### The evidence pack (`agent/evidence.py`)

An explanation may say only what its pack holds. Every item has an ID, and the pack is the same for all four variants of a flag:

| ID | Item | From |
|---|---|---|
| `F` | the flag's title and statement | the rule |
| `E1`… | each piece of evidence: date, record, value, how the rule used it | the rule |
| `L1`… | the rule's limitations | the rule |
| `S1`… | each source the rule cites, with its verified quote | `rules/sources.py` |
| `P1`… | 4 passages retrieved from the knowledge index | hybrid search (ADR 0007) |

- **The query** is English text built from the flag's title and statement, without dates or numbers, whatever the language of the explanation.
- **The search is limited** to the guideline documents plus the labels of drugs in the flag's own evidence. ADR 0007 planned on the labels of all the patient's drugs; those would invite comments on medicines the flag is not about.
- **Left out:** passages that hold an `S` quote, which would repeat it, and a passage sharing at least 80% of its words with a better-ranked one. A guideline's executive summary often repeats a table from its body. In the first run, two of four slots went to WHO Table 3 twice (96% shared words; other pairs shared 15–17%).
- **A passage is named by its document's citation**, so a sentence saying "the WHO guideline" can be checked against the passage it cites. Pages show only a passage's place and ID, never its text (ADR 0008).

### Explanations (`agent/explain.py`, prompt `explain-v3`)

The model returns at most 6 sentences, each with the IDs it relies on. The verifier checks each sentence and drops any that fails. The model's text is used if at least 2 sentences pass and one of them cites `F` or an `E` item. Otherwise the explanation is the rule's template:
- in English, the flag's own text and its limitations;
- in Italian, a translated title and reason, the values, and the rule's text quoted in English.

Each explanation records which it is and why.

**Code adds the last sentence.** It is the flag's own suggestion of a doctor ("This may be worth discussing with a doctor.", "A doctor can say whether this test is due.", "A doctor can say what this means for the prescription."), or its Italian version. A model-written doctor sentence has nothing to cite, so v1's instruction to write one guaranteed a dropped sentence. The suggestion must not depend on the model either.

**The prompt's rules:**
- cite only the IDs listed after the items, and never write an ID in the text;
- every number and date must be in a cited item, copied as written;
- item `F` says which criterion the record meets, and nothing more is known: never place the person's values in a table, category, stage, severity grade or risk level that `F` and `E` do not name;
- passages are background, used only for what they say about this flag's test, value or medicine, naming the source;
- never say the person has, or does not have, a disease, and never tell anyone to start, stop or change a medicine;
- write no doctor sentence and no description of medgraph.

**Prompt versions.** All of them were tuned on development flags only (see Evaluation).
- `explain-v1` was written before any run.
- `explain-v2` followed the 1-flag smoke run: the closing moved to code, passages became background, and an instruction to write "OMS" for WHO in Italian was added.
- `explain-v3` followed the development run:
  - the list of IDs: follow-up flags have no `E` items, and the model cited a bare "E";
  - no IDs in the text;
  - passages only for this flag;
  - "a missing test is not a criterion met": three of four follow-up explanations said the gap "meets the criteria";
  - v2's "OMS" instruction removed, because the model then wrote "criteri OMS" for KDIGO's criteria.

  v3 still wrote some references in words, so the verifier now removes those that only point at items (below). The misreading "no test … which means the CKD criteria are met" also recurred under v3.

### The verifier (`agent/verify.py`)

Deterministic and unit-tested. A sentence is dropped, with the reason recorded, if any check fails:

| Check | Catches |
|---|---|
| citations | none, or an ID not in the pack |
| numbers | a number in no cited item, compared by value ("1,4" in Italian is 1.4) |
| dates | a date in no cited item, in ISO, numeric, and English or Italian written forms |
| categories | a stage, KDIGO G or A category, anaemia severity grade (including "no anaemia") or risk level not named by a cited `F`, `E`, `L` or `S` item; a passage never licenses one |
| sources | WHO/OMS or KDIGO named, but not by a cited item |
| item IDs | an ID that does work in the prose ("la fonte L1 spiega", "Come indicato in F, …") |
| doctor | a sentence suggesting a doctor (code adds the closing) |
| diagnosis | "you have …", "Lei ha l'anemia", "diagnosed with …" and similar, in English and Italian |
| treatment | an instruction to start, stop or change a medicine, unless reported as a source's statement |
| language | the other language dominating the sentence |

**References are removed before the checks.** This covers references in brackets ("[E1]", "(F, S1)", "(item E1)"), and a trailing phrase that only points at items: ", as stated in S1", ", per limitation L1", ", come indicato da L1". The phrase must follow a comma or open a parenthesis; otherwise it stays, and the sentence is dropped. Removing references changes no claim, and the citations stay in the sentence's list. In the development run, v3 still wrote such tails, and dropping them cost whole explanations.

**The category check came from the smoke run.** The model placed 129.6 g/L in the WHO severity table, which the rule never applies. One Italian sentence read it as "no anemia", the opposite of the flag; another read it as "anemia lieve". Neither contained a number from outside the items, so neither was caught. Categories are compared in canonical form, so "stadio 3", "stage 3", "CKD 3" and "G3" match each other.

**The check is conservative.** On the smoke-run sentences it dropped the two misreadings, and also a correct one ("the WHO guideline defines no anaemia … as ≥130 g/L"), whose category came only from a passage.

### The note check (`agent/notes.py`, prompt `notecheck-v1`)

- **Notes read:** up to 3 of the patient's notes in the flag's period (from its earliest evidence, or the year before the evaluation date), newest first.
- **What the model returns:** a quote of at most 40 words that mentions the rule's finding, the note's ID, and whether the note affirms, negates or is uncertain about it; or nothing.
- **What code keeps:** the quote, only if it occurs in that note word for word (up to case and spacing) and matches the rule's finding terms. The stance is shown as the model's reading.
- **What the finding is:**
  - for "criteria met, no diagnosis" flags, the condition;
  - for follow-up flags, the test, since a test done elsewhere is what the flag cannot see;
  - for metformin flags, kidney function.
- **Effect on the flag:** a kept quote annotates it and never removes or reorders it.

On Synthea, notes are written from the coded record, so an uncoded finding is never mentioned. The check can only test the plumbing here.

### The run (`scripts/explain_flags.py`, `make explain`)

- **Sample:** stratified, up to 6 flags per rule, ordered by a seeded hash of patient and rule, so it ignores the store's order.
- **Cache:** everything is stored under `$MEDGRAPH_DATA_DIR/runs/explanations/<cohort>/` and reused while these are unchanged: the flag, the knowledge and index digests, the embedding model's digest and `PACK_VERSION` for packs, and the chat model's digest and the prompt version for explanations.
- **Order:** packs first, with bge-m3, then the chat model. The private server runs with `OLLAMA_MAX_LOADED_MODELS=1`, so the two never sit in memory together.
- **Outputs:** aggregates go to `docs/results/explanations-<cohort>.md`. A review file with every variant of 2 flags per rule goes to the run folder, outside the repository.

### Evaluation: development flags and the evaluation sample

Tuning on the flags being reported would inflate the results. So:
- **Development flags are never sampled.** Every flag read while the prompt and the checks were developed is listed in `DEVELOPMENT`, in the script, with the run that read it. These are 7 flags:
  - the smoke-run flag;
  - the 6 `--dev 1` flags, one per rule, ranked after the sample (metformin-egfr-30-44 has only 2 flags, both in the sample).

  The script warns if an entry matches no flag, since a mistyped ID would leave a flag in the sample silently.
- **Development runs:**
  - `explain-v1` on 1 flag;
  - `explain-v2` on 6;
  - `explain-v3` on 6, stopped once and restarted, after a pack bug: v2 packs named a passage by its citation alone, which drops "KDIGO".

  The verifier changes after v3 were checked by replaying the recorded sentences, with no model call. Of the 141 sentences v3 wrote, 119 pass the final checks, and 22 of 24 variants would use model text.
- **The 38-flag sample is run once**, with `explain-v3`, `notecheck-v1`, `pack-v3` and the final verifier, and reported whatever it shows.

### Pages

- **Explanation section.** Each flag on the patient page (`make view`) gets one, with a language and reader switch.
- **Citations.** They open the record node or the source panel. A passage shows its place and ID, not its text.
- **Offline.** Explanations are read from the cache. The page never calls the model, and an explanation is shown only for the flag hash it was written for.

## Results

A pipeline test on Synthea, not a finding: [docs/results/explanations-dev-1000.md](../results/explanations-dev-1000.md).
- **The run:** 38 flags × 4 variants in 3 h 11 min (2026-10-19), about 5 minutes per flag, with `qwen3.5:9b` on a 16 GB M3.

**What the checks did** (152 variants, run once):

| variant | model text | kept / returned | dropped |
|---|---|---|---|
| clinician-en | 38 of 38 | 195 / 212 | 17 (8%) |
| personal-en | 38 of 38 | 192 / 225 | 33 (15%) |
| clinician-it | 37 of 38 | 193 / 223 | 27 (12%) |
| personal-it | 30 of 38 | 148 / 228 | 50 (22%) |

- **Model text and templates:** model text in 143 of 152 variants (94%). The 9 templates are all Italian; 8 lacked a kept sentence citing `F` or `E`.
- **Dropped sentences:** 127 of 888 (14%).
  - 59 suggested a doctor; code adds that sentence.
  - 25 used an item ID in the prose.
  - 20 wrote a number, and 15 a date, that is in no cited item.
  - 7 used diagnostic wording, 4 a treatment instruction, and 3 a category from a passage.
- **Note check:** no note mentioned a finding (as expected on Synthea).
  - For 3 metformin flags the model returned a quote, but put the note's whole text in its ID field.
  - The quotes were medication lines ("24 hr metformin hydrochloride 500 mg…"), with none of the rule's kidney-function terms. They would have been refused anyway.

**What the checks miss: the assistant's reading.**
- **What was read:** the 274 kept model sentences of the 14 flags in the review file (2 per rule).
- **How:** each sentence was compared with the items it cites, and labelled ok, misread (says what its items do not, or the opposite), unsupported (a claim no cited item makes), off-topic, or truncated.
- **Status:** this is the assistant's judgement, not the user's, and not a measured rate with a confidence interval. It is in `reading-audit-assistant.jsonl` in the run folder.

| rule | kept | ok | misread | unsupported | off-topic | truncated |
|---|---|---|---|---|---|---|
| anaemia-criteria-no-diagnosis | 39 | 35 | 2 | 1 | 0 | 1 |
| ckd-albuminuria-follow-up | 31 | 23 | 1 | 1 | 6 | 0 |
| ckd-criteria-no-diagnosis | 40 | 36 | 2 | 2 | 0 | 0 |
| ckd-gfr-follow-up | 44 | 37 | 3 | 1 | 3 | 0 |
| ckd-haemoglobin-follow-up | 39 | 33 | 2 | 0 | 4 | 0 |
| metformin-egfr-30-44 | 40 | 35 | 5 | 0 | 0 | 0 |
| metformin-egfr-below-30 | 41 | 36 | 4 | 1 | 0 | 0 |
| **all** | **274** | **235 (86%)** | **19 (7%)** | **6 (2%)** | **13 (5%)** | **1** |

- **Misreadings repeat, which suggests prompt work rather than chance:**
  - a limitation's subject moves, from the check to the label or the guideline ("l'etichetta non esclude cambiamenti acuti", 3 times);
  - "an interruption would not be seen" becomes "no interruption" (twice);
  - a missing test "means the CKD criteria are met", or "are not met";
  - "no diagnosis is recorded" becomes "no diagnosis was made";
  - a plain-language "la malattia" presupposes a disease (twice);
  - a wrong interval read from KDIGO 2012's list ("ogni tre mesi in CKD 5ND").
- **By language:** 17 of the 26 misread, unsupported or truncated sentences are Italian.
- **Off-topic sentences come from passages** on follow-up flags: eGFR changes on an albuminuria flag, children's urine samples, CKD screening.

**Found in the evaluation, fixed afterwards.** The sample's numbers above are as run.
1. **The report printed note text.** A refusal reason held the model's "note ID", which was the whole note. Reasons are now fixed strings, and a report shows only a reason's fixed part (`reported()`).
   - The report was rewritten from the cache; the explanations are unchanged.
   - A full run now writes `run.json`, so a rewritten report keeps the run's provenance. This run's file was copied from its first report.
2. **The reference stripping truncated 1 kept sentence** ("…, secondo quanto."). A match could start inside "secondo quanto indicato da L3". A reference tail must now follow a comma or open a parenthesis.
3. **The note ID is read from its leading `N1`/`N2`/`N3`** when the model echoes the note with it.

These fixes followed a reading of the sample, so **the `explain-sample-v1` sample is now development data**. The next estimate needs a fresh sample: a new seed, excluding these 38 flags and `DEVELOPMENT`.

## Consequences

- **Known gaps:** sentences that pass every check and are still wrong.
  - A sentence can cite the right item and misread it. In the assistant's reading, about 1 kept sentence in 11 is misread or unsupported. The deterministic report cannot count these.
  - Numbers written as words are not checked.
  - The wording patterns catch common phrasings, not every one.
- **What this means for the pages:** explanations are labelled as model text checked against their items, not as verified. The flag's own text, its evidence and its sources stay the authority on the page. Whether model text should be shown to the person at all, or only after a review, is the user's decision (Phase 5).
- **Next steps that would help, each needing a fresh sample to measure:**
  - limitations written so they can't be misread (each names its subject: "this check …");
  - a follow-up flag's text that does not open with "The CKD criteria are met;";
  - passages limited to the flag's analyte, or none for follow-up flags;
  - an Italian review of the plain-language register.
- **Over-strictness:**
  - the category check drops correct reports of a passage's table;
  - "non si deve assumere cronicità" reads as a treatment instruction, since *assumere* also means taking a drug.
- **Italian text by the assistant.** The Italian templates and closing sentences are the assistant's translations, and need the user's review. So does the plain-language register.
- **Real notes.** The note check on real notes needs MIMIC-IV-Note, run locally (Phase 6).
