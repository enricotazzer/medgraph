# 0003: Lab-report extraction and its evaluation

- Status: proposed (awaiting the user's review at the end of Phase 1)
- Date: 2026-10-05

## Context

Lab results also arrive as free-text and PDF reports, in Italian and English, in layouts no parser has seen. An LLM reads layouts well, but it can also invent a plausible value, convert a unit silently, or misread `1,32` as one thousand three hundred and twenty. The project rule is that the LLM handles language and code handles medical logic. Each value that reaches a rule must therefore come from code that can be tested, and must trace back to text that is really on the page.

## Decision

**Two stages** (`medgraph/ingest/lab_report.py`)

1. **Transcription.** The LLM copies each result row's fields as **verbatim strings**: analyte name, value, unit, reference range, flag, plus the collection date. Output is forced to a JSON schema. Sampling is temperature 0, seed 42, thinking off. The model never converts, normalizes or interprets anything.
2. **Interpretation.** Deterministic, tested code does the rest:
   - **Grounding check:** every transcribed field must appear in the report text (whitespace-normalized). A row failing it is `ungrounded`: it is kept for review but never used.
   - **Locale detection** from the report's vocabulary and units. This decides decimal commas and day-first dates.
   - **Analyte-name mapping** through an explicit EN/IT name table. An unknown name is `unmapped`; the system never guesses.
   - **Parsing and conversion** of numbers, units and ranges, through the same functions and cited factors as FHIR labs (ADR 0002).
   - **Value/unit separation:** when the model puts the unit inside the value (`"130 g/L"`), code splits it off. Both parts are still checked against the text.
- A **rule-based baseline** (regexes over the known layouts) runs through the same interpretation stage. The LLM has to beat it to justify its cost.
- **PDF:** only PDFs with a text layer are accepted. Lines are rebuilt from word positions, and a gap wider than 1.5 average character widths becomes a column break. A PDF without text raises `NoTextLayerError` instead of being OCR'd silently.
- **Model:** `qwen3.5:9b` (Q4_K_M GGUF) through local Ollama, `num_ctx` 4096. The local-only guard (`settings.check_local_llm`) refuses any non-loopback host and any cloud model.

**Evaluation design** (`scripts/generate_lab_reports.py`, `scripts/evaluate_extraction.py`). Each choice below exists to stop the numbers from being inflated:

- **Reports are rendered by code templates from Synthea values, never by an LLM.** The model under test never sees text written by itself or by a related model. The ground truth is the exact printed string, so scoring needs no fuzzy matching.
- **Splits.**
  - Patients never appear in both splits.
  - The test split adds two **held-out layout families** (`two_column`, `narrative`) and **held-out analyte names**, used at a rate of 0.5, that development never shows.
  - Prompt changes are made **only on development**. The test split is run once per prompt version, and its results are reported whatever they are.
- **Metrics.**
  - Row precision and recall.
  - Per-field exactness for value, unit, range and flag.
  - The **end-to-end canonical value**, i.e. the number a rule would actually receive.
  - Name mapping, separately for seen and held-out names.
  - Collection date, locale and ungrounded rate.
  - 95% bootstrap intervals over **reports**, not rows, because rows in one report share their errors.
- **Reproducibility.**
  - Only the LLM's transcription is saved and reused. It is reused only when the report text hash, model, model digest, prompt version and context size all match.
  - Interpretation and scoring are always recomputed with the current code, so results never mix code versions. The rules baseline is never cached.
  - Metrics record:
    - the report set's digest;
    - the git commit, with a flag when there are uncommitted changes;
    - a **digest of the code that determines results** (`src/medgraph` and the report and evaluation scripts). Results are often produced before the code is committed, so the commit alone doesn't identify that code.

## Deviations and limitations

- **Reference ranges printed on the synthetic reports are illustrative test data, not cited clinical ranges.** They exist only to test transcription. medgraph never uses printed ranges as criteria; flags use cited thresholds (Phase 3).
- **Template reports are cleaner than real ones:** no scan noise, no handwriting, no tables broken across pages. The scores are an **upper bound** for real reports.
- **"Held out" means unseen by the prompt-tuning loop, not independent.** The held-out layouts and names were written by the same author as the development ones.
- **MLX build not benchmarked.** `qwen3.5:9b-mlx` failed to load (`missing embedding weight: model.embed_tokens.weight`). The GGUF build is slow on an M3 with 16 GB RAM, at roughly 3–5 output tokens per second, i.e. minutes per report.
- **Resources.** Running the test suite alongside the model exhausted memory once and crashed the session. A later run was killed from outside after 10 reports; the cause is undetermined, and the machine was short of memory and disk at the time.
  - LLM runs therefore go through `make extract-llm` (`scripts/dev/llm_run.sh`), in their own process session with `num_ctx` 4096. They resume from saved transcriptions, so an interruption costs one report.
  - **Truncation risk:** Ollama silently cuts any input that overflows the context. Every run is checked to confirm that prompt plus output stays inside it (see Evidence).
  - **Timing:** seconds per report depend on whatever else the machine is doing. The cost figures are indicative only; accuracy is unaffected, because decoding is deterministic.
- **Model location.** Ollama's model folder (`OLLAMA_MODELS`, on the T7) set via `launchctl` is lost on reboot. The runner therefore starts a private `ollama serve` on `127.0.0.1:11435` with the folder set explicitly, points `MEDGRAPH_LLM_BASE_URL` at it, and stops it when the job ends.

## Evidence

**Development split, prompt v2.** 24 reports, text; [archived results](../results/extraction-qwen35-9b-dev-transcribe-v2.md).

- **End-to-end canonical value: 90.0% (bootstrap 77–100), against 100% for the rules.** Row recall was 100%, and every loss came from fields that weren't split apart:
  - **21 rows:** unit and range copied into `value`, all in the English colon layout. Code splits off a bare unit (en-US), but not a unit followed by `(ref …)` (en-GB).
  - **10 rows:** a flag turned into a value prefix (`>3,76`, `* 3,64`) on rows flagged abnormal. **The grounding check rejected all 10**, so no wrong number reached the output. That is the check doing its job; recall paid the cost.
- **Context:** prompt plus output was at most 1,623 tokens of 4,096, and Ollama reported `truncated = 0` on all 24 requests.
- **A metric fix found during this review.** Name mapping first counted a row rejected for its value as a mapping failure, which reported 95.2%. It now scores the transcribed name on its own, which gives 100%. Rule-baseline results are unchanged.

**Development split, prompt v3 (final).** [Results](../results/extraction-qwen35-9b-dev.md).

- **What v3 changes:** five separate fields; no unit, range or flag in `value`; a flag is not a comparator; and one worked example, written in the colon layout, a development family.
- **Results:**
  - End-to-end canonical value: **100%**. No row was rejected as ungrounded.
  - Value exact: 91.9%. The remaining errors are all in en-US colon reports, where the unit is still copied into `value`; code splits it off.
  - Context peaked at 1,490 tokens, with `truncated = 0` everywhere.
- **This 100% is on the split the prompt was tuned on, so it is not an estimate.** The prompt was frozen at v3 before the test split was run.

**Test split, prompt v3, run once.** 48 reports, as text and as PDF. Results: [LLM](../results/extraction-qwen35-9b-test.md) and [rules](../results/extraction-rules-test.md). Intervals: bootstrap over reports, or exact binomial (`*`) where the observed rate is 0% or 100%.

| | LLM, text | LLM, PDF | rules, text |
| --- | --- | --- | --- |
| row recall | 99.8% (99–100) | 99.8% (99–100) | 67.9% (53–81) |
| row recall, held-out layouts | 100% | 100% | 0% |
| end-to-end, seen names | **100% (94–100\*)**, 66/66 | 66/66 | 69.7% (52–85), 46/66 |
| end-to-end, held-out names | 5.9% (0–14), 3/51 | 3/51 | 3/51 |
| end-to-end, all in-scope rows | 59.0% (48–70) | 59.0% | 41.9% (30–54) |
| **wrong values accepted** | **0 of 69 (0–6\*)** | 0 of 69 | 0 of 49 (0–8\*) |

- **Extraction generalized to the held-out layouts.** Every in-scope value printed under a known name, 20 of them on held-out layouts, reached the output correct, in text and in PDF alike. The rules read none of the held-out layouts.
- **The overall end-to-end figure (59%) is limited by the name table, by design.** Both methods map 3 of 51 held-out names, all through the parenthetical rule. Unknown names are refused, not guessed.
- **Remaining transcription errors (text):**
  - 19 rows: the unit copied into `value` (en-US colon and two-column, en-GB narrative). Five of them were in scope under known names, and code split the unit off correctly in all five. Four had held-out names, and ten were out of scope.
  - 2 rows: a narrative flag (`low`) dropped.
  - 1 missed row.
  - 2 rows: **the reference range copied as the value** (next point).
- **Known risk, found on the test split: a grounded value in the wrong field.** In one text report the model copied two one-sided ranges as values:
  - `Colesterolo totale` printed as `214 mg/dL (rif. < 200)` came out as value `< 200`, range `< 200`.
  - `Colesterolo HDL` printed as `51 mg/dL (rif. > 40)` came out as value `> 40`.

  The PDF version of the same report was correct. The grounding check can't catch this, because the text really is in the report. Both rows were out of scope, so nothing was accepted. On an in-scope analyte, though, the value would have been accepted as "below 200". One-sided ranges like these are common in real reports.
  - Since it was found on the test split, the pipeline was **not** changed after the fact.
  - A consistency check is the candidate fix: reject a row whose value equals its range, or occurs only inside it. It needs a fresh report set before any number is claimed for it.
- **Cost:** median 67 s per report (maximum 127 s), about 2 h for the 96 transcriptions. Another CPU-heavy job was running on the machine at the time, so timings are indicative only.

## Consequences

- Only rows with status `ok` enter the graph (Phase 2). Each carries a `SourceRef` to the report file hash and row index. `ungrounded`, `unmapped` and unparseable rows stay visible as review items.
- **Analyte identity is decided by the name table, not the LLM.** The LLM and the rules baseline therefore score the same on held-out names; that metric measures the table.
  - None of the 40 held-out names is in the table (checked). The few that map do so through the generic rule that drops a trailing parenthetical (`Ferritin (serum)`).
  - Adding a name means editing the tested table, not the prompt. A possible extension, to be decided: the LLM proposes mappings for unmapped names, and a person confirms each one before it enters the table.
- **Report eGFR rows don't carry their equation.** FHIR labs take it from the LOINC code. A report row keeps only the printed name (e.g. `VFG (stima MDRD)`). Rules don't rely on reported eGFR (ADR 0002), but the timeline should show the printed name.
- Scanned reports need an OCR stage with its own evaluation before they can be accepted.
