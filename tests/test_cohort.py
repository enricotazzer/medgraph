import datetime as dt
import json
import shutil
from pathlib import Path

from medgraph.cohort import (
    current_knowledge,
    evaluation_date,
    knowledge_graph_path,
    knowledge_inputs_for,
    label_lock_path,
    load_meta,
)
from medgraph.graph.knowledge import KnowledgeGraph, save_knowledge_graph
from medgraph.settings import REPO_CONFIGS, Settings


def test_the_default_config_dir_is_the_repositorys() -> None:
    assert Settings(_env_file=None).config_dir == REPO_CONFIGS
    assert (REPO_CONFIGS / "knowledge" / "documents.yaml").is_file()
    assert label_lock_path(REPO_CONFIGS, "dev-1000").is_file()


def test_flags_are_evaluated_on_the_simulation_end_date(tmp_path: Path) -> None:
    manifest = {
        "cohort": "tiny",
        "config": {
            "synthea": {"version": "v4.0.0"},
            "seed": 1,
            "clinician_seed": 2,
            "reference_date": "2026-01-01",
        },
        "outputs": {"content_digest": "abc"},
    }
    (tmp_path / "tiny").mkdir()
    (tmp_path / "tiny" / "MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
    meta = load_meta(tmp_path / "tiny")
    assert evaluation_date(meta, None) == dt.date(2026, 1, 1)
    assert evaluation_date(meta, dt.date(2025, 6, 1)) == dt.date(2025, 6, 1)
    assert load_meta(tmp_path / "other") == {"cohort": "other"}


def test_a_current_knowledge_graph_is_used_and_other_pins_refuse_it(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path)
    graph = KnowledgeGraph(
        cohort="dev-1000",
        inputs=knowledge_inputs_for(REPO_CONFIGS, "dev-1000"),
        nodes={},
        links=(),
        unlabelled={},
    )
    save_knowledge_graph(graph, knowledge_graph_path(settings, "dev-1000"))
    used, why = current_knowledge(settings, "dev-1000")
    assert used == graph
    assert why is None

    other = tmp_path / "configs"
    shutil.copytree(REPO_CONFIGS / "knowledge", other / "knowledge")
    lock = label_lock_path(other, "dev-1000")
    lock.write_text(lock.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    moved = Settings(_env_file=None, data_dir=tmp_path, config_dir=other)
    used, why = current_knowledge(moved, "dev-1000")
    assert used is None
    assert why is not None
    assert "built from other inputs (lock)" in why
