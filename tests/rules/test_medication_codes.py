"""The medication code lists agree with the committed label lock and the cited sources."""

from pathlib import Path

from medgraph.rag.dailymed import LabelLock
from medgraph.rules.medications import EPOETIN, METFORMIN, NSAIDS, RAS_INHIBITORS
from medgraph.rules.sources import SOURCES

LOCK = Path(__file__).parents[2] / "configs" / "knowledge" / "labels-dev-1000.lock.json"
DRUGS = {d.rxcui: d for d in LabelLock.model_validate_json(LOCK.read_text("utf-8")).drugs}


def test_every_code_maps_to_the_label_its_statements_cite() -> None:
    cited = [
        (METFORMIN, ("label-metformin-egfr-30", "label-metformin-egfr-45")),
        (EPOETIN, ("label-epogen-haemoglobin",)),
        *((drug, (source,)) for drug, source in RAS_INHIBITORS),
    ]
    for drug, sources in cited:
        for code, display in drug.codes.items():
            locked = DRUGS[code]
            assert locked.name == display
            assert locked.label is not None
            assert locked.label.setid == drug.label, f"{code} is pinned to another label"
        for source in sources:
            assert SOURCES[source].stored.startswith(f"label:{drug.label}@v")


def test_nsaid_codes_are_naproxen_or_ibuprofen() -> None:
    for code, display in NSAIDS.items():
        assert DRUGS[code].name == display
        assert set(DRUGS[code].ingredients) <= {"naproxen", "naproxen sodium", "ibuprofen"}


def test_no_code_is_in_two_groups() -> None:
    groups = [METFORMIN.codes, EPOETIN.codes, NSAIDS, *(d.codes for d, _ in RAS_INHIBITORS)]
    codes = [c for g in groups for c in g]
    assert len(codes) == len(set(codes))
