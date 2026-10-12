"""Write the cohort's concept map: every patient graph merged by concept, as one HTML page.

    uv run python scripts/concept_map.py dev-1000 [--out PATH]

Reads every graph in the cohort's store (``scripts/build_graphs.py``), which checks each
graph's digest as it loads, and merges them (``medgraph.graph.concepts``). The page is written
to ``$MEDGRAPH_DATA_DIR/views/<cohort>/concept-map.html``, outside the repository, and links to
the patient pages already in that folder (``make view``). Drug labels and guidelines are joined
by code if the knowledge graph is built (``make knowledge-graph``). It needs no server and
makes no network requests.
"""

import argparse
import time
from collections.abc import Iterator
from pathlib import Path

from build_knowledge_graph import load_current
from medgraph.graph.concepts import concept_map, with_knowledge
from medgraph.graph.schema import PatientGraph
from medgraph.graph.store import GraphStore
from medgraph.graph.view import render_concept_map
from medgraph.settings import Settings
from view_patient import SYNTHETIC_BANNER


def stored_graphs(store: GraphStore, every: int = 200) -> Iterator[PatientGraph]:
    patients = list(store.digests())
    for i, patient in enumerate(patients, 1):
        yield store.load(patient)
        if i % every == 0 or i == len(patients):
            print(f"{i} patients", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the cohort's concept map as an HTML page.")
    parser.add_argument("cohort")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    settings = Settings()
    db_path = settings.graphs_dir / f"{args.cohort}.sqlite"
    if not db_path.exists():
        parser.error(f"{db_path} not found; run scripts/build_graphs.py first")
    out = args.out or settings.views_dir / args.cohort / "concept-map.html"
    started = time.perf_counter()
    with GraphStore(db_path) as store:
        model = concept_map(stored_graphs(store))
        store_digest = store.meta()["store_digest"]
    knowledge = load_current(settings, args.cohort)
    if knowledge is not None:
        model = with_knowledge(model, knowledge)
    pages = {p.stem for p in out.parent.glob("*.html") if not p.name.startswith(".")}
    html = render_concept_map(
        model, cohort=args.cohort, store_digest=store_digest, banner=SYNTHETIC_BANNER, pages=pages
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(
        f"{len(model['patients'])} patients, {len(model['concepts'])} concepts, "
        f"{len(model['links'])} links; {len(pages & set(model['patients']))} patient pages linked; "
        f"wrote {out} ({len(html.encode()) / 1e6:.1f} MB) in {time.perf_counter() - started:.0f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
