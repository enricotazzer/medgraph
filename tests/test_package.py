import importlib
from importlib.metadata import version

import pytest

import medgraph

SUBPACKAGES = ["ingest", "normalize", "graph", "rules", "rag", "agent", "gnn", "api"]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports_and_states_its_job(name: str) -> None:
    module = importlib.import_module(f"medgraph.{name}")
    assert module.__doc__
    assert module.__doc__.strip()


def test_version_matches_distribution_metadata() -> None:
    assert medgraph.__version__ == version("medgraph")
