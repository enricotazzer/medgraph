"""Text from PDF lab reports. Only PDFs with a text layer are supported: a scanned report
is rejected with a clear error instead of being read badly.

Lines are rebuilt from word positions. A horizontal gap clearly wider than a normal space
becomes a column break (two or more spaces), so table columns stay separated whatever the
font; pdfplumber's own layout mode estimates spacing from a fixed character width and can
merge a long name into the next column.
"""

import re
from itertools import pairwise
from pathlib import Path
from typing import Any

import pdfplumber

COLUMN_GAP = 1.5  # in average character widths of the line


class NoTextLayerError(ValueError):
    """The PDF has no extractable text, as with a scanned or photographed report."""


def pdf_text(path: Path) -> str:
    """The PDF's text, one line per printed line, with column gaps kept."""
    lines: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
            lines += _lines(words)
            lines.append("")
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    if not text.strip():
        raise NoTextLayerError(
            f"{path.name} has no text layer (scanned or photographed?); "
            "only PDFs with selectable text are supported"
        )
    return text


def _lines(words: list[dict[str, Any]]) -> list[str]:
    rows: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda w: (round(float(w["top"])), float(w["x0"]))):
        if rows and abs(float(rows[-1][0]["top"]) - float(word["top"])) <= 2:
            rows[-1].append(word)
        else:
            rows.append([word])
    out = []
    for row in rows:
        row.sort(key=lambda w: float(w["x0"]))
        chars = sum(len(w["text"]) for w in row)
        char_width = sum(float(w["x1"]) - float(w["x0"]) for w in row) / max(chars, 1)
        line = row[0]["text"]
        for previous, word in pairwise(row):
            gap = float(word["x0"]) - float(previous["x1"])
            columns = round(gap / char_width) if char_width else 1
            line += " " * max(2, columns) if gap > COLUMN_GAP * char_width else " "
            line += word["text"]
        out.append(line)
    return out
