"""Cached explanations and note checks: one JSON record per flag (``scripts/explain_flags.py``).

A record lives at ``$MEDGRAPH_DATA_DIR/runs/explanations/<cohort>/<patient>/<rule>.json`` and
holds the flag's evidence pack, its explanations and its note check. Pages show a record only
for the flag it was written for: once the flag changes, its explanations are not shown.
"""

import json
from pathlib import Path
from typing import Any

from medgraph.agent.evidence import flag_hash
from medgraph.agent.explain import page_payload
from medgraph.rag.fetch import write_atomic
from medgraph.rules.model import PatientRules
from medgraph.settings import Settings


def explanations_dir(settings: Settings, cohort: str) -> Path:
    return settings.runs_dir / "explanations" / cohort


def record_path(run_dir: Path, patient: str, rule: str) -> Path:
    return run_dir / patient / f"{rule}.json"


def load_record(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, (json.dumps(record, indent=1, sort_keys=True) + "\n").encode())


def explanations_for(
    settings: Settings, cohort: str, patient: str, rules: PatientRules
) -> dict[str, Any]:
    """The cached explanations of this patient's flags, for the flags they were written for:
    a flag that has changed since shows none."""
    run_dir = explanations_dir(settings, cohort)
    found = {}
    for flag in rules.flags:
        record = load_record(record_path(run_dir, patient, flag.rule))
        if record and record.get("flag_hash") == flag_hash(flag):
            found[flag.rule] = page_payload(record)
    return found
