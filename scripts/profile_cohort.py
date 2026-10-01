"""Aggregate profile of a Synthea FHIR cohort, written as Markdown.

    uv run python scripts/profile_cohort.py dev-1000 [--out PATH] [--top N]

Answers "what does this cohort actually contain?" before ingestion is designed: which labs
exist for kidney function, urine albumin and hemoglobin, in which units, how often and over
what time span; which related conditions are coded; whether clinical notes exist.

Aggregates only, no per-patient rows. No clinical thresholds are applied here: guideline
logic lives, tested, in ``medgraph.rules``. The report has no timestamp, so regenerating it
from the same cohort gives the same file.
"""

import argparse
import base64
import datetime as dt
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from medgraph.ingest.files import iter_data_files
from medgraph.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
LOINC = "http://loinc.org"
SNOMED = "http://snomed.info/sct"
RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
# Whether "persistent for more than 3 months" can be assessed at all: a data-availability
# measure, not a clinical criterion.
SPAN_DAYS = 90

# Codes expected to matter for CKD and anemia, reported as present or absent so these
# assumptions are checked against the data instead of built in.
EXPECTED_LABS = {
    "2160-0": "Creatinine [Mass/volume] in Serum or Plasma",
    "38483-4": "Creatinine [Mass/volume] in Blood",
    "33914-3": "eGFR, MDRD equation",
    "62238-1": "eGFR, CKD-EPI equation",
    "98979-8": "eGFR, CKD-EPI 2021 equation",
    "9318-7": "Albumin/Creatinine [Mass Ratio] in Urine",
    "14959-1": "Microalbumin/Creatinine [Mass Ratio] in Urine",
    "14957-5": "Microalbumin [Mass/volume] in Urine",
    "718-7": "Hemoglobin [Mass/volume] in Blood",
    "4548-4": "Hemoglobin A1c/Hemoglobin.total in Blood",
}

KIDNEY = "kidney function"
URINE = "urine albumin/protein"
HEMOGLOBIN = "hemoglobin & hematocrit"
HBA1C = "HbA1c"
IRON = "iron & red-cell indices"
LAB_GROUPS = (KIDNEY, URINE, HEMOGLOBIN, HBA1C, IRON)
COOCCURRENCE_GROUPS = (KIDNEY, URINE, HEMOGLOBIN)
CONDITION_TERMS = (
    "kidney",
    "renal",
    "nephro",
    "dialysis",
    "albuminuria",
    "proteinuria",
    "anemia",
    "anaemia",
    "diabet",
)
AGE_BANDS = ((0, 17), (18, 39), (40, 64), (65, 79), (80, 200))

Resource = dict[str, Any]


def lab_group(display: str) -> str | None:
    """Group a LOINC display name by keyword, so unanticipated codes still surface."""
    d = display.lower()
    if "a1c" in d:
        return HBA1C
    if "stool" in d:
        return None
    if "urine" in d:
        return URINE if any(t in d for t in ("albumin", "protein", "creatinine")) else None
    if any(t in d for t in ("creatinine", "glomerular", "cystatin", "urea nitrogen")):
        return KIDNEY
    if any(t in d for t in ("hemoglobin", "haemoglobin", "hematocrit")):
        return HEMOGLOBIN
    if any(t in d for t in ("ferritin", "iron", "transferrin", "mcv", "mean corpuscular")):
        return IRON
    return None


def is_condition_of_interest(display: str) -> bool:
    d = display.lower()
    return any(term in d for term in CONDITION_TERMS)


@dataclass
class LoincStats:
    display: str
    group: str | None
    n_obs: int = 0
    categories: Counter[str] = field(default_factory=Counter)
    units: Counter[str] = field(default_factory=Counter)
    patients: set[str] = field(default_factory=set)
    # Tracked for grouped (kidney/anemia-relevant) codes only.
    values_by_unit: defaultdict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    dates_by_patient: defaultdict[str, list[dt.date]] = field(
        default_factory=lambda: defaultdict(list)
    )


@dataclass
class CodeStats:
    display: str
    patients: set[str] = field(default_factory=set)
    n_records: int = 0


@dataclass
class Profile:
    n_patients: int = 0
    n_deceased: int = 0
    n_other_bundles: int = 0
    n_events_after_death: int = 0
    genders: Counter[str] = field(default_factory=Counter)
    age_bands: Counter[str] = field(default_factory=Counter)
    resources: Counter[str] = field(default_factory=Counter)
    resources_per_patient: list[int] = field(default_factory=list)
    first_encounter: dt.date | None = None
    last_encounter: dt.date | None = None
    loinc: dict[str, LoincStats] = field(default_factory=dict)
    conditions: dict[str, CodeStats] = field(default_factory=dict)
    medications: dict[str, CodeStats] = field(default_factory=dict)
    patient_conditions: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    patient_lab_groups: defaultdict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    n_encounters: int = 0
    n_encounters_with_note: int = 0
    n_document_references: int = 0
    note_lengths: list[int] = field(default_factory=list)
    report_categories: Counter[str] = field(default_factory=Counter)
    reports_with_text: Counter[str] = field(default_factory=Counter)


def coding(concept: Resource | None, system: str) -> tuple[str, str] | None:
    """First (code, display) in ``concept`` from ``system``."""
    if not concept:
        return None
    for c in concept.get("coding", []):
        if c.get("system") == system and "code" in c:
            return str(c["code"]), str(c.get("display") or concept.get("text") or "")
    return None


def to_date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value[:10]) if value else None


def age_band(birth: dt.date, at: dt.date) -> str:
    age = at.year - birth.year - ((at.month, at.day) < (birth.month, birth.day))
    for low, high in AGE_BANDS:
        if low <= age <= high:
            return f"{low}+" if high >= 200 else f"{low}-{high}"
    return "unknown"


def collect(fhir_dir: Path, reference_date: dt.date | None) -> Profile:
    """Scan every bundle in ``fhir_dir`` and accumulate aggregate statistics."""
    profile = Profile()
    for path in iter_data_files(fhir_dir, "*.json"):
        bundle = json.loads(path.read_bytes())
        entries = bundle.get("entry", [])
        patients = [e["resource"] for e in entries if e["resource"]["resourceType"] == "Patient"]
        if not patients:
            profile.n_other_bundles += 1
            continue
        _add_patient_bundle(profile, patients[0], entries, reference_date)
    return profile


def _add_patient_bundle(
    profile: Profile, patient: Resource, entries: list[Resource], reference_date: dt.date | None
) -> None:
    pid = str(patient["id"])
    profile.n_patients += 1
    profile.genders[str(patient.get("gender", "unknown"))] += 1
    birth = to_date(patient.get("birthDate"))
    death = to_date(patient.get("deceasedDateTime"))
    profile.n_deceased += death is not None
    at = death or reference_date
    profile.age_bands[age_band(birth, at) if birth and at else "unknown"] += 1
    profile.resources_per_patient.append(len(entries))

    by_url = {str(e.get("fullUrl")): e["resource"] for e in entries}
    encounters_with_note: set[str] = set()
    encounter_urls: list[str] = []
    after_death = False
    for entry in entries:
        res: Resource = entry["resource"]
        rtype = str(res["resourceType"])
        profile.resources[rtype] += 1
        if rtype == "Encounter":
            start = to_date(res.get("period", {}).get("start"))
            encounter_urls.append(str(entry.get("fullUrl")))
            if start:
                profile.first_encounter = min(filter(None, (profile.first_encounter, start)))
                profile.last_encounter = max(filter(None, (profile.last_encounter, start)))
                after_death = after_death or (death is not None and start > death)
        elif rtype == "Observation":
            _add_observation(profile, pid, res)
        elif rtype == "Condition":
            code = coding(res.get("code"), SNOMED)
            if code:
                stats = profile.conditions.setdefault(code[0], CodeStats(code[1]))
                stats.patients.add(pid)
                stats.n_records += 1
                profile.patient_conditions[pid].add(code[0])
        elif rtype == "MedicationRequest":
            concept = res.get("medicationCodeableConcept")
            if concept is None:
                ref = res.get("medicationReference", {}).get("reference")
                concept = by_url.get(str(ref), {}).get("code")
            code = coding(concept, RXNORM)
            if code:
                stats = profile.medications.setdefault(code[0], CodeStats(code[1]))
                stats.patients.add(pid)
                stats.n_records += 1
        elif rtype == "DocumentReference":
            profile.n_document_references += 1
            for content in res.get("content", []):
                data = content.get("attachment", {}).get("data")
                if data:
                    profile.note_lengths.append(len(base64.b64decode(data).decode("utf-8")))
            for enc in res.get("context", {}).get("encounter", []):
                encounters_with_note.add(str(enc.get("reference")))
        elif rtype == "DiagnosticReport":
            category = _report_category(res)
            profile.report_categories[category] += 1
            if any(form.get("data") for form in res.get("presentedForm", [])):
                profile.reports_with_text[category] += 1
    profile.n_encounters += len(encounter_urls)
    profile.n_encounters_with_note += sum(url in encounters_with_note for url in encounter_urls)
    profile.n_events_after_death += after_death


def _add_observation(profile: Profile, pid: str, obs: Resource) -> None:
    code = coding(obs.get("code"), LOINC)
    if code is None:
        return
    stats = profile.loinc.get(code[0])
    if stats is None:
        stats = profile.loinc[code[0]] = LoincStats(code[1], lab_group(code[1]))
    stats.n_obs += 1
    stats.patients.add(pid)
    for category in obs.get("category", []):
        for c in category.get("coding", []):
            stats.categories[str(c.get("code"))] += 1
    quantity = obs.get("valueQuantity")
    unit = str(quantity.get("code") or quantity.get("unit") or "(none)") if quantity else None
    stats.units[unit or "(non-numeric)"] += 1
    if stats.group is None:
        return
    profile.patient_lab_groups[pid].add(stats.group)
    if quantity is not None and unit is not None and "value" in quantity:
        stats.values_by_unit[unit].append(float(quantity["value"]))
    when = to_date(obs.get("effectiveDateTime"))
    if when:
        stats.dates_by_patient[pid].append(when)


def _report_category(report: Resource) -> str:
    for category in report.get("category", []):
        for c in category.get("coding", []):
            return str(c.get("display") or c.get("code"))
    return "(none)"


# --- rendering ---------------------------------------------------------------------------


def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + " --- |" * len(headers)]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def fmt(value: float) -> str:
    return f"{value:.3g}"


def nearest_rank(sorted_values: list[float], q: float) -> float:
    return sorted_values[round(q * (len(sorted_values) - 1))]


def distribution(values: list[float]) -> str:
    v = sorted(values)
    points = (v[0], nearest_rank(v, 0.05), nearest_rank(v, 0.5), nearest_rank(v, 0.95), v[-1])
    return " / ".join(fmt(p) for p in points)


def units_cell(units: Counter[str]) -> str:
    return ", ".join(f"{u} ({n:,})" for u, n in units.most_common())


def span_counts(stats: LoincStats) -> tuple[int, int]:
    """Patients with >= 2 dated values, and those whose values span >= SPAN_DAYS days."""
    multi = [dates for dates in stats.dates_by_patient.values() if len(dates) >= 2]
    spanning = [d for d in multi if (max(d) - min(d)).days >= SPAN_DAYS]
    return len(multi), len(spanning)


def render_markdown(profile: Profile, meta: dict[str, Any], top: int) -> str:
    n = profile.n_patients
    out: list[str] = [
        f"# Synthea cohort profile: {meta.get('cohort', 'unknown')}",
        "",
        "> **Synthetic data.** Aggregates only; generated by `scripts/profile_cohort.py`. "
        "Synthea's diseases follow hand-written modules, so these numbers describe the "
        "generator, not any real population, and support pipeline development only.",
        "",
        table(
            ["Synthea", "seed", "clinician seed", "reference date", "content digest"],
            [
                [
                    meta.get("synthea_version", "?"),
                    meta.get("seed", "?"),
                    meta.get("clinician_seed", "?"),
                    meta.get("reference_date", "?"),
                    f"`{str(meta.get('content_digest', '?'))[:16]}…`",
                ]
            ],
        ),
        "",
        "## Patients",
        "",
        table(
            ["patients", "deceased", "other bundles", "encounters from", "encounters to"],
            [
                [
                    f"{n:,}",
                    f"{profile.n_deceased:,} ({pct(profile.n_deceased, n)})",
                    profile.n_other_bundles,
                    profile.first_encounter,
                    profile.last_encounter,
                ]
            ],
        ),
        "",
        f"Patients with an encounter dated after their death: {profile.n_events_after_death:,}. "
        "Ingestion must tolerate this Synthea artifact.",
        "",
        "Sex: " + ", ".join(f"{g} {c:,}" for g, c in profile.genders.most_common()) + ".",
        "",
        "Age at reference date (or at death): "
        + ", ".join(
            f"{band} {profile.age_bands[band]:,}"
            for band in sorted(
                (b for b in profile.age_bands if b != "unknown"),
                key=lambda b: int(b.split("-")[0].rstrip("+")),
            )
        )
        + (
            f"; unknown (no reference date or birth date) {profile.age_bands['unknown']:,}."
            if profile.age_bands["unknown"]
            else "."
        ),
        "",
        "## Resources",
        "",
        f"Resources per patient bundle: median "
        f"{statistics.median(profile.resources_per_patient or [0]):,.0f}.",
        "",
        table(
            ["resource type", "total"],
            [[t, f"{c:,}"] for t, c in profile.resources.most_common()],
        ),
        "",
    ]
    out += _render_labs(profile, n)
    out += _render_conditions(profile, n, top)
    out += _render_notes(profile)
    out += [
        f"## Top {top} LOINC codes",
        "",
        table(
            ["code", "display", "category", "observations", "patients", "units"],
            [
                [
                    code,
                    s.display,
                    ", ".join(c for c, _ in s.categories.most_common(2)),
                    f"{s.n_obs:,}",
                    f"{len(s.patients):,}",
                    units_cell(s.units),
                ]
                for code, s in sorted(profile.loinc.items(), key=lambda kv: -kv[1].n_obs)[:top]
            ],
        ),
        "",
        f"## Top {top} medications (RxNorm)",
        "",
        table(
            ["code", "display", "patients", "requests"],
            [
                [code, s.display, f"{len(s.patients):,}", f"{s.n_records:,}"]
                for code, s in sorted(
                    profile.medications.items(), key=lambda kv: -len(kv[1].patients)
                )[:top]
            ],
        ),
        "",
    ]
    return "\n".join(out)


def _render_labs(profile: Profile, n: int) -> list[str]:
    grouped = {code: s for code, s in profile.loinc.items() if s.group is not None}
    rows = []
    for group in LAB_GROUPS:
        for code, s in sorted(grouped.items(), key=lambda kv: -kv[1].n_obs):
            if s.group != group:
                continue
            multi, spanning = span_counts(s)
            per_patient = statistics.median(len(d) for d in s.dates_by_patient.values())
            rows.append(
                [
                    group,
                    code,
                    s.display,
                    f"{s.n_obs:,}",
                    f"{len(s.patients):,} ({pct(len(s.patients), n)})",
                    f"{multi:,}",
                    f"{spanning:,}",
                    f"{per_patient:g}",
                    units_cell(s.units),
                ]
            )
    dist_rows = [
        [code, s.display, unit, f"{len(values):,}", distribution(values)]
        for code, s in sorted(
            grouped.items(), key=lambda kv: (LAB_GROUPS.index(kv[1].group or ""), -kv[1].n_obs)
        )
        for unit, values in sorted(s.values_by_unit.items())
    ]
    multi_unit = [
        [code, s.display, units_cell(s.units)]
        for code, s in sorted(profile.loinc.items())
        if len(s.units) > 1
    ]
    return [
        "## Kidney- and anemia-relevant labs",
        "",
        "Codes are grouped by keywords in their display names, so codes nobody anticipated "
        "still appear. Check the grouping before relying on a row.",
        "",
        table(
            [
                "group",
                "code",
                "display",
                "observations",
                "patients ≥1",
                "patients ≥2",
                f"≥2 spanning ≥{SPAN_DAYS} d",
                "median values/patient",
                "units",
            ],
            rows,
        ),
        "",
        "Value distributions (min / p5 / median / p95 / max), by unit:",
        "",
        table(["code", "display", "unit", "values", "distribution"], dist_rows),
        "",
        "Expected codes:",
        "",
        table(
            ["code", "expected", "present", "display in data"],
            [
                [
                    code,
                    name,
                    "yes" if code in profile.loinc else "**no**",
                    profile.loinc[code].display if code in profile.loinc else "",
                ]
                for code, name in EXPECTED_LABS.items()
            ],
        ),
        "",
        "LOINC codes recorded with more than one unit (any code):",
        "",
        table(["code", "display", "units"], multi_unit) if multi_unit else "None.",
        "",
    ]


def _render_conditions(profile: Profile, n: int, top: int) -> list[str]:
    of_interest = {
        c: s for c, s in profile.conditions.items() if is_condition_of_interest(s.display)
    }

    def with_group(patients: set[str], group: str) -> str:
        hits = sum(group in profile.patient_lab_groups.get(p, set()) for p in patients)
        return pct(hits, len(patients))

    return [
        "## Kidney-, anemia- and diabetes-related conditions",
        "",
        "Share of patients with each condition who have at least one lab in a group, at any "
        "time (not temporally aligned).",
        "",
        table(
            ["SNOMED", "display", "patients", *[f"with {g}" for g in COOCCURRENCE_GROUPS]],
            [
                [
                    code,
                    s.display,
                    f"{len(s.patients):,} ({pct(len(s.patients), n)})",
                    *[with_group(s.patients, g) for g in COOCCURRENCE_GROUPS],
                ]
                for code, s in sorted(of_interest.items(), key=lambda kv: -len(kv[1].patients))
            ],
        ),
        "",
        f"## Top {top} conditions",
        "",
        table(
            ["SNOMED", "display", "patients"],
            [
                [code, s.display, f"{len(s.patients):,}"]
                for code, s in sorted(
                    profile.conditions.items(), key=lambda kv: -len(kv[1].patients)
                )[:top]
            ],
        ),
        "",
    ]


def _render_notes(profile: Profile) -> list[str]:
    lengths = profile.note_lengths
    return [
        "## Clinical notes",
        "",
        f"DocumentReference resources: {profile.n_document_references:,}; with text: "
        f"{len(lengths):,}; median length {statistics.median(lengths or [0]):,.0f} characters. "
        f"Encounters with a linked note: {profile.n_encounters_with_note:,} of "
        f"{profile.n_encounters:,} ({pct(profile.n_encounters_with_note, profile.n_encounters)}).",
        "",
        table(
            ["DiagnosticReport category", "reports", "with presentedForm text"],
            [
                [cat, f"{count:,}", f"{profile.reports_with_text[cat]:,}"]
                for cat, count in profile.report_categories.most_common()
            ],
        ),
        "",
    ]


def load_meta(cohort_dir: Path) -> dict[str, Any]:
    manifest_path = cohort_dir / "MANIFEST.json"
    if not manifest_path.exists():
        return {"cohort": cohort_dir.name}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cfg = manifest["config"]
    return {
        "cohort": manifest["cohort"],
        "synthea_version": cfg["synthea"]["version"],
        "seed": cfg["seed"],
        "clinician_seed": cfg["clinician_seed"],
        "reference_date": cfg["reference_date"],
        "content_digest": manifest["outputs"]["content_digest"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write an aggregate profile of a cohort.")
    parser.add_argument(
        "cohort", help="cohort directory, or a name under $MEDGRAPH_DATA_DIR/synthea"
    )
    parser.add_argument("--out", type=Path, help="markdown output path")
    parser.add_argument("--top", type=int, default=30, help="rows in the top-N tables")
    args = parser.parse_args(argv)

    cohort_dir = Path(args.cohort)
    if not cohort_dir.is_dir():
        cohort_dir = Settings().synthea_dir / args.cohort
    meta = load_meta(cohort_dir)
    reference = meta.get("reference_date")
    profile = collect(cohort_dir / "fhir", dt.date.fromisoformat(reference) if reference else None)
    out = args.out or REPO_ROOT / "docs" / "data" / f"synthea-{meta['cohort']}-profile.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(profile, meta, args.top), encoding="utf-8")
    print(f"{profile.n_patients:,} patients profiled; wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
