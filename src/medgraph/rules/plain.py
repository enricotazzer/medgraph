"""Plain-language texts of the flags and notes, in English and Italian, for the personal view.

Fixed texts written for each rule, never by a model. Each has:
- a title;
- what the record shows, filled in from the flag's own evidence and the rule's facts
  (values, dates, intervals, a recorded stage), never from numbers typed here;
- why it may matter: a paraphrase of one cited source, whose quote or citation contains every
  number it uses (a test checks this);
- the limit of the check a reader most needs to know, and the flag's closing doctor sentence.

The texts never say that a person has a disease. They say what the record shows, that results
meet a guideline's criteria, or that a test is not recorded. The Italian is impersonal ("nel
record"), which avoids choosing between "tu" and "Lei". Record text (medicine names, evidence
labels) stays as recorded, in English. These texts are the assistant's writing and need the
user's review, the Italian above all.
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import asdict, dataclass
from functools import partial
from typing import Any, Literal

from medgraph.rules.ckd import PERSISTENCE_DAYS
from medgraph.rules.flags import DOCTOR as DISCUSS
from medgraph.rules.medications import DOCTOR as PRESCRIPTION
from medgraph.rules.medications import (
    METFORMIN_ASSESS_BELOW,
    METFORMIN_CONTRAINDICATED_BELOW,
    RAS_INHIBITORS,
)
from medgraph.rules.model import Assessment, Evidence, Flag, Note, PatientRules

Language = Literal["en", "it"]
LANGUAGES: tuple[Language, ...] = ("en", "it")
TEST_DUE = "A doctor can say whether this test is due."
# Each flag ends with one of these sentences; the Italian is used wherever the flag is shown.
CLOSING_IT = {
    DISCUSS: "Può valere la pena parlarne con un medico.",
    TEST_DUE: "Un medico può dire se è il momento di fare questo test.",
    PRESCRIPTION: "Un medico può dire che cosa significa per la prescrizione.",
}
# How a chart names the source of a threshold line.
SOURCE_NAMES: dict[str, dict[Language, str]] = {
    "kdigo-2024-criteria": {"en": "KDIGO 2024", "it": "KDIGO 2024"},
    "who-2024-cutoffs": {"en": "WHO 2024", "it": "OMS 2024"},
    "label-metformin-egfr-30": {"en": "metformin label", "it": "scheda della metformina"},
    "label-metformin-egfr-45": {"en": "metformin label", "it": "scheda della metformina"},
}
_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


@dataclass(frozen=True)
class Text:
    title: str
    shows: str  # what the record shows
    why: str  # paraphrase of ``source``
    source: str  # rules.sources ID
    limit: str
    closing: str


Texts = dict[Language, Text]


def show_date(day: dt.date, language: Language) -> str:
    """ "30 December 2024", or "30/12/2024" in Italian."""
    if language == "en":
        return f"{day.day} {_MONTHS[day.month - 1]} {day.year}"
    return day.strftime("%d/%m/%Y")


def _closing(closing: str, language: Language) -> str:
    return closing if language == "en" else CLOSING_IT[closing]


def _labelled(evidence: tuple[Evidence, ...], prefix: str) -> Evidence | None:
    return next((e for e in evidence if e.label.startswith(prefix)), None)


# --- criteria met, no diagnosis -----------------------------------------------------------


def _anaemia(flag: Flag, a: Assessment | None) -> Texts:
    e = flag.evidence[0]
    cutoff = a.thresholds[0].shown if a and a.thresholds else None
    at = f" ({cutoff})" if cutoff else ""
    return {
        "en": Text(
            title="Latest haemoglobin below the WHO cutoff",
            shows=f"Your most recent haemoglobin, {e.value} on {show_date(e.date, 'en')}, is "
            f"below the WHO cutoff used for your age and recorded sex{at}. Your record has no "
            "anaemia diagnosis for this period.",
            why="The WHO 2024 guideline sets haemoglobin cutoffs by age, sex and pregnancy, and "
            "uses them to define anaemia.",
            source="who-2024-cutoffs",
            limit="Only the most recent value is assessed, and the cutoff is not adjusted for "
            "the altitude where you live, which is not recorded.",
            closing=DISCUSS,
        ),
        "it": Text(
            title="Ultima emoglobina sotto la soglia OMS",
            shows=f"L'emoglobina più recente, {e.value} il {show_date(e.date, 'it')}, è sotto "
            f"la soglia OMS usata per l'età e il sesso registrati{at}. Nel record non c'è una "
            "diagnosi di anemia per questo periodo.",
            why="Le linee guida OMS 2024 fissano soglie di emoglobina per età, sesso e "
            "gravidanza, e le usano per definire l'anemia.",
            source="who-2024-cutoffs",
            limit="Si valuta solo il valore più recente, e la soglia non è corretta per "
            "l'altitudine di residenza, che non è registrata.",
            closing=CLOSING_IT[DISCUSS],
        ),
    }


def _ckd(flag: Flag, a: Assessment | None) -> Texts:
    return {
        "en": Text(
            title="Kidney results meet the criteria for chronic kidney disease",
            shows=f"Kidney results in your record, at least {PERSISTENCE_DAYS} days apart, meet "
            "the KDIGO 2024 criteria for chronic kidney disease. Your record has no kidney "
            "diagnosis for this period.",
            why="KDIGO 2024 counts an eGFR below 60, or a urine albumin-to-creatinine ratio of "
            "30 mg/g or more, when it lasts at least 3 months; a single abnormal result is not "
            "enough, as it can come from a short-term kidney problem.",
            source="kdigo-2024-criteria",
            limit="Only eGFR and urine albumin are assessed, and a short-term kidney problem "
            "cannot be ruled out from the record.",
            closing=DISCUSS,
        ),
        "it": Text(
            title="I valori renali soddisfano i criteri della malattia renale cronica",
            shows=f"Valori renali del record, a distanza di almeno {PERSISTENCE_DAYS} giorni, "
            "soddisfano i criteri KDIGO 2024 della malattia renale cronica. Nel record non c'è "
            "una diagnosi renale per questo periodo.",
            why="KDIGO 2024 considera un eGFR sotto 60, o un rapporto albumina/creatinina nelle "
            "urine di 30 mg/g o più, quando dura almeno 3 mesi; un solo valore anomalo non "
            "basta, perché può dipendere da un problema renale di breve durata.",
            source="kdigo-2024-criteria",
            limit="Si valutano solo l'eGFR e l'albumina nelle urine, e dal record non si può "
            "escludere un problema renale di breve durata.",
            closing=CLOSING_IT[DISCUSS],
        ),
    }


# --- follow-up tests ----------------------------------------------------------------------

_ELSEWHERE = {
    "en": "A test done elsewhere and not in your record would not be seen.",
    "it": "Un test fatto altrove e non presente nel record non risulta.",
}
# rule: (title noun, short noun, Italian title noun, Italian short noun, source, why EN, IT)
_FOLLOW_UP: dict[str, tuple[str, str, str, str, str, str, str]] = {
    "ckd-gfr-follow-up": (
        "kidney function (GFR)",
        "GFR",
        "della funzione renale (GFR)",
        "del GFR",
        "kdigo-2024-monitoring",
        "KDIGO 2024 advises assessing GFR at least once a year in people with chronic kidney "
        "disease.",
        "KDIGO 2024 indica di valutare il GFR almeno una volta all'anno nelle persone con "
        "malattia renale cronica.",
    ),
    "ckd-albuminuria-follow-up": (
        "urine albumin",
        "urine albumin",
        "dell'albumina nelle urine",
        "dell'albumina nelle urine",
        "kdigo-2024-monitoring",
        "KDIGO 2024 advises assessing urine albumin at least once a year in adults with "
        "chronic kidney disease, and urine albumin or protein in children.",
        "KDIGO 2024 indica di valutare l'albumina nelle urine almeno una volta all'anno negli "
        "adulti con malattia renale cronica, e l'albumina o le proteine nelle urine nei "
        "bambini.",
    ),
    "ckd-haemoglobin-follow-up": (
        "haemoglobin",
        "haemoglobin",
        "dell'emoglobina",
        "dell'emoglobina",
        "kdigo-2012-anaemia-testing",
        "KDIGO 2012 advises measuring haemoglobin at least once a year in chronic kidney "
        "disease stage 3, and at least twice a year in stages 4 and 5 without dialysis.",
        "KDIGO 2012 indica di misurare l'emoglobina almeno una volta all'anno nella malattia "
        "renale cronica di stadio 3, e almeno due volte all'anno negli stadi 4 e 5 senza "
        "dialisi.",
    ),
}


def _follow_up(flag: Flag, a: Assessment | None) -> Texts:
    noun, short, noun_it, short_it, source, why_en, why_it = _FOLLOW_UP[flag.rule]
    if a is None or a.interval_months is None:
        raise ValueError(f"{flag.rule}: the assessment states no interval")
    months = a.interval_months
    last = flag.evidence[0] if flag.evidence else None
    stage = a.recorded_stage
    if stage is not None:
        known_en = f"A diagnosis of chronic kidney disease stage {stage} is recorded"
        known_it = (
            f"Nel record è registrata una diagnosi di malattia renale cronica di stadio {stage}"
        )
    else:
        known_en = "Your kidney results meet the criteria for chronic kidney disease"
        known_it = "I valori renali del record soddisfano i criteri della malattia renale cronica"
    if last is not None:
        found_en = f"The last {short} test in your record is from {show_date(last.date, 'en')}."
        found_it = f"L'ultimo test {short_it} nel record è del {show_date(last.date, 'it')}."
    else:
        found_en = f"Your record has no {short} test."
        found_it = f"Nel record non c'è un test {short_it}."
    return {
        "en": Text(
            title=f"No {noun} test in the last {months} months",
            shows=f"{known_en}. {found_en}",
            why=why_en,
            source=source,
            limit=_ELSEWHERE["en"],
            closing=TEST_DUE,
        ),
        "it": Text(
            title=f"Nessun test {noun_it} negli ultimi {months} mesi",
            shows=f"{known_it}. {found_it}",
            why=why_it,
            source=source,
            limit=_ELSEWHERE["it"],
            closing=CLOSING_IT[TEST_DUE],
        ),
    }


# --- medicines ----------------------------------------------------------------------------


def _metformin(flag: Flag, a: Assessment | None) -> Texts:
    drug = flag.evidence[0]
    egfr = _labelled(flag.evidence, "computed eGFR")
    if egfr is None:
        raise ValueError(f"{flag.rule}: no computed eGFR in the evidence")
    below = flag.rule == "metformin-egfr-below-30"
    low, high = METFORMIN_CONTRAINDICATED_BELOW, METFORMIN_ASSESS_BELOW
    if below:
        title_en = f"Metformin with an eGFR below {low}"
        title_it = f"Metformina con eGFR sotto {low}"
        source = "label-metformin-egfr-30"
        why_en = (
            "The US metformin label lists an eGFR below 30 as a contraindication, and says to "
            "stop it if the eGFR falls below 30."
        )
        why_it = (
            "La scheda tecnica statunitense della metformina indica un eGFR sotto 30 come "
            "controindicazione, e di sospenderla se l'eGFR scende sotto 30."
        )
    else:
        title_en = f"Metformin with an eGFR between {low} and {high - 1}"
        title_it = f"Metformina con eGFR tra {low} e {high - 1}"
        source = "label-metformin-egfr-45"
        why_en = (
            "The US metformin label does not recommend starting it with an eGFR between 30 and "
            "45, and says to weigh its benefits and risks when the eGFR falls below 45."
        )
        why_it = (
            "La scheda tecnica statunitense della metformina sconsiglia di iniziarla con un "
            "eGFR tra 30 e 45, e indica di valutarne benefici e rischi quando l'eGFR scende "
            "sotto 45."
        )
    return {
        "en": Text(
            title=title_en,
            shows=f"Your record shows metformin as an active prescription ({drug.label}), and "
            f"your latest eGFR, computed by medgraph from creatinine, is {egfr.value} "
            f"({show_date(egfr.date, 'en')}).",
            why=why_en,
            source=source,
            limit="The latest eGFR is used, as the label does, so a short-term change is not "
            "ruled out.",
            closing=PRESCRIPTION,
        ),
        "it": Text(
            title=title_it,
            shows=f"Nel record la metformina risulta una prescrizione attiva ({drug.label}), e "
            f"l'ultimo eGFR, calcolato da medgraph dalla creatinina, è {egfr.value} "
            f"({show_date(egfr.date, 'it')}).",
            why=why_it,
            source=source,
            limit="Si usa l'ultimo eGFR, come fa la scheda tecnica, quindi non si esclude una "
            "variazione di breve durata.",
            closing=CLOSING_IT[PRESCRIPTION],
        ),
    }


def _epoetin(flag: Flag, a: Assessment | None) -> Texts:
    drug = flag.evidence[0]
    last = _labelled(flag.evidence, "last haemoglobin test")
    if last is not None:
        found_en = f"The last haemoglobin test in your record is from {show_date(last.date, 'en')}."
        found_it = f"L'ultimo test dell'emoglobina nel record è del {show_date(last.date, 'it')}."
    else:
        found_en = "Your record has no haemoglobin test."
        found_it = "Nel record non c'è un test dell'emoglobina."
    return {
        "en": Text(
            title="Epoetin alfa with no haemoglobin test in the last month",
            shows=f"Your record shows epoetin alfa as an active prescription ({drug.label}). "
            f"{found_en}",
            why="The US Epogen label says to monitor haemoglobin at least weekly after starting "
            "or adjusting the dose until it is stable, then at least monthly.",
            source="label-epogen-haemoglobin",
            limit=_ELSEWHERE["en"],
            closing=TEST_DUE,
        ),
        "it": Text(
            title="Epoetina alfa senza test dell'emoglobina nell'ultimo mese",
            shows=f"Nel record l'epoetina alfa risulta una prescrizione attiva ({drug.label}). "
            f"{found_it}",
            why="La scheda tecnica statunitense di Epogen indica di controllare l'emoglobina "
            "almeno ogni settimana dopo l'inizio o un cambio di dose, finché è stabile, poi "
            "almeno ogni mese.",
            source="label-epogen-haemoglobin",
            limit=_ELSEWHERE["it"],
            closing=CLOSING_IT[TEST_DUE],
        ),
    }


# --- notes --------------------------------------------------------------------------------

_RAS_NAMES = {source: drug.name for drug, source in RAS_INHIBITORS}


def _metformin_no_test(note: Note) -> Texts:
    drug = note.evidence[0]
    return {
        "en": Text(
            title="Metformin, with no kidney function test in the record",
            shows=f"Your record shows metformin as an active prescription ({drug.label}) and no "
            "creatinine or eGFR test.",
            why="The US metformin label says to assess kidney function before starting it and "
            "from time to time after that.",
            source="label-metformin-renal-assessment",
            limit=_ELSEWHERE["en"],
            closing=DISCUSS,
        ),
        "it": Text(
            title="Metformina, senza test della funzione renale nel record",
            shows=f"Nel record la metformina risulta una prescrizione attiva ({drug.label}) e "
            "non c'è un test della creatinina o dell'eGFR.",
            why="La scheda tecnica statunitense della metformina indica di valutare la funzione "
            "renale prima di iniziarla e periodicamente dopo.",
            source="label-metformin-renal-assessment",
            limit=_ELSEWHERE["it"],
            closing=CLOSING_IT[DISCUSS],
        ),
    }


def _ras_nsaid(note: Note) -> Texts:
    source = note.sources[0]
    name = _RAS_NAMES[source]
    ras, nsaid = note.evidence[0], note.evidence[1]
    last = _labelled(note.evidence, "last GFR test")
    if last is not None:
        found_en = f"The latest GFR test in your record is from {show_date(last.date, 'en')}."
        found_it = f"L'ultimo test del GFR nel record è del {show_date(last.date, 'it')}."
    else:
        found_en = "Your record has no GFR test."
        found_it = "Nel record non c'è un test del GFR."
    return {
        "en": Text(
            title=f"{name.capitalize()} together with an anti-inflammatory painkiller (NSAID)",
            shows=f"Your record shows {ras.label} and {nsaid.label} as active prescriptions. "
            f"{found_en}",
            why=f"The US {name} label says that in people who are elderly, low on fluids or with "
            "reduced kidney function, NSAIDs taken with it may worsen kidney function, and to "
            "monitor kidney function from time to time.",
            source=source,
            limit="Both count as active because their requests are active in the record; "
            "whether they are taken together is not recorded.",
            closing=DISCUSS,
        ),
        "it": Text(
            title=f"{name.capitalize()} insieme a un antinfiammatorio (FANS)",
            shows=f"Nel record {ras.label} e {nsaid.label} risultano prescrizioni attive. "
            f"{found_it}",
            why=f"La scheda tecnica statunitense di {name} indica che, nelle persone anziane, "
            "disidratate o con funzione renale ridotta, i FANS presi insieme possono peggiorare "
            "la funzione renale, e di controllarla periodicamente.",
            source=source,
            limit="Entrambi risultano attivi perché le richieste sono attive nel record; il "
            "record non dice se vengono presi insieme.",
            closing=CLOSING_IT[DISCUSS],
        ),
    }


FLAG_TEXTS: dict[str, Callable[[Flag, Assessment | None], Texts]] = {
    "anaemia-criteria-no-diagnosis": _anaemia,
    "ckd-criteria-no-diagnosis": _ckd,
    "ckd-gfr-follow-up": _follow_up,
    "ckd-albuminuria-follow-up": _follow_up,
    "ckd-haemoglobin-follow-up": _follow_up,
    "metformin-egfr-below-30": _metformin,
    "metformin-egfr-30-44": _metformin,
    "epoetin-haemoglobin-monthly": _epoetin,
}
NOTE_TEXTS: dict[str, Callable[[Note], Texts]] = {
    "metformin-no-gfr-test": _metformin_no_test,
    "ras-inhibitor-nsaid": _ras_nsaid,
}
# The assessment a flag was raised from, when its rule name differs.
_ASSESSMENT_OF = {
    "anaemia-criteria-no-diagnosis": "anaemia-criteria",
    "ckd-criteria-no-diagnosis": "ckd-criteria",
    "metformin-egfr-below-30": "metformin-egfr",
    "metformin-egfr-30-44": "metformin-egfr",
}


def flag_texts(rules: PatientRules, flag: Flag) -> Texts:
    rule = _ASSESSMENT_OF.get(flag.rule, flag.rule)
    assessment = next((a for a in rules.assessments if a.rule == rule), None)
    return FLAG_TEXTS[flag.rule](flag, assessment)


def note_texts(note: Note) -> Texts:
    return NOTE_TEXTS[note.rule](note)


def personal_view(rules: PatientRules) -> dict[str, Any]:
    """What the personal view shows of the rule results: the texts of every flag and note, in
    the order of ``rules``, and the threshold lines its charts draw, each naming its source."""
    lines: dict[tuple[str, str, str], dict[str, Any]] = {}
    for a in rules.assessments:
        for t in a.thresholds:
            names = SOURCE_NAMES[t.source]
            lines.setdefault(
                (t.analyte, str(t.value), t.source),
                {
                    "analyte": t.analyte,
                    "value": float(t.value),
                    "unit": t.unit,
                    "shown": t.shown,
                    "source": t.source,
                    "label": {lang: f"{t.shown} · {names[lang]}" for lang in LANGUAGES},
                },
            )
    return {
        "flags": [_shown(f, partial(flag_texts, rules, f)) for f in rules.flags],
        "notes": [_shown(n, partial(note_texts, n)) for n in rules.notes],
        "lines": list(lines.values()),
    }


def _shown(item: Flag | Note, build: Callable[[], Texts]) -> dict[str, Any]:
    """An item's texts by language. One whose texts cannot be built (a rule with none yet, or
    a result without the shape its rule gives) shows its own English text, marked as such, so
    a page never fails on it; the tests check that every real rule has its texts."""
    try:
        texts = build()
    except (KeyError, IndexError, ValueError):
        own = Text(
            title=item.title,
            shows=item.statement,
            why="",
            source=item.sources[0] if item.sources else "",
            limit=" ".join(item.limitations),
            closing="",
        )
        return {"en": asdict(own), "it": asdict(own), "fallback": True}
    return {lang: asdict(text) for lang, text in texts.items()}
