"""Write one patient's graph and timeline as a self-contained HTML page.

    uv run python scripts/view_patient.py dev-1000 PATIENT [--out PATH]
    uv run python scripts/view_patient.py dev-1000 --list

``PATIENT`` is a patient ID or a unique prefix of one. The graph comes from the cohort's store
(``scripts/build_graphs.py``). The page is written to
``$MEDGRAPH_DATA_DIR/views/<cohort>/<patient>.html``, outside the repository; open it in a
browser. It needs no server and makes no network requests. The local app (``make app``)
serves the same page.

``--list`` prints patients worth a look: those with a condition in the monitoring table, a
filed lab report, or both, with their flag count from ``make flags`` ("?" if not run).
The page includes the follow-up flags, evaluated as of the cohort's simulation end date, and
the sources joined to the record by code if the knowledge graph is built
(``make knowledge-graph``).
"""

import argparse
import datetime as dt
from pathlib import Path

from medgraph.api.pages import patient_model, patient_rows
from medgraph.cohort import evaluation_date, load_meta
from medgraph.graph.store import GraphStore, StoreError
from medgraph.graph.view import render_page
from medgraph.settings import Settings


def list_patients(store: GraphStore, limit: int = 25) -> list[str]:
    return [
        f"{r.patient}  {r.records:>6,} nodes  reports {r.reports}  "
        f"flags {'?' if r.flags is None else r.flags}  {'; '.join(r.conditions)}"
        for r in patient_rows(store)
        if r.worth_a_look
    ][:limit]


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
    with GraphStore(db_path, read_only=True) as store:
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
    model, why = patient_model(settings, args.cohort, graph, as_of)
    if why is not None:
        print(why)
    html = render_page(model)
    out = args.out or settings.views_dir / args.cohort / f"{matches[0]}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html.encode()) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
