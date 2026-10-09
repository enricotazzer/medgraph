"""Extracting text from stored documents and finding quotes in it."""

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from medgraph.rag.text import (
    Page,
    clean_pdf_text,
    find,
    html_text,
    pdf_pages,
    printed_label,
    tokens,
)


def test_tokens_keep_numbers_signs_and_comparators() -> None:
    assert tokens("GFR <60 ml/min") == ["gfr", "<", "60", "ml", "min"]
    assert tokens("GFR < 60 ml / min") == tokens("GFR <60 ml/min")
    assert tokens("ACR ≥30 mg/g") == ["acr", ">=", "30", "mg", "g"]
    assert tokens("x 0.9938^Age") == ["x", "0.9938", "age"]
    # A sign belongs to a number that starts a token; a dash between numbers is a range.
    assert tokens("max(SCr/k,1)^-1.200") == ["max", "scr", "k", "1", "-1.200"]
    assert tokens("(SCr) –1.154") == ["scr", "-1.154"]
    assert tokens("CKD 4–5ND") == ["ckd", "4", "5nd"] == tokens("CKD 4-5ND")
    assert tokens("-0.241") != tokens("0.241")
    assert tokens("ﬁltration") == ["filtration"]  # the "fi" ligature


def test_find_reports_pages_and_crosses_page_breaks() -> None:
    pages = [
        Page(1, "S148", "Assess albuminuria in adults, or albuminuria/"),
        Page(2, "S149", "proteinuria in children, and GFR at least annually in people with CKD."),
    ]
    found = find("albuminuria/proteinuria in children, and GFR", pages)
    assert len(found) == 1
    assert (found[0].first.label, found[0].last.label) == ("S148", "S149")
    assert found[0].first.cite == "p. S148"
    assert find("GFR at least twice a year", pages) == []
    with pytest.raises(ValueError, match="empty"):
        find(" ;. ", pages)


def test_clean_pdf_text_rejoins_hyphenation_and_marks_bullets() -> None:
    raw = "the many ac￾\r\ntivities\r\nK at least annually\r\nK/DOQI guideline\r\nK"
    assert clean_pdf_text(raw, bullets=["K"]) == (
        "the many activities\n• at least annually\nK/DOQI guideline\nK"
    )


def test_printed_label_from_running_footer() -> None:
    import re

    patterns = [
        re.compile(r"^(S\d{3}) Kidney International \(2024\)$"),
        re.compile(r"^Kidney International \(2024\) (S\d{3})$"),
    ]
    assert printed_label("text\nKidney International (2024) S149", patterns) == "S149"
    assert printed_label("S150 Kidney International (2024)\nmore", patterns) == "S150"
    assert printed_label("Kidney International (2024) S149 and more", patterns) is None


def test_pdf_pages_read_text_and_footer_labels(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    pdf = canvas.Canvas(str(path), pagesize=A4)
    for number, body in [(7, "Assess GFR at least annually."), (8, "Second page text.")]:
        pdf.drawString(72, 700, body)
        pdf.drawString(72, 40, f"Example Journal {number}")
        pdf.showPage()
    pdf.save()
    pages = pdf_pages(path, page_patterns=[r"^Example Journal (\d+)$"])
    assert [(p.number, p.label) for p in pages] == [(1, "7"), (2, "8")]
    assert "Assess GFR at least annually." in pages[0].text
    assert find("assess GFR at least annually", pages)[0].first.label == "7"


def test_html_text_keeps_main_content_only() -> None:
    html = """<html><head><style>p {color: red}</style><script>var x = 1;</script></head>
    <body><nav><a>Menu item</a></nav>
    <main><h1>eGFR Equations</h1><p>eGFR = 142 &times; min(SCr/&kappa;,1)<sup>&alpha;</sup></p>
    <table><tr><td>female</td><td>&le; 0.7</td></tr></table></main>
    <footer>Contact us</footer></body></html>"""
    text = html_text(html)
    assert "Menu item" not in text
    assert "Contact us" not in text
    assert "color" not in text
    assert "eGFR = 142 × min(SCr/κ,1)^α" in text
    assert "female | ≤ 0.7" in text
    assert find("eGFR = 142 x min(SCr/κ,1)α", [Page(1, None, text)])
