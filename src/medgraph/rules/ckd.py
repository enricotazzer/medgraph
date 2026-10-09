"""KDIGO 2024 criteria for chronic kidney disease, from eGFR and urine ACR.

A marker is abnormal at eGFR < 60 mL/min/1.73 m2 or urine ACR >= 30 mg/g (source
``kdigo-2024-criteria``). eGFR is computed by medgraph with CKD-EPI 2021 (``rules/egfr.py``).

**Persistence (user decision, 2026-10-08).** The criteria count as met when the marker is
abnormal in a run of consecutive values that ends at the most recent value and spans at least
90 days: two abnormal values at least 90 days apart, with no normal value between them. A normal
value breaks the run, so a finding that has since normalized is not "currently met"; that
follows KDIGO's warning not to assume chronicity from a single abnormal level.

Categories (source ``kdigo-2024-categories``) are reported for the most recent values: G from the
whole-number eGFR, A from the ACR.
"""

import datetime as dt
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from medgraph.records import Comparator
from medgraph.rules.context import Context
from medgraph.rules.model import Assessment, ComputedEgfr, Evidence

GFR_ABNORMAL_BELOW = 60
ACR_ABNORMAL_FROM = Decimal(30)  # mg/g
PERSISTENCE_DAYS = 90
BLOOD_CREATININE = "http://loinc.org|38483-4"  # Creatinine [Mass/volume] in Blood


def gfr_category(egfr_whole: int) -> str:
    for lower, category in ((90, "G1"), (60, "G2"), (45, "G3a"), (30, "G3b"), (15, "G4")):
        if egfr_whole >= lower:
            return category
    return "G5"


def albuminuria_category(acr_mg_g: Decimal) -> str:
    if acr_mg_g < ACR_ABNORMAL_FROM:
        return "A1"
    return "A2" if acr_mg_g <= 300 else "A3"


def acr_abnormal(value: Decimal, comparator: Comparator | None) -> bool | None:
    """Whether an ACR is at or above 30 mg/g; ``None`` when a comparator leaves it open."""
    if comparator is None:
        return value >= ACR_ABNORMAL_FROM
    if comparator == "<":
        return False if value <= ACR_ABNORMAL_FROM else None
    if comparator == "<=":
        return False if value < ACR_ABNORMAL_FROM else None
    return True if value >= ACR_ABNORMAL_FROM else None  # ">" or ">="


@dataclass(frozen=True)
class Point:
    node: str
    label: str
    date: dt.date
    abnormal: bool
    shown: str  # value as used, with its unit


@dataclass(frozen=True)
class Marker:
    name: str  # "eGFR" or "urine ACR"
    points: tuple[Point, ...]  # classified values, in time order
    run: tuple[Point, ...]  # the abnormal run ending at the latest value, if any

    @property
    def run_days(self) -> int:
        return (self.run[-1].date - self.run[0].date).days if self.run else 0

    @property
    def persistent(self) -> bool:
        return len(self.run) >= 2 and self.run_days >= PERSISTENCE_DAYS


def current_run(points: tuple[Point, ...]) -> tuple[Point, ...]:
    """The consecutive abnormal values that end at the most recent value."""
    run: list[Point] = []
    for p in reversed(points):
        if not p.abnormal:
            break
        run.append(p)
    return tuple(reversed(run))


@dataclass(frozen=True)
class CkdResult:
    assessment: Assessment
    egfr: Marker | None
    acr: Marker | None
    latest_gfr_category: str | None

    @property
    def met(self) -> bool:
        return self.assessment.status == "met"

    @property
    def episode_start(self) -> dt.date | None:
        """First value of the earliest persistent run, for matching a recorded diagnosis."""
        starts = [m.run[0].date for m in (self.egfr, self.acr) if m and m.persistent]
        return min(starts) if starts else None


def _run_evidence(m: Marker) -> list[Evidence]:
    """The values that establish persistence: the run's first abnormal value, the first one
    at least 90 days later, and the most recent. The whole run is in the timeline."""
    first = m.run[0]
    later = next(p for p in m.run if (p.date - first.date).days >= PERSISTENCE_DAYS)
    chosen = {
        first.node: (first, f"first abnormal {m.name} of the run"),
        later.node: (later, f"first abnormal {m.name} {PERSISTENCE_DAYS} or more days later"),
    }
    chosen.setdefault(m.run[-1].node, (m.run[-1], f"most recent {m.name}, still abnormal"))
    return [e for p, note in chosen.values() for e in _evidence((p,), note)]


def _evidence(points: tuple[Point, ...], note: str) -> list[Evidence]:
    return [
        Evidence(node=p.node, label=p.label, date=p.date, value=p.shown, note=note) for p in points
    ]


def assess_ckd(
    ctx: Context, computed: tuple[ComputedEgfr, ...], egfr_skipped: Counter[str]
) -> CkdResult:
    creatinine = {v.node: v for v in ctx.series.get("creatinine", ())}
    egfr_points = tuple(
        Point(
            node=c.node,
            label=f"eGFR computed from {creatinine[c.node].label}",
            date=c.date,
            abnormal=c.whole < GFR_ABNORMAL_BELOW,
            shown=f"{c.whole} mL/min/1.73 m2 (creatinine {c.creatinine} mg/dL, age {c.age}, "
            f"{c.sex})",
        )
        for c in computed
    )
    acr_values = ctx.series.get("urine_acr", ())
    unclassified = 0
    acr_list = []
    for v in acr_values:
        abnormal = acr_abnormal(v.value, v.comparator)
        if abnormal is None:
            unclassified += 1
            continue
        acr_list.append(
            Point(
                node=v.node,
                label=v.label,
                date=v.date,
                abnormal=abnormal,
                shown=f"{v.comparator or ''}{v.value} mg/g",
            )
        )
    acr_points = tuple(acr_list)
    egfr = Marker("eGFR", egfr_points, current_run(egfr_points)) if egfr_points else None
    acr = Marker("urine ACR", acr_points, current_run(acr_points)) if acr_points else None

    sources = ["kdigo-2024-criteria", "kdigo-2024-categories"]
    limitations = [
        "Only eGFR and urine ACR are assessed; other markers of kidney damage (imaging, "
        "histology, urine sediment) are not.",
        "Acute kidney injury cannot be excluded from the record; the persistence rule reduces "
        "this risk but does not remove it.",
    ]
    if computed:
        sources.append("ckd-epi-2021")
        limitations.append(
            "eGFR uses the administrative gender recorded in FHIR as a proxy for sex."
        )
        if any(creatinine[c.node].code == BLOOD_CREATININE for c in computed):
            limitations.append(
                "CKD-EPI 2021 is defined for serum creatinine; some values are recorded as "
                "creatinine in blood (LOINC 38483-4) and were used as such."
            )
    if unclassified:
        limitations.append(
            f"{unclassified} urine ACR value(s) reported with a comparator that leaves the "
            "category open were not used."
        )

    latest_g = gfr_category(computed[-1].whole) if computed else None
    latest_a = None
    if acr_values and acr_values[-1].comparator is None:
        latest_a = albuminuria_category(acr_values[-1].value)
    category = " ".join(c for c in (latest_g, latest_a) if c) or None

    if egfr is None and acr is None:
        why = []
        if egfr_skipped:
            why.append("no eGFR could be computed (" + ", ".join(sorted(egfr_skipped)) + ")")
        elif "creatinine" not in ctx.series:
            why.append("no usable creatinine")
        why.append("no usable urine ACR" if not acr_values else "no classifiable urine ACR")
        assessment = Assessment(
            rule="ckd-criteria",
            status="not_assessable",
            summary="Kidney function could not be assessed.",
            reason="; ".join(why),
            sources=tuple(sources),
        )
        return CkdResult(assessment, egfr, acr, latest_g)

    persistent = [m for m in (egfr, acr) if m and m.persistent]
    if persistent:
        parts = [
            f"{m.name} {'below 60' if m.name == 'eGFR' else 'at or above 30 mg/g'} in "
            f"{len(m.run)} consecutive values from {m.run[0].date} to {m.run[-1].date} "
            f"({m.run_days} days)"
            for m in persistent
        ]
        evidence = [e for m in persistent for e in _run_evidence(m)]
        assessment = Assessment(
            rule="ckd-criteria",
            status="met",
            summary="KDIGO 2024 CKD criteria met: " + "; ".join(parts) + ".",
            category=category,
            evidence=tuple(evidence),
            sources=tuple(sources),
            limitations=tuple(limitations),
        )
        return CkdResult(assessment, egfr, acr, latest_g)

    parts = []
    evidence = []
    for m in (egfr, acr):
        if m is None:
            continue
        latest = m.points[-1]
        if m.run:
            parts.append(
                f"{m.name} abnormal since {m.run[0].date} ({len(m.run)} value(s), "
                f"{m.run_days} days): not yet persistent for {PERSISTENCE_DAYS} days"
            )
        else:
            parts.append(f"latest {m.name} {latest.shown} on {latest.date} is not abnormal")
        evidence += _evidence((latest,), f"latest {m.name}")
    assessment = Assessment(
        rule="ckd-criteria",
        status="not_met",
        summary="KDIGO 2024 CKD criteria not met: " + "; ".join(parts) + ".",
        category=category,
        evidence=tuple(evidence),
        sources=tuple(sources),
        limitations=tuple(limitations),
    )
    return CkdResult(assessment, egfr, acr, latest_g)
