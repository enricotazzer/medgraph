"""Note check: does a recent clinical note mention what a flag is about?

The model reads up to three of the patient's notes from the flag's period and returns a short
verbatim quote that mentions the finding, or nothing, and whether the note affirms or negates
it. Code keeps the quote only if it occurs in that note word for word (up to spacing and case)
and contains one of the rule's finding terms. A kept quote annotates the flag; it never removes
or reorders it. Whether the note affirms or negates is the model's reading, shown as such.

What counts as the finding depends on the rule: for "criteria met, no diagnosis" flags, the
condition; for follow-up flags, the test (a test done elsewhere is what the flag cannot see);
for medication flags, kidney function. On Synthea the check can only test the plumbing: the
notes are written from the coded record, so an uncoded finding is never mentioned
(``docs/data/README.md``).
"""

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from medgraph.agent.llm import LLMError, OllamaClient
from medgraph.graph.schema import PatientGraph, node_data
from medgraph.rules.model import Flag

PROMPT_VERSION = "notecheck-v1"
MAX_NOTES = 3
MAX_NOTE_CHARS = 6000
MAX_QUOTE_WORDS = 40
_NOTE_ID = re.compile(r"\s*\[?\s*(N\d+)\b")
Stance = Literal["affirms", "negates", "uncertain"]

_KIDNEY = r"kidney|renal|\bckd\b|nephro|\begfr\b|\bgfr\b|creatinine|rene\b|renale|creatinina"
_HAEMOGLOBIN = r"an(?:a)?emi|h(?:a)?emoglobin|\bhgb\b|\bhb\b|emoglobina|blood count|\bcbc\b"
_ALBUMIN = r"albumin|\bacr\b|proteinuri|urine protein|protein in (?:the )?urine|proteine urinarie"


@dataclass(frozen=True)
class Finding:
    description: str  # what the model is asked to look for, in English
    terms: str  # regular expression a kept quote must match (case-insensitive)


FINDINGS: dict[str, Finding] = {
    "ckd-criteria-no-diagnosis": Finding(
        "chronic kidney disease or reduced kidney function (for example CKD, renal "
        "impairment, a low eGFR)",
        _KIDNEY,
    ),
    "anaemia-criteria-no-diagnosis": Finding("anaemia or a low haemoglobin", _HAEMOGLOBIN),
    "ckd-gfr-follow-up": Finding(
        "a GFR or creatinine test: a result, an order or a plan", r"\begfr\b|\bgfr\b|creatinin"
    ),
    "ckd-albuminuria-follow-up": Finding(
        "an albuminuria test (urine albumin, albumin-creatinine ratio, urine protein): a "
        "result, an order or a plan",
        _ALBUMIN,
    ),
    "ckd-haemoglobin-follow-up": Finding(
        "a haemoglobin test or a blood count: a result, an order or a plan", _HAEMOGLOBIN
    ),
    "metformin-egfr-30-44": Finding(
        "the person's kidney function (eGFR, creatinine, CKD) in relation to metformin", _KIDNEY
    ),
    "metformin-egfr-below-30": Finding(
        "the person's kidney function (eGFR, creatinine, CKD) in relation to metformin", _KIDNEY
    ),
    "epoetin-haemoglobin-monthly": Finding(
        "a haemoglobin test or a blood count: a result, an order or a plan", _HAEMOGLOBIN
    ),
}

SYSTEM = """\
You read clinical notes and say whether any of them mentions one finding. Answer only from \
the notes.
If a note mentions it, copy a short span of that note word for word (at most {max_words} \
words) that shows the mention, give the note's ID, and say whether the note affirms the \
finding, negates it (for example "no history of ..."), or is uncertain. If several notes \
mention it, choose the most recent. If none does, return null for "mention"."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mention": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "note": {"type": "string"},
                        "quote": {"type": "string"},
                        "stance": {"type": "string", "enum": ["affirms", "negates", "uncertain"]},
                    },
                    "required": ["note", "quote", "stance"],
                },
            ]
        }
    },
    "required": ["mention"],
}


class NoteRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # "N1"...
    node: str
    date: dt.date


class NoteMention(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node: str
    date: dt.date
    quote: str
    stance: Stance  # the model's reading


class NoteCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str
    finding: str
    notes: tuple[NoteRef, ...]  # what was read, most recent first
    mention: NoteMention | None = None  # a quote that passed the checks
    rejected: str | None = None  # why a quote the model returned was not kept
    model_quote: str | None = None  # the rejected quote, for review
    prompt_version: str = PROMPT_VERSION
    model: str | None = None
    model_digest: str | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    error: str | None = None


def window_start(flag: Flag, as_of: dt.date) -> dt.date:
    """Notes from the flag's earliest evidence on, or from a year before the evaluation date
    when the flag has none (a test due with no test recorded)."""
    dates = [e.date for e in flag.evidence]
    return min(dates) if dates else as_of - dt.timedelta(days=365)


def candidate_notes(
    graph: PatientGraph, flag: Flag, as_of: dt.date, limit: int = MAX_NOTES
) -> list[tuple[str, dt.date, str]]:
    """``(node, date, text)`` of the most recent notes in the flag's period, newest first."""
    start = window_start(flag, as_of)
    found = []
    for node in graph.nodes:
        data = node_data(graph, node)
        if data.kind != "note" or data.start is None or not data.text:
            continue
        if start <= data.start.date <= as_of:
            found.append((node, data.start.date, data.text))
    found.sort(key=lambda n: (n[1], n[0]), reverse=True)
    return found[:limit]


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def quote_problem(quote: str, note_text: str, terms: str) -> str | None:
    """Why a returned quote cannot be kept; None if it can."""
    if not quote.strip():
        return "empty quote"
    if len(quote.split()) > MAX_QUOTE_WORDS:
        return f"quote longer than {MAX_QUOTE_WORDS} words"
    if _squash(quote) not in _squash(note_text):
        return "quote not in the note word for word"
    if not re.search(terms, quote, re.IGNORECASE):
        return "quote has none of the rule's finding terms"
    return None


def user_prompt(finding: Finding, notes: list[tuple[str, dt.date, str]]) -> str:
    parts = [f"Finding: {finding.description}.", "", "Notes, most recent first:"]
    for i, (_, date, text) in enumerate(notes, 1):
        parts.append(f"[N{i}] {date.isoformat()}\n{text[:MAX_NOTE_CHARS]}")
    return "\n".join(parts)


def check_notes(
    graph: PatientGraph,
    flag: Flag,
    as_of: dt.date,
    client: OllamaClient,
    model_digest: str | None,
) -> NoteCheck:
    finding = FINDINGS.get(flag.rule)
    notes = candidate_notes(graph, flag, as_of)
    refs = tuple(NoteRef(id=f"N{i}", node=n, date=d) for i, (n, d, _) in enumerate(notes, 1))
    base: dict[str, Any] = {
        "rule": flag.rule,
        "finding": finding.description if finding else "",
        "notes": refs,
        "model": client.model,
        "model_digest": model_digest,
    }
    if finding is None or not notes:
        return NoteCheck(**base)
    try:
        response = client.chat_json(
            SYSTEM.format(max_words=MAX_QUOTE_WORDS),
            user_prompt(finding, notes),
            SCHEMA,
            num_ctx=8192,
        )
    except (LLMError, httpx.HTTPError) as exc:
        return NoteCheck(**base, error=f"model call failed: {exc}")
    base |= {
        "model": response.model,
        "prompt_tokens": response.prompt_tokens,
        "output_tokens": response.output_tokens,
        "seconds": response.seconds,
    }
    mention = response.content.get("mention")
    if not isinstance(mention, dict):
        return NoteCheck(**base)
    quote = str(mention.get("quote", ""))
    stance = mention.get("stance")
    by_id = {ref.id: (ref, text) for ref, (_, _, text) in zip(refs, notes, strict=True)}
    # The model has returned a note's whole header and text as its ID: read the leading ID only.
    found = _NOTE_ID.match(str(mention.get("note", "")))
    note_id = found[1] if found else ""
    # Reasons are fixed strings: the model's own text can hold a note, and reasons reach reports.
    if note_id not in by_id:
        return NoteCheck(**base, rejected="unknown note ID", model_quote=quote)
    if stance not in ("affirms", "negates", "uncertain"):
        return NoteCheck(**base, rejected="unknown stance", model_quote=quote)
    ref, text = by_id[note_id]
    problem = quote_problem(quote, text[:MAX_NOTE_CHARS], finding.terms)
    if problem:
        return NoteCheck(**base, rejected=problem, model_quote=quote)
    return NoteCheck(
        **base,
        mention=NoteMention(
            node=ref.node, date=ref.date, quote=" ".join(quote.split()), stance=stance
        ),
    )
