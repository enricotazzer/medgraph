"""Explanations of a follow-up flag, written by the local model and checked sentence by sentence.

Four variants per flag: for a clinician or for the person whose record it is, in English or
Italian. All four rest on the same evidence pack (:mod:`medgraph.agent.evidence`). The model
returns sentences with the IDs of the items each relies on; the verifier
(:mod:`medgraph.agent.verify`) drops any sentence that fails a check. The model's text is used
only if at least two sentences survive and one of them cites the flag or its evidence;
otherwise the explanation is a fixed template for the rule. Each explanation says which it is.
Either way it ends with the flag's own suggestion of a doctor, added by code: a sentence with
nothing to cite would fail the checks, and the suggestion must not depend on the model.

Pages never call the model: explanations are written once, by ``scripts/explain_flags.py``,
cached by the pack's digest, the model's digest, the prompt version and the variant.
"""

import re
from collections.abc import Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from medgraph.agent.evidence import EvidencePack
from medgraph.agent.llm import LLMError, OllamaClient
from medgraph.agent.verify import Language, check_sentence, strip_refs
from medgraph.rules.flags import DOCTOR
from medgraph.rules.model import Flag
from medgraph.rules.plain import CLOSING_IT

_MONTHS_IN_TITLE = re.compile(r"in the last (\d+) months")

# v2: the closing sentence is added by code; passages are background, never a table to place
# the person in; no general disclaimers; "OMS" in Italian.
# v3: the IDs that may be cited are listed; IDs stay out of the text; a passage only for what it
# says about this flag; a missing test is not a criterion met; v2's "Call WHO OMS" removed (the
# model then called KDIGO's criteria OMS).
PROMPT_VERSION = "explain-v3"
Audience = Literal["clinician", "personal"]
VARIANTS: tuple[tuple[Audience, Language], ...] = (
    ("clinician", "en"),
    ("personal", "en"),
    ("clinician", "it"),
    ("personal", "it"),
)
MAX_SENTENCES = 6
MIN_KEPT = 2

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "maxItems": MAX_SENTENCES,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "cites": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "cites"],
            },
        }
    },
    "required": ["sentences"],
}

SYSTEM = """\
You explain one follow-up flag raised by medgraph, a research prototype that reads a health \
record. A flag says that recorded values meet published criteria, or that a test or a \
prescription may need a look. It is not a diagnosis.

Write for {audience} Write in {language}

Use only the numbered items you are given. Return sentences, each with the IDs of the items it \
relies on in "cites" (for example ["F", "E1"]), chosen from the IDs listed after the items. \
Never write an ID in the text itself: the reader does not see the items.
- Every number and every date in a sentence must appear in an item that sentence cites. Copy \
numbers as they are written there; do not round them or convert units.
- Say what the record shows (item F, and the E items if there are any), why the sources \
consider it relevant (the S items), and what the check cannot see (the L items).
- Item F says which criterion the record meets, and nothing more is known. Never place the \
person's values in a table, category, stage, grade of severity or risk level that F and E do \
not name.
- A missing test is not a criterion met: when F is about a test that may be due, say that the \
record shows no such test in the period, and what the source says about how often to do it.
- Passages (P items) are background. Use one only for what it says about this flag's test, \
value or medicine, naming its source, and leave out the rest.
- Never say or suggest that the person has, or does not have, a disease: say what the record \
shows, or what the criteria are.
- Never tell anyone to start, stop or change a medicine or a dose. You may report what a \
source says, naming the source.
- Do not add anything that is not in the items.
- Do not write a sentence suggesting a doctor, or saying what medgraph or a flag is: both are \
added after your sentences.
- Write at most {max_sentences} sentences."""

AUDIENCE: dict[Audience, dict[Language, str]] = {
    "clinician": {
        "en": "a clinician. Be concise and precise; clinical terms are fine.",
        "it": "a clinician. Be concise and precise; clinical terms are fine.",
    },
    "personal": {
        "en": (
            "the person whose record this is, who has no medical training. Use plain words "
            'and short sentences, explain any technical term, and address them as "you".'
        ),
        "it": (
            "the person whose record this is, who has no medical training. Use plain words "
            "and short sentences, explain any technical term, and address them with the "
            'formal "Lei".'
        ),
    },
}
LANGUAGE: dict[Language, str] = {
    "en": "English.",
    "it": (
        "Italian. Write natural Italian, not a word-for-word translation. Keep drug names, "
        "record labels and units exactly as they appear in the items. Write decimals with a "
        "comma (121,13) and dates as day, month, year (9 agosto 2023)."
    ),
}

# Italian templates (the English template is the flag's own text). They name the source and
# use only numbers in it; a test runs every template through the verifier. A follow-up title
# takes its interval from the flag's: the haemoglobin interval is 12 or 6 months by stage.
# Every explanation ends with the flag's own last sentence; CLOSING_IT has its Italian.
TITLE_IT = {
    "anaemia-criteria-no-diagnosis": (
        "Emoglobina sotto la soglia OMS; nel record non c'è una diagnosi di anemia"
    ),
    "ckd-criteria-no-diagnosis": (
        "I valori renali soddisfano i criteri della malattia renale cronica; nel record non "
        "c'è una diagnosi renale"
    ),
    "ckd-gfr-follow-up": "Nessun test del GFR negli ultimi {months} mesi",
    "ckd-albuminuria-follow-up": "Nessun test dell'albuminuria negli ultimi {months} mesi",
    "ckd-haemoglobin-follow-up": "Nessun test dell'emoglobina negli ultimi {months} mesi",
    "metformin-egfr-30-44": "Metformina attiva; l'ultimo eGFR è tra 30 e 44",
    "metformin-egfr-below-30": "Metformina attiva; l'ultimo eGFR è sotto 30",
    "epoetin-haemoglobin-monthly": (
        "Epoetina alfa attiva; nessun test dell'emoglobina nell'ultimo mese"
    ),
}
WHY_IT = {
    "anaemia-criteria-no-diagnosis": (
        "L'OMS (2024) indica soglie di emoglobina, per età e sesso, sotto le quali si parla di "
        "anemia; il valore nel record è sotto la soglia, ma nel record non c'è una diagnosi di "
        "anemia."
    ),
    "ckd-criteria-no-diagnosis": (
        "Le linee guida KDIGO 2024 parlano di malattia renale cronica quando il GFR resta sotto "
        "60 o l'albuminuria resta alta per almeno 3 mesi; i valori nel record soddisfano questo "
        "criterio, ma nel record non c'è una diagnosi renale."
    ),
    "ckd-gfr-follow-up": (
        "Le linee guida KDIGO 2024 indicano di valutare il GFR almeno una volta l'anno nelle "
        "persone con malattia renale cronica."
    ),
    "ckd-albuminuria-follow-up": (
        "Le linee guida KDIGO 2024 indicano di valutare l'albuminuria almeno una volta l'anno "
        "nelle persone con malattia renale cronica."
    ),
    "ckd-haemoglobin-follow-up": (
        "Le linee guida KDIGO 2012 sull'anemia nella malattia renale cronica indicano di "
        "misurare l'emoglobina almeno una volta l'anno nello stadio 3, e più spesso quando c'è "
        "anemia."
    ),
    "metformin-egfr-30-44": (
        "Secondo l'etichetta della metformina, iniziarla con un eGFR tra 30 e 45 "
        "mL/min/1,73 m² non è raccomandato, e quando l'eGFR scende sotto 45 vanno valutati "
        "benefici e rischi del proseguimento."
    ),
    "metformin-egfr-below-30": (
        "Secondo l'etichetta della metformina, il farmaco è controindicato con un eGFR sotto 30 "
        "mL/min/1,73 m² e va sospeso se l'eGFR scende sotto 30."
    ),
    "epoetin-haemoglobin-monthly": (
        "Secondo l'etichetta dell'epoetina alfa, l'emoglobina va controllata almeno ogni "
        "settimana finché è stabile, poi almeno ogni mese."
    ),
}


class Sentence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    cites: tuple[str, ...]
    by_code: bool = False  # the closing sentence, added to the model's text by code


class DroppedSentence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    cites: tuple[str, ...]
    reasons: tuple[str, ...]


class Explanation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    audience: Audience
    language: Language
    origin: Literal["model", "template"]
    sentences: tuple[Sentence, ...]
    dropped: tuple[DroppedSentence, ...] = ()
    unused: tuple[Sentence, ...] = ()  # model sentences that passed, when the template was used
    fallback: str | None = None  # why the template was used
    pack_digest: str
    prompt_version: str = PROMPT_VERSION
    model: str | None = None
    model_digest: str | None = None
    prompt_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


def system_prompt(audience: Audience, language: Language) -> str:
    return SYSTEM.format(
        audience=AUDIENCE[audience][language],
        language=LANGUAGE[language],
        max_sentences=MAX_SENTENCES,
    )


def user_prompt(pack: EvidencePack) -> str:
    items = "\n".join(f"[{item.id}] {item.text}" for item in pack.items)
    return f"Items:\n{items}\n\nIDs you may cite: {', '.join(i.id for i in pack.items)}."


def closing(flag: Flag, language: Language) -> Sentence:
    """The flag's suggestion of a doctor, in the explanation's language."""
    english = next((s for s in CLOSING_IT if flag.statement.endswith(s)), DOCTOR)
    text = english if language == "en" else CLOSING_IT[english]
    return Sentence(text=text, cites=("F",), by_code=True)


def template(flag: Flag, pack: EvidencePack, language: Language) -> tuple[Sentence, ...]:
    """The fixed explanation of a flag: its own text in English; in Italian, a translated title
    and reason, the values, and the rule's text quoted in English."""
    ids = pack.by_id()
    evidence = tuple(i for i in ids if i.startswith("E"))
    limits = tuple(i for i in ids if i.startswith("L"))
    statements = tuple(i for i in ids if i.startswith("S"))
    sentences: list[Sentence] = []
    if language == "en":
        # The flag's own text already ends with its closing sentence.
        sentences.append(Sentence(text=ids["F"].text, cites=("F",)))
        if limits:
            limitations = " ".join(flag.limitations)
            sentences.append(Sentence(text=f"Limits of this check: {limitations}", cites=limits))
        return tuple(sentences)
    title = TITLE_IT.get(flag.rule, flag.title)
    if "{months}" in title:
        interval = _MONTHS_IN_TITLE.search(flag.title)
        if interval is None:
            raise ValueError(f"{flag.rule}: no interval in the title {flag.title!r}")
        title = title.format(months=interval[1])
    sentences.append(Sentence(text=f"{title}.", cites=("F",)))
    if flag.rule in WHY_IT and statements:
        sentences.append(Sentence(text=WHY_IT[flag.rule], cites=statements))
    if evidence:
        values = "; ".join(ids[i].text for i in evidence)
        sentences.append(Sentence(text=f"Nel record: {values}.", cites=evidence))
    sentences.append(
        Sentence(text=f"Testo della regola, in inglese: {flag.statement}", cites=("F",))
    )
    if limits:
        limitations = " ".join(flag.limitations)
        sentences.append(
            Sentence(text=f"Limiti del controllo, in inglese: {limitations}", cites=limits)
        )
    sentences.append(closing(flag, "it"))
    return tuple(sentences)


def _raw_sentences(content: dict[str, Any]) -> list[tuple[str, tuple[str, ...]]]:
    raw = content.get("sentences")
    if not isinstance(raw, list):
        return []
    out = []
    for entry in raw[:MAX_SENTENCES]:
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        cites = entry.get("cites")
        if not isinstance(text, str):
            continue
        ids = tuple(str(c).strip().strip("[]") for c in cites) if isinstance(cites, list) else ()
        out.append((text.strip(), tuple(dict.fromkeys(i for i in ids if i))))
    return out


def review(
    raw: Sequence[tuple[str, tuple[str, ...]]], pack: EvidencePack, language: Language
) -> tuple[list[Sentence], list[DroppedSentence], str | None]:
    """Kept sentences, dropped ones with their reasons, and why the model's text cannot be used
    (None when it can)."""
    kept: list[Sentence] = []
    dropped: list[DroppedSentence] = []
    for text, cites in raw:
        problems = check_sentence(text, cites, pack, language)
        if problems:
            dropped.append(DroppedSentence(text=text, cites=cites, reasons=tuple(problems)))
        else:
            kept.append(Sentence(text=strip_refs(text), cites=cites))
    if not raw:
        return kept, dropped, "no sentences returned"
    if len(kept) < MIN_KEPT:
        return kept, dropped, f"{len(kept)} sentence(s) passed the checks; {MIN_KEPT} needed"
    if not any(c == "F" or c.startswith("E") for s in kept for c in s.cites):
        return kept, dropped, "no kept sentence cites the flag or its evidence"
    return kept, dropped, None


def explain(
    flag: Flag,
    pack: EvidencePack,
    audience: Audience,
    language: Language,
    client: OllamaClient,
    model_digest: str | None,
) -> Explanation:
    """One variant: the model's text if enough of it passes the checks, else the template."""
    base: dict[str, Any] = {
        "audience": audience,
        "language": language,
        "pack_digest": pack.digest(),
        "model": client.model,
        "model_digest": model_digest,
    }
    try:
        response = client.chat_json(
            system_prompt(audience, language), user_prompt(pack), SCHEMA, num_ctx=8192
        )
    except (LLMError, httpx.HTTPError) as exc:
        return Explanation(
            **base,
            origin="template",
            sentences=template(flag, pack, language),
            fallback=f"model call failed: {exc}",
        )
    base |= {
        "model": response.model,
        "prompt_tokens": response.prompt_tokens,
        "output_tokens": response.output_tokens,
        "seconds": response.seconds,
    }
    kept, dropped, fallback = review(_raw_sentences(response.content), pack, language)
    if fallback is None:
        return Explanation(
            **base,
            origin="model",
            sentences=(*kept, closing(flag, language)),
            dropped=tuple(dropped),
        )
    return Explanation(
        **base,
        origin="template",
        sentences=template(flag, pack, language),
        dropped=tuple(dropped),
        unused=tuple(kept),
        fallback=fallback,
    )


def page_payload(record: dict[str, Any]) -> dict[str, Any]:
    """What a patient page shows of a flag's cached explanations and note check
    (``scripts/explain_flags.py``). Passages are named by place and ID, never by their text: the
    pages show checked quotes, not the sources' text."""
    pack = EvidencePack.model_validate(record["pack"])
    items = {
        item.id: {
            "kind": item.kind,
            "text": None if item.kind == "passage" else item.text,
            "node": item.node,
            "ref": item.ref,
            "where": item.where,
        }
        for item in pack.items
    }
    variants = {}
    for key, raw in record.get("explanations", {}).items():
        explanation = Explanation.model_validate(raw)
        variants[key] = {
            "origin": explanation.origin,
            "fallback": explanation.fallback,
            "model": explanation.model,
            "prompt_version": explanation.prompt_version,
            "sentences": [s.model_dump(mode="json") for s in explanation.sentences],
        }
    note = record.get("note_check") or {}
    mention = note.get("mention")
    return {
        "items": items,
        "variants": variants,
        "note": mention,
        "notes_read": len(note.get("notes", [])),
        "note_checked": bool(note),
    }
