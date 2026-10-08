"""Build every patient's graph in a cohort, check its invariants and save it to SQLite.

    uv run python scripts/build_graphs.py configs/graphs/dev-1000.yaml [--limit N]

Lab reports listed in the config are filed in their patients' records. Each report's
transcription comes from a saved LLM run (no model is called), checked against the report's
text hash and the current prompt version, and is interpreted again with the current code.

Writes ``$MEDGRAPH_DATA_DIR/graphs/<cohort>.sqlite`` from scratch and an aggregate report,
``docs/data/synthea-<cohort>-graphs.md``. The report holds no per-patient rows and no timings,
and it records the store digest, so rebuilding should reproduce it exactly.
"""

import argparse
import hashlib
import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

from evaluate_extraction import ExtractionConfig, code_digest
from generate_lab_reports import patient_tag, printed_patient_tag
from generate_synthea import git_state
from medgraph.graph.build import AttachedReport, build_patient_graph
from medgraph.graph.check import check_coverage, check_graph
from medgraph.graph.schema import (
    EDGE_ENDPOINTS,
    EDGE_KINDS,
    NODE_KINDS,
    SCHEMA_VERSION,
    PatientGraph,
    graph_info,
    node_data,
)
from medgraph.graph.store import GraphStore, cohort_digest
from medgraph.graph.timeline import Timeline, build_timeline
from medgraph.ingest.fhir import BundleError, read_bundle_file
from medgraph.ingest.files import iter_data_files, sha256_file
from medgraph.ingest.lab_report import PROMPT_VERSION, Transcription, interpret
from medgraph.normalize.analytes import ANALYTES
from medgraph.rules.monitoring import MONITORING, SNOMED
from medgraph.settings import Settings
from profile_cohort import REPO_ROOT, load_meta, nearest_rank, pct, table

SCRIPTS = ("build_graphs.py", "evaluate_extraction.py", "generate_lab_reports.py")
# Kinds that can occur during an encounter (see EDGE_ENDPOINTS["occurred_during"]).
ENCOUNTER_RECORDS = {kind for kind, _ in EDGE_ENDPOINTS["occurred_during"]}


class ReportSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    extraction: str  # configs/extraction/<name>.yaml: its report set and saved transcriptions
    split: Literal["dev", "test"]


class GraphsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cohort: str
    reports: tuple[ReportSource, ...] = ()


def load_reports(
    sources: tuple[ReportSource, ...], settings: Settings
) -> tuple[dict[str, list[AttachedReport]], Counter[str]]:
    """Interpreted reports by printed patient tag, and what was skipped and why."""
    by_tag: dict[str, list[AttachedReport]] = defaultdict(list)
    skipped: Counter[str] = Counter()
    for source in sources:
        path = REPO_ROOT / "configs" / "extraction" / f"{source.extraction}.yaml"
        cfg = ExtractionConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        report_dir = settings.lab_reports_dir / cfg.report_set / source.split
        saved_dir = settings.runs_dir / "extraction" / cfg.name / source.split / "predictions"
        for report_path in iter_data_files(report_dir, "*.txt"):
            text = report_path.read_text(encoding="utf-8")
            saved_path = saved_dir / "text" / f"{report_path.stem}.json"
            if not saved_path.exists():
                skipped["no saved transcription"] += 1
                continue
            saved = json.loads(saved_path.read_text(encoding="utf-8"))
            if saved.get("text_sha256") != hashlib.sha256(text.encode()).hexdigest():
                skipped["saved transcription is of another text"] += 1
                continue
            if saved.get("prompt_version") != PROMPT_VERSION or saved.get("error"):
                skipped["saved transcription from another prompt, or failed"] += 1
                continue
            tag = printed_patient_tag(text)
            if tag is None:
                skipped["no patient tag printed"] += 1
                continue
            result = interpret(
                Transcription.model_validate(saved["transcription"]),
                text,
                f"sha256:{sha256_file(report_path)}",
            )
            name = f"{cfg.report_set}/{source.split}/{report_path.name}"
            by_tag[tag].append(AttachedReport(name=name, result=result))
    return by_tag, skipped


@dataclass
class Totals:
    """Aggregates over the cohort; no per-patient rows leave this object."""

    patients: int = 0
    problems: list[str] = field(default_factory=list)
    build_issues: int = 0
    ingest_issues: Counter[str] = field(default_factory=Counter)
    nodes: Counter[str] = field(default_factory=Counter)
    edges: Counter[str] = field(default_factory=Counter)
    per_patient_nodes: list[float] = field(default_factory=list)
    per_patient_edges: list[float] = field(default_factory=list)
    with_reason: Counter[str] = field(default_factory=Counter)
    encounter_linked: Counter[str] = field(default_factory=Counter)
    monitored_records: Counter[str] = field(default_factory=Counter)
    monitored_patients: Counter[str] = field(default_factory=Counter)
    series_patients: Counter[str] = field(default_factory=Counter)
    series_points: Counter[str] = field(default_factory=Counter)
    single_value: Counter[str] = field(default_factory=Counter)
    corroborated: Counter[str] = field(default_factory=Counter)
    set_aside: dict[str, Counter[str]] = field(default_factory=lambda: defaultdict(Counter))
    reports: int = 0
    report_patients: int = 0
    undated_reports: int = 0
    row_status: Counter[str] = field(default_factory=Counter)
    usable_rows: int = 0
    repeat_rows: int = 0
    multi_match_rows: int = 0
    seconds: list[float] = field(default_factory=list)

    def add(self, graph: PatientGraph, timeline: Timeline) -> None:
        self.patients += 1
        info = graph_info(graph)
        self.build_issues += len(info.build_issues)
        self.ingest_issues.update(f"{i.code} ({i.severity})" for i in info.ingest_issues)
        self.per_patient_nodes.append(graph.number_of_nodes())
        self.per_patient_edges.append(graph.number_of_edges())
        self.edges.update(kind for *_, kind in graph.edges(keys=True))
        monitored_codes: set[str] = set()
        has_report = False
        for node in graph.nodes:
            data = node_data(graph, node)
            self.nodes[data.kind] += 1
            out_kinds = [k for *_, k in graph.out_edges(node, keys=True)]
            in_kinds = [k for *_, k in graph.in_edges(node, keys=True)]
            if data.kind in ("medication_request", "procedure") and "treated_by" in in_kinds:
                self.with_reason[data.kind] += 1
            if "occurred_during" in out_kinds:
                self.encounter_linked[data.kind] += 1
            if data.kind == "condition" and "monitored_by" in out_kinds and data.code:
                code = data.code.removeprefix(f"{SNOMED}|")
                self.monitored_records[code] += 1
                monitored_codes.add(code)
            if data.kind in ("lab_result", "report_row") and not data.usable and data.analyte:
                reason = data.status if data.status != "ok" else "no date"
                self.set_aside[data.analyte][f"{data.kind}: {reason}"] += 1
            if data.kind == "lab_report":
                self.reports += 1
                has_report = True
                self.undated_reports += data.start is None
            if data.kind == "report_row":
                self.row_status[str(data.status)] += 1
                if data.usable:
                    self.usable_rows += 1
                    matches = out_kinds.count("same_measurement")
                    self.repeat_rows += matches > 0
                    self.multi_match_rows += matches > 1
        self.report_patients += has_report
        self.monitored_patients.update(monitored_codes)
        for series in timeline.series:
            self.series_patients[series.analyte] += 1
            self.series_points[series.analyte] += len(series.points)
            self.single_value[series.analyte] += len(series.points) == 1
            self.corroborated[series.analyte] += sum(1 for p in series.points if p.corroborated_by)


def counts(values: list[float]) -> str:
    """min / p5 / median / p95 / max of counts, as whole numbers."""
    v = sorted(values)
    points = (v[0], nearest_rank(v, 0.05), nearest_rank(v, 0.5), nearest_rank(v, 0.95), v[-1])
    return " / ".join(f"{p:,.0f}" for p in points)


def render(
    cfg: GraphsConfig,
    totals: Totals,
    meta: dict[str, Any],
    provenance: dict[str, Any],
    skipped: Counter[str],
    unfiled: int,
) -> str:
    t = totals
    lines = [
        f"# Patient graphs: {cfg.cohort}",
        "",
        "> **Synthetic data, pipeline test.** Aggregates only; generated by "
        "`scripts/build_graphs.py` from the same code the app uses (`ingest`, `normalize`, "
        "`graph`). Synthea follows hand-written disease modules, so nothing here is a clinical "
        "finding.",
        "",
        f"- Cohort `{meta['cohort']}`, content digest `{meta.get('content_digest', 'n/a')}`.",
        f"- Graph schema `{SCHEMA_VERSION}`; store digest `{provenance['store_digest']}`.",
        f"- Code digest `{provenance['code_digest']}`; git commit "
        f"`{provenance['git']['commit']}`"
        + (" with uncommitted changes" if provenance["git"]["dirty"] else "")
        + ".",
        "",
        f"**{t.patients:,} patients built. Invariant violations: {len(t.problems)}.** "
        f"Build issues (e.g. a reason naming a condition that failed to parse): {t.build_issues}. "
        "Ingestion issues carried on the graphs: "
        + (", ".join(f"{k} {n:,}" for k, n in sorted(t.ingest_issues.items())) or "none")
        + ".",
        "",
        "Invariants checked on every graph (`graph/check.py`): every node has valid attributes "
        "and a source (analyte concepts excepted); every edge joins the kinds its type allows, "
        "and every guideline edge carries a citation and quote; every usable lab result is in "
        "exactly one series, and every usable report row is in one or repeats a value that is; "
        "no unusable value is in a series; `precedes` edges match the series order; every "
        "record and report row has its node.",
        "",
        "## Size",
        "",
        "Nodes per patient (min / p5 / median / p95 / max): "
        f"{counts(t.per_patient_nodes)}. Edges: {counts(t.per_patient_edges)}.",
        "",
        table(
            ["node kind", "nodes", "linked to an encounter"],
            [
                [
                    kind,
                    f"{t.nodes[kind]:,}",
                    pct(t.encounter_linked[kind], t.nodes[kind])
                    if kind in ENCOUNTER_RECORDS
                    else "n/a",
                ]
                for kind in NODE_KINDS
                if t.nodes[kind]
            ],
        ),
        "",
        table(
            ["edge kind", "edges"],
            [[kind, f"{t.edges[kind]:,}"] for kind in EDGE_KINDS if t.edges[kind]],
        ),
        "",
        "## Treatment links from the data",
        "",
        "`treated_by` comes only from FHIR `reasonReference`: "
        + "; ".join(
            f"{t.with_reason[kind]:,} of {t.nodes[kind]:,} {label} "
            f"({pct(t.with_reason[kind], t.nodes[kind])}) name the condition they treat"
            for kind, label in (
                ("medication_request", "medication requests"),
                ("procedure", "procedures"),
            )
        )
        + ". The rest have no stated reason, and none is inferred.",
        "",
        "## Guideline links (`monitored_by`)",
        "",
        "From the cited table in `rules/monitoring.py`; other codes get no link.",
        "",
        table(
            ["SNOMED CT", "display", "condition records", "patients", "linked analytes"],
            [
                [
                    code,
                    display,
                    f"{t.monitored_records[code]:,}",
                    f"{t.monitored_patients[code]:,}",
                    ", ".join(link.analyte for link in links),
                ]
                for code, (display, links) in MONITORING.items()
            ],
        ),
        "",
        "## Lab series",
        "",
        "A series holds usable values only (status `ok` and dated), one point per measurement.",
        "",
        table(
            [
                "analyte",
                "patients with a series",
                "values",
                "single-value series",
                "values also on a report",
            ],
            [
                [
                    a.key,
                    f"{t.series_patients[a.key]:,}",
                    f"{t.series_points[a.key]:,}",
                    f"{t.single_value[a.key]:,}",
                    f"{t.corroborated[a.key]:,}",
                ]
                for a in ANALYTES
                if t.series_patients[a.key]
            ],
        ),
        "",
        "Values kept as nodes but set aside (never in a series, never used by rules):",
        "",
        table(
            ["analyte", "source: reason (count)"],
            [
                [a.key, ", ".join(f"{k} {n:,}" for k, n in sorted(t.set_aside[a.key].items()))]
                for a in ANALYTES
                if t.set_aside.get(a.key)
            ],
        ),
        "",
        "## Lab reports filed in records",
        "",
        f"Sources: {', '.join(f'`{s.extraction}` {s.split}' for s in cfg.reports) or 'none'} "
        "(saved LLM transcriptions of the text reports, re-interpreted with the current code).",
        "",
        f"- **{t.reports} reports filed** in {t.report_patients} patients' records; "
        f"{unfiled} printed a patient tag that matched no patient; skipped: "
        + (", ".join(f"{k} {n}" for k, n in sorted(skipped.items())) or "none")
        + ".",
        f"- Reports without a collection date (rows off the timeline): {t.undated_reports}.",
        "- Rows by status: "
        + ", ".join(f"{k} {n:,}" for k, n in sorted(t.row_status.items()))
        + ".",
        f"- **Usable rows: {t.usable_rows}. Rows repeating a FHIR result: {t.repeat_rows}** "
        f"({pct(t.repeat_rows, t.usable_rows)}); usable rows matching no FHIR result: "
        f"{t.usable_rows - t.repeat_rows}; rows matching more than one: {t.multi_match_rows}.",
        "",
        "Every synthetic report was printed from FHIR values, so a usable row that matches no "
        "FHIR result would mean a wrong value or date got through extraction, or that matching "
        "failed. Matching uses the printed precision: a value printed as `109 µmol/L` repeats "
        "a recorded `1.2345 mg/dL`.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build, check and store patient graphs.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--limit", type=int, help="first N patients only; no report is written")
    parser.add_argument("--out", type=Path, help="markdown output path")
    args = parser.parse_args(argv)
    cfg = GraphsConfig.model_validate(yaml.safe_load(args.config.read_text(encoding="utf-8")))
    settings = Settings()
    cohort_dir = settings.synthea_dir / cfg.cohort
    meta = load_meta(cohort_dir)
    by_tag, skipped = load_reports(cfg.reports, settings)
    print(f"reports: {sum(len(v) for v in by_tag.values())} to file; skipped {dict(skipped)}")

    suffix = f"-first{args.limit}" if args.limit else ""
    db_path = settings.graphs_dir / f"{cfg.cohort}{suffix}.sqlite"
    for stale in (db_path, db_path.with_name(f"._{db_path.name}")):
        stale.unlink(missing_ok=True)
    totals = Totals()
    started = time.perf_counter()
    with GraphStore(db_path) as store:
        for path in iter_data_files(cohort_dir / "fhir", "*.json"):
            if args.limit and totals.patients >= args.limit:
                break
            try:
                record = read_bundle_file(path)
            except BundleError:
                continue
            t0 = time.perf_counter()
            reports = tuple(by_tag.pop(patient_tag(record), []))
            graph = build_patient_graph(record, reports)
            problems = check_graph(graph) + check_coverage(graph, record, reports)
            totals.problems += [f"{record.patient.id}: {p}" for p in problems]
            totals.add(graph, build_timeline(graph))
            store.save(graph)
            totals.seconds.append(time.perf_counter() - t0)
            if totals.patients % 100 == 0:
                print(
                    f"{totals.patients} patients, {time.perf_counter() - started:.0f}s", flush=True
                )
        provenance: dict[str, Any] = {
            "store_digest": cohort_digest(store.digests()),
            "code_digest": code_digest(scripts=SCRIPTS),
            "git": git_state(REPO_ROOT),
        }
        store.set_meta(
            {
                "config": cfg.model_dump_json(),
                "cohort_digest": str(meta.get("content_digest")),
                "store_digest": provenance["store_digest"],
                "code_digest": provenance["code_digest"],
                "git": json.dumps(provenance["git"]),
            }
        )
    unfiled = sum(len(v) for v in by_tag.values()) if not args.limit else 0
    elapsed = time.perf_counter() - started
    print(
        f"built {totals.patients} graphs in {elapsed:.0f}s "
        f"(median {statistics.median(totals.seconds):.2f}s, max {max(totals.seconds):.1f}s); "
        f"store {db_path} ({db_path.stat().st_size / 1e6:.0f} MB); "
        f"store digest {provenance['store_digest']}"
    )
    for problem in totals.problems[:20]:
        print("PROBLEM", problem)
    if not args.limit:
        out = args.out or REPO_ROOT / "docs" / "data" / f"synthea-{cfg.cohort}-graphs.md"
        out.write_text(render(cfg, totals, meta, provenance, skipped, unfiled), encoding="utf-8")
        print(f"wrote {out.relative_to(REPO_ROOT) if out.is_relative_to(REPO_ROOT) else out}")
    return 1 if totals.problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
