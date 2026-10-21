import datetime as dt
from pathlib import Path

from medgraph.agent.cache import explanations_dir, explanations_for, record_path, save_record
from medgraph.agent.evidence import flag_hash
from medgraph.rules.model import Flag, PatientRules
from medgraph.settings import Settings


def test_a_page_shows_only_explanations_of_unchanged_flags(tmp_path: Path) -> None:
    flag = Flag(rule="ckd-gfr-follow-up", kind="follow_up_due", title="t", statement="s")
    rules = PatientRules(
        patient_id="pat-a", as_of=dt.date(2026, 1, 1), evaluated=True, flags=(flag,)
    )
    settings = Settings(_env_file=None, data_dir=tmp_path)
    pack = {
        "patient": "pat-a",
        "rule": flag.rule,
        "flag_hash": flag_hash(flag),
        "query": "",
        "searched": [],
        "items": [{"id": "F", "kind": "flag", "text": "t. s"}],
    }
    save_record(
        record_path(explanations_dir(settings, "tiny"), "pat-a", flag.rule),
        {"flag_hash": flag_hash(flag), "pack": pack, "explanations": {}, "note_check": None},
    )
    assert set(explanations_for(settings, "tiny", "pat-a", rules)) == {"ckd-gfr-follow-up"}
    changed = rules.model_copy(update={"flags": (flag.model_copy(update={"statement": "s2"}),)})
    assert explanations_for(settings, "tiny", "pat-a", changed) == {}
    assert explanations_for(settings, "other", "pat-a", rules) == {}
