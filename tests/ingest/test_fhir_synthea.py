"""Full-cohort check: every dev-1000 bundle parses and nothing in scope is dropped.

Opt-in (``--run-synthea``): needs the generated cohort in ``MEDGRAPH_DATA_DIR``.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from medgraph.ingest.fhir import BundleError, read_bundle_file
from medgraph.ingest.files import iter_data_files
from medgraph.settings import Settings

PARSED_TYPES = {
    "Encounter": "encounters",
    "Condition": "conditions",
    "MedicationRequest": "medication_requests",
    "Observation": "observations",
    "Procedure": "procedures",
    "DiagnosticReport": "diagnostic_reports",
}


@pytest.mark.synthea
def test_dev_cohort_parses_without_loss() -> None:
    fhir_dir = Settings().synthea_dir / "dev-1000" / "fhir"
    if not fhir_dir.is_dir():
        pytest.fail(f"cohort not found at {fhir_dir}; run `make synthea-dev`")
    patients = non_patient = 0
    issues: Counter[str] = Counter()
    for path in iter_data_files(fhir_dir, "*.json"):
        raw_counts = _raw_counts(path)
        try:
            record = read_bundle_file(path)
        except BundleError:
            assert raw_counts["Patient"] == 0
            non_patient += 1
            continue
        patients += 1
        for rtype, attr in PARSED_TYPES.items():
            assert len(getattr(record, attr)) == raw_counts[rtype], f"{path.name}: {rtype}"
        assert not [i for i in record.issues if i.severity == "error"], path.name
        issues.update(i.code for i in record.issues)
    assert (patients, non_patient) == (1148, 2)
    # Synthea records an encounter after death for most deceased patients (see docs/data).
    assert set(issues) <= {"event_after_death"}


def _raw_counts(path: Path) -> Counter[str]:
    bundle = json.loads(path.read_bytes())
    return Counter(e["resource"]["resourceType"] for e in bundle.get("entry", []))
