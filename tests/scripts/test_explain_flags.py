import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest

import explain_flags as ef
from medgraph.agent.llm import OllamaClient
from medgraph.graph.build import build_patient_graph
from medgraph.graph.knowledge import KnowledgeGraph, KnowledgeNode
from medgraph.graph.store import GraphStore
from medgraph.ingest.fhir import read_bundle_file
from medgraph.rag.chunks import Chunk
from medgraph.rag.index import KnowledgeIndex
from medgraph.rules.model import Evidence, Flag, PatientRules
from medgraph.settings import Settings

AS_OF = dt.date(2026, 1, 1)


def flag(rule: str = "ckd-gfr-follow-up") -> Flag:
    return Flag(
        rule=rule,
        kind="follow_up_due",
        title="No GFR test in the last 12 months",
        statement="The CKD criteria are met; no GFR test in the 12 months before 2026-01-01.",
        evidence=(
            Evidence(node="lab_result:obs-a1", label="last GFR test", date=dt.date(2024, 12, 30)),
        ),
        sources=("kdigo-2024-monitoring",),
        limitations=("A test done elsewhere and not in this record would not be seen.",),
    )


def rules(patient: str, *flags: Flag) -> tuple[str, PatientRules]:
    return patient, PatientRules(patient_id=patient, as_of=AS_OF, evaluated=True, flags=flags)


def test_the_sample_is_stratified_seeded_and_ignores_store_order() -> None:
    rows = [rules(f"p{i}", flag(), flag("ckd-albuminuria-follow-up")) for i in range(10)]
    sample = ef.sample_flags(rows, per_rule=3)
    assert Counter(f.rule for _, f in sample) == {
        "ckd-albuminuria-follow-up": 3,
        "ckd-gfr-follow-up": 3,
    }
    assert ef.sample_flags(list(reversed(rows)), per_rule=3) == sample
    assert ef.sample_flags(rows, per_rule=3, seed="other") != sample


def test_development_flags_are_never_sampled_and_dev_flags_come_after_the_sample() -> None:
    rows = [rules(f"p{i}", flag()) for i in range(10)]
    sample = ef.sample_flags(rows, per_rule=3, exclude=())
    read = {(sample[0][0], "ckd-gfr-follow-up")}
    without = ef.sample_flags(rows, per_rule=3, exclude=read)
    assert without == [*sample[1:], ef.sample_flags(rows, per_rule=4, exclude=())[3]]
    dev = ef.sample_flags(rows, per_rule=2, skip=3, exclude=read)
    taken = {p for p, _ in without} | {sample[0][0]}
    assert len(dev) == 2
    assert not {p for p, _ in dev} & taken


class FakeOllama:
    """Answers tags, embed and chat; counts the model calls."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            models = [
                {"name": "qwen3.5:9b", "digest": "q1"},
                {"name": "bge-m3:latest", "digest": "b1"},
            ]
            return httpx.Response(200, json={"models": models})
        body = json.loads(request.content)
        if request.url.path == "/api/embed":
            self.calls["embed"] += 1
            return httpx.Response(200, json={"embeddings": [[1.0, 0.0] for _ in body["input"]]})
        self.calls["chat"] += 1
        if "sentences" in body["format"]["properties"]:
            content = {
                "sentences": [
                    {"text": "The last GFR test was on 2024-12-30.", "cites": ["E1"]},
                    {"text": "KDIGO 2024 says to assess GFR at least annually.", "cites": ["S1"]},
                    {"text": "Il paziente deve iniziare una terapia.", "cites": ["F"]},
                ]
            }
        else:
            content = {"mention": None}
        message = {"content": json.dumps(content)}
        return httpx.Response(200, json={"model": body["model"], "message": message})


@pytest.fixture
def setup(tmp_path: Path, fhir_fixture_dir: Path) -> dict[str, Any]:
    graph = build_patient_graph(read_bundle_file(fhir_fixture_dir / "patient-a.json"))
    store = GraphStore(tmp_path / "graphs.sqlite")
    store.save(graph)
    index = KnowledgeIndex(tmp_path / "index.sqlite")
    chunks = [
        Chunk(
            id=f"guide@v/1.{i}",
            source="guide",
            version="v",
            title="Guide",
            where="p. 1",
            text=f"GFR monitoring in chronic kidney disease, part {i}.",
        )
        for i in range(1, 4)
    ]
    index.replace_chunks(chunks, {"cohort": "tiny"})
    index.save_embeddings("bge-m3", "b1", [c.id for c in chunks], [[1, 0], [0.9, 0.1], [0, 1]])
    knowledge = KnowledgeGraph(
        cohort="tiny",
        inputs={},
        nodes={"document:guide": KnowledgeNode(kind="document", title="Guide", citation="Guide")},
        links=(),
        unlabelled={},
    )
    fake = FakeOllama()
    settings = Settings(_env_file=None, data_dir=tmp_path)
    transport = httpx.MockTransport(fake.handler)
    return {
        "sample": [("pat-a", flag())],
        "run_dir": tmp_path / "runs",
        "store": store,
        "index": index,
        "knowledge": knowledge,
        "embedder": OllamaClient(settings, model="bge-m3", transport=transport),
        "chat": OllamaClient(settings, transport=transport),
        "fake": fake,
    }


def run(setup: dict[str, Any]) -> dict[str, str]:
    return ef.run(
        setup["sample"],
        run_dir=setup["run_dir"],
        as_of=AS_OF,
        store=setup["store"],
        index=setup["index"],
        knowledge=setup["knowledge"],
        embedder=setup["embedder"],
        chat=setup["chat"],
    )


def test_a_run_writes_packs_variants_and_note_checks_then_resumes(setup: dict[str, Any]) -> None:
    digests = run(setup)
    assert digests == {"chat_digest": "q1", "embedding_digest": "b1"}
    record = ef.load_record(ef.record_path(setup["run_dir"], "pat-a", "ckd-gfr-follow-up"))
    pack = record["pack"]
    assert [i["id"] for i in pack["items"]] == ["F", "E1", "L1", "S1", "P1", "P2", "P3"]
    assert pack["searched"] == ["guide"]
    assert set(record["explanations"]) == {
        "clinician-en",
        "personal-en",
        "clinician-it",
        "personal-it",
    }
    english = record["explanations"]["clinician-en"]
    assert english["origin"] == "model"
    assert [d["reasons"] for d in english["dropped"]] == [["treatment instruction"]]
    # The fake answers in English: in Italian, its sentences fail the language check.
    assert record["explanations"]["personal-it"]["origin"] == "template"
    assert record["note_check"]["notes"] == []  # the fixture's notes are undated
    assert setup["fake"].calls == {"embed": 1, "chat": 4}

    run(setup)  # everything is cached: no model call
    assert setup["fake"].calls == {"embed": 1, "chat": 4}


def test_the_report_and_review_come_from_the_cache(setup: dict[str, Any]) -> None:
    run(setup)
    records = [ef.load_record(ef.record_path(setup["run_dir"], "pat-a", "ckd-gfr-follow-up"))]
    summary = ef.summarize(records)
    assert summary["variants"]["clinician-en"]["model"] == 1
    assert summary["variants"]["clinician-en"]["reasons"] == {"treatment instruction": 1}
    provenance = {
        "cohort": "tiny",
        "model": "qwen3.5:9b",
        "knowledge": "k" * 64,
        "as_of": "2026-01-01",
        "per_rule": 6,
        "seed": ef.SEED,
        "code_digest": "c" * 64,
        "git": {"commit": "abc", "dirty": False},
        "chat_digest": "q1",
        "embedding_digest": "b1",
    }
    report = ef.report_markdown(summary, provenance, Counter({"ckd-gfr-follow-up": 1}))
    assert "| clinician-en | 1 | 1 (100%) | 0 |" in report
    assert "treatment instruction: 1" in report
    review = ef.review_markdown(records)
    assert "### Evidence pack" in review
    assert "~~Il paziente deve iniziare una terapia.~~" in review


def test_the_report_never_shows_text_the_model_wrote(setup: dict[str, Any]) -> None:
    """The evaluation run's first report printed whole notes, which the model had returned as a
    note's ID, through the refusal reason; and a cited "ID" is model text too."""
    run(setup)
    record = ef.load_record(ef.record_path(setup["run_dir"], "pat-a", "ckd-gfr-follow-up"))
    note = "N3] 2025-08-10\n\n# Chief Complaint\nSeth414 is a 81 year-old male."
    record["note_check"]["rejected"] = f"unknown note {note!r}"
    record["explanations"]["clinician-en"]["dropped"][0]["reasons"] = [
        "unknown citation the note of Seth414"
    ]
    summary = ef.summarize([record])
    provenance = {
        "cohort": "tiny",
        "model": "m",
        "knowledge": "k" * 64,
        "as_of": "2026-01-01",
        "per_rule": 6,
        "seed": ef.SEED,
        "code_digest": "c" * 64,
        "git": {"commit": "abc", "dirty": True},
        "chat_digest": "q1",
        "embedding_digest": "b1",
        "report": {"code_digest": "d" * 64, "git": {}, "date": "2026-10-19"},
    }
    report = ef.report_markdown(summary, provenance, Counter({"ckd-gfr-follow-up": 1}))
    assert "Seth414" not in report
    assert "unknown note: 1" in report
    assert "unknown citation: 1" in report
    assert "rewritten from the cache on 2026-10-19 by code `dddddddddddd`" in report


@pytest.mark.parametrize(
    ("reason", "shown"),
    [
        ("number not in cited items", "number not in cited items"),
        ("item ID in the text", "item ID in the text"),
        (
            "quote has none of the rule's finding terms",
            "quote has none of the rule's finding terms",
        ),
        ("1 sentence(s) passed the checks; 2 needed", "1 sentence(s) passed the checks; 2 needed"),
        ("unknown citation E", "unknown citation"),
        ("model call failed: 500 Server Error", "model call failed"),
        ("a reason\nwith a line break", "other"),
        ("x" * 61, "other"),
    ],
)
def test_reasons_are_shown_by_their_fixed_part(reason: str, shown: str) -> None:
    assert ef.reported(reason) == shown
