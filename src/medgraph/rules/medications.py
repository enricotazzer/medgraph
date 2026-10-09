"""Medication rules from drug labels (DailyMed), within the CKD and anaemia scope.

Flags, where a label states an eGFR threshold or a test interval:
- ``metformin-egfr-below-30``: metformin is active and the latest computed eGFR is below 30.
  The label contraindicates metformin below 30 and says to discontinue it if the eGFR falls
  below 30.
- ``metformin-egfr-30-44``: metformin is active and the latest computed eGFR is 30 to 44. The
  label does not recommend starting it there, and says to assess the benefit and risk of
  continuing when the eGFR falls below 45.
- ``epoetin-haemoglobin-monthly``: epoetin alfa is active and no haemoglobin test is recorded
  in the month before the evaluation date. The label says to monitor haemoglobin at least
  weekly after starting or adjusting therapy until stable, then at least monthly. A test
  overdue under the monthly interval is overdue in every phase.

Notes, where a label names a situation the record shows but gives no interval:
- ``ras-inhibitor-nsaid``: an ACE inhibitor, an ARB or sacubitril/valsartan is active together
  with an NSAID. Each of these labels says to monitor renal function periodically with NSAID
  therapy.
- ``metformin-no-gfr-test``: metformin is active and no GFR test (creatinine or eGFR) is
  recorded at all. The label says to assess renal function before starting and periodically
  thereafter.

How the rules read the record:
- **Active.** A drug is active when the latest request for one of its codes, authored on or
  before the evaluation date, has status ``active``. The status describes the record when it
  was exported (in Synthea, the simulation end). If the drug was started before the
  evaluation date and is still active at export, it was active on that date, unless it was
  interrupted in between, which the record would not show.
- **eGFR.** Label thresholds are compared with the whole-number eGFR that medgraph computes
  with CKD-EPI 2021, as the KDIGO categories are. The labels say "eGFR" without naming an
  equation. The latest value is used, as the labels do, so an acute change is not excluded.
- **Codes.** Code lists name the RxNorm codes of the dev-1000 cohort, each with the label its
  statement comes from. A test checks that the committed label lock maps every code to that
  label. Other data needs its codes added, with a test.

Creatinine-clearance thresholds, which most renal dosing statements use, are left out. medgraph
does not compute creatinine clearance (it needs body weight, and the Cockcroft-Gault
equation), and comparing it with an eGFR would substitute one measure for another. Those
passages stay retrievable for explanations.
"""

from dataclasses import dataclass

from medgraph.rules.context import Context, Medication, months_before
from medgraph.rules.model import Assessment, ComputedEgfr, Evidence, Flag, Note

RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"
DOCTOR = "A doctor can say what this means for the prescription."


@dataclass(frozen=True)
class LabelledDrug:
    """RxNorm codes that share a drug label; the label's statements apply to all of them."""

    name: str
    codes: dict[str, str]  # RxCUI -> display, as the cohort records it
    label: str  # DailyMed set ID of the label the cited statements come from


METFORMIN = LabelledDrug(
    "metformin",
    {"860975": "24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet"},
    "c3dfa8a1-d10a-4a1a-8eba-5f4e2a5a2949",
)
EPOETIN = LabelledDrug(
    "epoetin alfa",
    {"205923": "1 ML Epoetin Alfa 4000 UNT/ML Injection [Epogen]"},
    "1f2d0b28-9cc5-4523-80b8-637fdaf3f7a5",
)
# ACE inhibitors, ARBs and sacubitril/valsartan, each with the source of its label's NSAID
# warning (rules/sources.py).
RAS_INHIBITORS: tuple[tuple[LabelledDrug, str], ...] = (
    (
        LabelledDrug(
            "lisinopril",
            {
                "197884": "lisinopril 40 MG Oral Tablet",
                "205326": "lisinopril 30 MG Oral Tablet",
                "311353": "lisinopril 2.5 MG Oral Tablet",
                "311354": "lisinopril 5 MG Oral Tablet",
                "314076": "lisinopril 10 MG Oral Tablet",
                "314077": "lisinopril 20 MG Oral Tablet",
            },
            "838c2d78-d2d8-4981-9ec9-e50ef9e1a5d8",
        ),
        "label-zestril-nsaid",
    ),
    (
        LabelledDrug(
            "lisinopril and hydrochlorothiazide",
            {
                "197885": "hydrochlorothiazide 12.5 MG / lisinopril 10 MG Oral Tablet",
                "197886": "hydrochlorothiazide 12.5 MG / lisinopril 20 MG Oral Tablet",
            },
            "0d3a966f-f937-05a8-a90f-5aa52ebbd613",
        ),
        "label-zestoretic-nsaid",
    ),
    (
        LabelledDrug(
            "enalapril",
            {
                "858804": "enalapril maleate 2.5 MG Oral Tablet",
                "858810": "enalapril maleate 20 MG Oral Tablet",
                "858817": "enalapril maleate 10 MG Oral Tablet",
            },
            "39631f1f-5d19-43c1-b504-bf56d991ed97",
        ),
        "label-vasotec-nsaid",
    ),
    (
        LabelledDrug(
            "ramipril",
            {"198188": "ramipril 2.5 MG Oral Capsule", "198189": "ramipril 5 MG Oral Capsule"},
            "d6d57158-e8f9-4c91-8317-0374e0c87d33",
        ),
        "label-ramipril-aurobindo-nsaid",
    ),
    (
        LabelledDrug(
            "ramipril",
            {"261962": "ramipril 10 MG Oral Capsule"},
            "394a49a8-81bb-43ba-8d6a-fa6fa5030587",
        ),
        "label-ramipril-remedyrepack-nsaid",
    ),
    (
        LabelledDrug(
            "amlodipine and benazepril",
            {"898356": "amlodipine 5 MG / benazepril hydrochloride 20 MG Oral Capsule"},
            "4653cabc-f249-4b4e-95b6-5fb4ddc0ad5d",
        ),
        "label-lotrel-nsaid",
    ),
    (
        LabelledDrug(
            "losartan",
            {
                "979480": "losartan potassium 100 MG Oral Tablet",
                "979485": "losartan potassium 25 MG Oral Tablet",
                "979492": "losartan potassium 50 MG Oral Tablet",
            },
            "9949448f-c3b9-44ee-94ed-c1aca8c90f39",
        ),
        "label-cozaar-nsaid",
    ),
    (
        LabelledDrug(
            "losartan and hydrochlorothiazide",
            {
                "979464": "hydrochlorothiazide 12.5 MG / losartan potassium 100 MG Oral Tablet",
                "979468": "hydrochlorothiazide 12.5 MG / losartan potassium 50 MG Oral Tablet",
                "979471": "hydrochlorothiazide 25 MG / losartan potassium 100 MG Oral Tablet",
            },
            "4116ccde-2e23-45f4-b12f-6337df877744",
        ),
        "label-hyzaar-nsaid",
    ),
    (
        LabelledDrug(
            "valsartan",
            {
                "349199": "valsartan 80 MG Oral Tablet",
                "349201": "valsartan 160 MG Oral Tablet",
            },
            "5ddba454-f3e6-43c2-a7a6-58365d297213",
        ),
        "label-diovan-nsaid",
    ),
    (
        LabelledDrug(
            "valsartan and hydrochlorothiazide",
            {"349353": "hydrochlorothiazide 25 MG / valsartan 160 MG Oral Tablet"},
            "d76a0419-05ee-437e-884c-65807aea9569",
        ),
        "label-diovan-hct-nsaid",
    ),
    (
        LabelledDrug(
            "telmisartan",
            {"213432": "telmisartan 80 MG Oral Tablet [Micardis]"},
            "3bb7bc69-7752-4c16-9c60-e61eb5355a4d",
        ),
        "label-micardis-nsaid",
    ),
    (
        LabelledDrug(
            "sacubitril and valsartan",
            {"1656356": "sacubitril 97 MG / valsartan 103 MG Oral Tablet [Entresto]"},
            "000dc81d-ab91-450c-8eae-8eb74e72296f",
        ),
        "label-entresto-nsaid",
    ),
)
# NSAIDs as the RAS-inhibitor labels mean them ("NSAIDs, including selective COX-2
# inhibitors"). Low-dose aspirin is not listed: those label sections do not name it.
NSAIDS: dict[str, str] = {
    "849574": "Naproxen sodium 220 MG Oral Tablet",
    "198014": "Naproxen 500 MG Oral Tablet",
    "198405": "Ibuprofen 100 MG Oral Tablet",
    "310965": "Ibuprofen 200 MG Oral Tablet",
    "206905": "Ibuprofen 400 MG Oral Tablet [Ibu]",
}
ACTIVE = "the request's status is active in the record; an interruption would not be seen"


def active(ctx: Context, codes: dict[str, str]) -> Medication | None:
    """The latest active request among ``codes``, if the drug is active."""
    latest: dict[str, Medication] = {}
    for m in ctx.medications:  # in time order, so the last one per code wins
        if m.code is not None and m.code[0] == RXNORM and m.code[1] in codes:
            latest[m.code[1]] = m
    running = [m for m in latest.values() if m.status == "active"]
    return max(running, key=lambda m: (m.authored, m.node)) if running else None


def _prescribed(m: Medication) -> Evidence:
    return Evidence(node=m.node, label=m.label, date=m.authored, note="request status active")


def _egfr(e: ComputedEgfr) -> Evidence:
    return Evidence(
        node=e.node,
        label="computed eGFR (CKD-EPI 2021)",
        date=e.date,
        value=f"{e.whole} mL/min/1.73 m2",
        note=f"from creatinine {e.creatinine} mg/dL",
    )


def _metformin(
    ctx: Context, computed: tuple[ComputedEgfr, ...]
) -> tuple[list[Assessment], list[Flag], list[Note]]:
    drug = active(ctx, METFORMIN.codes)
    if drug is None:
        return [], [], []
    limitations = (
        f"Metformin counts as active because {ACTIVE}.",
        "The label does not name an eGFR equation; medgraph's computed CKD-EPI 2021 value is "
        "used, rounded to a whole number.",
        "The latest value is used, as the label does; an acute change is not excluded.",
    )
    gfr_tests = [*ctx.tests.get("creatinine", ()), *ctx.tests.get("egfr", ())]
    if not computed:
        notes = []
        if not gfr_tests:
            notes.append(
                Note(
                    rule="metformin-no-gfr-test",
                    title="Metformin is active; no kidney function test is recorded",
                    statement=(
                        f"The record shows metformin as an active prescription ({drug.label}, "
                        f"requested on {drug.authored}) and no creatinine or eGFR test. The "
                        "metformin label says to assess renal function before starting it and "
                        "periodically thereafter."
                    ),
                    evidence=(_prescribed(drug),),
                    sources=("label-metformin-renal-assessment",),
                    limitations=(limitations[0], "A test done elsewhere would not be seen."),
                )
            )
        reason = (
            "no GFR test is recorded"
            if not gfr_tests
            else "no eGFR could be computed from the recorded creatinine"
        )
        assessment = Assessment(
            rule="metformin-egfr",
            status="not_assessable",
            summary=f"Metformin is active ({drug.label}).",
            reason=reason,
            evidence=(_prescribed(drug),),
            sources=("label-metformin-egfr-30", "label-metformin-egfr-45"),
        )
        return [assessment], [], notes

    latest = computed[-1]
    evidence = (_prescribed(drug), _egfr(latest))
    found = (
        f"The record shows metformin as an active prescription ({drug.label}, requested on "
        f"{drug.authored}) and a latest eGFR of {latest.whole} mL/min/1.73 m2, computed with "
        f"CKD-EPI 2021 from the creatinine of {latest.date}."
    )
    if latest.whole < 30:
        rule, source = "metformin-egfr-below-30", "label-metformin-egfr-30"
        title = "Metformin is active; the latest eGFR is below 30"
        says = (
            "The metformin label says it is contraindicated below an eGFR of 30 mL/min/1.73 m2, "
            "and to discontinue it if the eGFR falls below 30."
        )
    elif latest.whole < 45:
        rule, source = "metformin-egfr-30-44", "label-metformin-egfr-45"
        title = "Metformin is active; the latest eGFR is between 30 and 44"
        says = (
            "The metformin label does not recommend starting it at an eGFR between 30 and 45 "
            "mL/min/1.73 m2, and says to assess the benefit and risk of continuing when the eGFR "
            "falls below 45."
        )
    else:
        assessment = Assessment(
            rule="metformin-egfr",
            status="not_met",
            summary=f"Metformin is active; latest computed eGFR {latest.whole} (45 or above).",
            evidence=evidence,
            sources=("label-metformin-egfr-30", "label-metformin-egfr-45"),
        )
        return [assessment], [], []
    assessment = Assessment(
        rule="metformin-egfr",
        status="met",
        summary=f"{found} {says}",
        evidence=evidence,
        sources=(source,),
        limitations=limitations,
    )
    flag = Flag(
        rule=rule,
        kind="medication_threshold",
        title=title,
        statement=f"{found} {says} {DOCTOR}",
        evidence=evidence,
        sources=(source,),
        limitations=limitations,
    )
    return [assessment], [flag], []


def _epoetin(ctx: Context) -> tuple[list[Assessment], list[Flag]]:
    drug = active(ctx, EPOETIN.codes)
    if drug is None:
        return [], []
    window_start = months_before(ctx.as_of, 1)
    tests = ctx.tests.get("hemoglobin", ())
    last = max(tests, key=lambda t: (t.date, t.node)) if tests else None
    evidence = [_prescribed(drug)]
    if last is not None:
        evidence.append(
            Evidence(
                node=last.node,
                label="last haemoglobin test",
                date=last.date,
                note=None if last.usable else "recorded, value not usable",
            )
        )
    if last is not None and last.date >= window_start:
        return [
            Assessment(
                rule="epoetin-haemoglobin-monthly",
                status="not_met",
                summary=f"Epoetin alfa is active; last haemoglobin test on {last.date}, within "
                f"a month of {ctx.as_of}.",
                evidence=tuple(evidence),
                sources=("label-epogen-haemoglobin",),
            )
        ], []
    found = f"the last was on {last.date}" if last else "none is recorded"
    summary = (
        f"The record shows epoetin alfa as an active prescription ({drug.label}, requested on "
        f"{drug.authored}) and no haemoglobin test in the month before {ctx.as_of} ({found})."
    )
    says = (
        "The Epogen label says to monitor haemoglobin at least weekly after starting or "
        "adjusting therapy until it is stable, then at least monthly."
    )
    limitations = (
        f"Epoetin alfa counts as active because {ACTIVE}.",
        "A test done elsewhere and not in this record would not be seen.",
    )
    return [
        Assessment(
            rule="epoetin-haemoglobin-monthly",
            status="met",
            summary=summary,
            evidence=tuple(evidence),
            sources=("label-epogen-haemoglobin",),
            limitations=limitations,
        )
    ], [
        Flag(
            rule="epoetin-haemoglobin-monthly",
            kind="medication_test_due",
            title="Epoetin alfa is active; no haemoglobin test in the last month",
            statement=f"{summary} {says} A doctor can say whether this test is due.",
            evidence=tuple(evidence),
            sources=("label-epogen-haemoglobin",),
            limitations=limitations,
        )
    ]


def _ras_nsaid(ctx: Context) -> list[Note]:
    nsaid = active(ctx, NSAIDS)
    if nsaid is None:
        return []
    gfr_tests = [*ctx.tests.get("creatinine", ()), *ctx.tests.get("egfr", ())]
    last = max(gfr_tests, key=lambda t: (t.date, t.node)) if gfr_tests else None
    latest = (
        f"The latest GFR test in the record is from {last.date}."
        if last
        else "No GFR test is recorded."
    )
    notes = []
    for drug, source in RAS_INHIBITORS:
        ras = active(ctx, drug.codes)
        if ras is None:
            continue
        evidence = [_prescribed(ras), _prescribed(nsaid)]
        if last is not None:
            evidence.append(Evidence(node=last.node, label="last GFR test", date=last.date))
        notes.append(
            Note(
                rule="ras-inhibitor-nsaid",
                title=f"{drug.name.capitalize()} with an NSAID",
                statement=(
                    f"The record shows {ras.label} and {nsaid.label} as active prescriptions. "
                    f"The {drug.name} label says that in patients who are elderly, "
                    "volume-depleted or with compromised renal function, NSAIDs taken with it "
                    "may worsen renal function, and to monitor renal function periodically. "
                    f"{latest}"
                ),
                evidence=tuple(evidence),
                sources=(source,),
                limitations=(f"Both count as active because {ACTIVE}.",),
            )
        )
    return notes


def assess_medications(
    ctx: Context, computed: tuple[ComputedEgfr, ...]
) -> tuple[list[Assessment], list[Flag], list[Note]]:
    assessments, flags, notes = _metformin(ctx, computed)
    epo_assessments, epo_flags = _epoetin(ctx)
    return [*assessments, *epo_assessments], [*flags, *epo_flags], [*notes, *_ras_nsaid(ctx)]
