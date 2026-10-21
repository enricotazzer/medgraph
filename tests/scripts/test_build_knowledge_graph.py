from pathlib import Path

import pytest

import build_knowledge_graph as bkg
from medgraph.cohort import knowledge_graph_path
from medgraph.graph.knowledge import KnowledgeGraph, save_knowledge_graph
from medgraph.settings import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)  # no .env from the repository
    return Settings()


def test_pages_leave_sources_out_without_a_graph(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert bkg.load_current(settings, "dev-1000") is None
    assert "run make knowledge-graph" in capsys.readouterr().out


def test_a_graph_built_from_other_inputs_is_not_used(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = KnowledgeGraph(
        cohort="dev-1000", inputs={"lock": "0"}, nodes={}, links=(), unlabelled={}
    )
    save_knowledge_graph(stale, knowledge_graph_path(settings, "dev-1000"))
    assert bkg.load_current(settings, "dev-1000") is None
    assert "built from other inputs" in capsys.readouterr().out


def test_a_cohort_without_a_lock_has_no_sources(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = KnowledgeGraph(cohort="none", inputs={}, nodes={}, links=(), unlabelled={})
    save_knowledge_graph(empty, knowledge_graph_path(settings, "none"))
    assert bkg.load_current(settings, "none") is None
    assert "knowledge graph not used" in capsys.readouterr().out
