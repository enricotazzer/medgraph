"""Checking a cited quote against the stored text of its document.

A quote is written as fragments separated by "[...]". Each fragment must occur in the
document (``medgraph.rag.text.find``: verbatim up to case, spacing, punctuation and line
breaks). When the locator names pages ("p. S149", "pp. 283 and 288", "pp. 288-291"), each
fragment must be found on one of them, so a wrong page is caught too.

Some tables do not read row by row in the extracted text: a PDF may give a column of labels,
then a column of values. Their content is written separately as a *table transcription*:
rows separated by ";". This is a weaker check, and it is reported as one: every word and
number of a row must occur on a page where the quote itself was found.
"""

import re
from collections.abc import Sequence

from medgraph.rag.text import Page, find, tokens

PAGE_REF = re.compile(r"\bpp?\. ?([^;,()]+)")
ROMAN = re.compile(r"^[ivxlc]+$")
OMISSION = "[...]"


def fragments(quote: str) -> list[str]:
    return [f.strip(" ;,.") for f in quote.split(OMISSION) if f.strip(" ;,.")]


def locator_pages(locator: str) -> set[str]:
    """Page labels a locator names; numeric ranges ("288-291") are expanded."""
    pages: set[str] = set()
    for group in PAGE_REF.findall(locator):
        for part in re.split(r",| and ", group):
            part = part.strip().replace("\u2013", "-")  # en dash
            if m := re.fullmatch(r"(S?)(\d+)-S?(\d+)", part):
                prefix, start, end = m.group(1), int(m.group(2)), int(m.group(3))
                pages |= {f"{prefix}{n}" for n in range(start, end + 1)}
            elif re.fullmatch(r"S?\d+", part) or ROMAN.match(part):
                pages.add(part)
    return pages


def check_quote(
    quote: str, pages: Sequence[Page], *, locator: str = "", table: str = ""
) -> list[str]:
    """Problems with a quote; an empty list means every part was found where it is cited.

    For a drug label, ``pages`` are its sections and the locator is "section <titles>": each
    fragment must then be found in that section.
    """
    problems = []
    wanted = locator_pages(locator)
    if locator.startswith("section "):
        wanted = {locator.removeprefix("section ")}
    found_on: set[int] = set()
    for fragment in fragments(quote):
        hits = find(fragment, pages)
        if not hits:
            problems.append(f"not in the document: {fragment[:70]!r}")
            continue
        if wanted:
            hits = [h for h in hits if h.first.label in wanted or h.last.label in wanted]
            if not hits:
                problems.append(f"not where the locator says {sorted(wanted)}: {fragment[:70]!r}")
                continue
        found_on |= {h.first.number for h in hits} | {h.last.number for h in hits}
    page_tokens = [set(tokens(p.text)) for p in pages if p.number in found_on]
    for row in (r.strip() for r in table.split(";")):
        if row and not any(set(tokens(row)) <= words for words in page_tokens):
            problems.append(f"table row not on the quote's pages: {row!r}")
    return problems
