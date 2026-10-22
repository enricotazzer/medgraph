"""Records a check may need and the rules cannot read (rules-v3, ADR 0013).

The rules recognise diagnoses, procedures and medicines only by the codes listed in
``rules/diagnoses.py`` and ``rules/medications.py``, and lab results only by the LOINC codes of
``normalize/analytes.py``. The lists hold dev-1000's own codes. A record coded otherwise is
invisible to the rules:

- a check that flags something missing (no diagnosis, no recent test) would raise a flag the
  record contradicts;
- a check that flags something present (metformin and a low eGFR) would miss it, or take an
  older value for the latest.

So the rules first look for records they may be failing to read. A record dated on or before
the evaluation date (or undated) is unread when its code is not in the lists, and:

- **kidney:** a condition whose name mentions kidney disease, albuminuria or dialysis, or a
  procedure whose name mentions dialysis or a kidney transplant;
- **anaemia:** a condition whose name mentions anaemia;
- **medicines:** a medication request whose name mentions a medicine a rule checks (metformin;
  epoetin; the ACE inhibitors, ARBs and NSAIDs of the RAS-inhibitor note);
- **lab:** a laboratory result coded outside LOINC, or any result coded outside LOINC whose name
  the report name table (``normalize/analyte_names.py``) maps to an analyte in scope;
- any of these kinds with no name at all, only an unlisted code, unless its code system holds
  nothing a check looks for (:data:`OUT_OF_SCOPE`: dental procedures, which Synthea records by
  code only).

A check whose topics have an unread record is not done: it raises no flag or note, and the
result lists it with the records that held it back. Names are matched only to hold a check
back, never to raise one.

What this does not cover:
- **LOINC results:** LOINC codes are exact, so a LOINC result outside the registry is out of
  scope by design, not unread.
- **Lab report rows** the name table does not map: they are confirmed or set aside by a person
  (Phase 5c.2).
- **Pregnancy** under an unlisted code (it lowers the anaemia cutoff), smoking status coded
  outside LOINC, and brand names of the checked medicines.
- **Only the first coding of a record is kept** (``graph/build.py``): a record that codes a
  condition in two systems is read by the first.
"""

import datetime as dt
import re
import unicodedata
from collections.abc import Iterable

from medgraph.graph.schema import PatientGraph, node_data
from medgraph.normalize.analyte_names import analyte_for_name
from medgraph.normalize.labs import LOINC
from medgraph.rules.diagnoses import (
    ANAEMIA_DIAGNOSES,
    DIALYSIS_PROCEDURES,
    KIDNEY_DIAGNOSES,
    parse_code,
)
from medgraph.rules.medications import EPOETIN, METFORMIN, NSAIDS, RAS_INHIBITORS, RXNORM
from medgraph.rules.model import Topic, Unchecked, Unread, UnreadReason

# Names are folded (lower case, accents removed) before matching. Italian forms are included:
# "rene", "renale", "nefropatia", "dialisi", "anemia", "metformina", "naprossene".
KIDNEY_CONDITION = re.compile(
    r"\b(kidney|renal|rene\b|reni\b|nephr|nefr|glomerul|ckd\b|irc\b|dialy|dialis|ur(a)?emi)"
    r"|albuminuri|proteinuri"
)
KIDNEY_PROCEDURE = re.compile(
    r"\b(dialy|dialis|haemodial|hemodial|emodial)"
    r"|\b(transplant|trapiant)\w*\b.*\b(kidney|renal|rene\b|reni\b)"
    r"|\b(kidney|renal|rene\b|reni\b).*\b(transplant|trapiant)"
)
ANAEMIA_CONDITION = re.compile(r"\ban(a)?emi")
MEDICINES: dict[Topic, re.Pattern[str]] = {
    "metformin": re.compile(r"\bmetformin"),
    "epoetin": re.compile(r"\bepoetin"),
    "ras_nsaid": re.compile(
        r"\b(lisinopril|enalapril|ramipril|benazepril|losartan|valsartan|telmisartan|sacubitril"
        r"|ibuprofen|naproxen|naprossen)"
    ),
}
_LISTED_DRUGS = frozenset(
    (RXNORM, code)
    for codes in (
        METFORMIN.codes,
        EPOETIN.codes,
        NSAIDS,
        *(drug.codes for drug, _ in RAS_INHIBITORS),
    )
    for code in codes
)
_LISTED_CONDITIONS = frozenset(KIDNEY_DIAGNOSES) | frozenset(ANAEMIA_DIAGNOSES)
# Code systems that hold nothing a check looks for, by record kind.
OUT_OF_SCOPE: dict[str, frozenset[str]] = {
    "procedure": frozenset({"http://www.ada.org/cdt"}),  # CDT: the ADA's dental procedure codes
}

# The topics each flag and note rule reads. Every rule reads lab results; the medication rules
# read their own medicines.
DEPENDS: dict[str, tuple[Topic, ...]] = {
    "ckd-criteria-no-diagnosis": ("kidney", "lab"),
    "anaemia-criteria-no-diagnosis": ("anaemia", "lab"),
    "ckd-gfr-follow-up": ("kidney", "lab"),
    "ckd-albuminuria-follow-up": ("kidney", "lab"),
    "ckd-haemoglobin-follow-up": ("kidney", "lab"),
    "metformin-egfr-below-30": ("metformin", "lab"),
    "metformin-egfr-30-44": ("metformin", "lab"),
    "metformin-no-gfr-test": ("metformin", "lab"),
    "epoetin-haemoglobin-monthly": ("epoetin", "lab"),
    "ras-inhibitor-nsaid": ("ras_nsaid", "lab"),
}
TOPIC_WORDS: dict[Topic, str] = {
    "kidney": "kidney disease, dialysis or a kidney transplant",
    "anaemia": "anaemia",
    "metformin": "metformin",
    "epoetin": "epoetin",
    "ras_nsaid": "an ACE inhibitor, an ARB or an NSAID",
    "lab": "a test the checks use",
}


def fold(text: str) -> str:
    """Lower case, accents removed."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def why(reason: UnreadReason, topics: Iterable[Topic]) -> str:
    """One English sentence: why a record is unread."""
    if reason == "no_name":
        return "It has no name, only a code that is not in the lists the checks use."
    if reason == "lab_code":
        return (
            "It is a laboratory result coded outside LOINC, the system the checks read lab "
            "results by."
        )
    topics = tuple(topics)
    if topics == ("lab",):
        return (
            "Its name is that of a test the checks use, and it is coded outside LOINC, the "
            "system the checks read lab results by."
        )
    words = " and ".join(TOPIC_WORDS[t] for t in topics)
    return f"Its name mentions {words}, and its code is not in the lists the checks use."


def _no_name(label: str, code: str | None) -> bool:
    value = code.rpartition("|")[2] if code else ""
    return not label.strip() or label.strip() == value


def _topics(kind: str, label: str, code: str | None, category: str | None) -> list[Topic]:
    """The topics a record is unread for (none if it is read)."""
    name = fold(label)
    parsed = parse_code(code)
    if kind == "condition":
        found: list[Topic] = []
        if parsed not in KIDNEY_DIAGNOSES and KIDNEY_CONDITION.search(name):
            found.append("kidney")
        if parsed not in ANAEMIA_DIAGNOSES and ANAEMIA_CONDITION.search(name):
            found.append("anaemia")
        return found
    if kind == "procedure":
        listed = parsed in DIALYSIS_PROCEDURES
        return ["kidney"] if not listed and KIDNEY_PROCEDURE.search(name) else []
    if kind == "medication_request":
        if parsed in _LISTED_DRUGS:
            return []
        return [topic for topic, pattern in MEDICINES.items() if pattern.search(name)]
    if kind == "observation":
        system = parsed[0] if parsed else None
        if system == LOINC:
            return []
        return ["lab"] if category == "laboratory" or analyte_for_name(label) else []
    return []


_ALL_OF: dict[str, list[Topic]] = {
    "condition": ["kidney", "anaemia"],
    "procedure": ["kidney"],
    "medication_request": list(MEDICINES),
    "observation": ["lab"],
}


def _listed(kind: str, code: str | None) -> bool:
    """Whether the lists cover ``code``, or its system holds nothing a check looks for."""
    parsed = parse_code(code)
    if parsed is not None and parsed[0] in OUT_OF_SCOPE.get(kind, ()):
        return True
    if kind == "condition":
        return parsed in _LISTED_CONDITIONS
    if kind == "procedure":
        return parsed in DIALYSIS_PROCEDURES
    if kind == "medication_request":
        return parsed in _LISTED_DRUGS
    return parsed is not None and parsed[0] == LOINC


def find_unread(graph: PatientGraph, as_of: dt.date) -> tuple[Unread, ...]:
    """The records of ``graph``, up to ``as_of``, that a check may need and cannot read."""
    found = []
    for node in graph.nodes:
        d = node_data(graph, node)
        if d.kind not in ("condition", "procedure", "medication_request", "observation"):
            continue
        day = d.start.date if d.start else None
        if day is not None and day > as_of:
            continue
        reason: UnreadReason
        topics = _topics(d.kind, d.label, d.code, d.category)
        if topics:
            reason = "lab_code" if topics == ["lab"] and d.category == "laboratory" else "name"
        elif _no_name(d.label, d.code) and not _listed(d.kind, d.code):
            reason = "no_name"
            topics = _ALL_OF[d.kind]
        else:
            continue
        found.append(
            Unread(
                node=node,
                kind=d.kind,
                label=d.label,
                code=d.code,
                date=day,
                topics=tuple(topics),
                reason=reason,
                why=why(reason, topics),
            )
        )
    return tuple(found)


def unchecked(unread: Iterable[Unread]) -> tuple[Unchecked, ...]:
    """The flag and note rules held back by ``unread``, in :data:`DEPENDS` order."""
    topics = {t for u in unread for t in u.topics}
    return tuple(
        Unchecked(rule=rule, topics=tuple(t for t in needs if t in topics))
        for rule, needs in DEPENDS.items()
        if topics.intersection(needs)
    )
