"""Follow-up flags for CKD and anaemia, from a patient's graph as of an evaluation date.

Five rules:

- ``ckd-criteria-no-diagnosis``: the KDIGO 2024 CKD criteria are met and no kidney diagnosis is
  recorded for that episode (``rules/diagnoses.py``).
- ``anaemia-criteria-no-diagnosis``: the most recent haemoglobin is below the WHO 2024 cutoff
  and no anaemia diagnosis is recorded for it.
- ``ckd-gfr-follow-up`` and ``ckd-albuminuria-follow-up``: CKD is recorded or its criteria are
  met, and no GFR test (creatinine or eGFR), or no albuminuria test (urine ACR; for a child,
  also urine protein), is recorded in the 12 months before the evaluation date (KDIGO 2024
  PP 2.1.1).
- ``ckd-haemoglobin-follow-up``: CKD stage 3, or 4-5 not on dialysis, and no haemoglobin
  recorded in 12 or 6 months respectively (KDIGO 2012 Rec 1.1.1). With anaemia, KDIGO 2012's
  intervals are shorter still (Recs 1.1.2 and 3.12), so a test overdue under 1.1.1 is overdue
  in every case.

For a follow-up rule, status ``met`` means a test is overdue. A test counts as done when it is
recorded, even if its value could not be used. Kidney failure, dialysis and transplant are
outside these monitoring rules and are reported as not assessable. Dialysis is recognized from
a recorded diagnosis code or from a dialysis procedure in the 30 days before the evaluation
date (``rules/diagnoses.py``); a dialysis session during a CKD episode also counts as recorded
kidney disease for ``ckd-criteria-no-diagnosis``. A deceased patient gets no flags.

Medication rules from drug labels, with their flags and notes, are in ``rules/medications.py``.

Flags say what the record shows and suggest talking to a doctor. They never state a diagnosis.
"""

import datetime as dt
from dataclasses import replace

from medgraph.graph.schema import PatientGraph
from medgraph.rules.anaemia import AnaemiaResult, assess_anaemia
from medgraph.rules.ckd import CkdResult, assess_ckd
from medgraph.rules.context import Context, Procedure, Test, context_from_graph, months_before
from medgraph.rules.diagnoses import (
    ANAEMIA_DIAGNOSES,
    CKD_STAGES,
    DIALYSIS_PROCEDURES,
    DIALYSIS_WINDOW_DAYS,
    KIDNEY_DIAGNOSES,
    KIDNEY_FAILURE,
)
from medgraph.rules.egfr import computed_series
from medgraph.rules.medications import assess_medications
from medgraph.rules.model import Assessment, Evidence, Flag, PatientRules

DOCTOR = "This may be worth discussing with a doctor."


def evaluate(graph: PatientGraph, as_of: dt.date, *, dialysis: bool = True) -> PatientRules:
    """The rules for one patient. ``dialysis=False`` ignores dialysis procedures; it exists only
    to measure what recognizing them changes (``scripts/evaluate_flags.py``)."""
    ctx = context_from_graph(graph, as_of)
    if not dialysis:
        ctx = replace(ctx, procedures=())
    if ctx.deceased is not None and ctx.deceased <= as_of:
        return PatientRules(
            patient_id=ctx.patient_id,
            as_of=as_of,
            evaluated=False,
            note=f"Deceased on {ctx.deceased}: not evaluated, no flags.",
        )
    computed, skipped = computed_series(ctx)
    ckd = assess_ckd(ctx, computed, skipped)
    anaemia = assess_anaemia(ctx)
    flags = [f for f in (_ckd_no_diagnosis(ctx, ckd), _anaemia_no_diagnosis(ctx, anaemia)) if f]
    follow_up, follow_flags = _follow_up(ctx, ckd)
    med_assessments, med_flags, notes = assess_medications(ctx, computed)
    return PatientRules(
        patient_id=ctx.patient_id,
        as_of=as_of,
        evaluated=True,
        computed_egfr=computed,
        assessments=(ckd.assessment, anaemia.assessment, *follow_up, *med_assessments),
        flags=(*flags, *follow_flags, *med_flags),
        notes=tuple(notes),
    )


def dialysis_sessions(ctx: Context) -> list[Procedure]:
    return [p for p in ctx.procedures if p.code in DIALYSIS_PROCEDURES]


def on_dialysis(ctx: Context) -> Procedure | None:
    """The latest dialysis session, if one is recorded in the window before the evaluation
    date."""
    sessions = dialysis_sessions(ctx)
    if not sessions:
        return None
    last = sessions[-1]
    return last if (ctx.as_of - last.date).days <= DIALYSIS_WINDOW_DAYS else None


def _ckd_no_diagnosis(ctx: Context, ckd: CkdResult) -> Flag | None:
    start = ckd.episode_start
    if not ckd.met or start is None:
        return None
    recorded = [
        d
        for d in ctx.diagnoses
        if d.code in KIDNEY_DIAGNOSES
        and d.recorded_by(ctx.as_of)
        and (d.abatement is None or d.abatement >= start)
    ]
    if recorded or any(p.date >= start for p in dialysis_sessions(ctx)):
        return None
    a = ckd.assessment
    return Flag(
        rule="ckd-criteria-no-diagnosis",
        kind="criteria_met_no_diagnosis",
        title="Kidney values meet the CKD criteria; no kidney diagnosis is recorded",
        statement=(
            f"The recorded values meet the KDIGO 2024 criteria for chronic kidney disease "
            f"({a.summary.removeprefix('KDIGO 2024 CKD criteria met: ').rstrip('.')}; latest "
            f"category {a.category}). No kidney diagnosis is recorded for this period. {DOCTOR}"
        ),
        evidence=a.evidence,
        sources=a.sources,
        limitations=a.limitations,
    )


def _anaemia_no_diagnosis(ctx: Context, anaemia: AnaemiaResult) -> Flag | None:
    if not anaemia.met or anaemia.latest is None:
        return None
    day = anaemia.latest.date
    recorded = [
        d
        for d in ctx.diagnoses
        if d.code in ANAEMIA_DIAGNOSES
        and d.recorded_by(ctx.as_of)
        and (d.abatement is None or d.abatement >= day)
    ]
    if recorded:
        return None
    a = anaemia.assessment
    return Flag(
        rule="anaemia-criteria-no-diagnosis",
        kind="criteria_met_no_diagnosis",
        title="Haemoglobin below the WHO cutoff; no anaemia diagnosis is recorded",
        statement=f"{a.summary} No anaemia diagnosis is recorded for this period. {DOCTOR}",
        evidence=a.evidence,
        sources=a.sources,
        limitations=a.limitations,
    )


def _last(tests: list[Test]) -> Test | None:
    return max(tests, key=lambda t: (t.date, t.node)) if tests else None


def _follow_up(ctx: Context, ckd: CkdResult) -> tuple[list[Assessment], list[Flag]]:
    as_of = ctx.as_of
    failure = [d for d in ctx.diagnoses if d.code in KIDNEY_FAILURE and d.recorded_by(as_of)]
    stages = [
        CKD_STAGES[d.code] for d in ctx.diagnoses if d.code in CKD_STAGES and d.ongoing_on(as_of)
    ]
    coded_stage = max(stages, default=None)
    rules = ("ckd-gfr-follow-up", "ckd-albuminuria-follow-up", "ckd-haemoglobin-follow-up")
    if coded_stage is None and not ckd.met:
        return [], []
    known = (
        f"CKD stage {coded_stage} is recorded"
        if coded_stage is not None
        else "The CKD criteria are met"
    )
    dialysis = on_dialysis(ctx)
    what = None
    if failure:
        what = f"{failure[0].label} is recorded"
    elif dialysis is not None:
        what = (
            f"renal dialysis is recorded on {dialysis.date} (within {DIALYSIS_WINDOW_DAYS} "
            f"days of {as_of})"
        )
    if what is not None:
        reason = (
            f"{what}: monitoring under kidney failure, dialysis or transplant is outside these "
            "rules"
        )
        return [
            Assessment(rule=r, status="not_assessable", summary=f"{known}.", reason=reason)
            for r in rules
        ], []

    assessments: list[Assessment] = []
    flags: list[Flag] = []
    year_ago = months_before(as_of, 12)
    age = ctx.age_years(as_of)
    child = age is not None and age < 18
    checks = [
        (
            "ckd-gfr-follow-up",
            "GFR",
            [*ctx.tests.get("creatinine", ()), *ctx.tests.get("egfr", ())],
            12,
            "kdigo-2024-monitoring",
            "KDIGO 2024 Practice Point 2.1.1 says to assess GFR at least annually in people "
            "with CKD",
        ),
        (
            "ckd-albuminuria-follow-up",
            "albuminuria",
            [
                *ctx.tests.get("urine_acr", ()),
                *(ctx.tests.get("urine_protein", ()) if child else ()),
            ],
            12,
            "kdigo-2024-monitoring",
            "KDIGO 2024 Practice Point 2.1.1 says to assess albuminuria (in children, "
            "albuminuria or proteinuria) at least annually in people with CKD",
        ),
    ]
    stage_for_hb = _stage_for_haemoglobin(ckd, coded_stage)
    if stage_for_hb is not None:
        stage, months = stage_for_hb
        checks.append(
            (
                "ckd-haemoglobin-follow-up",
                "haemoglobin",
                list(ctx.tests.get("hemoglobin", ())),
                months,
                "kdigo-2012-anaemia-testing",
                f"KDIGO 2012 Recommendation 1.1.1 says to measure haemoglobin at least "
                f"{'annually' if months == 12 else 'twice per year'} in CKD {stage}; with "
                "anaemia the recommended intervals are shorter",
            )
        )
    else:
        assessments.append(
            Assessment(
                rule="ckd-haemoglobin-follow-up",
                status="not_assessable",
                summary=f"{known}.",
                reason="KDIGO 2012 Rec 1.1.1 sets intervals for CKD stage 3 to 5 only; the "
                "stage here is 1-2 or unknown",
            )
        )

    for rule, what, tests, months, source, guidance in checks:
        window_start = year_ago if months == 12 else months_before(as_of, months)
        last = _last(tests)
        sources = [source] + (["kdigo-2012-anaemia-intervals"] if what == "haemoglobin" else [])
        if last is not None and last.date >= window_start:
            assessments.append(
                Assessment(
                    rule=rule,
                    status="not_met",
                    summary=f"{known}; last {what} test on {last.date}, within {months} months "
                    f"of {as_of}.",
                    evidence=(Evidence(node=last.node, label=f"last {what} test", date=last.date),),
                    sources=tuple(sources),
                )
            )
            continue
        found = f"the last was on {last.date}" if last else "none is recorded"
        evidence = (
            (
                Evidence(
                    node=last.node,
                    label=f"last {what} test",
                    date=last.date,
                    note="usable value" if last.usable else "recorded, value not usable",
                ),
            )
            if last
            else ()
        )
        summary = f"{known}; no {what} test in the {months} months before {as_of} ({found})."
        assessments.append(
            Assessment(
                rule=rule,
                status="met",
                summary=summary,
                evidence=evidence,
                sources=tuple(sources),
            )
        )
        flags.append(
            Flag(
                rule=rule,
                kind="follow_up_due",
                title=f"No {what} test in the last {months} months",
                statement=f"{summary.rstrip('.')}. {guidance}. A doctor can say whether this "
                "test is due.",
                evidence=evidence,
                sources=tuple(sources),
                limitations=("A test done elsewhere and not in this record would not be seen.",),
            )
        )
    return assessments, flags


def _stage_for_haemoglobin(ckd: CkdResult, coded_stage: int | None) -> tuple[str, int] | None:
    """CKD stage label and haemoglobin interval in months, from the latest computed eGFR
    category if there is one, otherwise from the recorded stage."""
    category = ckd.latest_gfr_category
    if category in ("G3a", "G3b"):
        return "3", 12
    if category in ("G4", "G5"):
        return "4-5 (not on dialysis)", 6
    if category is None and coded_stage == 3:
        return "3", 12
    if category is None and coded_stage == 4:
        return "4-5 (not on dialysis)", 6
    return None
