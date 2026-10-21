"""A Synthea cohort's files, as the pages and reports need them.

Shared by the scripts and the local app (:mod:`medgraph.api`): the cohort's manifest, the date
its flags are evaluated on, and the knowledge graph its pages join, which is used only while it
was built from the current documents, label lock and quoted statements.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

from medgraph.graph.knowledge import (
    KnowledgeError,
    KnowledgeGraph,
    knowledge_inputs,
    load_knowledge_graph,
)
from medgraph.settings import Settings

SYNTHETIC_BANNER = (
    "Synthetic data (Synthea): a pipeline test, not a real person's record. Synthea's diseases "
    "follow hand-written modules."
)


def load_meta(cohort_dir: Path) -> dict[str, Any]:
    manifest_path = cohort_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return {"cohort": cohort_dir.name}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cfg = manifest["config"]
    return {
        "cohort": manifest["cohort"],
        "synthea_version": cfg["synthea"]["version"],
        "seed": cfg["seed"],
        "clinician_seed": cfg["clinician_seed"],
        "reference_date": cfg["reference_date"],
        "content_digest": manifest["outputs"]["content_digest"],
    }


def evaluation_date(meta: dict[str, Any], override: dt.date | None) -> dt.date:
    """``--as-of``, else the cohort's simulation end date, else today (UTC)."""
    if override is not None:
        return override
    if "reference_date" in meta:
        return dt.date.fromisoformat(meta["reference_date"])
    return dt.datetime.now(dt.UTC).date()


def label_lock_path(config_dir: Path, cohort: str) -> Path:
    return config_dir / "knowledge" / f"labels-{cohort}.lock.json"


def knowledge_graph_path(settings: Settings, cohort: str) -> Path:
    return settings.knowledge_dir / f"graph-{cohort}.json"


def knowledge_inputs_for(config_dir: Path, cohort: str) -> dict[str, str]:
    return knowledge_inputs(
        config_dir / "knowledge" / "documents.yaml", label_lock_path(config_dir, cohort)
    )


def current_knowledge(settings: Settings, cohort: str) -> tuple[KnowledgeGraph | None, str | None]:
    """The cohort's knowledge graph if it is built and up to date, else None and why not."""
    path = knowledge_graph_path(settings, cohort)
    if not path.exists():
        why = f"no knowledge graph ({path}); pages leave sources out: run make knowledge-graph"
        return None, why
    try:
        return load_knowledge_graph(path, knowledge_inputs_for(settings.config_dir, cohort)), None
    except (KnowledgeError, FileNotFoundError) as exc:
        return None, f"knowledge graph not used: {exc}"
