"""Checking a cited quote against stored text."""

import pytest

from medgraph.rag.quotes import check_quote, fragments, locator_pages
from medgraph.rag.text import Page

PAGES = [
    Page(1, "S148", "Assess albuminuria in adults, and GFR at least annually in people with CKD."),
    Page(2, "S149", "Table 2\nPregnancy\nFirst trimester\nSecond trimester\n<110\n<105"),
]


def test_fragments_split_at_omissions() -> None:
    assert fragments("For CKD patients without anemia [...], measure Hb [...]") == [
        "For CKD patients without anemia",
        "measure Hb",
    ]


@pytest.mark.parametrize(
    ("locator", "pages"),
    [
        ("Figure 3, p. S139; Practice Points 1.1.3.1 and 1.1.3.2, p. S149", {"S139", "S149"}),
        ("Chapter 1 (pp. 288-291), Recommendation 1.1.1", {"288", "289", "290", "291"}),
        ("Practice Point 2.1.1, pp. S155 and S196", {"S155", "S196"}),
        ("Table 2 (executive summary, p. xi)", {"xi"}),
        ("Practice Point 2.1.1", set()),
        ("Recommendations 1.1.2 (p. 288) and 3.12.1-3.12.3 (p. 306)", {"288", "306"}),
    ],
)
def test_locator_pages(locator: str, pages: set[str]) -> None:
    assert locator_pages(locator) == pages


def test_a_quote_found_on_the_cited_page_passes() -> None:
    assert (
        check_quote("Assess albuminuria [...] GFR at least annually", PAGES, locator="p. S148")
        == []
    )
    assert check_quote("Assess albuminuria in adults", PAGES) == []


def test_a_missing_or_misplaced_fragment_is_reported() -> None:
    assert check_quote("Assess albuminuria [...] GFR at least twice a year", PAGES) == [
        "not in the document: 'GFR at least twice a year'"
    ]
    assert check_quote("Assess albuminuria in adults", PAGES, locator="p. S149") == [
        "not where the locator says ['S149']: 'Assess albuminuria in adults'"
    ]


def test_table_rows_must_be_on_the_quotes_page() -> None:
    quote = "Pregnancy First trimester Second trimester <110 <105"
    assert check_quote(quote, PAGES, table="first trimester <110; second trimester <105") == []
    assert check_quote(quote, PAGES, table="third trimester <110") == [
        "table row not on the quote's pages: 'third trimester <110'"
    ]


def test_a_label_quote_must_be_in_the_cited_section() -> None:
    sections = [
        Page(1, "2 DOSAGE > 2.4 Renal Impairment", "Assess eGFR at least annually.", "section"),
        Page(2, "4 CONTRAINDICATIONS", "Do not use below eGFR 30.", "section"),
    ]
    quote = "Do not use below eGFR 30."
    assert check_quote(quote, sections, locator="section 4 CONTRAINDICATIONS") == []
    assert check_quote(quote, sections, locator="section 2 DOSAGE > 2.4 Renal Impairment") == [
        "not where the locator says ['2 DOSAGE > 2.4 Renal Impairment']: 'Do not use below eGFR 30'"
    ]
