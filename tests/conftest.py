"""Shared pytest configuration: opt-in markers for tests that need external resources."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

OPT_IN = {
    "llm": "needs a local Ollama server",
    "synthea": "needs a generated Synthea cohort",
    "mimic": "needs local MIMIC-IV, never in CI",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    for marker, why in OPT_IN.items():
        parser.addoption(
            f"--run-{marker}",
            action="store_true",
            default=False,
            help=f"run tests marked '{marker}' ({why})",
        )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker, why in OPT_IN.items():
        if config.getoption(f"--run-{marker}"):
            continue
        skip = pytest.mark.skip(reason=f"{why}; enable with --run-{marker}")
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def fhir_fixture_dir() -> Path:
    return FIXTURES / "fhir"
