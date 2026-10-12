"""Build a cohort's knowledge graph: its stored sources, joined to patient concepts by code.

    uv run python scripts/build_knowledge_graph.py dev-1000

Reads the pinned documents (``configs/knowledge/documents.yaml``) and the cohort's pinned
labels (``configs/knowledge/labels-<cohort>.lock.json``) from the knowledge store, checking
every file against its pin, and every quoted statement against the stored text
(``medgraph.graph.knowledge``). A problem stops the build. The graph goes to
``$MEDGRAPH_DATA_DIR/knowledge/graph-<cohort>.json``; patient pages and the concept map use it
when it is there and up to date.
"""

import argparse
import time
from collections import Counter
from pathlib import Path

from fetch_knowledge import CONFIG_DIR, lock_path
from medgraph.graph.knowledge import (
    KnowledgeError,
    KnowledgeGraph,
    build_knowledge_graph,
    knowledge_inputs,
    load_knowledge_graph,
    save_knowledge_graph,
)
from medgraph.rag.dailymed import LabelLock
from medgraph.rag.documents import load_documents
from medgraph.rag.store import KnowledgeStore
from medgraph.rag.stored import PinError
from medgraph.settings import Settings


def graph_path(settings: Settings, cohort: str) -> Path:
    return settings.knowledge_dir / f"graph-{cohort}.json"


def current_inputs(cohort: str) -> dict[str, str]:
    return knowledge_inputs(CONFIG_DIR / "documents.yaml", lock_path(cohort))


def load_current(settings: Settings, cohort: str) -> KnowledgeGraph | None:
    """The cohort's knowledge graph if it is built and up to date; otherwise None, saying why."""
    path = graph_path(settings, cohort)
    if not path.exists():
        print(f"no knowledge graph ({path}); pages leave sources out: run make knowledge-graph")
        return None
    try:
        return load_knowledge_graph(path, current_inputs(cohort))
    except (KnowledgeError, FileNotFoundError) as exc:
        print(f"knowledge graph not used: {exc}")
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a cohort's knowledge graph.")
    parser.add_argument("cohort")
    args = parser.parse_args(argv)
    settings = Settings()
    if not lock_path(args.cohort).exists():
        parser.error(f"{lock_path(args.cohort)} not found; run make knowledge ARGS=--lock")
    started = time.perf_counter()
    lock = LabelLock.model_validate_json(lock_path(args.cohort).read_text(encoding="utf-8"))
    try:
        graph = build_knowledge_graph(
            cohort=args.cohort,
            specs=load_documents(CONFIG_DIR / "documents.yaml"),
            lock=lock,
            kstore=KnowledgeStore(settings.knowledge_dir),
            inputs=current_inputs(args.cohort),
        )
    except (KnowledgeError, PinError, FileNotFoundError) as exc:
        print(f"not built:\n{exc}")
        return 1
    out = graph_path(settings, args.cohort)
    save_knowledge_graph(graph, out)
    nodes = Counter(n.kind for n in graph.nodes.values())
    links = Counter(x.kind for x in graph.links)
    print(
        f"nodes {dict(sorted(nodes.items()))}; links {dict(sorted(links.items()))}; "
        f"{len(graph.unlabelled)} drug codes without a label; digest {graph.digest()[:12]}; "
        f"wrote {out} in {time.perf_counter() - started:.0f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
