import datetime as dt
import json
from pathlib import Path
from typing import Any

import evaluate_extraction as ev
from generate_lab_reports import TruthReport, TruthRow
from medgraph.ingest.lab_report import (
    TranscribedRow,
    Transcription,
    interpret,
    transcribe_with_rules,
)

TEXT = """\
Collection date: 07/03/2025
Creatinine         117      µmol/L    [44 - 80]    H
S-Creatinine2      1        mg/dL
Haemoglobin        135      g/L       [120 - 155]
Glucose            98       mg/dL     [70 - 99]
"""


def truth_row(
    name: str,
    value: str,
    unit: str,
    rng: str,
    flag: str,
    analyte: str | None,
    canonical: str | None,
    seen: bool | None,
) -> TruthRow:
    return TruthRow(
        test=analyte or "glucose",
        loinc="x",
        analyte=analyte,
        analyte_text=name,
        name_seen=seen,
        value_text=value,
        unit_text=unit,
        range_text=rng,
        flag_text=flag,
        qualitative=False,
        canonical_value=canonical,
        canonical_unit="mg/dL" if canonical else None,
    )


TRUTH = TruthReport(
    report_id="test-table-en-GB-00",
    split="test",
    family="table",
    language="en",
    style="en-GB",
    sex="F",
    collection_date=dt.date(2025, 3, 7),
    collection_date_text="07/03/2025",
    rows=(
        truth_row(
            "Creatinine", "117", "µmol/L", "44 - 80", "H", "creatinine", str(117 / 88.4), True
        ),
        truth_row("Haemoglobin", "135", "g/L", "120 - 155", "", "hemoglobin", "13.5", True),
        truth_row("Glucose", "98", "mg/dL", "70 - 99", "", None, None, None),
    ),
)


def score(*rows: TranscribedRow) -> ev.ReportScore:
    transcription = Transcription(collection_date="07/03/2025", rows=rows)
    result = interpret(transcription, TEXT, "report:t")
    return ev.score_report(TRUTH, transcription, result, "text", {"narrative"})


def test_perfect_transcription() -> None:
    s = score(
        TranscribedRow(
            analyte="Creatinine", value="117", unit="µmol/L", reference_range="[44 - 80]", flag="H"
        ),
        TranscribedRow(
            analyte="Haemoglobin", value="135", unit="g/L", reference_range="[120 - 155]"
        ),
        TranscribedRow(analyte="Glucose", value="98", unit="mg/dL", reference_range="[70 - 99]"),
    )
    assert (s.truth_rows, s.predicted_rows, s.matched) == (3, 3, 3)
    assert (s.value_ok, s.unit_ok, s.range_ok, s.flag_ok) == (3, 3, 3, 3)  # brackets ignored
    assert s.in_scope == 2
    assert s.seen_names == s.seen_names_ok == 2
    assert (s.ungrounded, s.misplaced, s.language_ok) == (0, 0, 1)
    # "07/03/2025" alone proves no day/month order: refused, never guessed.
    assert (s.date_ok, s.date_refused, s.date_wrong, s.date_order_wrong) == (0, 1, 0, 0)
    assert (s.in_scope_seen, s.end_to_end_seen_ok, s.in_scope_held_out) == (2, 2, 0)
    assert (s.accepted, s.accepted_wrong) == (2, 0)  # glucose is unmapped, not accepted


def test_a_plausible_wrong_value_that_is_accepted_is_counted() -> None:
    # "1 mg/dL" is in the report (another row), so it is grounded and plausible: only the
    # comparison with the truth can tell that creatinine was printed as 117 umol/L.
    s = score(
        TranscribedRow(analyte="Creatinine", value="1", unit="mg/dL"),
        TranscribedRow(analyte="Haemoglobin", value="135", unit="g/L"),
    )
    assert (s.ungrounded, s.accepted, s.accepted_wrong) == (0, 2, 1)
    assert ev.rate(ev.totals([s]), "wrong values accepted") == 1 / 2


def test_a_date_settled_by_another_date_is_scored_correct() -> None:
    transcription = Transcription(collection_date="07/03/2025")
    text = TEXT.replace("07/03/2025", "07/03/2025    Report date: 19/03/2025")
    s = ev.score_report(TRUTH, transcription, interpret(transcription, text, "r"), "text", set())
    assert (s.date_ok, s.date_refused, s.date_wrong) == (1, 0, 0)


def test_a_value_copied_from_the_range_is_counted_as_misplaced() -> None:
    s = score(TranscribedRow(analyte="Haemoglobin", value="120", unit="g/L"))  # from [120 - 155]
    assert (s.misplaced, s.accepted, s.accepted_wrong) == (1, 0, 0)


def test_errors_are_counted_per_field() -> None:
    s = score(
        TranscribedRow(analyte="Creatinine", value="117", unit="mg/dL"),  # wrong unit, no range
        TranscribedRow(analyte="Haemoglobin", value="153", unit="g/L"),  # invented value
    )
    assert (s.matched, s.value_ok, s.unit_ok, s.range_ok) == (2, 1, 1, 0)
    assert s.ungrounded == 1
    assert s.seen_names_ok == 2  # names are scored on their own, even on a rejected row
    assert s.end_to_end_ok == 0  # "117 mg/dL" is not the printed 117 umol/L
    assert ev.rate(ev.totals([s]), "row recall") == 2 / 3


def report_dir(tmp_path: Path, text: str = TEXT) -> Path:
    reports = tmp_path / "reports"
    (reports / "test").mkdir(parents=True, exist_ok=True)
    (reports / "test" / f"{TRUTH.report_id}.txt").write_text(text, encoding="utf-8")
    return reports


def test_llm_transcriptions_are_reused_only_for_the_same_text_and_key(tmp_path: Path) -> None:
    calls: list[str] = []

    def transcribe(text: str) -> tuple[Transcription, dict[str, Any] | None]:
        calls.append(text)
        row = TranscribedRow(analyte="Glucose", value="98", unit="mg/dL")
        return Transcription(collection_date="07/03/2025", rows=(row,)), {"seconds": 1.0}

    cfg = ev.ExtractionConfig(name="t", method="llm", model="m", report_set="r", formats=("text",))
    key = {"model": "m", "model_digest": "d1", "prompt_version": "v", "num_ctx": 4096}

    def go(reports: Path, cache_key: dict[str, Any]) -> list[dict[str, Any]]:
        out = tmp_path / "out"
        return ev.run(cfg, [TRUTH], reports, out, transcribe, cache_key, log=lambda _: None)

    reports = report_dir(tmp_path)
    go(reports, key)
    (prediction,) = go(reports, key)
    assert len(calls) == 1  # reused
    assert prediction["result"]["rows"][0]["status"] == "unmapped"  # interpreted afresh
    saved = json.loads(next((tmp_path / "out").rglob("*.json")).read_text(encoding="utf-8"))
    assert "result" not in saved  # only the transcription is cached
    go(reports, {**key, "model_digest": "d2"})  # a re-pulled model is a different model
    assert len(calls) == 2
    go(report_dir(tmp_path, TEXT + "Ferritin  45  ng/mL\n"), {**key, "model_digest": "d2"})
    assert len(calls) == 3  # a regenerated report is a different input


def test_rule_transcriptions_are_never_cached(tmp_path: Path) -> None:
    calls = 0

    def transcribe(text: str) -> tuple[Transcription, dict[str, Any] | None]:
        nonlocal calls
        calls += 1
        return transcribe_with_rules(text), None

    cfg = ev.ExtractionConfig(name="t", method="rules", report_set="r", formats=("text",))
    for _ in range(2):
        out = tmp_path / "out"
        ev.run(cfg, [TRUTH], report_dir(tmp_path), out, transcribe, log=lambda _: None)
    assert calls == 2


def test_code_digest_tracks_the_result_code(tmp_path: Path) -> None:
    (tmp_path / "src" / "medgraph").mkdir(parents=True)
    (tmp_path / "scripts").mkdir()
    for name in ev.RESULT_SCRIPTS:
        (tmp_path / "scripts" / name).write_text("", encoding="utf-8")
    module = tmp_path / "src" / "medgraph" / "rules.py"
    module.write_text("THRESHOLD = 60\n", encoding="utf-8")
    before = ev.code_digest(tmp_path)
    assert ev.code_digest(tmp_path) == before
    module.write_text("THRESHOLD = 90\n", encoding="utf-8")
    assert ev.code_digest(tmp_path) != before


def test_provenance_line_says_when_code_is_uncommitted() -> None:
    line = ev.provenance_line(
        {"git": {"commit": "86c8688abc", "dirty": True}, "code_digest": "f" * 64}
    )
    assert "`86c8688` plus uncommitted changes" in line
    assert "`ffffffffffff`" in line


def test_interval_is_exact_where_the_bootstrap_collapses() -> None:
    def scores(accepted: int, wrong: int) -> list[ev.ReportScore]:
        return [
            ev.ReportScore(
                "r", "text", "table", "en", False, accepted=accepted, accepted_wrong=wrong
            )
        ]

    low, high, exact = ev.interval(scores(69, 0), "wrong values accepted", 200, 0) or (0, 0, 0)
    assert (low, exact) == (0.0, True)
    assert abs(high - 0.0521) < 1e-4  # Clopper-Pearson upper bound for 0 of 69
    low, high, exact = ev.interval(scores(66, 66), "wrong values accepted", 200, 0) or (0, 0, 0)
    assert (high, exact) == (1.0, True)
    assert abs(low - 0.9456) < 1e-4
    assert ev.interval(scores(0, 0), "wrong values accepted", 200, 0) is None


def test_bootstrap_interval_is_deterministic_and_bounded() -> None:
    scores = [score(TranscribedRow(analyte="Glucose", value="98", unit="mg/dL")) for _ in range(5)]
    scores.append(score())
    first = ev.bootstrap_ci(scores, "row recall", 200, seed=1)
    assert first == ev.bootstrap_ci(scores, "row recall", 200, seed=1)
    assert first is not None
    assert 0 <= first[0] <= first[1] <= 1
