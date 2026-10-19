import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from medgraph.agent.evidence import (
    EvidencePack,
    build_pack,
    document_citations,
    flag_hash,
    query_for,
    retrieve,
)
from medgraph.agent.explain import (
    TITLE_IT,
    VARIANTS,
    WHY_IT,
    closing,
    explain,
    review,
    system_prompt,
    template,
    user_prompt,
)
from medgraph.agent.llm import OllamaClient
from medgraph.agent.verify import check_sentence, sources_named
from medgraph.graph.knowledge import KnowledgeGraph, KnowledgeNode
from medgraph.rag.chunks import Chunk
from medgraph.rag.index import KnowledgeIndex
from medgraph.rules.model import Evidence, Flag
from medgraph.rules.sources import SOURCES
from medgraph.settings import Settings

FLAGS = {
    "anaemia-criteria-no-diagnosis": Flag(
        rule="anaemia-criteria-no-diagnosis",
        kind="criteria_met_no_diagnosis",
        title="Haemoglobin below the WHO cutoff; no anaemia diagnosis is recorded",
        statement=(
            "Most recent haemoglobin 121.13 g/L on 2023-08-09 is below the WHO 2024 cutoff of "
            "130 g/L for men 15-65 years. No anaemia diagnosis is recorded for this period. This "
            "may be worth discussing with a doctor."
        ),
        evidence=(
            Evidence(
                node="lab_result:o1",
                label="Hemoglobin [Mass/volume] in Blood",
                date=dt.date(2023, 8, 9),
                value="121.13 g/L",
                note="most recent haemoglobin",
            ),
        ),
        sources=("who-2024-cutoffs", "who-2024-elevation"),
        limitations=("Only the most recent haemoglobin is assessed.",),
    ),
    "ckd-criteria-no-diagnosis": Flag(
        rule="ckd-criteria-no-diagnosis",
        kind="criteria_met_no_diagnosis",
        title="Kidney values meet the CKD criteria; no kidney diagnosis is recorded",
        statement=(
            "The recorded values meet the KDIGO 2024 criteria for chronic kidney disease (eGFR "
            "below 60 in 3 consecutive values from 2024-01-02 to 2025-02-06 (401 days); latest "
            "category G3a). No kidney diagnosis is recorded for this period."
        ),
        sources=("kdigo-2024-criteria", "kdigo-2024-categories", "ckd-epi-2021"),
    ),
    "ckd-gfr-follow-up": Flag(
        rule="ckd-gfr-follow-up",
        kind="follow_up_due",
        title="No GFR test in the last 12 months",
        statement="The CKD criteria are met; no GFR test in the 12 months before 2026-01-01.",
        sources=("kdigo-2024-monitoring",),
    ),
    "ckd-albuminuria-follow-up": Flag(
        rule="ckd-albuminuria-follow-up",
        kind="follow_up_due",
        title="No albuminuria test in the last 12 months",
        statement=(
            "CKD stage 2 is recorded; no albuminuria test in the 12 months before 2026-01-01."
        ),
        sources=("kdigo-2024-monitoring",),
    ),
    "ckd-haemoglobin-follow-up": Flag(
        rule="ckd-haemoglobin-follow-up",
        kind="follow_up_due",
        title="No haemoglobin test in the last 12 months",
        statement=(
            "CKD stage 2 is recorded; no haemoglobin test in the 12 months before 2026-01-01."
        ),
        sources=("kdigo-2012-anaemia-testing", "kdigo-2012-anaemia-intervals"),
    ),
    "metformin-egfr-30-44": Flag(
        rule="metformin-egfr-30-44",
        kind="medication_threshold",
        title="Metformin is active; the latest eGFR is between 30 and 44",
        statement="The latest eGFR is 35 mL/min/1.73 m2.",
        sources=("label-metformin-egfr-45",),
    ),
    "metformin-egfr-below-30": Flag(
        rule="metformin-egfr-below-30",
        kind="medication_threshold",
        title="Metformin is active; the latest eGFR is below 30",
        statement="The latest eGFR is 18 mL/min/1.73 m2.",
        sources=("label-metformin-egfr-30",),
    ),
}
PASSAGE = Chunk(
    id="who-2024-haemoglobin@315a402cb68e/12.1",
    source="who-2024-haemoglobin",
    version="315a402cb68e",
    title="WHO haemoglobin guideline",
    where="p. 12",
    text="Anaemia is a condition in which the haemoglobin concentration is lower than normal.",
)


def pack_for(rule: str) -> EvidencePack:
    return build_pack("p", FLAGS[rule], [PASSAGE] if rule.startswith("anaemia") else [])


def client(reply: dict[str, Any], seen: list[dict[str, Any]], tmp_path: Path) -> OllamaClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "qwen3.5:9b",
                "message": {"content": json.dumps(reply)},
                "prompt_eval_count": 900,
                "eval_count": 120,
            },
        )

    settings = Settings(_env_file=None, data_dir=tmp_path)
    return OllamaClient(settings, transport=httpx.MockTransport(handler))


def test_the_pack_numbers_every_item_and_is_the_same_for_every_variant() -> None:
    pack = pack_for("anaemia-criteria-no-diagnosis")
    assert [i.id for i in pack.items] == ["F", "E1", "L1", "S1", "S2", "P1"]
    assert pack.by_id()["E1"].text == (
        "2023-08-09: Hemoglobin [Mass/volume] in Blood: 121.13 g/L (most recent haemoglobin)"
    )
    assert pack.by_id()["S1"].ref == "who-2024-cutoffs"
    assert pack.flag_hash == flag_hash(FLAGS["anaemia-criteria-no-diagnosis"])
    assert pack.digest() == pack_for("anaemia-criteria-no-diagnosis").digest()
    assert "[P1] WHO haemoglobin guideline, p. 12: Anaemia is" in user_prompt(pack)


def test_a_passage_is_named_by_its_documents_citation() -> None:
    """The WHO guideline's title does not name the WHO; its citation does, so a sentence saying
    "the WHO guideline" can cite the passage."""
    flag = FLAGS["anaemia-criteria-no-diagnosis"]
    citation = "WHO. Guideline on haemoglobin cutoffs. Geneva: World Health Organization; 2024"
    pack = build_pack("p", flag, [PASSAGE], citations={PASSAGE.source: citation})
    passage = pack.by_id()["P1"]
    assert passage.text.startswith(f"{citation}, p. 12: Anaemia is")
    assert passage.where == "WHO haemoglobin guideline, p. 12"  # what a page shows
    sentence = "The WHO guideline says the haemoglobin concentration is lower than normal."
    assert check_sentence(sentence, ["P1"], pack, "en") == []


def test_a_document_is_named_by_title_and_citation() -> None:
    """KDIGO's citations do not name KDIGO and the WHO's title does not name the WHO: a passage
    carries both, so the source check can find either."""
    knowledge = KnowledgeGraph(
        cohort="t",
        inputs={},
        nodes={
            "document:kdigo-2012-anaemia": KnowledgeNode(
                kind="document",
                title="KDIGO Clinical Practice Guideline for Anemia in Chronic Kidney Disease",
                citation="Kidney Int Suppl 2012;2(4):279-335",
            ),
            "document:who-2024-haemoglobin": KnowledgeNode(
                kind="document",
                title="Guideline on haemoglobin cutoffs",
                citation="Geneva, World Health Organization, 2024",
            ),
        },
        links=(),
        unlabelled={},
    )
    named = document_citations(knowledge)
    assert sources_named(named["kdigo-2012-anaemia"]) == {"KDIGO"}
    assert sources_named(named["who-2024-haemoglobin"]) == {"WHO"}
    assert named["kdigo-2012-anaemia"] == (
        "KDIGO Clinical Practice Guideline for Anemia in Chronic Kidney Disease "
        "(Kidney Int Suppl 2012;2(4):279-335)"
    )


def test_retrieval_skips_quoted_passages_and_near_duplicates(tmp_path: Path) -> None:
    table = "Table 3 haemoglobin cutoffs to define anaemia severity in adults men women children"

    def chunk(n: int, text: str) -> Chunk:
        return Chunk(
            id=f"who@v/{n}.1", source="who", version="v", title="WHO", where="p", text=text
        )

    chunks = [
        chunk(1, f"{table} remarks"),
        chunk(2, f"{table} remarks."),  # the executive summary's copy
        chunk(3, "Haemoglobin cutoffs were adjusted for elevation of residence."),
        chunk(4, "Haemoglobin cutoffs in pregnancy, by trimester."),
    ]
    with KnowledgeIndex(tmp_path / "index.sqlite") as index:
        index.replace_chunks(chunks, {"cohort": "test"})
        found = retrieve(index, "haemoglobin cutoffs anaemia", None, "m", sources={"who"}, limit=3)
        assert found[0].id == "who@v/1.1"
        assert {c.id for c in found} == {"who@v/1.1", "who@v/3.1", "who@v/4.1"}
        found = retrieve(
            index, "haemoglobin cutoffs anaemia", None, "m", sources={"who"}, exclude={"who@v/1.1"}
        )
        assert {c.id for c in found} == {"who@v/2.1", "who@v/3.1", "who@v/4.1"}


def test_the_query_leaves_out_dates_and_numbers() -> None:
    query = query_for(FLAGS["anaemia-criteria-no-diagnosis"])
    assert "2023" not in query
    assert "121" not in query
    assert query.startswith("Haemoglobin below the WHO cutoff")


@pytest.mark.parametrize(("audience", "language"), VARIANTS)
def test_prompts_name_audience_and_language(audience: str, language: str) -> None:
    prompt = system_prompt(audience, language)  # type: ignore[arg-type]
    assert ("Italian" in prompt) == (language == "it")
    assert ("no medical training" in prompt) == (audience == "personal")
    assert "Never tell anyone to start, stop or change a medicine" in prompt


@pytest.mark.parametrize("rule", sorted(FLAGS))
@pytest.mark.parametrize("language", ["en", "it"])
def test_every_template_passes_the_checks(rule: str, language: str) -> None:
    """A template states nothing the verifier would refuse; only the rule's own English text,
    quoted in the Italian template, is in another language."""
    pack = pack_for(rule)
    for sentence in template(FLAGS[rule], pack, language):  # type: ignore[arg-type]
        problems = check_sentence(sentence.text, sentence.cites, pack, language)  # type: ignore[arg-type]
        if "in inglese" in sentence.text:
            problems = [p for p in problems if p != "wrong language"]
        # The model may not suggest a doctor, because code does: here, in the template.
        problems = [p for p in problems if p != "doctor sentence (code adds it)"]
        assert problems == [], (sentence.text, problems)


def test_there_is_an_italian_template_for_every_flag_rule() -> None:
    assert set(FLAGS) <= set(TITLE_IT) == set(WHY_IT)
    assert all(SOURCES[s] for f in FLAGS.values() for s in f.sources)


def test_model_text_is_used_when_enough_of_it_passes(tmp_path: Path) -> None:
    pack = pack_for("anaemia-criteria-no-diagnosis")
    reply = {
        "sentences": [
            {"text": "The most recent haemoglobin was 121.13 g/L on 2023-08-09.", "cites": ["E1"]},
            {"text": "It is below the WHO 2024 cutoff of 130 g/L.", "cites": ["F"]},
            {"text": "You have anaemia.", "cites": ["F"]},
            {"text": "It was 118 g/L last year.", "cites": ["E1"]},
            {"text": "A doctor can say what this means.", "cites": ["F"]},
        ]
    }
    seen: list[dict[str, Any]] = []
    result = explain(
        FLAGS["anaemia-criteria-no-diagnosis"],
        pack,
        "personal",
        "en",
        client(reply, seen, tmp_path),
        "d",
    )
    assert result.origin == "model"
    assert [s.text for s in result.sentences] == [
        "The most recent haemoglobin was 121.13 g/L on 2023-08-09.",
        "It is below the WHO 2024 cutoff of 130 g/L.",
        "This may be worth discussing with a doctor.",  # the flag's own, added by code
    ]
    assert [s.by_code for s in result.sentences] == [False, False, True]
    assert [d.reasons for d in result.dropped] == [
        ("diagnostic wording",),
        ("number not in cited items",),
        ("doctor sentence (code adds it)",),
    ]
    assert (result.model_digest, result.output_tokens) == ("d", 120)
    (request,) = seen
    assert request["options"]["temperature"] == 0
    assert request["format"]["properties"]["sentences"]["maxItems"] == 6


def test_the_template_is_used_when_too_little_passes(tmp_path: Path) -> None:
    pack = pack_for("anaemia-criteria-no-diagnosis")
    reply = {
        "sentences": [
            {"text": "Lei ha l'anemia.", "cites": ["F"]},
            {"text": "Il valore è 12,1 g/dL.", "cites": ["E1"]},
            {"text": "L'ultima emoglobina è del 9 agosto 2023.", "cites": ["E1"]},
        ]
    }
    result = explain(
        FLAGS["anaemia-criteria-no-diagnosis"],
        pack,
        "personal",
        "it",
        client(reply, [], tmp_path),
        "d",
    )
    assert result.origin == "template"
    assert result.fallback == "1 sentence(s) passed the checks; 2 needed"
    assert result.sentences[0].text.startswith("Emoglobina sotto la soglia OMS")
    assert len(result.dropped) == 2
    assert [s.text for s in result.unused] == ["L'ultima emoglobina è del 9 agosto 2023."]


CRITERIA_CLOSING = "This may be worth discussing with a doctor."
TEST_CLOSING = "A doctor can say whether this test is due."
PRESCRIPTION_CLOSING = "A doctor can say what this means for the prescription."


@pytest.mark.parametrize(
    ("ending", "english", "italian"),
    [
        (CRITERIA_CLOSING, CRITERIA_CLOSING, "Può valere la pena parlarne con un medico."),
        (TEST_CLOSING, TEST_CLOSING, "Un medico può dire se è il momento di fare questo test."),
        (
            PRESCRIPTION_CLOSING,
            PRESCRIPTION_CLOSING,
            "Un medico può dire che cosa significa per la prescrizione.",
        ),
        ("No closing.", CRITERIA_CLOSING, "Può valere la pena parlarne con un medico."),
    ],
)
def test_every_explanation_ends_with_the_flags_own_closing(
    ending: str, english: str, italian: str
) -> None:
    flag = FLAGS["ckd-gfr-follow-up"].model_copy(update={"statement": f"Some text. {ending}"})
    sentence = closing(flag, "en")
    assert (sentence.text, sentence.cites, sentence.by_code) == (english, ("F",), True)
    assert closing(flag, "it").text == italian
    assert template(flag, build_pack("p", flag, []), "it")[-1].text == italian


def test_the_prompt_leaves_the_closing_to_code_and_passages_as_background() -> None:
    prompt = system_prompt("personal", "it")
    assert "End with" not in prompt
    assert "Passages (P items) are background" in prompt
    assert "Never write an ID in the text" in prompt
    assert "OMS" not in prompt  # v2 asked for it, and KDIGO's criteria became OMS's


def test_the_items_end_with_the_ids_that_may_be_cited() -> None:
    """A follow-up flag has no E items: listing the IDs keeps the model from citing "E"."""
    prompt = user_prompt(pack_for("ckd-gfr-follow-up"))
    assert prompt.endswith("IDs you may cite: F, S1.")


def test_kept_text_must_cite_the_flag_or_its_evidence() -> None:
    pack = pack_for("anaemia-criteria-no-diagnosis")
    raw = [
        ("Anaemia means the haemoglobin is lower than normal.", ("P1",)),
        ("Only the most recent haemoglobin is assessed.", ("L1",)),
    ]
    kept, _, fallback = review(raw, pack, "en")
    assert len(kept) == 2
    assert fallback == "no kept sentence cites the flag or its evidence"


def test_a_failed_model_call_falls_back_to_the_template(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "out of memory"})

    settings = Settings(_env_file=None, data_dir=tmp_path)
    broken = OllamaClient(settings, transport=httpx.MockTransport(handler))
    pack = pack_for("ckd-gfr-follow-up")
    result = explain(FLAGS["ckd-gfr-follow-up"], pack, "clinician", "en", broken, None)
    assert result.origin == "template"
    assert result.fallback is not None
    assert result.fallback.startswith("model call failed")
    assert result.sentences[0].cites == ("F",)


def test_a_page_gets_the_variants_and_items_but_never_a_passages_text(tmp_path: Path) -> None:
    from medgraph.agent.explain import page_payload

    flag = FLAGS["anaemia-criteria-no-diagnosis"]
    pack = pack_for("anaemia-criteria-no-diagnosis")
    reply = {
        "sentences": [
            {"text": "The most recent haemoglobin was 121.13 g/L.", "cites": ["E1"]},
            {"text": "Anaemia means a lower haemoglobin than normal.", "cites": ["P1"]},
        ]
    }
    result = explain(flag, pack, "clinician", "en", client(reply, [], tmp_path), "d")
    record = {
        "pack": pack.model_dump(mode="json"),
        "explanations": {"clinician-en": result.model_dump(mode="json")},
        "note_check": None,
    }
    payload = page_payload(record)
    assert payload["items"]["P1"] == {
        "kind": "passage",
        "text": None,
        "node": None,
        "ref": PASSAGE.id,
        "where": "WHO haemoglobin guideline, p. 12",
    }
    assert payload["items"]["E1"]["node"] == "lab_result:o1"
    assert payload["variants"]["clinician-en"]["origin"] == "model"
    assert payload["note_checked"] is False
    assert PASSAGE.text not in json.dumps(payload)
