"""Checks on a model-written explanation, sentence by sentence. Deterministic: no model.

A sentence is kept only if:

- it cites at least one item of its evidence pack, and only items that exist;
- every number it writes is a number in an item it cites, compared by value ("1,4" in an
  Italian sentence is 1.4; "121.130" equals "121.13"), and every date is a date in an item it
  cites, in any of the formats below;
- it is written in the language asked for;
- it does not state a diagnosis or instruct a change of treatment (the patterns below);
- every stage, KDIGO category, grade of anaemia severity or risk level it names is named by a
  cited item from the rule (the flag, its evidence, limitations or quoted statements). A
  retrieved passage alone never licenses one: placing the person's values in a passage's table
  is medical logic, which the model must not do;
- every source it names (WHO or OMS, KDIGO) is named by an item it cites;
- it does not name an item ID in its words ("come indicato in F"): IDs belong in its citations;
- it does not suggest a doctor: code ends every explanation with the flag's own suggestion.

The checks are the floor, not the ceiling. Numbers written as words ("three months", "tre
mesi") are not checked; a sentence can cite the right items and still misread them; the
wording patterns catch common phrasings in English and Italian, not every one. What each check
catches, and a few things it does not, are listed in the tests.
"""

import datetime as dt
import re
from collections.abc import Callable, Iterable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Literal

from medgraph.agent.evidence import EvidencePack

Language = Literal["en", "it"]

_EN_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
_IT_MONTHS = [
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]
MONTHS = {name: i for names in (_EN_MONTHS, _IT_MONTHS) for i, name in enumerate(names, 1)}
MONTHS.update({name[:3]: i for i, name in enumerate(_EN_MONTHS, 1)})
MONTHS["sept"] = 9
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{4})\b")
_DAY_MONTH_YEAR = re.compile(
    rf"\b(\d{{1,2}})(?:\u00b0|\u00ba|st|nd|rd|th)?\s+({_MONTH})\.?,?\s+(\d{{4}})\b", re.I
)
_MONTH_DAY_YEAR = re.compile(
    rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I
)
_MONTH_YEAR = re.compile(rf"\b({_MONTH})\.?\s+(?:of\s+|del\s+|di\s+)?(\d{{4}})\b", re.I)
# An item ID, possibly with a word before it ("item E1", "fonte L1"). The IDs are
# case-sensitive; the words are not.
_AN_ID = (
    r"(?:(?i:(?:the\s+)?(?:items?|passages?|sources?|limitations?|statements?|evidence|flag|"
    r"guidance\s+in|elementi|elemento|fonti|fonte|documento|limiti|limite))\s+)?[FELSP]\d*"
)
_IDS = rf"{_AN_ID}(?:\s*(?:[,;]|(?i:and|e))\s*{_AN_ID})*"
_ITEM_REF = re.compile(r"\[\s*[A-Z]\d*(?:\s*[,;]\s*[A-Z]\d*)*\s*\]" rf"|\(\s*{_IDS}\s*\)")
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?])")
# A trailing phrase that only points at items ("as stated in S1", "per L1 and S2", "come
# indicato in F"): a reference written in words, set off by a comma or a parenthesis, so
# that a match never starts inside a phrase ("secondo quanto indicato da L3" once left
# "secondo quanto").
_POINTING = (
    r"(?:as\s+)?(?:stated|noted|shown|indicated|described|listed|specified|reported|mentioned|"
    r"cited|explained|given|outlined|detailed|set\s+out)\s+in|according\s+to|per|see"
    r"|(?:come\s+)?(?:indicat|specificat|descritt|riportat|mostrat|avvertit|menzionat|citat|"
    r"segnalat|spiegat)[oaie]\s+(?:in|da)"
    r"|secondo\s+quanto\s+(?:indicato|specificato|riportato)\s+(?:in|da)|secondo"
)
_REF_PHRASE = re.compile(
    r"(?<=[\w)\]])(?:[,;]\s*\(?|\s*\()"
    rf"(?i:\b(?:{_POINTING}))\s+{_IDS}\)?(?=\s*[.;:,]|\s*$)"
)
_NUMBER = re.compile(r"(?<![\w.,])[-\u2212]?\d+(?:[.,]\d+)*")

_NEGATION = re.compile(
    r"\b(?:no|not|never|without|none|nor|non|nessun[oa]?|mai|senza|né)\b", re.IGNORECASE
)
_CONDITIONS_EN = (
    r"(?:chronic kidney disease|ckd|kidney disease|kidney failure|renal (?:disease|failure|"
    r"insufficiency|impairment)|ana?emia|ana?emic)"
)
_CONDITIONS_IT = (
    r"(?:malattia renale cronica|mrc|insufficienza renale|nefropatia|malattia renale|anemia|"
    r"anemic[oa])"
)
DIAGNOSTIC = (
    re.compile(
        r"\b(?:you|the patient|patient|he|she|they)\s+(?:have|has|had|suffers?|is suffering|are "
        rf"suffering)\s+(?:from\s+)?(?:\w+\s+){{0,3}}{_CONDITIONS_EN}\b",
        re.I,
    ),
    re.compile(r"\b(?:is|are|was|were|been|be)\s+diagnosed\s+with\b", re.I),
    re.compile(rf"\byour\s+(?:\w+\s+){{0,2}}{_CONDITIONS_EN}\b", re.I),
    re.compile(
        r"\b(?:this|it|these values|the values|the record)\s+(?:confirms?|proves?|means?)\s+"
        r"(?:that\s+)?(?:you|the patient)\s+(?:have|has|are|is)\b",
        re.I,
    ),
    re.compile(
        r"(?:^|[.;:]\s*|\b(?:lei|il paziente|la paziente|paziente)\s+)(?:ha|hai|presenta|soffre di|"
        r"soffri di|è affett[oa] da|sei affett[oa] da)\s+"
        rf"(?:\w+['\u2019]|\w+\s+){{0,3}}{_CONDITIONS_IT}\b",
        re.I,
    ),
    re.compile(
        r"\b(?:la sua|il suo|la tua|il tuo)\s+"
        rf"(?:\w+['\u2019]|\w+\s+){{0,2}}{_CONDITIONS_IT}\b",
        re.I,
    ),
    re.compile(r"\b(?:le|ti|gli)\s+è\s+stat[oa]\s+diagnosticat[oa]\b", re.I),
    re.compile(r"\b(?:è|sei)\s+(?:anemic[oa]|nefropatic[oa])\b", re.I),
)
_SEVERITY_IT = {"lieve": "mild", "moderata": "moderate", "grave": "severe", "severa": "severe"}
_RISK_IT = {
    "alto": "high",
    "elevato": "high",
    "molto alto": "very high",
    "molto elevato": "very high",
    "moderatamente aumentato": "moderately increased",
}
_NOT_A_CATEGORY = r"(?!\s+(?:diagnos|code|condition|is recorded|was recorded|registrat|codificat))"
# Each pattern names a category in a canonical form, so an Italian sentence and an English item
# can be compared: "stage 3", "stadio 3", "CKD 3" and "G3" are all "G3" (but not "G3a").
CATEGORIES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (re.compile(r"\bG([1-5][ab]?)\b"), lambda m: f"G{m[1]}"),
    (re.compile(r"\b(?:stage|stadio|CKD|MRC)\s+([1-5][ab]?)\b", re.I), lambda m: f"G{m[1]}"),
    (re.compile(r"\bA([1-3])\b"), lambda m: f"A{m[1]}"),
    (
        re.compile(rf"\b(mild|moderate|severe)(?:ly)?\s+{_CONDITIONS_EN}", re.I),
        lambda m: f"severity {m[1].lower()}",
    ),
    (
        re.compile(rf"\b{_CONDITIONS_IT}\s+(lieve|moderata|grave|severa)\b", re.I),
        lambda m: f"severity {_SEVERITY_IT[m[1].lower()]}",
    ),
    (
        re.compile(rf"\b(lieve|moderata|grave|severa)\s+{_CONDITIONS_IT}", re.I),
        lambda m: f"severity {_SEVERITY_IT[m[1].lower()]}",
    ),
    (
        re.compile(
            rf"\b(?:no[- ]|without\s+|non-?)an(?:a)?emi(?:a|c)\b{_NOT_A_CATEGORY}"
            rf"|\b(?:no|nessuna|assenza di|senza)\s+anemia\b{_NOT_A_CATEGORY}",
            re.I,
        ),
        lambda m: "severity none",
    ),
    (
        re.compile(
            r"\b(very high|high|moderately increased)\s+risk\b"
            r"|\brisk\s+(?:is|was|would be)\s+(very high|high|moderately increased)\b",
            re.I,
        ),
        lambda m: f"risk {(m[1] or m[2]).lower()}",
    ),
    (
        re.compile(
            r"\brischio\s+(?:è\s+)?(molto alto|molto elevato|alto|elevato|moderatamente "
            r"aumentato)\b",
            re.I,
        ),
        lambda m: f"risk {_RISK_IT[m[1].lower()]}",
    ),
)
_ACTIONS_EN = r"(?:stop|start|take|discontinue|increase|reduce|lower|raise|switch|avoid|change)"
_ACTIONS_IT = (
    r"(?:smettere|sospendere|interrompere|iniziare|cominciare|prendere|assumere|aumentare|"
    r"ridurre|cambiare|evitare)"
)
_ATTRIBUTION = re.compile(
    r"\b(?:says?|said|states?|recommends?|advises?|according to|per the|indicates?|label|"
    r"guideline|secondo|indica|raccomanda|afferma|dice|riporta|consiglia|etichetta|"
    r"foglietto|linea guida|linee guida)\b",
    re.I,
)
TREATMENT = (
    re.compile(
        r"^\s*(?:please\s+)?(?:stop|start|take|discontinue|switch|avoid|do not take|don't take|"
        r"continue taking)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:you|the patient|patient|they)\s+(?:should|must|need to|needs to|ought to|have to|"
        rf"has to)\s+(?:\w+\s+)?{_ACTIONS_EN}\b",
        re.I,
    ),
    re.compile(
        r"^\s*(?:smetta|smetti|sospenda|sospendi|interrompa|interrompi|inizi|inizia|prenda|prendi|"
        r"non prenda|non prendere|aumenti|aumenta|riduca|riduci|cambi|cambia|eviti|evita)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:deve|dovrebbe|devi|dovresti|bisogna|occorre|è necessario)\s+(?:\w+\s+)?"
        rf"{_ACTIONS_IT}\b",
        re.I,
    ),
)
# A passive instruction ("should be discontinued") is allowed only when the sentence attributes
# it to a source ("the label says ..."); otherwise it reads as medgraph's own instruction.
PASSIVE_TREATMENT = (
    re.compile(
        r"\b(?:should|must)\s+(?:be\s+)?(?:stopped|started|discontinued|increased|reduced|"
        r"switched|changed|avoided)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:deve|dovrebbe|devono|dovrebbero)\s+essere\s+(?:sospes|interrott|iniziat|ridott|"
        r"aumentat|evitat)\w*",
        re.I,
    ),
)
_EN_WORDS = frozenset(
    [
        "the",
        "and",
        "is",
        "are",
        "of",
        "with",
        "to",
        "for",
        "this",
        "that",
        "was",
        "were",
        "not",
        "be",
        "your",
        "you",
        "an",
        "it",
        "its",
        "has",
        "have",
        "from",
        "or",
        "by",
        "on",
        "at",
        "which",
    ]
)
_IT_WORDS = frozenset(
    [
        "il",
        "lo",
        "la",
        "le",
        "gli",
        "di",
        "che",
        "è",
        "e",
        "con",
        "per",
        "non",
        "del",
        "della",
        "dei",
        "delle",
        "nel",
        "nella",
        "un",
        "una",
        "sono",
        "questo",
        "questa",
        "lei",
        "suo",
        "sua",
        "al",
        "alla",
        "da",
        "ha",
        "hanno",
        "come",
        "più",
    ]
)


def strip_refs(text: str) -> str:
    """The sentence without item references the model may have written into it ("[E1]",
    "(F, S1)", ", as stated in S1"), and without the space they leave before punctuation.
    An ID that does work in the sentence ("la fonte L1 spiega") stays, and fails a check."""
    text = _REF_PHRASE.sub("", _ITEM_REF.sub(" ", text))
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", " ".join(text.split()))


def categories_in(text: str) -> set[str]:
    """Stages, KDIGO categories, anaemia severity grades and risk levels a text names."""
    return {name(m) for pattern, name in CATEGORIES for m in pattern.finditer(text)}


def dates_in(
    text: str, language: Language = "en"
) -> tuple[set[tuple[int, int, int]], set[tuple[int, int]], str]:
    """Full dates, month-and-year dates, and the text with every date removed."""
    full: set[tuple[int, int, int]] = set()
    partial: set[tuple[int, int]] = set()

    def add(y: int, m: int, d: int) -> None:
        try:
            dt.date(y, m, d)
        except ValueError:
            return
        full.add((y, m, d))

    def iso(m: re.Match[str]) -> str:
        add(int(m[1]), int(m[2]), int(m[3]))
        return " "

    def numeric(m: re.Match[str]) -> str:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        add(y, b, a)  # day first, as Italian and most of Europe write it
        if language == "en":
            add(y, a, b)  # or month first, US style
        return " "

    def day_month_year(m: re.Match[str]) -> str:
        add(int(m[3]), MONTHS[m[2].lower()], int(m[1]))
        return " "

    def month_day_year(m: re.Match[str]) -> str:
        add(int(m[3]), MONTHS[m[1].lower()], int(m[2]))
        return " "

    def month_year(m: re.Match[str]) -> str:
        partial.add((int(m[2]), MONTHS[m[1].lower()]))
        return " "

    for pattern, replace in (
        (_ISO, iso),
        (_NUMERIC_DATE, numeric),
        (_DAY_MONTH_YEAR, day_month_year),
        (_MONTH_DAY_YEAR, month_day_year),
        (_MONTH_YEAR, month_year),
    ):
        text = pattern.sub(replace, text)
    return full, partial, text


def _values(token: str, language: Language) -> set[Decimal | str]:
    """What a written number may mean: "1,4" is 1.4 in Italian; "1,148" is 1148 in English.
    A dotted code such as "2.1.1" stays a string."""
    token = token.replace("\u2212", "-")
    if token.count(".") + token.count(",") > 1 and not re.fullmatch(
        r"-?\d{1,3}(?:[.,]\d{3})+", token
    ):
        return {token}
    readings = {token.replace(",", ".") if language == "it" else token.replace(",", "")}
    if re.fullmatch(r"-?\d{1,3}(?:[.,]\d{3})+", token):
        readings.add(token.replace(".", "").replace(",", ""))  # a thousands separator
    if language == "it" and "," not in token:
        readings.add(token)
    values: set[Decimal | str] = set()
    for reading in readings:
        try:
            values.add(Decimal(reading).normalize())
        except InvalidOperation:
            values.add(reading)
    return values


def numbers_in(text: str, language: Language = "en") -> list[set[Decimal | str]]:
    return [_values(t, language) for t in _NUMBER.findall(text)]


def _cited_material(
    texts: Iterable[str], extra_dates: Iterable[dt.date]
) -> tuple[set[Decimal | str], set[tuple[int, int, int]]]:
    numbers: set[Decimal | str] = set()
    dates: set[tuple[int, int, int]] = {(d.year, d.month, d.day) for d in extra_dates}
    for text in texts:
        full, _, rest = dates_in(text, "en")
        dates |= full
        for values in numbers_in(rest, "en"):
            numbers |= values
        # A number may also be written as part of a date in a source ("2024"): keep years.
        numbers |= {Decimal(y) for y, _, _ in full}
    return numbers, dates


def wording_problems(text: str) -> list[str]:
    problems = []
    for pattern in DIAGNOSTIC:
        for m in pattern.finditer(text):
            before = " ".join(text[: m.start()].split()[-6:])
            if not _NEGATION.search(before):
                problems.append("diagnostic wording")
                break
        if problems:
            break
    if any(p.search(text) for p in TREATMENT) or (
        any(p.search(text) for p in PASSIVE_TREATMENT) and not _ATTRIBUTION.search(text)
    ):
        problems.append("treatment instruction")
    return problems


def language_problem(text: str, language: Language) -> str | None:
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    english = sum(w in _EN_WORDS for w in words)
    italian = sum(w in _IT_WORDS for w in words)
    if language == "it" and english >= 3 and english > italian:
        return "wrong language"
    if language == "en" and italian >= 3 and italian > english:
        return "wrong language"
    return None


# Sources a sentence may name, each with the ways of writing it: a cited item must name it too.
SOURCE_NAMES = {
    "WHO": re.compile(
        r"\b(?:WHO|OMS|World Health Organi[sz]ation|Organizzazione mondiale della sanità)\b",
        re.I,
    ),
    "KDIGO": re.compile(r"\bKDIGO\b", re.I),
}
_WHO_WORD = re.compile(r"\bwho\b")  # the English pronoun, lower case, is not the WHO
DOCTOR = re.compile(r"\b(?:doctors?|physicians?|medic[oia])\b", re.I)


def sources_named(text: str) -> set[str]:
    named = {name for name, pattern in SOURCE_NAMES.items() if pattern.search(text)}
    if "WHO" in named and not SOURCE_NAMES["WHO"].search(_WHO_WORD.sub(" ", text)):
        named.discard("WHO")
    return named


_POINTS_TO = (
    r"(?:in|see|from|by|item|items|source|da|dal|dalla|nel|nella|vedi|elemento|fonte|documento)"
)


def names_an_item(text: str, ids: Iterable[str]) -> bool:
    """Whether the text names a pack item in its words ("as shown in F", "la fonte L1"), which
    means nothing to a reader. A page reference such as "p. S196" is not an item ID."""
    numbered = [re.escape(i) for i in ids if i != "F"]
    if numbered and re.search(rf"(?<![\w/.\-])(?:{'|'.join(numbered)})(?![\w/\-]|\.\d)", text):
        return True
    return bool(re.search(rf"\b(?i:{_POINTS_TO})\s+F\b", text))


def check_sentence(
    text: str, cites: Sequence[str], pack: EvidencePack, language: Language
) -> list[str]:
    """Why a sentence cannot be kept; an empty list means it passes every check."""
    text = strip_refs(text)
    if not text:
        return ["empty"]
    items = pack.by_id()
    if not cites:
        return ["no citation"]
    unknown = [c for c in cites if c not in items]
    if unknown:
        return [f"unknown citation {', '.join(unknown)}"]
    cited = [items[c] for c in cites]
    numbers, dates = _cited_material(
        (item.text for item in cited), (item.date for item in cited if item.date)
    )
    problems = []
    full, partial, rest = dates_in(text, language)
    if any(d not in dates for d in full) or any(
        not any((y, m) == (d[0], d[1]) for d in dates) for y, m in partial
    ):
        problems.append("date not in cited items")
    if any(not (values & numbers) for values in numbers_in(rest, language)):
        problems.append("number not in cited items")
    named = categories_in(text)
    if named and named - set().union(
        *(categories_in(item.text) for item in cited if item.kind != "passage")
    ):
        problems.append("category not from the rule")
    if sources_named(text) - set().union(*(sources_named(item.text) for item in cited)):
        problems.append("source not in cited items")
    if names_an_item(text, items):
        problems.append("item ID in the text")
    if DOCTOR.search(text):
        problems.append("doctor sentence (code adds it)")
    problems += wording_problems(text)
    if problem := language_problem(text, language):
        problems.append(problem)
    return problems
