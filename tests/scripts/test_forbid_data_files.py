import pytest

import forbid_data_files as hook


@pytest.mark.parametrize(
    "path",
    [
        "data/synthea/dev-1000/fhir/patient.json",
        "runs/2026-09-30/metrics.json",
        ".env",
        "configs/.env.local",
        "exports/cohort.ndjson",
        "labs.csv",
        "labs.csv.gz",
        "models/gnn.pt",
        "report.pdf",
        "tools/synthea-with-dependencies.jar",
        "graph.sqlite",
    ],
)
def test_blocks_data_models_and_env_files(path: str) -> None:
    assert hook.violation(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        "src/medgraph/rules/__init__.py",
        ".env.example",
        "configs/synthea/dev-1000.yaml",
        "tests/fixtures/fhir/patient-a.json",
        "tests/fixtures/reports/sample.pdf",
        "docs/data/synthea-dev-1000-profile.md",
        "docs/figures/architecture.pdf",
    ],
)
def test_allows_code_configs_fixtures_and_docs(path: str) -> None:
    assert hook.violation(path) is None


def test_fixtures_cannot_smuggle_env_files() -> None:
    assert hook.violation("tests/fixtures/.env") is not None


def test_main_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert hook.main(["src/medgraph/__init__.py"]) == 0
    assert hook.main(["src/medgraph/__init__.py", "data/x.json"]) == 1
    assert "data/x.json: inside a data directory" in capsys.readouterr().err
