import datetime as dt

import pytest
from pydantic import ValidationError

from medgraph.records import CodeableConcept, Coding, Timepoint


def test_instant_requires_instant_precision_and_vice_versa() -> None:
    aware = dt.datetime(2025, 1, 1, 9, tzinfo=dt.UTC)
    with pytest.raises(ValidationError, match="instant is required"):
        Timepoint(date=dt.date(2025, 1, 1), precision="instant")
    with pytest.raises(ValidationError, match="instant is required"):
        Timepoint(date=dt.date(2025, 1, 1), precision="day", instant=aware)


def test_naive_instants_are_rejected() -> None:
    naive = dt.datetime(2025, 1, 1, 9)  # noqa: DTZ001 - constructing the invalid case
    with pytest.raises(ValidationError, match="timezone-aware"):
        Timepoint(date=dt.date(2025, 1, 1), precision="instant", instant=naive)


def test_records_are_immutable() -> None:
    tp = Timepoint(date=dt.date(2025, 1, 1), precision="day")
    with pytest.raises(ValidationError):
        tp.date = dt.date(2026, 1, 1)  # type: ignore[misc]


def test_codeable_concept_helpers() -> None:
    concept = CodeableConcept(
        codings=(
            Coding(system="http://snomed.info/sct", code="271737000", display="Anemia"),
            Coding(system="http://example.org", code="X"),
        )
    )
    assert concept.code("http://snomed.info/sct") == "271737000"
    assert concept.code("http://loinc.org") is None
    assert concept.label == "Anemia"
    assert CodeableConcept(codings=(Coding(system=None, code="X"),)).label == "X"
    assert CodeableConcept(text="free text").label == "free text"
