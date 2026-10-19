import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from medgraph.agent.llm import OllamaClient
from medgraph.agent.notes import FINDINGS, candidate_notes, check_notes, quote_problem
from medgraph.graph.build import build_patient_graph
from medgraph.graph.schema import NodeData, PatientGraph
from medgraph.ingest.fhir import read_bundle_file
from medgraph.records import Timepoint
from medgraph.rules.model import Evidence, Flag
from medgraph.settings import Settings

AS_OF = dt.date(2026, 1, 1)
FLAG = Flag(
    rule="ckd-criteria-no-diagnosis",
    kind="criteria_met_no_diagnosis",
    title="Kidney values meet the CKD criteria; no kidney diagnosis is recorded",
    statement="...",
    evidence=(
        Evidence(node="lab_result:obs-a1", label="eGFR", date=dt.date(2025, 3, 1), value="41"),
    ),
)
NOTES = {
    "note:old": (dt.date(2024, 6, 1), "Assessment: renal function reduced, eGFR 41."),
    "note:n1": (dt.date(2025, 4, 2), "Plan: review blood pressure.\nNo kidney problems known."),
    "note:n2": (dt.date(2025, 9, 15), "Assessment and Plan: CKD stage 3a, eGFR 41; recheck."),
    "note:n3": (dt.date(2025, 11, 30), "Seen for a sprained ankle."),
    "note:n4": (dt.date(2025, 12, 20), "Influenza vaccine given."),
    "note:late": (dt.date(2026, 2, 1), "Kidney disease noted."),
}


@pytest.fixture
def graph(fhir_fixture_dir: Path) -> PatientGraph:
    g = build_patient_graph(read_bundle_file(fhir_fixture_dir / "patient-a.json"))
    for node, (date, text) in NOTES.items():
        g.add_node(
            node,
            **NodeData(
                kind="note", label="Note", start=Timepoint(date=date, precision="day"), text=text
            ).attrs(),
        )
    return g


def client(
    reply: dict[str, Any] | None, seen: list[dict[str, Any]], tmp_path: Path
) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        content = json.dumps({"mention": reply})
        return httpx.Response(200, json={"model": "m", "message": {"content": content}})

    settings = Settings(_env_file=None, data_dir=tmp_path)
    return OllamaClient(settings, transport=httpx.MockTransport(handler))


def test_the_most_recent_notes_of_the_flags_period_are_read(graph: PatientGraph) -> None:
    notes = candidate_notes(graph, FLAG, AS_OF)
    assert [n for n, _, _ in notes] == ["note:n4", "note:n3", "note:n2"]  # not before the
    # flag's first evidence, not after the evaluation date, at most three


@pytest.mark.parametrize(
    ("quote", "problem"),
    [
        ("CKD stage 3a, eGFR 41", None),
        ("ckd   STAGE 3a,\neGFR 41", None),  # spacing and case do not matter
        ("CKD stage 3, eGFR 41", "quote not in the note word for word"),
        ("recheck", "quote has none of the rule's finding terms"),
        ("", "empty quote"),
    ],
)
def test_a_quote_is_kept_only_if_verbatim_and_on_topic(quote: str, problem: str | None) -> None:
    note = NOTES["note:n2"][1]
    assert quote_problem(quote, note, FINDINGS["ckd-criteria-no-diagnosis"].terms) == problem


def test_a_verbatim_mention_annotates_the_flag(graph: PatientGraph, tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    reply = {"note": "N3", "quote": "CKD stage 3a, eGFR 41", "stance": "affirms"}
    result = check_notes(graph, FLAG, AS_OF, client(reply, seen, tmp_path), "d")
    assert result.mention is not None
    assert (result.mention.node, result.mention.date) == ("note:n2", dt.date(2025, 9, 15))
    assert result.mention.stance == "affirms"
    assert result.rejected is None
    (request,) = seen
    prompt = request["messages"][1]["content"]
    assert "chronic kidney disease or reduced kidney function" in prompt
    assert "[N1] 2025-12-20" in prompt


def test_an_invented_quote_is_refused_and_kept_for_review(
    graph: PatientGraph, tmp_path: Path
) -> None:
    reply = {"note": "N3", "quote": "known chronic kidney disease", "stance": "affirms"}
    result = check_notes(graph, FLAG, AS_OF, client(reply, [], tmp_path), "d")
    assert result.mention is None
    assert result.rejected == "quote not in the note word for word"
    assert result.model_quote == "known chronic kidney disease"


def test_a_quote_from_an_unknown_note_is_refused(graph: PatientGraph, tmp_path: Path) -> None:
    reply = {"note": "N9", "quote": "CKD stage 3a", "stance": "affirms"}
    result = check_notes(graph, FLAG, AS_OF, client(reply, [], tmp_path), "d")
    assert result.mention is None
    assert result.rejected == "unknown note ID"


def test_a_note_id_given_with_the_notes_text_is_read_and_the_text_never_kept(
    graph: PatientGraph, tmp_path: Path
) -> None:
    """In the evaluation run the model returned a note's header and whole text as its ID, and
    the refusal reason, which reaches the report, held the note."""
    note = NOTES["note:n2"][1]
    reply = {"note": f"N3] 2025-09-15\n\n{note}", "quote": "CKD stage 3a, eGFR 41", "stance": "x"}
    result = check_notes(graph, FLAG, AS_OF, client(reply, [], tmp_path), "d")
    assert result.rejected == "unknown stance"  # the ID was read: N3
    reply["stance"] = "affirms"
    result = check_notes(graph, FLAG, AS_OF, client(reply, [], tmp_path), "d")
    assert result.mention is not None
    assert result.mention.node == "note:n2"


def test_no_mention_is_a_valid_answer(graph: PatientGraph, tmp_path: Path) -> None:
    result = check_notes(graph, FLAG, AS_OF, client(None, [], tmp_path), "d")
    assert (result.mention, result.rejected) == (None, None)
    assert len(result.notes) == 3


def test_no_notes_in_the_period_means_no_model_call(graph: PatientGraph, tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []
    early = FLAG.model_copy(update={"evidence": ()})
    result = check_notes(graph, early, dt.date(2020, 1, 1), client(None, seen, tmp_path), "d")
    assert result.notes == ()
    assert seen == []
