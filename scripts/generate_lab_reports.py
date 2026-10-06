"""Generate synthetic lab reports (text and PDF) with ground truth, from a Synthea cohort.

    uv run python scripts/generate_lab_reports.py configs/lab_reports/reports-v1.yaml [--force]

Each report is one patient's lab results on one collection date, printed by deterministic
templates: no LLM is involved, so generation and extraction cannot share a model's habits.
Six layout families exist; the test split also uses families and analyte names that the
development split never shows (see the config), so results on unseen layouts and unseen
names can be reported separately. Patients never appear in both splits.

Everything printed is synthetic and marked as such. Reference ranges are illustrative test
data (see ``lab_report_catalog``); medgraph never reads them back as medical knowledge.
"""

import argparse
import datetime as dt
import hashlib
import json
import random
import textwrap
from collections.abc import Iterator
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from lab_report_catalog import (
    BY_LOINC,
    QUALITATIVE_WORDS,
    SECTION_TITLES,
    SOURCE_UNITS,
    TESTS,
    Printing,
    Style,
    Test,
)
from medgraph.ingest.fhir import BundleError, read_bundle_file
from medgraph.ingest.files import content_digest, iter_data_files, remove_tree, sha256_file
from medgraph.normalize.analytes import BY_KEY
from medgraph.records import PatientRecord
from medgraph.settings import Settings

Family = Literal["table", "dotted", "colon", "sections", "two_column", "narrative"]
Language = Literal["it", "en"]
SECTION_ORDER = ("hematology", "chemistry", "urine")
MONTHS_IT = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
             "agosto", "settembre", "ottobre", "novembre", "dicembre")  # fmt: skip
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December")  # fmt: skip


# --- configuration and ground truth ------------------------------------------------------


class SplitConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    families: tuple[Family, ...]
    reports_per_cell: int = Field(gt=0)  # per (family, language)
    held_out_name_rate: float = Field(ge=0, le=1)


class ReportsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    source_cohort: str
    seed: int
    max_rows: int = Field(gt=0)
    test_patient_fraction: float = Field(gt=0, lt=1)
    dev: SplitConfig
    test: SplitConfig


class TruthRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    test: str  # catalog key
    loinc: str
    analyte: str | None  # medgraph registry key, for in-scope analytes
    analyte_text: str
    name_seen: bool | None  # in-scope analytes: is this spelling in the name table?
    value_text: str
    unit_text: str
    range_text: str  # the interval as printed, without brackets or a "ref" label
    flag_text: str
    qualitative: bool
    canonical_value: str | None  # derived from the printed value; None if not convertible
    canonical_unit: str | None


class TruthReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    report_id: str
    split: Literal["dev", "test"]
    family: Family
    language: Language
    style: Style
    sex: str
    collection_date: dt.date
    collection_date_text: str
    rows: tuple[TruthRow, ...]


# --- source data -----------------------------------------------------------------------


class SourceValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    test: str
    loinc: str
    value: Decimal | None  # in SOURCE_UNITS[test], or the eGFR's recorded unit
    unit: str
    qualitative: str | None  # "negative" | "positive" | "trace"


def lab_days(record: PatientRecord) -> dict[dt.date, list[SourceValue]]:
    """Printable results per collection date: one value per test, first one wins."""
    days: dict[dt.date, dict[str, SourceValue]] = {}
    for obs in record.observations:
        loinc = obs.code.code("http://loinc.org")
        test = BY_LOINC.get(loinc or "")
        if test is None or obs.effective is None or loinc is None:
            continue
        value = None
        qualitative = None
        unit = ""
        if test.qualitative:
            label = obs.value_concept.label.lower() if obs.value_concept else ""
            qualitative = next((w for w in QUALITATIVE_WORDS if w in label), None)
            if qualitative is None:
                continue
        else:
            q = obs.value_quantity
            unit = (q.code or q.unit or "") if q else ""
            expected = SOURCE_UNITS[test.key]
            if q is None or not (unit == expected or (test.key == "egfr" and unit == "mL/min")):
                continue
            value = q.value
        day = days.setdefault(obs.effective.date, {})
        day.setdefault(
            test.key,
            SourceValue(
                test=test.key, loinc=loinc, value=value, unit=unit, qualitative=qualitative
            ),
        )
    return {
        date: list(values.values())
        for date, values in days.items()
        if any(BY_LOINC[v.loinc].registry_key for v in values.values())
    }


# --- formatting ------------------------------------------------------------------------


def fmt_number(value: Decimal, decimals: int, language: Language) -> str:
    quantum = Decimal(1).scaleb(-decimals)
    text = str(value.quantize(quantum, rounding=ROUND_HALF_UP))
    return text.replace(".", ",") if language == "it" else text


def fmt_date(date: dt.date, style: Style, textual: bool) -> str:
    if textual:
        if style == "it":
            return f"{date.day} {MONTHS_IT[date.month - 1]} {date.year}"
        if style == "en-GB":
            return f"{date.day} {MONTHS_EN[date.month - 1][:3]} {date.year}"
        return f"{MONTHS_EN[date.month - 1]} {date.day}, {date.year}"
    if style == "en-US":
        return f"{date.month:02d}/{date.day:02d}/{date.year}"
    return f"{date.day:02d}/{date.month:02d}/{date.year}"


def language_of(style: Style) -> Language:
    return "it" if style == "it" else "en"


def printing_for(test: Test, style: Style, source: SourceValue) -> Printing:
    if test.key == "egfr" and source.unit == "mL/min":  # printed as recorded, never converted
        return Printing("mL/min", "mL/min", 0)
    return test.printing[style]


def reference(test: Test, style: Style, sex: str) -> tuple[Decimal | None, Decimal | None] | None:
    if test.qualitative:
        return None
    if test.sex_ranges:
        return test.sex_ranges["female" if sex == "F" else "male"][style]
    return test.ranges.get(style)


def places(x: Decimal) -> int:
    exponent = x.as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


def fmt_range(bounds: tuple[Decimal | None, Decimal | None], language: Language) -> str:
    """An interval with the decimals it is defined with, e.g. ``0,70 - 1,20`` or ``< 30``."""
    low, high = bounds

    def n(x: Decimal) -> str:
        return fmt_number(x, places(x), language)

    if low is not None and high is not None:
        return f"{n(low)} - {n(high)}"
    if high is not None:
        return f"< {n(high)}"
    assert low is not None
    return f"> {n(low)}"


# --- building a report -------------------------------------------------------------------


class Row(BaseModel):
    """A row ready to print, with its ground truth."""

    model_config = ConfigDict(frozen=True)

    section: str
    truth: TruthRow


def build_rows(
    values: list[SourceValue],
    style: Style,
    sex: str,
    split: str,
    held_out_rate: float,
    max_rows: int,
    rnd: random.Random,
) -> list[Row]:
    language = language_of(style)
    in_scope = [v for v in values if BY_LOINC[v.loinc].registry_key]
    others = [v for v in values if not BY_LOINC[v.loinc].registry_key]
    rnd.shuffle(others)
    chosen = (in_scope + others)[:max_rows]
    order = {t.key: i for i, t in enumerate(TESTS)}
    chosen.sort(key=lambda v: (SECTION_ORDER.index(BY_LOINC[v.loinc].section), order[v.test]))
    rows = []
    for v in chosen:
        test = BY_LOINC[v.loinc]
        names = test.names_it if language == "it" else test.names_en
        use_held_out = (
            split == "test"
            and test.registry_key is not None
            and bool(names.held_out)
            and rnd.random() < held_out_rate
        )
        name = rnd.choice(names.held_out if use_held_out else names.seen)
        rows.append(
            Row(section=test.section, truth=truth_row(test, v, name, use_held_out, style, sex))
        )
    return rows


def truth_row(
    test: Test, v: SourceValue, name: str, held_out: bool, style: Style, sex: str
) -> TruthRow:
    language = language_of(style)
    if test.qualitative:
        assert v.qualitative is not None
        word = QUALITATIVE_WORDS[v.qualitative][language]
        expected = QUALITATIVE_WORDS["negative"][language]
        return TruthRow(
            test=test.key,
            loinc=v.loinc,
            analyte=None,
            analyte_text=name,
            name_seen=None,
            value_text=word,
            unit_text="",
            range_text=expected,
            flag_text="",
            qualitative=True,
            canonical_value=None,
            canonical_unit=None,
        )
    assert v.value is not None
    printing = printing_for(test, style, v)
    printed = (v.value * printing.factor).quantize(
        Decimal(1).scaleb(-printing.decimals), rounding=ROUND_HALF_UP
    )
    bounds = reference(test, style, sex)
    flag = ""
    if bounds is not None:
        low, high = bounds
        if high is not None and printed > high:
            flag = "H"
        elif low is not None and printed < low:
            flag = "L"
    canonical_value = canonical_unit = None
    if test.registry_key is not None:
        analyte = BY_KEY[test.registry_key]
        factor = analyte.to_canonical.get(printing.ucum)
        if factor is not None:
            canonical_value = str(printed * factor)
            canonical_unit = analyte.canonical_unit
    return TruthRow(
        test=test.key,
        loinc=v.loinc,
        analyte=test.registry_key,
        analyte_text=name,
        name_seen=None if test.registry_key is None else not held_out,
        value_text=fmt_number(printed, printing.decimals, language),
        unit_text=printing.unit_text,
        range_text=fmt_range(bounds, language) if bounds else "",
        flag_text=flag,
        qualitative=False,
        canonical_value=canonical_value,
        canonical_unit=canonical_unit,
    )


# --- layouts -----------------------------------------------------------------------------


def header(report: TruthReport, patient_tag: str, rnd: random.Random) -> list[str]:
    it = report.language == "it"
    report_date = fmt_date(report.collection_date + dt.timedelta(days=1), report.style, False)
    label = rnd.choice(
        ["Data prelievo", "Data del prelievo"]
        if it
        else ["Collection date", "Date collected", "Collected"]
    )
    return [
        "LABORATORIO SINTETICO MEDGRAPH" if it else "MEDGRAPH SYNTHETIC LABORATORY",
        "REFERTO SINTETICO - SOLO PER TEST" if it else "SYNTHETIC REPORT - FOR TESTING ONLY",
        f"{'ID paziente' if it else 'Patient ID'}: {patient_tag}    "
        f"{'Sesso' if it else 'Sex'}: {report.sex}",
        f"{label}: {report.collection_date_text}    "
        f"{'Data referto' if it else 'Report date'}: {report_date}",
        "",
    ]


def footer(language: Language) -> list[str]:
    if language == "it":
        return ["", "Referto validato elettronicamente.", "Pagina 1 di 1"]
    return ["", "Electronically validated report.", "Page 1 of 1"]


def flag_mark(flag: str, family: Family, language: Language) -> str:
    if not flag:
        return ""
    if family in ("dotted", "colon"):
        return "*"
    if family == "narrative":
        words = {"H": ("alto", "high"), "L": ("basso", "low")}[flag]
        return words[0] if language == "it" else words[1]
    return flag


def pad(cells: list[tuple[int, str]]) -> str:
    line = ""
    for column, text in cells:
        line = line.ljust(column) if len(line) < column else line + "  "
        line += text
    return line.rstrip()


def render_rows(
    rows: list[Row], family: Family, language: Language, rnd: random.Random
) -> tuple[list[str], list[TruthRow]]:
    """Body lines for ``family``; returns the truth rows with family-specific flags."""
    it = language == "it"
    lines: list[str] = []
    truths = [
        r.truth.model_copy(update={"flag_text": flag_mark(r.truth.flag_text, family, language)})
        for r in rows
    ]
    if family == "table":
        lines.append(
            pad(
                [
                    (0, "ESAME" if it else "TEST"),
                    (31, "RISULTATO" if it else "RESULT"),
                    (42, "UNITA'" if it else "UNITS"),
                    (60, "VALORI DI RIFERIMENTO" if it else "REFERENCE RANGE"),
                    (83, "" if it else "FLAG"),
                ]
            )
        )
        lines += [
            pad(
                [
                    (0, t.analyte_text),
                    (31, t.value_text),
                    (42, t.unit_text),
                    (60, t.range_text),
                    (83, t.flag_text),
                ]
            )
            for t in truths
        ]
    elif family == "dotted":
        for t in truths:
            dots = "." * max(3, 34 - len(t.analyte_text) - 1)
            rng_text = f"({t.range_text})" if t.range_text else ""
            lines.append(
                pad(
                    [
                        (0, f"{t.analyte_text} {dots}"),
                        (36, t.value_text),
                        (46, t.unit_text),
                        (63, rng_text),
                        (82, t.flag_text),
                    ]
                )
            )
    elif family == "colon":
        label = "rif." if it else "ref"
        for t in truths:
            parts = [f"{t.analyte_text}: {t.value_text}"]
            parts += [t.unit_text] if t.unit_text else []
            parts += [f"({label} {t.range_text})"] if t.range_text else []
            parts += [t.flag_text] if t.flag_text else []
            lines.append(" ".join(parts))
    elif family == "sections":
        current = None
        for row, t in zip(rows, truths, strict=True):
            if row.section != current:
                current = row.section
                lines += (
                    ["", SECTION_TITLES[current]["it" if it else "en"]]
                    if lines
                    else [SECTION_TITLES[current]["it" if it else "en"]]
                )
            rng_text = f"[{t.range_text}]" if t.range_text else ""
            lines.append(
                pad(
                    [
                        (2, t.analyte_text),
                        (34, t.value_text),
                        (44, t.unit_text),
                        (61, rng_text),
                        (82, t.flag_text),
                    ]
                )
            )
            if rnd.random() < 0.25 and not t.qualitative:
                lines.append(pad([(4, "metodo: fotometrico" if it else "method: photometric")]))
    elif family == "two_column":
        cells = [
            f"{t.analyte_text}  {t.value_text} {t.unit_text}".rstrip()
            + (f"  [{t.range_text}]" if t.range_text else "")
            + (f" {t.flag_text}" if t.flag_text else "")
            for t in truths
        ]
        width = max(len(c) for c in cells[0::2]) + 4
        for left, right in zip(cells[0::2], [*cells[1::2], ""], strict=False):
            lines.append(pad([(0, left), (width, right)]))
    else:  # narrative
        ref = "riferimento" if it else "reference"
        sentences = []
        for t in truths:
            s = f"{t.analyte_text} {t.value_text}" + (f" {t.unit_text}" if t.unit_text else "")
            s += f" ({ref} {t.range_text})" if t.range_text else ""
            s += f", {t.flag_text}" if t.flag_text else ""
            sentences.append(s + ".")
        intro = "Risultati degli esami:" if it else "Test results:"
        # Never split inside a word or at a hyphen: "S-Creatinine" must stay whole.
        lines += textwrap.wrap(
            " ".join([intro, *sentences]), width=88, break_on_hyphens=False, break_long_words=False
        )
    return lines, truths


def render_pdf(lines: list[str], path: Path, font_size: float) -> None:
    pdf = canvas.Canvas(str(path), pagesize=A4, invariant=1)  # byte-reproducible
    pdf.setTitle("Synthetic lab report")
    _, height = A4
    y = height - 20 * mm
    for i, line in enumerate(lines):
        if y < 20 * mm:
            pdf.showPage()
            y = height - 20 * mm
        pdf.setFont("Courier-Bold" if i < 2 else "Courier", font_size)
        pdf.drawString(15 * mm, y, line)
        y -= font_size * 1.35
    pdf.save()


# --- generation --------------------------------------------------------------------------


def split_of(path: Path, seed: int, test_fraction: float) -> Literal["dev", "test"]:
    digest = hashlib.sha256(f"{seed}:{path.name}".encode()).digest()
    return "test" if digest[0] / 256 < test_fraction else "dev"


Split = Literal["dev", "test"]


def report_specs(cfg: ReportsConfig) -> Iterator[tuple[Split, Family, Style, int]]:
    splits: tuple[tuple[Split, SplitConfig], ...] = (("dev", cfg.dev), ("test", cfg.test))
    for split, split_cfg in splits:
        for family in split_cfg.families:
            for k in range(split_cfg.reports_per_cell * 2):
                # Languages alternate; English alternates US and UK lab conventions.
                style = "it" if k % 2 == 0 else ("en-US" if k % 4 == 1 else "en-GB")
                yield split, family, style, k // 2


def generate(cfg: ReportsConfig, fhir_dir: Path, out_dir: Path) -> list[TruthReport]:
    bundles = sorted(
        iter_data_files(fhir_dir, "*.json"),
        key=lambda p: hashlib.sha256(f"{cfg.seed}:{p.name}".encode()).hexdigest(),
    )
    pools = {
        s: [p for p in bundles if split_of(p, cfg.seed, cfg.test_patient_fraction) == s]
        for s in ("dev", "test")
    }
    cursors = {"dev": 0, "test": 0}
    reports = []
    for n, (split, family, style, k) in enumerate(report_specs(cfg)):
        rnd = random.Random(f"{cfg.seed}:{n}")
        record, days = next_patient(pools[split], cursors, split)
        date = rnd.choice(sorted(days))
        sex = {"female": "F", "male": "M"}.get(record.patient.gender, "U")
        split_cfg = cfg.dev if split == "dev" else cfg.test
        rows = build_rows(
            days[date], style, sex, split, split_cfg.held_out_name_rate, cfg.max_rows, rnd
        )
        language = language_of(style)
        date_text = fmt_date(date, style, textual=rnd.random() < 0.3)
        report_id = f"{split}-{family}-{style}-{k:02d}"
        draft = TruthReport(
            report_id=report_id,
            split=split,
            family=family,
            language=language,
            style=style,
            sex=sex,
            collection_date=date,
            collection_date_text=date_text,
            rows=(),
        )
        body, truths = render_rows(rows, family, language, rnd)
        patient_tag = hashlib.sha256(record.patient.id.encode()).hexdigest()[:8]
        lines = header(draft, patient_tag, rnd) + body + footer(language)
        report = draft.model_copy(update={"rows": tuple(truths)})
        target = out_dir / split
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{report_id}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
        render_pdf(lines, target / f"{report_id}.pdf", 7.5 if family == "two_column" else 9)
        (target / f"{report_id}.truth.json").write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        reports.append(report)
    return reports


def next_patient(
    pool: list[Path], cursors: dict[str, int], split: str
) -> tuple[PatientRecord, dict[dt.date, list[SourceValue]]]:
    while cursors[split] < len(pool):
        path = pool[cursors[split]]
        cursors[split] += 1
        try:
            record = read_bundle_file(path)
        except BundleError:
            continue
        days = lab_days(record)
        if days:
            return record, days
    raise RuntimeError(f"not enough patients with in-scope labs in the {split} split")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic lab reports.")
    parser.add_argument("config", type=Path)
    parser.add_argument("--force", action="store_true", help="replace an existing report set")
    args = parser.parse_args(argv)
    with args.config.open(encoding="utf-8") as fh:
        cfg = ReportsConfig.model_validate(yaml.safe_load(fh))
    settings = Settings()
    out_dir = settings.lab_reports_dir / cfg.name
    if out_dir.exists() and any(out_dir.iterdir()):
        if not args.force:
            print(f"{out_dir} exists; pass --force to replace it")
            return 1
        remove_tree(out_dir)
    reports = generate(cfg, settings.synthea_dir / cfg.source_cohort / "fhir", out_dir)
    files = {p.relative_to(out_dir).as_posix(): sha256_file(p) for p in iter_data_files(out_dir)}
    manifest = {
        "config": cfg.model_dump(mode="json"),
        "reports": len(reports),
        "rows": sum(len(r.rows) for r in reports),
        "content_digest": content_digest(files),
        "files": files,
    }
    (out_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    print(
        f"{len(reports)} reports, {manifest['rows']} rows -> {out_dir}\n"
        f"content digest {manifest['content_digest']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
