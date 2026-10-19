"""What the explanation verifier keeps and drops: hand-written good and bad sentences."""

import datetime as dt

import pytest

from medgraph.agent.evidence import EvidencePack, PackItem
from medgraph.agent.verify import (
    Language,
    categories_in,
    check_sentence,
    dates_in,
    names_an_item,
    strip_refs,
    wording_problems,
)

PACK = EvidencePack(
    patient="p",
    rule="anaemia-criteria-no-diagnosis",
    flag_hash="h",
    query="q",
    searched=(),
    items=(
        PackItem(
            id="F",
            kind="flag",
            text=(
                "Haemoglobin below the WHO cutoff; no anaemia diagnosis is recorded. Most recent "
                "haemoglobin 121.13 g/L on 2023-08-09 is below the WHO 2024 cutoff of 130 g/L "
                "for men 15-65 years. This may be worth discussing with a doctor."
            ),
        ),
        PackItem(
            id="E1",
            kind="evidence",
            text="2023-08-09: Hemoglobin [Mass/volume] in Blood: 121.13 g/L (most recent)",
            date=dt.date(2023, 8, 9),
            node="lab_result:o1",
        ),
        PackItem(id="L1", kind="limitation", text="Only the most recent haemoglobin is assessed."),
        PackItem(
            id="S1",
            kind="statement",
            text='KDIGO 2024 ..., Practice Point 2.1.1, p. 688: "Assess GFR at least annually."',
            ref="kdigo-2024-monitoring",
        ),
        PackItem(
            id="S2",
            kind="statement",
            text=(
                'KDIGO 2012 ..., Recommendation 1.1.1: "For CKD patients without anemia [...], '
                'measure Hb concentration [...] at least annually in patients with CKD 3"'
            ),
            ref="kdigo-2012-anaemia-testing",
        ),
        PackItem(
            id="P1",
            kind="passage",
            text=(
                "WHO guideline, p. xii: Table 3. Haemoglobin cutoffs to define anaemia severity "
                "Adults, 15-65 years, men: no anaemia >=130; mild anaemia 110-129; moderate "
                "anaemia 80-109; severe anaemia <80. Risk is very high below 70."
            ),
            ref="who@v/12.1",
        ),
    ),
)
CKD_PACK = EvidencePack(
    patient="p",
    rule="ckd-criteria-no-diagnosis",
    flag_hash="h",
    query="q",
    searched=(),
    items=(
        PackItem(
            id="F",
            kind="flag",
            text=(
                "Kidney values meet the CKD criteria. eGFR below 60 in 3 consecutive values "
                "(latest category G3a)."
            ),
        ),
        PackItem(
            id="P1",
            kind="passage",
            text="KDIGO heat map: G3a A1 moderately increased risk; G3a A2 high risk.",
            ref="kdigo@v/3.1",
        ),
    ),
)


def problems(text: str, cites: list[str], language: Language = "en") -> list[str]:
    return check_sentence(text, cites, PACK, language)


@pytest.mark.parametrize(
    ("text", "cites", "language"),
    [
        ("The most recent haemoglobin was 121.13 g/L on 2023-08-09.", ["E1"], "en"),
        ("The latest haemoglobin, 121.13 g/L on 9 August 2023, is below 130 g/L.", ["F"], "en"),
        ("On August 9, 2023 the haemoglobin was 121.13 g/L.", ["E1"], "en"),
        ("No anaemia diagnosis is recorded for this period.", ["F"], "en"),
        ("Secondo l'OMS la soglia per gli uomini è 130 g/L.", ["F"], "it"),
        ("Only the most recent value, for people who have several, is assessed.", ["L1"], "en"),
        ("The record does not show that you have anaemia.", ["F"], "en"),
        ("KDIGO 2024 Practice Point 2.1.1 says to assess GFR at least annually.", ["S1"], "en"),
        ("L'emoglobina più recente è 121,13 g/L, del 9 agosto 2023.", ["E1"], "it"),
        ("Il valore del 09/08/2023 è sotto la soglia di 130 g/L.", ["F"], "it"),
        ("Nel record non è registrata una diagnosi di anemia.", ["F"], "it"),
        ("Valutato solo il valore più recente [L1].", ["L1"], "it"),
        ("In agosto 2023 l'emoglobina era sotto la soglia.", ["E1"], "it"),
        ("The WHO guideline grades severity, from mild to severe.", ["P1"], "en"),
        ("KDIGO 2012 says to measure Hb at least annually in CKD stage 3.", ["S2"], "en"),
        (
            "Secondo KDIGO 2012, senza anemia l'emoglobina va misurata almeno una volta l'anno "
            "nello stadio 3.",
            ["S2"],
            "it",
        ),
    ],
)
def test_faithful_sentences_are_kept(text: str, cites: list[str], language: Language) -> None:
    assert problems(text, cites, language) == []


@pytest.mark.parametrize(
    ("text", "cites", "language", "reason"),
    [
        ("The haemoglobin was low.", [], "en", "no citation"),
        ("The haemoglobin was low.", ["E7"], "en", "unknown citation E7"),
        ("The most recent haemoglobin was 12.1 g/dL.", ["E1"], "en", "number not in cited items"),
        ("The most recent haemoglobin was 121 g/L.", ["E1"], "en", "number not in cited items"),
        ("The cutoff for men is 130 g/L.", ["E1"], "en", "number not in cited items"),
        ("It was measured on 2023-09-08.", ["E1"], "en", "date not in cited items"),
        ("It was measured on 8 September 2023.", ["E1"], "en", "date not in cited items"),
        ("It was measured in July 2023.", ["E1"], "en", "date not in cited items"),
        ("L'emoglobina era 121,3 g/L.", ["E1"], "it", "number not in cited items"),
        ("Il prelievo è del 08/09/2023.", ["E1"], "it", "date not in cited items"),
        ("You have anaemia.", ["F"], "en", "diagnostic wording"),
        ("The patient has mild chronic kidney disease.", ["F"], "en", "diagnostic wording"),
        ("Your anaemia should be checked.", ["F"], "en", "diagnostic wording"),
        ("He was diagnosed with anaemia.", ["F"], "en", "diagnostic wording"),
        ("These values confirm that you have a problem.", ["F"], "en", "diagnostic wording"),
        ("Lei ha una lieve anemia.", ["F"], "it", "diagnostic wording"),
        ("Lei ha l'anemia.", ["F"], "it", "diagnostic wording"),
        ("Ha un'anemia lieve.", ["F"], "it", "diagnostic wording"),
        ("Ha la malattia renale cronica.", ["F"], "it", "diagnostic wording"),
        ("Le è stata diagnosticata l'anemia.", ["F"], "it", "diagnostic wording"),
        ("Stop taking iron.", ["F"], "en", "treatment instruction"),
        ("You should start iron tablets.", ["F"], "en", "treatment instruction"),
        ("Iron should be started.", ["F"], "en", "treatment instruction"),
        ("Sospenda il farmaco.", ["F"], "it", "treatment instruction"),
        ("Dovrebbe iniziare una terapia con ferro.", ["F"], "it", "treatment instruction"),
        ("The haemoglobin is below the cutoff for this person.", ["F"], "it", "wrong language"),
        ("L'emoglobina è sotto la soglia per questa persona.", ["F"], "en", "wrong language"),
        # A passage's table applied to the person's value (seen in the first run): the rule
        # checks one cutoff and grades nothing.
        (
            "This value is in the range of mild anaemia for men.",
            ["E1", "P1"],
            "en",
            "category not from the rule",
        ),
        (
            "Secondo l'OMS il valore rientra nella fascia di anemia lieve.",
            ["P1"],
            "it",
            "category not from the rule",
        ),
        (
            "Questa concentrazione rientra nella categoria 'no anemia'.",
            ["P1"],
            "it",
            "category not from the rule",
        ),
        ("The person is non-anaemic by this table.", ["P1"], "en", "category not from the rule"),
        ("The risk is very high.", ["P1"], "en", "category not from the rule"),
        ("This corresponds to CKD stage 3.", ["E1"], "en", "category not from the rule"),
        # Seen in the second run: KDIGO's criteria attributed to the WHO, a doctor suggested
        # twice, and item IDs written into the prose.
        (
            "Secondo l'OMS il GFR va valutato almeno una volta l'anno.",
            ["S1"],
            "it",
            "source not in cited items",
        ),
        ("KDIGO sets the cutoff of 130 g/L for men.", ["F"], "en", "source not in cited items"),
        ("A doctor can say what this means.", ["F"], "en", "doctor sentence (code adds it)"),
        ("Può parlarne con il suo medico.", ["F"], "it", "doctor sentence (code adds it)"),
        ("Come indicato in F, nel record non c'è il test.", ["F"], "it", "item ID in the text"),
        (
            "La fonte L1 spiega che un test altrove non si vede.",
            ["L1"],
            "it",
            "item ID in the text",
        ),
        (
            "The guideline (see S1) says to assess GFR at least annually.",
            ["S1"],
            "en",
            "item ID in the text",
        ),
    ],
)
def test_unfaithful_sentences_are_dropped(
    text: str, cites: list[str], language: Language, reason: str
) -> None:
    assert reason in problems(text, cites, language)


def test_a_source_reported_as_saying_so_is_not_an_instruction() -> None:
    assert wording_problems("The label says metformin should be discontinued below 30.") == []
    assert wording_problems("Secondo l'etichetta, la metformina deve essere sospesa.") == []
    assert wording_problems("Metformin should be discontinued.") == ["treatment instruction"]


def test_ordinary_words_are_not_mistaken_for_instructions() -> None:
    for text in (
        "Lower eGFR values mean the kidneys filter less.",
        "Reduced kidney function was not coded.",
        "A doctor can discuss whether a test is due.",
        "Il record presenta due valori compatibili con l'anemia.",
    ):
        assert wording_problems(text) == [], text


def test_dates_are_read_in_every_supported_form() -> None:
    full, partial, rest = dates_in("2023-08-09, 9 August 2023, August 9, 2023 and March 2024")
    assert full == {(2023, 8, 9)}
    assert partial == {(2024, 3)}
    assert "2023" not in rest
    us_or_eu, _, _ = dates_in("03/04/2025", "en")
    assert us_or_eu == {(2025, 4, 3), (2025, 3, 4)}  # English text: either order
    eu, _, _ = dates_in("03/04/2025", "it")
    assert eu == {(2025, 4, 3)}  # Italian: day first


def test_item_references_in_the_text_are_not_numbers() -> None:
    assert strip_refs("Below the cutoff [E1, F].") == "Below the cutoff."
    assert strip_refs("It was 129.6 g/L (E1), below 130 g/L (F, S1; P1).") == (
        "It was 129.6 g/L, below 130 g/L."
    )
    assert strip_refs("Category (A2) and Hemoglobin [Mass/volume].") == (
        "Category (A2) and Hemoglobin [Mass/volume]."
    )
    assert problems("Below the cutoff [E1].", ["E1"]) == []
    assert problems("It was 121.13 g/L (E1).", ["E1"]) == []


@pytest.mark.parametrize(
    ("text", "stripped"),
    [
        # A trailing phrase that only points at items is a reference in words (second run).
        ("This value meets the criterion, as stated in S1.", "This value meets the criterion."),
        ("No adjustment was made, per L1 and S2.", "No adjustment was made."),
        ("Only the latest value counts, as noted in item L2.", "Only the latest value counts."),
        ("It comes from the guideline, described in passage P4.", "It comes from the guideline."),
        ("Il valore è sotto la soglia, come indicato in F.", "Il valore è sotto la soglia."),
        ("Non si vede (come avvertito in L1).", "Non si vede."),
        (
            "Si basa sul sesso registrato, secondo quanto indicato da L3.",
            "Si basa sul sesso registrato.",
        ),
        ("It would not be visible, per limitation L1.", "It would not be visible."),
        (
            "Tests on 2016-02-22 (item E1) and 2017-02-21 (item E2).",
            "Tests on 2016-02-22 and 2017-02-21.",
        ),
        ("Potrebbe non essere visibile, come indicato da L1.", "Potrebbe non essere visibile."),
        (
            "A morning sample is preferred, according to guidance in P2.",
            "A morning sample is preferred.",
        ),
        (
            "A 20% change warrants evaluation, as outlined in P3.",
            "A 20% change warrants evaluation.",
        ),
        # An ID that does work in the sentence stays, and the check drops the sentence.
        ("La fonte L1 spiega che un test altrove non si vede.", None),
        ("Come indicato in F, nel record non c'è il test.", None),
        ("The dose per day is listed in the label.", None),
        # A tail not set off by a comma or a parenthesis stays whole, and is dropped. The
        # evaluation run kept "..., secondo quanto." when a match could start mid-phrase.
        ("Lo dicono le linee guida citate in S1 e P1.", None),
        ("La soglia si riferisce al sesso registrato secondo quanto indicato da L3.", None),
    ],
)
def test_reference_phrases_are_stripped_like_brackets(text: str, stripped: str | None) -> None:
    assert strip_refs(text) == (stripped or text)


def test_page_references_and_ordinary_words_are_not_item_ids() -> None:
    ids = ["F", "E1", "L1", "S1", "P1"]
    assert not names_an_item("Kidney Int 2024;105(4S):S117-S314, pp. S155 and S196.", ids)
    assert not names_an_item("E poi il valore era sotto la soglia di 130 g/L.", ids)
    assert not names_an_item("A value of 5.1 F is not a temperature anyone records.", ids)
    assert names_an_item("as listed in P1.", ids)


def test_categories_compare_across_languages_and_forms() -> None:
    assert categories_in("CKD stage 3, stadio 3, CKD 3 and G3") == {"G3"}
    assert categories_in("latest category G3a A2") == {"G3a", "A2"}
    assert categories_in("anemia lieve; lieve anemia; mild anaemia") == {"severity mild"}
    assert categories_in("rischio molto alto") == {"risk very high"}
    assert categories_in("No anaemia diagnosis is recorded; nessuna anemia registrata") == set()
    assert categories_in("HbA1c 6.1%; 130 g/L") == set()


def test_a_category_the_rule_names_can_be_repeated_but_not_one_from_a_passage() -> None:
    assert check_sentence("The latest category is G3a.", ["F"], CKD_PACK, "en") == []
    assert check_sentence("L'ultima categoria è G3a.", ["F"], CKD_PACK, "it") == []
    assert check_sentence("G3a means a high risk.", ["F", "P1"], CKD_PACK, "en") == [
        "category not from the rule"
    ]
    assert check_sentence("Category G3b applies.", ["F"], CKD_PACK, "en") == [
        "category not from the rule"
    ]


def test_known_gaps() -> None:
    """Numbers in words are not checked, and a cited item can be misread: both pass."""
    assert problems("This was three months ago.", ["E1"]) == []
    assert problems("The haemoglobin of 121.13 g/L is normal.", ["E1"]) == []
