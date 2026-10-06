"""Evaluate lab-report extraction against the synthetic ground truth.

    uv run python scripts/evaluate_extraction.py configs/extraction/qwen35-9b.yaml --split dev

Runs one transcription method (the rule-based baseline or the local LLM) over a report set,
then the same deterministic interpretation, and scores every field against the ground truth.
Each prediction is saved as soon as it is made, so a long LLM run can be stopped and resumed.

Results are measurements on synthetic, template-generated reports: an upper bound for real
reports, and labelled as such in every summary.
"""

import argparse
import hashlib
import json
import math
import random
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, fields
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from generate_lab_reports import ReportsConfig, TruthReport, TruthRow
from generate_synthea import git_state
from medgraph.agent.llm import LLMError, OllamaClient
from medgraph.ingest.files import content_digest, iter_data_files, sha256_file
from medgraph.ingest.lab_report import (
    PROMPT_VERSION,
    ReportResult,
    ReportRow,
    Transcription,
    interpret,
    strip_range_decoration,
    transcribe_with_llm,
    transcribe_with_rules,
)
from medgraph.ingest.pdf import pdf_text
from medgraph.normalize.analyte_names import analyte_for_name, name_key
from medgraph.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
Format = Literal["text", "pdf"]


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    method: Literal["rules", "llm"]
    model: str | None = None
    report_set: str
    formats: tuple[Format, ...] = ("text", "pdf")
    bootstrap_samples: int = 1000
    seed: int = 0
    num_ctx: int = 4096  # LLM context window; small keeps memory down on a 16 GB machine


# --- scoring ---------------------------------------------------------------------------


@dataclass
class ReportScore:
    report_id: str
    format: str
    family: str
    language: str
    held_out_family: bool
    truth_rows: int = 0
    predicted_rows: int = 0
    matched: int = 0
    value_ok: int = 0
    unit_ok: int = 0
    range_ok: int = 0
    flag_ok: int = 0
    ungrounded: int = 0
    in_scope: int = 0  # truth rows with a canonical value
    end_to_end_ok: int = 0
    in_scope_seen: int = 0  # ... printed under a name development also shows
    end_to_end_seen_ok: int = 0
    in_scope_held_out: int = 0  # ... printed under a held-out name
    end_to_end_held_out_ok: int = 0
    accepted: int = 0  # interpreted rows with status ok: what rules would use
    accepted_wrong: int = 0  # ... whose analyte or value disagrees with the truth
    seen_names: int = 0
    seen_names_ok: int = 0
    held_out_names: int = 0
    held_out_names_ok: int = 0
    date_ok: int = 0
    locale_ok: int = 0
    seconds: float = 0.0
    output_tokens: int = 0


def _squash(text: str) -> str:
    return " ".join(text.split())


def _same_range(predicted: str, truth: str) -> bool:
    return _squash(strip_range_decoration(predicted)) == _squash(truth)


@dataclass(frozen=True)
class RowMatch:
    """A truth row and the transcribed row paired with it by analyte name (``None``: missed)."""

    truth: int
    predicted: int | None
    value_ok: bool = False
    unit_ok: bool = False
    range_ok: bool = False
    flag_ok: bool = False


def match_rows(
    truth: TruthReport, transcription: Transcription
) -> tuple[list[RowMatch], list[int]]:
    """Pair rows by normalized analyte name, in order; also return unpaired transcribed rows."""
    remaining: dict[str, list[int]] = {}
    for i, t in enumerate(truth.rows):
        remaining.setdefault(name_key(t.analyte_text), []).append(i)
    pairs: dict[int, int] = {}  # truth index -> transcribed index
    extra = []
    for j, row in enumerate(transcription.rows):
        candidates = remaining.get(name_key(row.analyte))
        if candidates:
            pairs[candidates.pop(0)] = j
        else:
            extra.append(j)
    matches = []
    for i, t in enumerate(truth.rows):
        paired = pairs.get(i)
        if paired is None:
            matches.append(RowMatch(i, None))
            continue
        p = transcription.rows[paired]
        matches.append(
            RowMatch(
                i,
                paired,
                value_ok=_squash(p.value) == _squash(t.value_text),
                unit_ok=_squash(p.unit) == _squash(t.unit_text),
                range_ok=_same_range(p.reference_range, t.range_text),
                flag_ok=_squash(p.flag) == _squash(t.flag_text),
            )
        )
    return matches, extra


def _same_value(row: ReportRow, truth: TruthRow) -> bool:
    """The interpreted row carries the truth's analyte and canonical value."""
    if row.value is None or truth.canonical_value is None or row.analyte != truth.analyte:
        return False
    expected = Decimal(truth.canonical_value)
    return abs(row.value - expected) <= Decimal("1e-9") * max(Decimal(1), abs(expected))


def score_report(
    truth: TruthReport,
    transcription: Transcription,
    result: ReportResult,
    fmt: str,
    held_out_families: set[str],
) -> ReportScore:
    score = ReportScore(
        report_id=truth.report_id,
        format=fmt,
        family=truth.family,
        language=truth.language,
        held_out_family=truth.family in held_out_families,
        truth_rows=len(truth.rows),
        predicted_rows=len(transcription.rows),
        ungrounded=sum(r.status == "ungrounded" for r in result.rows),
    )
    matches, _ = match_rows(truth, transcription)
    truth_of: dict[int, TruthRow] = {}  # transcribed index -> paired truth row
    for m in matches:
        t = truth.rows[m.truth]
        if t.canonical_value is not None:
            score.in_scope += 1
            score.in_scope_seen += bool(t.name_seen)
            score.in_scope_held_out += not t.name_seen
        if m.predicted is None:
            continue
        truth_of[m.predicted] = t
        score.matched += 1
        score.value_ok += m.value_ok
        score.unit_ok += m.unit_ok
        score.range_ok += m.range_ok
        score.flag_ok += m.flag_ok
        r = result.rows[m.predicted]
        if t.analyte is not None:
            # The transcribed name alone: a row rejected for its value still has a name.
            correct = analyte_for_name(transcription.rows[m.predicted].analyte) == t.analyte
            if t.name_seen:
                score.seen_names += 1
                score.seen_names_ok += correct
            else:
                score.held_out_names += 1
                score.held_out_names_ok += correct
        if t.canonical_value is None:
            continue
        ok = r.status in ("ok", "implausible") and _same_value(r, t)
        score.end_to_end_ok += ok
        score.end_to_end_seen_ok += ok and bool(t.name_seen)
        score.end_to_end_held_out_ok += ok and not t.name_seen
    for j, r in enumerate(result.rows):
        if r.status == "ok":
            score.accepted += 1
            paired = truth_of.get(j)
            score.accepted_wrong += paired is None or not _same_value(r, paired)
    score.date_ok = int(result.collection_date == truth.collection_date)
    expected_locale = ("it", "it") if truth.language == "it" else ("en", truth.style)
    score.locale_ok = int((result.number_locale, result.date_locale) == expected_locale)
    return score


RATES: dict[str, tuple[str, str]] = {
    "row recall": ("matched", "truth_rows"),
    "row precision": ("matched", "predicted_rows"),
    "value exact": ("value_ok", "matched"),
    "unit exact": ("unit_ok", "matched"),
    "range exact": ("range_ok", "matched"),
    "flag exact": ("flag_ok", "matched"),
    "end-to-end canonical value": ("end_to_end_ok", "in_scope"),
    "end-to-end, seen names": ("end_to_end_seen_ok", "in_scope_seen"),
    "end-to-end, held-out names": ("end_to_end_held_out_ok", "in_scope_held_out"),
    "ungrounded rows (rejected)": ("ungrounded", "predicted_rows"),
    "wrong values accepted": ("accepted_wrong", "accepted"),
    "name mapping, seen names": ("seen_names_ok", "seen_names"),
    "name mapping, held-out names": ("held_out_names_ok", "held_out_names"),
    "collection date": ("date_ok", "reports"),
    "locale detection": ("locale_ok", "reports"),
}


def totals(scores: Iterable[ReportScore]) -> dict[str, float]:
    total: dict[str, float] = {"reports": 0}
    for s in scores:
        total["reports"] += 1
        for f in fields(ReportScore):
            value = getattr(s, f.name)
            if isinstance(value, int | float) and not isinstance(value, bool):
                total[f.name] = total.get(f.name, 0) + value
    return total


def rate(total: dict[str, float], name: str) -> float | None:
    numerator, denominator = RATES[name]
    return total.get(numerator, 0) / total[denominator] if total.get(denominator) else None


def bootstrap_ci(
    scores: list[ReportScore], name: str, samples: int, seed: int
) -> tuple[float, float] | None:
    """95% percentile interval, resampling reports (rows within a report are not independent)."""
    if not scores:
        return None
    rnd = random.Random(seed)
    values = []
    for _ in range(samples):
        value = rate(totals(rnd.choices(scores, k=len(scores))), name)
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


def interval(
    scores: list[ReportScore], name: str, samples: int, seed: int
) -> tuple[float, float, bool] | None:
    """95% interval for a rate, and whether it is exact rather than bootstrapped.

    At an observed 0% or 100% every bootstrap resample gives the same rate, so the percentile
    interval collapses to a point and overstates certainty. There the exact (Clopper-Pearson)
    interval on the pooled count is used instead. It treats rows as independent, which is
    optimistic when errors cluster within reports.
    """
    numerator, denominator = RATES[name]
    total = totals(scores)
    n, k = int(total.get(denominator, 0)), int(total.get(numerator, 0))
    if n == 0:
        return None
    if k == 0:
        return 0.0, 1 - 0.025 ** (1 / n), True
    if k == n:
        return 0.025 ** (1 / n), 1.0, True
    bounds = bootstrap_ci(scores, name, samples, seed)
    return (*bounds, False) if bounds else None


# --- running ---------------------------------------------------------------------------


def load_truths(report_dir: Path, split: str) -> list[TruthReport]:
    return [
        TruthReport.model_validate_json(p.read_text(encoding="utf-8"))
        for p in iter_data_files(report_dir / split, "*.truth.json")
    ]


def report_text(report_dir: Path, truth: TruthReport, fmt: Format) -> str:
    stem = report_dir / truth.split / truth.report_id
    if fmt == "pdf":
        return pdf_text(stem.with_suffix(".pdf"))
    return stem.with_suffix(".txt").read_text(encoding="utf-8")


def run(
    cfg: ExtractionConfig,
    truths: list[TruthReport],
    report_dir: Path,
    out_dir: Path,
    transcribe: Callable[[str], tuple[Transcription, dict[str, Any] | None]],
    cache_key: dict[str, Any] | None = None,
    log: Callable[[str], None] = lambda message: print(message, flush=True),
) -> list[dict[str, Any]]:
    """Transcribe and interpret every report.

    Only the transcription is saved and reused, and only when ``cache_key`` is given (the LLM:
    slow and model-dependent) and the saved one was made from the same report text with the
    same ``cache_key`` (model, model digest, prompt version, context size). Interpretation is
    deterministic and cheap, so it is always redone with the current code: results never mix
    code versions.
    """
    predictions = []
    for fmt in cfg.formats:
        for n, truth in enumerate(truths, 1):
            path = out_dir / "predictions" / fmt / f"{truth.report_id}.json"
            text = report_text(report_dir, truth, fmt)
            key = {"text_sha256": hashlib.sha256(text.encode()).hexdigest(), **(cache_key or {})}
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
            if cache_key is not None and saved and all(saved.get(k) == v for k, v in key.items()):
                record = saved
            else:
                try:
                    transcription, meta = transcribe(text)
                    error = None
                except (LLMError, ValueError) as exc:
                    transcription, meta = Transcription(), None
                    error = f"{type(exc).__name__}: {exc}"
                record = {
                    "report_id": truth.report_id,
                    "format": fmt,
                    **key,
                    "transcription": transcription.model_dump(mode="json"),
                    "llm": meta,
                    "error": error,
                }
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
                seconds = f" in {meta['seconds']:.0f}s" if meta else ""
                rows = len(transcription.rows)
                log(f"[{fmt} {n}/{len(truths)}] {truth.report_id}: {rows} rows{seconds}")
            transcription = Transcription.model_validate(record["transcription"])
            result = interpret(transcription, text, f"report:{truth.report_id}:{fmt}")
            predictions.append({**record, "result": result.model_dump(mode="json")})
    return predictions


def provenance_line(provenance: dict[str, Any]) -> str:
    git = provenance.get("git") or {}
    commit = (git.get("commit") or "unknown")[:7]
    changes = {True: " plus uncommitted changes", False: "", None: " (working tree unknown)"}
    return (
        f"Code: commit `{commit}`{changes[git.get('dirty')]}, code digest "
        f"`{provenance.get('code_digest', '?')[:12]}`; report set digest "
        f"`{provenance.get('report_set_digest', '?')[:12]}`."
    )


def summarize(
    cfg: ExtractionConfig,
    split: str,
    truths: list[TruthReport],
    predictions: list[dict[str, Any]],
    held_out_families: set[str],
    provenance: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    by_id = {t.report_id: t for t in truths}
    scores = []
    for p in predictions:
        score = score_report(
            by_id[p["report_id"]],
            Transcription.model_validate(p["transcription"]),
            ReportResult.model_validate(p["result"]),
            p["format"],
            held_out_families,
        )
        if p["llm"]:
            score.seconds = p["llm"]["seconds"]
            score.output_tokens = p["llm"]["output_tokens"]
        scores.append(score)

    def table(groups: dict[str, list[ReportScore]], metrics: list[str], ci: bool) -> list[str]:
        head = "| metric | " + " | ".join(groups) + " |"
        lines = [head, "|" + " --- |" * (len(groups) + 1)]
        for metric in metrics:
            cells = []
            for group in groups.values():
                value = rate(totals(group), metric)
                if value is None:
                    cells.append("n/a")
                    continue
                cell = f"{100 * value:.1f}%"
                bounds = interval(group, metric, cfg.bootstrap_samples, cfg.seed) if ci else None
                if bounds:  # rounded outwards: never narrower than computed
                    low, high, exact = bounds
                    star = "*" if exact else ""
                    cell += f" ({math.floor(100 * low)}-{math.ceil(100 * high)}{star})"
                cells.append(cell)
            lines.append(f"| {metric} | " + " | ".join(cells) + " |")
        return lines

    def pick(fmt: str, language: str | None = None, family: str | None = None) -> list[ReportScore]:
        return [
            s
            for s in scores
            if s.format == fmt
            and (language is None or s.language == language)
            and (family is None or s.family == family)
        ]

    formats: dict[str, list[ReportScore]] = {f: pick(f) for f in cfg.formats}
    main_metrics = list(RATES)
    by_language = {
        f"{lang} {f}": pick(f, language=lang) for lang in ("it", "en") for f in cfg.formats
    }
    families = sorted({s.family for s in scores}, key=lambda f: (f in held_out_families, f))
    by_family = {
        f + (" (held out)" if f in held_out_families else ""): pick(cfg.formats[0], family=f)
        for f in families
    }
    timed = [s for s in scores if s.seconds]
    lines = [
        f"# Extraction evaluation: {cfg.name}, {split} split",
        "",
        "> **Synthetic data.** Template-generated reports from Synthea values (see "
        "`scripts/generate_lab_reports.py`). Real reports are messier, so these numbers are an "
        "upper bound. Generated by `scripts/evaluate_extraction.py`.",
        "",
        f"Method: **{cfg.method}**"
        + (
            f", model `{cfg.model}` (digest `{provenance.get('model_digest', '?')}`), prompt "
            f"`{PROMPT_VERSION}`"
            if cfg.method == "llm"
            else ""
        )
        + f". Report set `{cfg.report_set}`, {len(truths)} reports, "
        f"{sum(len(t.rows) for t in truths)} rows per format. 95% intervals in parentheses: "
        "bootstrap over reports, or, marked *, exact binomial where the observed rate is 0% or "
        "100% (the bootstrap collapses there; the exact interval treats rows as independent).",
        "",
        provenance_line(provenance),
        "",
        "## Overall",
        "",
        *table(formats, main_metrics, ci=True),
        "",
        "## By language",
        "",
        *table(
            by_language,
            [
                "row recall",
                "value exact",
                "unit exact",
                "end-to-end canonical value",
                "collection date",
            ],
            ci=False,
        ),
        "",
        f"## By layout family ({cfg.formats[0]})",
        "",
        *table(
            by_family,
            [
                "row recall",
                "row precision",
                "value exact",
                "end-to-end canonical value",
                "end-to-end, seen names",
            ],
            ci=False,
        ),
        "",
    ]
    if timed:
        seconds = sorted(s.seconds for s in timed)
        lines += [
            "## Cost",
            "",
            f"Median {seconds[len(seconds) // 2]:.0f} s per report (max {seconds[-1]:.0f} s), "
            f"{sum(s.output_tokens for s in timed) / len(timed):.0f} output tokens per report on "
            "an Apple M3 with 16 GB RAM.",
            "",
        ]
    errors = [p for p in predictions if p["error"]]
    lines += [f"Failed transcriptions: {len(errors)}.", ""]
    metrics = {
        "config": cfg.model_dump(mode="json"),
        "split": split,
        "provenance": provenance,
        "overall": {f: {m: rate(totals(g), m) for m in main_metrics} for f, g in formats.items()},
        "scores": [asdict(s) for s in scores],
    }
    return "\n".join(lines), metrics


RESULT_SCRIPTS = ("evaluate_extraction.py", "generate_lab_reports.py", "lab_report_catalog.py")


def code_digest(repo: Path = REPO_ROOT) -> str:
    """Digest of the code that determines the results: the package and the evaluation scripts.

    Results are often produced from uncommitted code, so the commit alone does not identify it;
    recomputing this digest on a later checkout shows whether that checkout is the same code.
    """
    package = (repo / "src" / "medgraph").rglob("*.py")
    paths = [*package, *(repo / "scripts" / name for name in RESULT_SCRIPTS)]
    return content_digest({p.relative_to(repo).as_posix(): sha256_file(p) for p in paths})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate lab-report extraction.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--split", choices=["dev", "test"], required=True)
    parser.add_argument(
        "--formats",
        nargs="+",
        choices=["text", "pdf"],
        help="override the config's formats (e.g. text only for a quick dev run)",
    )
    args = parser.parse_args(argv)
    with args.config.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if args.formats:
        raw["formats"] = args.formats
    cfg = ExtractionConfig.model_validate(raw)
    settings = Settings()
    report_dir = settings.lab_reports_dir / cfg.report_set
    manifest = json.loads((report_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    reports_cfg = ReportsConfig.model_validate(manifest["config"])
    held_out = {str(f) for f in reports_cfg.test.families} - set(reports_cfg.dev.families)
    truths = load_truths(report_dir, args.split)
    out_dir = settings.runs_dir / "extraction" / cfg.name / args.split
    provenance: dict[str, Any] = {
        "report_set_digest": manifest["content_digest"],
        "git": git_state(REPO_ROOT),
        "code_digest": code_digest(),
    }
    cache_key = None
    if cfg.method == "llm":
        client = OllamaClient(settings, model=cfg.model)
        provenance["model_digest"] = client.model_digest()
        cache_key = {
            "model": cfg.model,
            "model_digest": provenance["model_digest"],
            "prompt_version": PROMPT_VERSION,
            "num_ctx": cfg.num_ctx,
        }

        def transcribe(text: str) -> tuple[Transcription, dict[str, Any] | None]:
            transcription, response = transcribe_with_llm(text, client, cfg.num_ctx)
            meta = {
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "output_tokens": response.output_tokens,
                "seconds": response.seconds,
            }
            return transcription, meta
    else:

        def transcribe(text: str) -> tuple[Transcription, dict[str, Any] | None]:
            return transcribe_with_rules(text), None

    predictions = run(cfg, truths, report_dir, out_dir, transcribe, cache_key)
    summary, metrics = summarize(cfg, args.split, truths, predictions, held_out, provenance)
    (out_dir / "metrics.json").write_text(
        json.dumps(metrics, indent=1, default=str) + "\n", "utf-8"
    )
    doc = REPO_ROOT / "docs" / "results" / f"extraction-{cfg.name}-{args.split}.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(summary, encoding="utf-8")
    print(f"wrote {doc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
