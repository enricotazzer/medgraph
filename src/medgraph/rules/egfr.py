"""eGFR from serum creatinine with the CKD-EPI 2021 equation (source ``ckd-epi-2021``).

medgraph computes eGFR itself instead of trusting a reported value: Synthea reports MDRD, labs
differ in their equation, and the KDIGO criteria are applied to one equation consistently. The
computed value is kept apart from any reported eGFR and labelled as computed.

Inputs: creatinine in mg/dL, completed years of age on the sample date, and sex taken from the
FHIR administrative gender (a proxy, stated on every result). The equation is for adults, so
nothing is computed under 18, and nothing for gender ``other`` or ``unknown``, or for a
creatinine reported with a comparator (``< 0.3``). Arithmetic is in ``Decimal``. The KDIGO
categories are applied to the value rounded to a whole number (half up); the unrounded value
is kept.
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal

from medgraph.rules.context import Context, Sex
from medgraph.rules.model import ComputedEgfr

KAPPA: dict[Sex, Decimal] = {"female": Decimal("0.7"), "male": Decimal("0.9")}
ALPHA: dict[Sex, Decimal] = {"female": Decimal("-0.241"), "male": Decimal("-0.302")}
CONSTANT = Decimal(142)
EXPONENT_ABOVE_KAPPA = Decimal("-1.200")
AGE_FACTOR = Decimal("0.9938")
FEMALE_FACTOR = Decimal("1.012")
MIN_AGE = 18


def ckd_epi_2021(creatinine_mg_dl: Decimal, age: int, sex: Sex) -> Decimal:
    """eGFR in mL/min/1.73 m2.

    142 x min(SCr/k, 1)^a x max(SCr/k, 1)^-1.200 x 0.9938^age (x 1.012 if female).
    """
    if creatinine_mg_dl <= 0:
        raise ValueError("creatinine must be positive")
    if age < MIN_AGE:
        raise ValueError(f"CKD-EPI 2021 is for ages {MIN_AGE} and older")
    ratio = creatinine_mg_dl / KAPPA[sex]
    egfr = (
        CONSTANT
        * min(ratio, Decimal(1)) ** ALPHA[sex]
        * max(ratio, Decimal(1)) ** EXPONENT_ABOVE_KAPPA
        * AGE_FACTOR**age
    )
    return egfr * FEMALE_FACTOR if sex == "female" else egfr


def mdrd_2006(creatinine_mg_dl: Decimal, age: int, sex: Sex) -> Decimal:
    """IDMS-traceable MDRD Study eGFR (source ``mdrd-2006``), without the race factor, which
    medgraph does not record.

    Used only to check whether a reported MDRD eGFR is consistent with the creatinine it
    should come from; no rule uses it.
    """
    if creatinine_mg_dl <= 0:
        raise ValueError("creatinine must be positive")
    egfr = Decimal(175) * creatinine_mg_dl ** Decimal("-1.154") * Decimal(age) ** Decimal("-0.203")
    return egfr * Decimal("0.742") if sex == "female" else egfr


def whole(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def computed_series(ctx: Context) -> tuple[tuple[ComputedEgfr, ...], Counter[str]]:
    """eGFR for every usable creatinine value, and why the others got none."""
    computed = []
    skipped: Counter[str] = Counter()
    for v in ctx.series.get("creatinine", ()):
        age = ctx.age_years(v.date)
        sex = ctx.sex
        if age is None:
            skipped["no birth date"] += 1
        elif age < MIN_AGE:
            skipped[f"under {MIN_AGE} on the sample date"] += 1
        elif sex is None:
            skipped[f"administrative gender {ctx.gender}"] += 1
        elif v.comparator is not None:
            skipped["creatinine reported with a comparator"] += 1
        else:
            egfr = ckd_epi_2021(v.value, age, sex)
            computed.append(
                ComputedEgfr(
                    node=v.node,
                    date=v.date,
                    creatinine=str(v.value),
                    age=age,
                    sex=sex,
                    value=str(egfr),
                    whole=whole(egfr),
                )
            )
    return tuple(computed), skipped
