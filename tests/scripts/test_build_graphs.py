import hashlib
import json
import shutil
from pathlib import Path

import pytest

import build_graphs as bg
import view_patient as vp
from medgraph.graph.store import GraphStore
from medgraph.ingest.lab_report import PROMPT_VERSION
from medgraph.settings import Settings

REPORT = """\
LABORATORIO SINTETICO
ID paziente: {tag}    Data prelievo: 07/03/2025
Creatinina         1,4        mg/dL    0,50 - 0,90    H
"""
TRANSCRIPTION = {
    "collection_date": "07/03/2025",
    "rows": [
        {
            "analyte": "Creatinina",
            "value": "1,4",
            "unit": "mg/dL",
            "reference_range": "",
            "flag": "",
        }
    ],
}


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fhir_fixture_dir: Path) -> Path:
    data = tmp_path / "data"
    shutil.copytree(fhir_fixture_dir, data / "synthea" / "tiny" / "fhir")
    monkeypatch.setenv("MEDGRAPH_DATA_DIR", str(data))
    monkeypatch.chdir(tmp_path)  # no .env from the repository
    return data


def save_report(data: Path, repo: Path, name: str, text: str, transcribed_text: str) -> None:
    (repo / "configs" / "extraction").mkdir(parents=True, exist_ok=True)
    (repo / "configs" / "extraction" / "fake.yaml").write_text(
        "name: fake\nmethod: llm\nmodel: m\nreport_set: rs\n", encoding="utf-8"
    )
    report_dir = data / "lab_reports" / "rs" / "test"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{name}.txt").write_text(text, encoding="utf-8")
    saved = data / "runs" / "extraction" / "fake" / "test" / "predictions" / "text"
    saved.mkdir(parents=True, exist_ok=True)
    (saved / f"{name}.json").write_text(
        json.dumps(
            {
                "text_sha256": hashlib.sha256(transcribed_text.encode()).hexdigest(),
                "prompt_version": PROMPT_VERSION,
                "transcription": TRANSCRIPTION,
                "error": None,
            }
        ),
        encoding="utf-8",
    )


def test_reports_are_filed_by_patient_tag_and_checked_against_their_text(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    monkeypatch.setattr(bg, "REPO_ROOT", repo)
    tag = hashlib.sha256(b"pat-a").hexdigest()[:8]
    text = REPORT.format(tag=tag)
    save_report(data_dir, repo, "r1", text, text)
    save_report(data_dir, repo, "r2", text, text + "edited")  # transcription of another text
    by_tag, skipped = bg.load_reports(
        (bg.ReportSource(extraction="fake", split="test"),), Settings()
    )
    (attached,) = by_tag[tag]
    assert attached.name == "rs/test/r1.txt"
    assert attached.result.source.startswith("sha256:")
    assert attached.result.rows[0].status == "ok"
    assert skipped == {"saved transcription is of another text": 1}


def test_builds_checks_stores_and_reports(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "tiny.yaml"
    config.write_text("cohort: tiny\n", encoding="utf-8")
    out = tmp_path / "graphs.md"
    assert bg.main([str(config), "--out", str(out)]) == 0
    report = out.read_text(encoding="utf-8")
    assert "**2 patients built. Invariant violations: 0.**" in report
    assert "2 of 2 medication requests (100%) name the condition they treat" in report
    with GraphStore(data_dir / "graphs" / "tiny.sqlite") as store:
        assert set(store.digests()) == {"pat-a", "pat-b"}
        digest = store.meta()["store_digest"]
    assert digest in report

    # Rebuilding gives the same store and the same report.
    assert bg.main([str(config), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == report

    assert vp.main(["tiny", "--list"]) == 0
    listed = capsys.readouterr().out
    assert "pat-a" in listed
    assert "Chronic kidney disease stage 3" in listed
    assert vp.main(["tiny", "pat-b"]) == 0
    page = data_dir / "views" / "tiny" / "pat-b.html"
    assert "Synthetic data (Synthea)" in page.read_text(encoding="utf-8")
