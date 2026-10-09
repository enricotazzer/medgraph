"""The sections of a DailyMed label (SPL XML) as plain text.

Each section keeps its LOINC section code and its path of titles ("5 WARNINGS AND
PRECAUTIONS > 5.3 Impaired Renal Function"), so a passage can be cited to the section it came
from. Text is written as the label prints it:
- paragraphs and table captions become lines;
- list items start with "•";
- table rows become cells separated by " | ";
- superscripts are written with "^".

Left out:
- the Highlights excerpts, which repeat the full prescribing information;
- product data elements (codes, ingredients, packaging);
- package-label panels;
- images.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from medgraph.rag.text import Page

HL7 = "{urn:hl7-org:v3}"
SKIPPED_SECTIONS = frozenset(
    {
        "48780-1",  # SPL product data elements
        "51945-4",  # package label, principal display panel
    }
)
SKIPPED_ELEMENTS = frozenset({"excerpt", "renderMultiMedia", "observationMedia"})
LINE_ELEMENTS = frozenset({"paragraph", "caption", "title"})


@dataclass(frozen=True)
class Section:
    index: int  # position in document order, from 1
    code: str | None  # LOINC section code
    path: tuple[str, ...]  # titles from the top-level section down
    text: str  # this section's own text, without its subsections

    @property
    def title(self) -> str:
        return " > ".join(t for t in self.path if t) or "(untitled section)"


def _local(tag: str) -> str:
    return tag.removeprefix(HL7)


def _inline(element: ET.Element) -> str:
    """Text of an inline element and everything in it."""
    return (element.text or "") + "".join(_child(c) + (c.tail or "") for c in element)


def _child(element: ET.Element) -> str:
    name = _local(element.tag)
    if name in SKIPPED_ELEMENTS:
        return ""
    if name == "br":
        return " "
    if name == "sup":
        return "^" + _inline(element)
    return _inline(element)


def _squash(text: str) -> str:
    return " ".join(text.split())


def _lines(element: ET.Element) -> list[str]:
    """Block structure of a ``<text>`` element as lines."""
    lines: list[str] = []
    loose = [element.text or ""]

    def flush() -> None:
        if line := _squash("".join(loose)):
            lines.append(line)
        loose.clear()

    for child in element:
        name = _local(child.tag)
        if name in LINE_ELEMENTS:
            flush()
            if line := _squash(_inline(child)):
                lines.append(line)
        elif name == "list":
            flush()
            for item in child.findall(f"{HL7}item"):
                nested = _lines(item)
                if nested:
                    lines.append("• " + nested[0])
                    lines.extend(nested[1:])
        elif name == "table":
            flush()
            if (caption := child.find(f"{HL7}caption")) is not None:
                lines.append(_squash(_inline(caption)))
            for row in child.iter(f"{HL7}tr"):
                cells = [_squash(_inline(c)) for c in row if _local(c.tag) in ("td", "th")]
                if any(cells):
                    lines.append(" | ".join(cells))
        else:
            loose.append(_child(child))
        loose.append(child.tail or "")
    flush()
    return lines


def sections(xml: bytes) -> tuple[Section, ...]:
    root = ET.fromstring(xml)
    body = root.find(f"{HL7}component/{HL7}structuredBody")
    if body is None:
        return ()
    found: list[Section] = []

    def walk(section: ET.Element, path: tuple[str, ...]) -> None:
        code_el = section.find(f"{HL7}code")
        code = code_el.get("code") if code_el is not None else None
        if code in SKIPPED_SECTIONS:
            return
        title_el = section.find(f"{HL7}title")
        here = (*path, _squash(_inline(title_el)) if title_el is not None else "")
        text_el = section.find(f"{HL7}text")
        text = "\n".join(_lines(text_el)) if text_el is not None else ""
        if text:
            found.append(Section(len(found) + 1, code, here, text))
        for child in section.findall(f"{HL7}component/{HL7}section"):
            walk(child, here)

    for top in body.findall(f"{HL7}component/{HL7}section"):
        walk(top, ())
    return tuple(found)


def section_pages(xml: bytes) -> tuple[Page, ...]:
    """The sections as pages for ``medgraph.rag.text.find``: a match cites its section."""
    return tuple(Page(s.index, s.title, s.text, unit="section") for s in sections(xml))
