"""Passages with stable IDs, cut by page or label section."""

from pathlib import Path

import pytest

from medgraph.rag import chunks as ch
from medgraph.rag.chunks import document_chunks, label_chunks, pack, paragraphs, words
from medgraph.rag.documents import DocumentSpec, reference_pages
from medgraph.rag.spl import sections
from medgraph.rag.text import Page

SPL = Path(__file__).parents[1] / "fixtures" / "spl" / "minimal.xml"


def test_paragraphs_rebuild_wrapped_lines() -> None:
    lines = [
        "Chronic kidney disease is defined as abnormalities of kidney structure or",
        "function, present for a minimum of 3 months.",
        "Practice Point 1.1.3.1: Proof of chronicity can be established by review of",
        "past measurements.",
        "• first item",
        "• second item",
    ]
    assert paragraphs(lines) == [
        "Chronic kidney disease is defined as abnormalities of kidney structure or "
        "function, present for a minimum of 3 months.",
        "Practice Point 1.1.3.1: Proof of chronicity can be established by review of "
        "past measurements.",
        "• first item",
        "• second item",
    ]


def test_pack_keeps_passages_within_the_word_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ch, "MAX_WORDS", 5)
    assert pack(["one two", "three four", "five six seven eight nine ten. Eleven twelve."]) == [
        "one two three four",
        "five six seven eight nine",
        "ten. Eleven twelve.",  # the long paragraph is cut, then its pieces packed again
    ]
    assert all(words(p) <= 5 for p in pack(["a " * 23]))


def test_document_passages_cite_one_page_and_drop_running_lines() -> None:
    pages = [
        Page(1, "S148", "Header line\nFirst page text.\nS148 Footer"),
        Page(2, "S149", "Second page text.\nS149 Footer"),
    ]
    found = document_chunks(
        "doc", "abc123", "A guideline", pages, running=lambda line: line.endswith("Footer")
    )
    assert [(c.id, c.where, c.text) for c in found] == [
        ("doc@abc123/1.1", "p. S148", "Header line First page text."),
        ("doc@abc123/2.1", "p. S149", "Second page text."),
    ]
    # The same input always gives the same passages.
    assert found == document_chunks(
        "doc", "abc123", "A guideline", pages, running=lambda line: line.endswith("Footer")
    )


def test_label_passages_carry_their_section_and_code() -> None:
    found = label_chunks("set-1", 7, "Examplix (exampline)", sections(SPL.read_bytes()))
    assert [(c.id, c.code) for c in found] == [
        ("label:set-1@v7/1.1", "34068-7"),
        ("label:set-1@v7/2.1", "42229-5"),
        ("label:set-1@v7/3.1", "34070-3"),
    ]
    assert (
        found[1].where
        == "section 2 DOSAGE AND ADMINISTRATION > 2.2 Dose in Patients with Renal Impairment"
    )
    assert "• eGFR below 30 mL/min/1.73 m^2: do not use." in found[1].text


def test_reference_pages_are_found_by_printed_label() -> None:
    spec = DocumentSpec(
        id="d",
        title="t",
        citation="c",
        url="https://x",
        format="pdf",
        sha256="0" * 64,
        licence="l",
        reference_pages=("S12", "S13"),
    )
    pages = tuple(Page(n, f"S{10 + n}", "") for n in range(1, 5))
    assert reference_pages(pages, spec) == {2, 3}
    with pytest.raises(ValueError, match="not found"):
        reference_pages(pages[:2], spec)
