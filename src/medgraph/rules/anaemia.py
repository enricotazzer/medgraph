"""WHO 2024 haemoglobin cutoffs for anaemia (source ``who-2024-cutoffs``).

**Which value (user decision, 2026-10-08):** the most recent usable haemoglobin decides.

**Groups** (completed age on the sample date): 6-23 months < 105 g/L; 24-59 months < 110;
5-11 years < 115; 12-14 years < 120; 15-65 years: women < 120, men < 130. During pregnancy:
first and third trimester < 110, second < 105.

**Not assessable:**
- Under 6 months and over 65 years. WHO 2024 sets no cutoff there and lists people over 65 as
  a research gap (``who-2024-age-gap``).
- From 15 years, when the administrative gender is other or unknown.
- In a recorded pregnancy, a value between 105 and 110 g/L. The record has no trimester, and
  only there does the trimester change the answer: below 105 is below every trimester's
  cutoff, and 110 or above is above all of them.

**Adjustments** (``who-2024-smoking``, ``who-2024-elevation``):
- For a current smoker the cutoff is raised by 3 g/L, WHO's figure for "smoker, quantity
  unknown". The smoking status is the latest one recorded on or before the haemoglobin date.
- The elevation of residence is not recorded, so no elevation adjustment is made. That is
  WHO's adjustment for 1-499 m, and every result says so.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from medgraph.records import Comparator
from medgraph.rules.context import Context, Sex, Value
from medgraph.rules.diagnoses import CURRENT_SMOKER, NOT_SMOKING, PREGNANCY
from medgraph.rules.model import Assessment, Evidence, Status

SMOKER_ADJUSTMENT = Decimal(3)  # g/L, WHO Table 5, smoker, quantity unknown
PREGNANCY_CUTOFFS = (Decimal(110), Decimal(105), Decimal(110))  # g/L, by trimester


@dataclass(frozen=True)
class Group:
    label: str
    cutoffs: tuple[Decimal, ...]  # g/L; three in pregnancy (one per trimester)


def who_group(age_months: int, sex: Sex | None, pregnant: bool) -> Group | str:
    """The WHO 2024 group and cutoff(s) for a sample, or why there is none."""
    if pregnant:
        return Group("pregnancy, trimester not recorded", PREGNANCY_CUTOFFS)
    if age_months < 6:
        return "under 6 months: WHO 2024 sets no haemoglobin cutoff"
    if age_months < 24:
        return Group("children 6-23 months", (Decimal(105),))
    if age_months < 60:
        return Group("children 24-59 months", (Decimal(110),))
    years = age_months // 12
    if years <= 11:
        return Group("children 5-11 years", (Decimal(115),))
    if years <= 14:
        return Group("children 12-14 years", (Decimal(120),))
    if years <= 65:
        if sex == "female":
            return Group("non-pregnant women 15-65 years", (Decimal(120),))
        if sex == "male":
            return Group("men 15-65 years", (Decimal(130),))
        return (
            "15 years or older with administrative gender other or unknown: the cutoff "
            "depends on sex"
        )
    return "over 65 years: WHO 2024 sets no haemoglobin cutoff for this age"


def below(value_g_l: Decimal, comparator: Comparator | None, cutoff: Decimal) -> bool | None:
    """Whether a (possibly censored) haemoglobin is below the cutoff; ``None`` if undecided."""
    if comparator is None:
        return value_g_l < cutoff
    if comparator == "<":
        return True if value_g_l <= cutoff else None
    if comparator == "<=":
        return True if value_g_l < cutoff else None
    return False if value_g_l >= cutoff else None  # ">" or ">="


def smoking_on(ctx: Context, day: dt.date) -> tuple[bool | None, str]:
    """Whether the latest smoking status on or before ``day`` says current smoker."""
    statuses = [(d, text) for d, text in ctx.smoking if d <= day]
    if not statuses:
        return None, "no smoking status recorded"
    when, text = statuses[-1]
    if text in CURRENT_SMOKER:
        return True, f"current smoker ({text}, {when})"
    if text in NOT_SMOKING:
        return False, f"not a current smoker ({text}, {when})"
    return None, f"smoking status not recognised ({text}, {when})"


@dataclass(frozen=True)
class AnaemiaResult:
    assessment: Assessment
    latest: Value | None

    @property
    def met(self) -> bool:
        return self.assessment.status == "met"


def assess_anaemia(ctx: Context) -> AnaemiaResult:
    sources = ["who-2024-cutoffs", "who-2024-elevation"]
    values = ctx.series.get("hemoglobin", ())
    if not values:
        return AnaemiaResult(
            Assessment(
                rule="anaemia-criteria",
                status="not_assessable",
                summary="Anaemia could not be assessed.",
                reason="no usable haemoglobin",
                sources=tuple(sources),
            ),
            None,
        )
    latest = values[-1]
    day = latest.date
    age_months = ctx.age_months(day)
    pregnant = any(d.code in PREGNANCY and d.spans(day) for d in ctx.diagnoses)
    group = (
        who_group(age_months, ctx.sex, pregnant)
        if age_months is not None
        else "no birth date recorded"
    )
    hb_g_l = latest.value * 10  # canonical g/dL to g/L, the unit of the WHO table
    shown = f"{latest.comparator or ''}{hb_g_l.normalize():f} g/L"
    if isinstance(group, str):
        if "over 65" in group:
            sources.append("who-2024-age-gap")
        return AnaemiaResult(
            Assessment(
                rule="anaemia-criteria",
                status="not_assessable",
                summary=f"Most recent haemoglobin {shown} on {day} could not be assessed.",
                reason=group,
                evidence=(_evidence(latest, shown, "most recent haemoglobin"),),
                sources=tuple(sources),
            ),
            latest,
        )

    smoker, smoking_text = smoking_on(ctx, day)
    adjustment = SMOKER_ADJUSTMENT if smoker else Decimal(0)
    if smoker:
        sources.append("who-2024-smoking")
    cutoffs = tuple(c + adjustment for c in group.cutoffs)
    results = {below(hb_g_l, latest.comparator, c) for c in cutoffs}
    cutoff_text = (
        f"{cutoffs[0]} g/L"
        if len(set(cutoffs)) == 1
        else " / ".join(f"{c}" for c in cutoffs) + " g/L by trimester"
    )
    where = f"the WHO 2024 cutoff of {cutoff_text} for {group.label}"
    if adjustment:
        where += f", raised by {adjustment} g/L for a {smoking_text}"
    limitations = [
        "The elevation of residence is not recorded, so no elevation adjustment was made "
        "(WHO's adjustment for 1-499 m).",
        "Only the most recent haemoglobin is assessed.",
    ]
    if smoker is None:
        limitations.append(f"Smoking: {smoking_text}; no smoking adjustment was made.")
    if group.label.startswith(("men", "non-pregnant women")):
        limitations.append(
            "The cutoff uses the administrative gender recorded in FHIR as a proxy for sex."
        )
    evidence = (_evidence(latest, shown, f"most recent haemoglobin; {where}"),)

    status: Status
    if results == {True}:
        status, verb = "met", "is below"
    elif results == {False}:
        status, verb = "not_met", "is at or above"
    else:
        reason = (
            "the cutoff depends on the trimester, which is not recorded"
            if pregnant
            else "the value's comparator leaves it open"
        )
        return AnaemiaResult(
            Assessment(
                rule="anaemia-criteria",
                status="not_assessable",
                summary=f"Most recent haemoglobin {shown} on {day}: {where}.",
                reason=reason,
                evidence=evidence,
                sources=tuple(sources),
                limitations=tuple(limitations),
            ),
            latest,
        )
    return AnaemiaResult(
        Assessment(
            rule="anaemia-criteria",
            status=status,
            summary=f"Most recent haemoglobin {shown} on {day} {verb} {where}.",
            evidence=evidence,
            sources=tuple(sources),
            limitations=tuple(limitations),
        ),
        latest,
    )


def _evidence(v: Value, shown: str, note: str) -> Evidence:
    return Evidence(
        node=v.node, label=v.label, date=v.date, value=shown, note=note, sources=v.sources
    )
