from pathlib import Path

import pytest

from medgraph.settings import Settings


@pytest.fixture(autouse=True)
def _no_ambient_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEDGRAPH_DATA_DIR", raising=False)


def test_defaults_to_repo_local_data_dir() -> None:
    settings = Settings(_env_file=None)
    assert settings.data_dir == Path("data")
    assert settings.synthea_dir == Path("data/synthea")
    assert settings.tools_dir == Path("data/tools")
    assert settings.runs_dir == Path("data/runs")


def test_environment_variable_overrides_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", str(tmp_path))
    assert Settings(_env_file=None).data_dir == tmp_path


def test_env_file_is_read(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"MEDGRAPH_DATA_DIR={tmp_path / 'cohorts'}\nUNRELATED=1\n")
    assert Settings(_env_file=env_file).data_dir == tmp_path / "cohorts"


def test_home_directory_is_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", "~/medgraph-data")
    assert Settings(_env_file=None).data_dir == Path.home() / "medgraph-data"
