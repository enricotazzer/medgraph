"""Write one patient's graph and timeline as a self-contained HTML page.

    uv run python scripts/view_patient.py dev-1000 PATIENT [--out PATH]
    uv run python scripts/view_patient.py dev-1000 --list

``PATIENT`` is a patient ID or a unique prefix of one. The graph comes from the cohort's store
(``scripts/build_graphs.py``). The page is written to
``$MEDGRAPH_DATA_DIR/views/<cohort>/<patient>.html``, outside the repository; open it in a
browser. It needs no server and makes no network requests.

``--list`` prints patients worth a look: those with a condition in the monitoring table, a
filed lab report, or both, with their flag count from ``make flags`` ("?" if not run).
The page includes the follow-up flags, evaluated as of the cohort's simulation end date.
"""

import argparse
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path

from evaluate_flags import evaluation_date
from medgraph.graph.store import GraphStore, StoreError
from medgraph.graph.timeline import build_timeline
from medgraph.graph.view import render_html
from medgraph.rules.flags import evaluate
from medgraph.rules.model import RULESET
from medgraph.rules.monitoring import MONITORING, SNOMED
from medgraph.settings import Settings
from profile_cohort import load_meta

SYNTHETIC_BANNER = (
    "Synthetic data (Synthea): a pipeline test, not a real person's record. Synthea's diseases "
    "follow hand-written modules."
)


def list_patients(store: GraphStore, limit: int = 25) -> list[str]:
    conditions: dict[str, set[str]] = defaultdict(set)
    for patient, _, attrs in store.nodes_of_kind("condition"):
        code = str(attrs.get("code", "")).removeprefix(f"{SNOMED}|")
        if code in MONITORING:
            conditions[patient].add(MONITORING[code][0].removesuffix(" (disorder)"))
    reports = Counter(patient for patient, _, _ in store.nodes_of_kind("lab_report"))
    sizes = dict(store.db.execute("SELECT patient_id, n_nodes FROM graphs").fetchall())
    flags = {
        patient: len(json.loads(payload)["flags"])
        for patient, payload in store.db.execute(
            "SELECT patient_id, payload FROM rules WHERE ruleset = ?", (RULESET,)
        )
    }
    candidates = sorted(
        set(conditions) | set(reports),
        key=lambda p: (-(p in reports and p in conditions), -len(conditions[p]), sizes[p], p),
    )
    return [
        f"{p}  {sizes[p]:>6,} nodes  reports {reports[p]}  flags {flags.get(p, '?')}  "
        f"{'; '.join(sorted(conditions[p]))}"
        for p in candidates[:limit]
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a patient's graph as an HTML page.")
    parser.add_argument("cohort")
    parser.add_argument("patient", nargs="?", help="patient ID or unique prefix")
    parser.add_argument("--list", action="store_true", help="suggest patients to view")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--as-of",
        type=dt.date.fromisoformat,
        help="evaluation date for the flags (default: the cohort's simulation end date)",
    )
    args = parser.parse_args(argv)
    settings = Settings()
    db_path = settings.graphs_dir / f"{args.cohort}.sqlite"
    if not db_path.exists():
        parser.error(f"{db_path} not found; run scripts/build_graphs.py first")
    with GraphStore(db_path) as store:
        if args.list or not args.patient:
            print("\n".join(list_patients(store)))
            return 0
        matches = [p for p in store.digests() if p.startswith(args.patient)]
        if len(matches) != 1:
            parser.error(f"{len(matches)} patients match {args.patient!r}; give a longer prefix")
        try:
            graph = store.load(matches[0])
        except StoreError as exc:
            parser.error(str(exc))
    as_of = evaluation_date(load_meta(settings.synthea_dir / args.cohort), args.as_of)
    rules = evaluate(graph, as_of).model_dump(mode="json")
    html = render_html(graph, build_timeline(graph), SYNTHETIC_BANNER, rules)
    out = args.out or settings.views_dir / args.cohort / f"{matches[0]}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html.encode()) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
