"""Evaluate the CKD and anaemia rules for every patient in a cohort's graph store.

    uv run python scripts/evaluate_flags.py dev-1000 [--as-of 2026-01-01] [--out PATH]

Reads the stored graphs (``make graphs``) and evaluates ``medgraph.rules`` as of the cohort's
simulation end date (Synthea's reference date) unless ``--as-of`` is given. The results go into
the store's ``rules`` table, and an aggregate report into
``docs/data/synthea-<cohort>-flags.md``.

Synthetic data: every number is a pipeline test. Agreement with Synthea's own diagnosis codes
is reported as a consistency check, never as accuracy: Synthea generates the codes and the
values from the same hand-written modules. The rules are never tuned to match it.
"""

import argparse
import datetime as dt
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from evaluate_extraction import code_digest
from generate_synthea import git_state
from medgraph.graph.store import GraphStore, cohort_digest
from medgraph.rules.context import context_from_graph
from medgraph.rules.diagnoses import (
    ANAEMIA_DIAGNOSES,
    DIALYSIS_WINDOW_DAYS,
    KIDNEY_DIAGNOSES,
    KIDNEY_FAILURE,
)
from medgraph.rules.egfr import computed_series, mdrd_2006
from medgraph.rules.flags import dialysis_sessions, evaluate, on_dialysis
from medgraph.rules.model import RULESET, PatientRules
from medgraph.rules.sources import SOURCES
from medgraph.settings import Settings
from profile_cohort import REPO_ROOT, load_meta, pct, table

SCRIPTS = ("evaluate_flags.py", "build_graphs.py", "evaluate_extraction.py")
RULES = (
    "ckd-criteria",
    "anaemia-criteria",
    "ckd-gfr-follow-up",
    "ckd-albuminuria-follow-up",
    "ckd-haemoglobin-follow-up",
    "metformin-egfr",
    "epoetin-haemoglobin-monthly",
)
FLAG_RULES = (
    "ckd-criteria-no-diagnosis",
    "anaemia-criteria-no-diagnosis",
    "ckd-gfr-follow-up",
    "ckd-albuminuria-follow-up",
    "ckd-haemoglobin-follow-up",
    "metformin-egfr-below-30",
    "metformin-egfr-30-44",
    "epoetin-haemoglobin-monthly",
)
NOTE_RULES = ("ras-inhibitor-nsaid", "metformin-no-gfr-test")


@dataclass
class Totals:
    patients: int = 0
    evaluated: int = 0
    status: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    reasons: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    flagged: Counter[str] = field(default_factory=Counter)
    any_flag: int = 0
    noted: Counter[str] = field(default_factory=Counter)
    notes: int = 0
    flagged_without_dialysis: Counter[str] = field(default_factory=Counter)
    any_flag_without_dialysis: int = 0
    on_dialysis: int = 0
    dialysis_ever: int = 0
    dialysis_coded: int = 0
    egfr_computed: int = 0
    egfr_skipped: Counter[str] = field(default_factory=Counter)
    egfr_pairs: list[tuple[int, Decimal]] = field(default_factory=list)  # (computed, reported)
    mdrd_gaps: list[float] = field(default_factory=list)  # reported minus MDRD of the creatinine
    ckd_vs_code: Counter[tuple[str, bool]] = field(default_factory=Counter)
    anaemia_vs_code: Counter[tuple[str, bool]] = field(default_factory=Counter)

    def add(self, result: PatientRules, extra: dict[str, Any]) -> None:
        self.patients += 1
        if not result.evaluated:
            return
        self.evaluated += 1
        for a in result.assessments:
            self.status[a.rule][a.status] += 1
            if a.status == "not_assessable" and a.reason:
                self.reasons[a.rule][re.sub(r"\d{4}-\d{2}-\d{2}", "<date>", a.reason)] += 1
        rules = {f.rule for f in result.flags}
        self.flagged.update(rules)
        self.any_flag += bool(rules)
        self.noted.update({n.rule for n in result.notes})
        self.notes += len(result.notes)
        without = {f.rule for f in extra["without_dialysis"].flags}
        self.flagged_without_dialysis.update(without)
        self.any_flag_without_dialysis += bool(without)
        self.on_dialysis += extra["on_dialysis"]
        self.dialysis_ever += extra["dialysis_ever"]
        self.dialysis_coded += extra["dialysis_ever"] and extra["failure_code"]
        self.egfr_computed += len(result.computed_egfr)
        self.egfr_skipped.update(extra["skipped"])
        self.egfr_pairs += extra["pairs"]
        self.mdrd_gaps += extra["mdrd_gaps"]
        status = {a.rule: a.status for a in result.assessments}
        self.ckd_vs_code[(status["ckd-criteria"], extra["kidney_code"])] += 1
        self.anaemia_vs_code[(status["anaemia-criteria"], extra["anaemia_code"])] += 1


def agreement(counts: Counter[tuple[str, bool]], code_label: str) -> str:
    rows = []
    for status in ("met", "not_met", "not_assessable"):
        yes, no = counts[(status, True)], counts[(status, False)]
        rows.append([status.replace("_", " "), f"{yes:,}", f"{no:,}"])
    return table(["criteria", f"{code_label} recorded", "not recorded"], rows)


def evaluation_date(meta: dict[str, Any], override: dt.date | None) -> dt.date:
    """``--as-of``, else the cohort's simulation end date, else today (UTC)."""
    if override is not None:
        return override
    if "reference_date" in meta:
        return dt.date.fromisoformat(meta["reference_date"])
    return dt.datetime.now(dt.UTC).date()


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def render(cohort: str, as_of: dt.date, t: Totals, provenance: dict[str, Any]) -> str:
    differences = [float(Decimal(c) - r) for c, r in t.egfr_pairs]
    cross = sum(1 for c, r in t.egfr_pairs if (c < 60) != (r < 60))
    lines = [
        f"# Follow-up rules: {cohort}",
        "",
        "> **Synthetic data, pipeline test.** Aggregates only; generated by "
        "`scripts/evaluate_flags.py` from the stored patient graphs. Synthea follows "
        "hand-written disease modules, so nothing here is a clinical finding, and agreement "
        "with Synthea's own diagnoses is a consistency check, not accuracy.",
        "",
        f"- Ruleset `{RULESET}`, evaluated as of {as_of} (Synthea's simulation end date).",
        f"- Graph store digest `{provenance['store_digest']}`; code digest "
        f"`{provenance['code_digest']}`; git commit `{provenance['git']['commit']}`"
        + (" with uncommitted changes" if provenance["git"]["dirty"] else "")
        + ".",
        "",
        f"**{t.patients:,} patients; {t.evaluated:,} evaluated.** "
        f"{t.patients - t.evaluated} deceased patients are not evaluated and get no flags.",
        "",
        "## Flags",
        "",
        table(
            ["flag", "patients", "share of evaluated"],
            [[r, f"{t.flagged[r]:,}", pct(t.flagged[r], t.evaluated)] for r in FLAG_RULES],
        ),
        "",
        f"Patients with at least one flag: {t.any_flag:,} ({pct(t.any_flag, t.evaluated)}).",
        "",
        "The medication flags come from drug labels (`rules/medications.py`); the label "
        "statements are in the sources table below.",
        "",
        "## Notes",
        "",
        "A note quotes what a label says about a situation the record shows, with no "
        "threshold or interval to check. Notes are shown on the patient page and are not "
        "flags.",
        "",
        table(
            ["note", "patients"],
            [[r, f"{t.noted[r]:,}"] for r in NOTE_RULES],
        ),
        "",
        "## Dialysis",
        "",
        f"- Evaluated patients with a recorded dialysis session: {t.dialysis_ever:,}; of them, "
        f"{t.dialysis_coded:,} have a kidney-failure code (end-stage renal disease or a "
        f"transplant state). On dialysis at the evaluation date (a session in the "
        f"{DIALYSIS_WINDOW_DAYS} days before it): {t.on_dialysis:,}.",
        "- Recognizing dialysis from procedures takes these patients out of the CKD monitoring "
        "rules, and a session during a CKD episode counts as recorded kidney disease. Flags "
        "with dialysis procedures ignored, for comparison:",
        "",
        table(
            ["flag", "dialysis recognized", "dialysis ignored"],
            [
                [r, f"{t.flagged[r]:,}", f"{t.flagged_without_dialysis[r]:,}"]
                for r in FLAG_RULES
                if t.flagged[r] != t.flagged_without_dialysis[r]
            ]
            + [["any flag", f"{t.any_flag:,}", f"{t.any_flag_without_dialysis:,}"]],
        ),
        "",
        "## Assessments",
        "",
        "For the follow-up rules, *met* means a test is overdue. They apply only where CKD is "
        "recorded or its criteria are met.",
        "",
        table(
            ["rule", "met", "not met", "not assessable"],
            [
                [r, *(f"{t.status[r][s]:,}" for s in ("met", "not_met", "not_assessable"))]
                for r in RULES
            ],
        ),
        "",
        "Why a rule could not be assessed:",
        "",
        table(
            ["rule", "reason", "patients"],
            [
                [r, reason, f"{n:,}"]
                for r in RULES
                for reason, n in sorted(t.reasons[r].items(), key=lambda x: (-x[1], x[0]))
            ],
        ),
        "",
        "## Computed eGFR (CKD-EPI 2021)",
        "",
        f"- eGFR computed for {t.egfr_computed:,} usable creatinine values; not computed for "
        + (", ".join(f"{n:,} ({k})" for k, n in sorted(t.egfr_skipped.items())) or "none")
        + ".",
        f"- Against the eGFR Synthea reports (MDRD) on the same day: {len(differences):,} pairs; "
        + (
            f"median difference (computed minus reported) {statistics.median(differences):+.1f} "
            f"mL/min/1.73 m2; the two fall on opposite sides of 60 in {cross:,} pairs "
            f"({pct(cross, len(differences))})."
            if differences
            else "no pairs."
        ),
        "",
        "**Synthea's reported eGFR does not follow from its own creatinine.** Applying the "
        "MDRD Study equation (`mdrd-2006`, without the race factor, which is not recorded) to "
        "the same-day creatinine differs from the reported MDRD value by a median of "
        + (
            f"{statistics.median(t.mdrd_gaps):+.1f} mL/min/1.73 m2 (5th to 95th percentile "
            f"{_quantile(t.mdrd_gaps, 0.05):+.1f} to {_quantile(t.mdrd_gaps, 0.95):+.1f})."
            if t.mdrd_gaps
            else "n/a."
        )
        + " The generator models eGFR and creatinine separately. medgraph's criteria use eGFR "
        "computed from creatinine, so they are expected to disagree with diagnoses Synthea "
        "assigns from its own eGFR.",
        "",
        "## Consistency with Synthea's diagnoses (not accuracy)",
        "",
        "Kidney diagnoses: the codes in `rules/diagnoses.py`, recorded by the evaluation date.",
        "",
        agreement(t.ckd_vs_code, "kidney diagnosis"),
        "",
        "Anaemia diagnoses:",
        "",
        agreement(t.anaemia_vs_code, "anaemia diagnosis"),
        "",
        "## Sources",
        "",
        table(
            ["ID", "citation"],
            [[s.id, s.citation] for s in SOURCES.values()],
        ),
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate follow-up rules for a cohort.")
    parser.add_argument("cohort")
    parser.add_argument("--as-of", type=dt.date.fromisoformat)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    settings = Settings()
    meta = load_meta(settings.synthea_dir / args.cohort)
    as_of = evaluation_date(meta, args.as_of)
    print(f"evaluating as of {as_of}", flush=True)
    totals = Totals()
    with GraphStore(settings.graphs_dir / f"{args.cohort}.sqlite") as store:
        store.clear_rules(RULESET, as_of.isoformat())
        digests = store.digests()
        for n, patient_id in enumerate(digests, 1):
            graph = store.load(patient_id)
            result = evaluate(graph, as_of)
            store.save_rules(patient_id, RULESET, as_of.isoformat(), result.model_dump_json())
            ctx = context_from_graph(graph, as_of)
            _, skipped = computed_series(ctx)
            reported = {v.date: v.value for v in ctx.series.get("egfr", ())}
            mdrd_gaps = [
                float(reported[c.date] - mdrd_2006(Decimal(c.creatinine), c.age, c.sex))
                for c in result.computed_egfr
                if c.date in reported
            ]
            pairs = [
                (c.whole, reported[c.date]) for c in result.computed_egfr if c.date in reported
            ]
            sessions = dialysis_sessions(ctx)
            extra = {
                "without_dialysis": evaluate(graph, as_of, dialysis=False),
                "on_dialysis": on_dialysis(ctx) is not None,
                "dialysis_ever": bool(sessions),
                "failure_code": any(
                    d.code in KIDNEY_FAILURE and d.recorded_by(as_of) for d in ctx.diagnoses
                ),
                "skipped": skipped,
                "pairs": pairs,
                "mdrd_gaps": mdrd_gaps,
                "kidney_code": any(
                    d.code in KIDNEY_DIAGNOSES and d.recorded_by(as_of) for d in ctx.diagnoses
                ),
                "anaemia_code": any(
                    d.code in ANAEMIA_DIAGNOSES and d.recorded_by(as_of) for d in ctx.diagnoses
                ),
            }
            totals.add(result, extra)
            if n % 200 == 0:
                print(f"{n} patients", flush=True)
        provenance = {
            "store_digest": cohort_digest(digests),
            "code_digest": code_digest(scripts=SCRIPTS),
            "git": git_state(REPO_ROOT),
        }
    out = args.out or REPO_ROOT / "docs" / "data" / f"synthea-{args.cohort}-flags.md"
    out.write_text(render(args.cohort, as_of, totals, provenance), encoding="utf-8")
    print(
        f"{totals.evaluated} evaluated, {totals.any_flag} with a flag; "
        f"flags {dict(totals.flagged)}; wrote {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
