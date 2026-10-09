"""Text from stored documents, and finding a quote in it.

Extraction keeps the document's words and order and records where each page is:
- **PDF** text comes from pdfium, which follows the publisher's reading order (columns
  included). Line-end hyphenation is rejoined, and glyphs that a document's fonts map to
  letters at the start of a list item are shown as "•" (configured per document).
- **HTML** text is the page's ``<main>`` element when it has one, without scripts, styles or
  navigation. Superscripts are written with "^" (``m^2``).

A quote is *found* when its words occur contiguously in the document after tokenizing both
the same way: case, spacing, punctuation, bullets and line breaks are ignored; numbers keep
their sign and decimals, and comparators (<, >, <=, >=, =) are kept. So "GFR <60" and "GFR
< 60" match, "-0.241" and "0.241" do not, and "4-5ND" written with an en dash matches "4-5ND".
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium

ASCII = str.maketrans(
    {
        "\u2265": ">=",  # greater-than or equal to
        "\u2264": "<=",
        "\u2013": "-",  # en dash
        "\u2014": "-",  # em dash
        "\u2212": "-",  # minus sign
        "\u2010": "-",  # hyphen
        "\u00d7": "x",  # multiplication sign
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
    }
)
TOKEN = re.compile(
    r"(?<![^\W_.])-?\d+(?:\.\d+)?(?![^\W_])"  # a number, with its sign when it has one
    r"|[^\W_]+"  # a word: letters and digits
    r"|[<>]=?|="  # a comparator
)
SOFT_HYPHEN = re.compile("[￾­]\\s*")


@dataclass(frozen=True)
class Page:
    """A page of a document, or a section of a drug label (``unit="section"``)."""

    number: int  # 1-based position in the file
    label: str | None  # the printed page number, or the section's titles
    text: str
    unit: Literal["page", "section"] = "page"

    @property
    def cite(self) -> str:
        if self.unit == "section":
            return f"section {self.label}"
        return f"p. {self.label}" if self.label else f"page {self.number} of the file"


def tokens(text: str) -> list[str]:
    return TOKEN.findall(unicodedata.normalize("NFKC", text).translate(ASCII).lower())


@dataclass(frozen=True)
class Found:
    first: Page
    last: Page


def find(quote: str, pages: Sequence[Page]) -> list[Found]:
    """Every place where ``quote`` occurs; a match may run across a page break."""
    needle = tokens(quote)
    if not needle:
        raise ValueError("an empty quote matches everywhere")
    words: list[str] = []
    where: list[int] = []
    for i, page in enumerate(pages):
        page_tokens = tokens(page.text)
        words.extend(page_tokens)
        where.extend([i] * len(page_tokens))
    n = len(needle)
    return [
        Found(pages[where[start]], pages[where[start + n - 1]])
        for start in range(len(words) - n + 1)
        if words[start] == needle[0] and words[start : start + n] == needle
    ]


# --- PDF ----------------------------------------------------------------------------------


def clean_pdf_text(raw: str, bullets: Iterable[str] = ()) -> str:
    text = SOFT_HYPHEN.sub("", raw.replace("\r\n", "\n").replace("\r", "\n"))
    for glyph in bullets:
        text = re.sub(rf"(?m)^{re.escape(glyph)} (?=\S)", "• ", text)
    return text


def printed_label(text: str, patterns: Sequence[re.Pattern[str]]) -> str | None:
    for line in text.splitlines():
        for pattern in patterns:
            if m := pattern.match(line.strip()):
                return m.group(1)
    return None


def pdf_pages(
    path: Path,
    *,
    page_patterns: Sequence[str] = (),
    pdf_labels: bool = False,
    bullets: Iterable[str] = (),
) -> tuple[Page, ...]:
    patterns = [re.compile(p) for p in page_patterns]
    bullets = tuple(bullets)
    pdf = pdfium.PdfDocument(path)
    try:
        pages = []
        for i in range(len(pdf)):
            textpage = pdf[i].get_textpage()
            text = clean_pdf_text(textpage.get_text_range(), bullets)
            label = (pdf.get_page_label(i) or None) if pdf_labels else printed_label(text, patterns)
            pages.append(Page(i + 1, label, text))
        return tuple(pages)
    finally:
        pdf.close()


# --- HTML ---------------------------------------------------------------------------------

SKIP = frozenset({"script", "style", "noscript", "nav", "header", "footer", "template", "svg"})
BLOCK = frozenset(
    {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}
    | {"ul", "ol", "dt", "dd", "caption", "blockquote", "main", "article"}
)
CELL = frozenset({"td", "th"})


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main_parts: list[str] | None = None
        self._skip = 0
        self._in_main = 0

    def _emit(self, text: str) -> None:
        self.parts.append(text)
        if self._in_main and self.main_parts is not None:
            self.main_parts.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP:
            self._skip += 1
        elif tag == "main":
            self._in_main += 1
            if self.main_parts is None:
                self.main_parts = []
        if tag in BLOCK:
            self._emit("\n")
        elif tag in CELL:
            self._emit(" | ")
        elif tag == "sup" and not self._skip:
            self._emit("^")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag == "main":
            self._in_main = max(0, self._in_main - 1)
        if tag in BLOCK:
            self._emit("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._emit(data)


def html_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    parts = parser.main_parts if parser.main_parts else parser.parts
    lines = (re.sub("[ \t\u00a0]+", " ", line).strip() for line in "".join(parts).splitlines())
    return "\n".join(line for line in lines if line)


def html_pages(path: Path) -> tuple[Page, ...]:
    return (Page(1, None, html_text(path.read_text(encoding="utf-8"))),)
