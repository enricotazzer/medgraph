"""The sources every rule cites: where each threshold, equation and interval comes from.

Each entry names the document and the place in it, and quotes the passage. Every quote is
checked against the stored text of the document named in ``stored`` (the knowledge store,
``configs/knowledge/documents.yaml``; ``make knowledge``, then
``pytest --run-knowledge``):
- each fragment between "[...]" occurs verbatim, up to case, spacing, punctuation and line
  breaks (``medgraph.rag.quotes``);
- each is found on a page the locator names.

``table`` holds what a table says row by row, where the extracted text reads it column by
column (a list of row labels, then a list of values). It is a transcription, not a quote; the
check is weaker: each row's words and numbers occur on the page of the quote.

For CKD-EPI 2021 and MDRD the stored text is NIDDK's transcription of the published
equations, not the papers themselves.

A rule that uses a number must cite the entry it comes from; a test enforces this.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    id: str
    document: str
    locator: str  # where in the document
    quote: str  # verbatim fragments separated by "[...]"
    stored: str  # knowledge-store document ID, or "label:<set id>@v<version>"
    table: str = ""  # a table transcribed row by row (rows separated by ";"), if any

    @property
    def citation(self) -> str:
        return f"{self.document}, {self.locator}"


KDIGO_2024 = (
    "KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management of Chronic Kidney "
    "Disease. Kidney Int 2024;105(4S):S117-S314, doi:10.1016/j.kint.2023.10.018"
)
KDIGO_2012_ANAEMIA = (
    "KDIGO Clinical Practice Guideline for Anemia in Chronic Kidney Disease. "
    "Kidney Int Suppl 2012;2(4)"
)
WHO_2024 = (
    "WHO. Guideline on haemoglobin cutoffs to define anaemia in individuals and populations. "
    "Geneva: World Health Organization; 2024. ISBN 978-92-4-008854-2"
)
CKD_EPI = (
    "Inker LA et al. New creatinine- and cystatin C-based equations to estimate GFR without "
    "race. N Engl J Med 2021;385:1737-49, doi:10.1056/NEJMoa2102953"
)


def _label(
    id: str,
    name: str,
    labeler: str,
    setid: str,
    version: int,
    effective: str,
    section: str,
    quote: str,
) -> Source:
    """A statement from a DailyMed drug label, pinned to the version in the cohort's lock."""
    return Source(
        id=id,
        document=(
            f"{name}, prescribing information ({labeler}). DailyMed set ID {setid}, "
            f"version {version}, effective {effective}"
        ),
        locator=f"section {section}",
        quote=quote,
        stored=f"label:{setid}@v{version}",
    )


METFORMIN_LABEL = (
    "Metformin Hydrochloride",
    "Laurus Labs Limited",
    "c3dfa8a1-d10a-4a1a-8eba-5f4e2a5a2949",
    8,
    "2026-09-25",
    "2 DOSAGE AND ADMINISTRATION > 2.4 Recommended Dosage in Patients with Renal Impairment",
)
# Drug-label statements the medication rules cite (``rules/medications.py``). The RAS-inhibitor
# entries are the same warning in each ACE inhibitor's or ARB's label: NSAIDs with the drug.
LABEL_SOURCES = (
    _label(
        "label-metformin-egfr-30",
        *METFORMIN_LABEL,
        (
            "Metformin hydrochloride tablets/Metformin hydrochloride extended-release tablets "
            "are contraindicated in patients with an estimated glomerular filtration rate "
            "(eGFR) below 30 mL/minute/1.73 m^2. [...] Discontinue metformin hydrochloride "
            "tablets/metformin hydrochloride extended-release tablets if the patient\u2019s eGFR "
            "later falls below 30 mL/minute/1.73 m^2"
        ),
    ),
    _label(
        "label-metformin-egfr-45",
        *METFORMIN_LABEL,
        (
            "Initiation of metformin hydrochloride tablets/metformin hydrochloride "
            "extended-release tablets in patients with an eGFR between 30 to 45 "
            "mL/minute/1.73 m^2 is not recommended. In patients taking metformin hydrochloride "
            "tablets/metformin hydrochloride extended-release tablets whose eGFR later falls "
            "below 45 mL/min/1.73 m^2, assess the benefit risk of continuing therapy."
        ),
    ),
    _label(
        "label-metformin-renal-assessment",
        *METFORMIN_LABEL,
        (
            "Assess renal function prior to initiation of metformin hydrochloride "
            "tablets/metformin hydrochloride extended-release tablets and periodically "
            "thereafter."
        ),
    ),
    _label(
        "label-epogen-haemoglobin",
        "EPOGEN (epoetin alfa)",
        "Amgen, Inc",
        "1f2d0b28-9cc5-4523-80b8-637fdaf3f7a5",
        137,
        "2026-06-23",
        "2 DOSAGE AND ADMINISTRATION > 2.2 Patients with Chronic Kidney Disease",
        (
            "For all patients with CKD: When initiating or adjusting therapy, monitor "
            "hemoglobin levels at least weekly until stable, then monitor at least monthly."
        ),
    ),
    _label(
        "label-entresto-nsaid",
        "ENTRESTO (sacubitril and valsartan)",
        "Novartis Pharmaceuticals Corporation",
        "000dc81d-ab91-450c-8eae-8eb74e72296f",
        25,
        "2026-07-06",
        (
            "7 DRUG INTERACTIONS > 7.3 Nonsteroidal Anti-Inflammatory Drugs "
            "(NSAIDs) Including Selective Cyclooxygenase-2 Inhibitors (COX-2 "
            "Inhibitors)"
        ),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, concomitant "
            "use of NSAIDs, including COX-2 inhibitors, with ENTRESTO may result "
            "in worsening of renal function, including possible acute renal "
            "failure. [...] Monitor renal function periodically."
        ),
    ),
    _label(
        "label-zestoretic-nsaid",
        "Zestoretic (lisinopril and hydrochlorothiazide)",
        "Almatica Pharma Inc.",
        "0d3a966f-f937-05a8-a90f-5aa52ebbd613",
        16,
        "2025-03-31",
        ("PRECAUTIONS > Drug Interactions > Lisinopril"),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, co-"
            "administration of NSAIDs, including selective COX-2 inhibitors, with"
            " ACE inhibitors, including lisinopril, may result in deterioration "
            "of renal function, including possible acute renal failure. [...] "
            "Monitor renal function periodically in patients receiving lisinopril"
            " and NSAID therapy."
        ),
    ),
    _label(
        "label-ramipril-remedyrepack-nsaid",
        "Ramipril",
        "REMEDYREPACK INC.",
        "394a49a8-81bb-43ba-8d6a-fa6fa5030587",
        7,
        "2026-09-28",
        (
            "7 DRUG INTERACTIONS > 7.6 Non-Steroidal Anti-Inflammatory Agents "
            "including Selective Cyclooxygenase-2 Inhibitors (COX-2 Inhibitors)"
        ),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, "
            "coadministration of NSAIDs, including selective COX-2 inhibitors, "
            "with ACE inhibitors, including ramipril, may result in deterioration"
            " of renal function, including possible acute renal failure. [...] "
            "Monitor renal function periodically in patients receiving ramipril "
            "and NSAID therapy."
        ),
    ),
    _label(
        "label-vasotec-nsaid",
        "Vasotec (enalapril maleate)",
        "Bausch Health US LLC",
        "39631f1f-5d19-43c1-b504-bf56d991ed97",
        15,
        "2026-01-23",
        ("PRECAUTIONS"),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, "
            "coadministration of NSAIDs, including selective COX-2 inhibitors, "
            "with ACE inhibitors, including enalapril, may result in "
            "deterioration of renal function, including possible acute renal "
            "failure. [...] Monitor renal function periodically in patients "
            "receiving enalapril and NSAID therapy."
        ),
    ),
    _label(
        "label-micardis-nsaid",
        "Micardis (telmisartan)",
        "Physicians Total Care, Inc.",
        "3bb7bc69-7752-4c16-9c60-e61eb5355a4d",
        5,
        "2012-02-14",
        ("7 DRUG INTERACTIONS"),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, co-"
            "administration of NSAIDs, including selective COX-2 inhibitors, with"
            " angiotensin II receptor antagonists, including telmisartan, may "
            "result in deterioration of renal function, including possible acute "
            "renal failure. [...] Monitor renal function periodically in patients"
            " receiving telmisartan and NSAID therapy."
        ),
    ),
    _label(
        "label-hyzaar-nsaid",
        "HYZAAR (losartan potassium and hydrochlorothiazide)",
        "Organon LLC",
        "4116ccde-2e23-45f4-b12f-6337df877744",
        10,
        "2025-11-26",
        (
            "7 DRUG INTERACTIONS > 7.3 Non-Steroidal Anti-Inflammatory Agents "
            "Including Selective Cyclooxygenase-2 Inhibitors"
        ),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, "
            "coadministration of NSAIDs, including selective COX-2 inhibitors, "
            "with angiotensin II receptor antagonists (including losartan) may "
            "result in deterioration of renal function, including possible acute "
            "renal failure. [...] Monitor renal function periodically in patients"
            " receiving losartan and NSAID therapy."
        ),
    ),
    _label(
        "label-lotrel-nsaid",
        "Lotrel (amlodipine besylate and benazepril hydrochloride)",
        "Physicians Total Care, Inc.",
        "4653cabc-f249-4b4e-95b6-5fb4ddc0ad5d",
        3,
        "2012-03-02",
        ("7 DRUG INTERACTIONS > 7.1 Drug/Drug interactions"),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, co-"
            "administration of NSAIDs, including selective COX-2 inhibitors, with"
            " ACE inhibitors, including benazepril, may result in deterioration "
            "of renal function, including possible acute renal failure. [...] "
            "Monitor renal function periodically in patients receiving benazepril"
            " and NSAID therapy."
        ),
    ),
    _label(
        "label-diovan-nsaid",
        "Diovan (valsartan)",
        "Novartis Pharmaceuticals Corporation",
        "5ddba454-f3e6-43c2-a7a6-58365d297213",
        32,
        "2026-08-11",
        (
            "7 DRUG INTERACTIONS > 7.2 Non-Steroidal Anti-Inflammatory Agents "
            "Including Selective Cyclooxygenase-2 Inhibitors (COX-2 Inhibitors)"
        ),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, "
            "coadministration of NSAIDs, including selective COX-2 inhibitors, "
            "with angiotensin II receptor antagonists, including valsartan, may "
            "result in deterioration of renal function, including possible acute "
            "renal failure. [...] Monitor renal function periodically in patients"
            " receiving valsartan and NSAID therapy."
        ),
    ),
    _label(
        "label-zestril-nsaid",
        "Zestril (lisinopril)",
        "Upsher-Smith Laboratories, LLC",
        "838c2d78-d2d8-4981-9ec9-e50ef9e1a5d8",
        2,
        "2025-01-02",
        (
            "7 DRUG INTERACTIONS > 7.3 Non-Steroidal Anti-Inflammatory Agents "
            "Including Selective Cyclooxygenase-2 Inhibitors (COX-2 Inhibitors)"
        ),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, "
            "coadministration of NSAIDs, including selective COX-2 inhibitors, "
            "with ACE inhibitors, including lisinopril, may result in "
            "deterioration of renal function, including possible acute renal "
            "failure. [...] Monitor renal function periodically in patients "
            "receiving lisinopril and NSAID therapy."
        ),
    ),
    _label(
        "label-cozaar-nsaid",
        "COZAAR (losartan potassium)",
        "Organon LLC",
        "9949448f-c3b9-44ee-94ed-c1aca8c90f39",
        9,
        "2026-01-20",
        (
            "7 DRUG INTERACTIONS > 7.3 Non-Steroidal Anti-Inflammatory Drugs "
            "(NSAIDs) Including Selective Cyclooxygenase-2 Inhibitors (COX-2 "
            "Inhibitors)"
        ),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, "
            "coadministration of NSAIDs, including selective COX-2 inhibitors, "
            "with angiotensin II receptor antagonists (including losartan) may "
            "result in deterioration of renal function, including possible acute "
            "renal failure. [...] Monitor renal function periodically in patients"
            " receiving losartan and NSAID therapy."
        ),
    ),
    _label(
        "label-ramipril-aurobindo-nsaid",
        "Ramipril",
        "Aurobindo Pharma Limited",
        "d6d57158-e8f9-4c91-8317-0374e0c87d33",
        19,
        "2026-09-18",
        (
            "7 DRUG INTERACTIONS > 7.6 Non-Steroidal Anti-Inflammatory Agents "
            "including Selective Cyclooxygenase-2 Inhibitors (COX-2 Inhibitors)"
        ),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, "
            "coadministration of NSAIDs, including selective COX-2 inhibitors, "
            "with ACE inhibitors, including ramipril, may result in deterioration"
            " of renal function, including possible acute renal failure. [...] "
            "Monitor renal function periodically in patients receiving ramipril "
            "and NSAID therapy."
        ),
    ),
    _label(
        "label-diovan-hct-nsaid",
        "Diovan HCT (valsartan and hydrochlorothiazide)",
        "Novartis Pharmaceuticals Corporation",
        "d76a0419-05ee-437e-884c-65807aea9569",
        34,
        "2026-02-13",
        ("7 DRUG INTERACTIONS"),
        (
            "In patients who are elderly, volume-depleted (including those on "
            "diuretic therapy), or with compromised renal function, "
            "coadministration of NSAIDs, including selective COX-2 inhibitors, "
            "with angiotensin II receptor antagonists, including valsartan, may "
            "result in deterioration of renal function, including possible acute "
            "renal failure. [...] Monitor renal function periodically in patients"
            " receiving valsartan and NSAID therapy."
        ),
    ),
)


SOURCES: dict[str, Source] = {
    s.id: s
    for s in (
        Source(
            id="ckd-epi-2021",
            document=CKD_EPI,
            locator="2021 CKD-EPI creatinine equation, as published by NIDDK (eGFR Equations "
            "for Adults)",
            quote=(
                "Estimate glomerular filtration rate (GFR) in individuals ages 18 and older "
                "[...] eGFR = 142 \u00d7 min(SCr/\u03ba,1)^\u03b1 "
                "\u00d7 max(SCr/\u03ba,1)^-1.200 \u00d7 "
                "0.9938^Age \u00d7 1.012 [if female] [...] SCr = standardized serum creatinine in "
                "mg/dL \u03ba = 0.7 (females) or 0.9 (males) \u03b1 = -0.241 (females) or -0.302 "
                "(males)"
            ),
            stored="niddk-egfr-adults",
        ),
        Source(
            id="mdrd-2006",
            document=(
                "Levey AS et al. Using standardized serum creatinine values in the Modification "
                "of Diet in Renal Disease study equation for estimating glomerular filtration "
                "rate. Ann Intern Med 2006;145(4):247-54, doi:10.7326/0003-4819-145-4-"
                "200608150-00004"
            ),
            locator="IDMS-traceable MDRD Study equation, conventional units, as published by "
            "NIDDK (Previous eGFR Equations for Reference)",
            quote=(
                "eGFR = 175 \u00d7 (SCr)^\u20131.154 \u00d7 (Age)^\u20130.203 "
                "\u00d7 (0.742 if female) \u00d7 "
                "(1.212 if African American) [...] SCr = standardized serum creatinine in mg/dL"
            ),
            stored="niddk-egfr-previous",
        ),
        Source(
            id="kdigo-2024-categories",
            document=KDIGO_2024,
            locator="CKD nomenclature, figure on p. S126",
            quote=(
                "G1 \u226590 G2 60\u201389 G3a 45\u201359 G3b 30\u201344 G4 15\u201329 "
                "G5 Kidney failure <15 "
                "[...] Severely decreased Moderately to severely decreased Mildly to moderately "
                "decreased Mildly decreased Normal or high [...] Normal to mildly increased "
                "Moderately increased Severely increased <30 mg/g <3 mg/mmol 30\u2013300 mg/g "
                "3\u201330 mg/mmol >300 mg/g >30 mg/mmol"
            ),
            stored="kdigo-2024-ckd",
            table=(
                "G1 Normal or high \u226590; G2 Mildly decreased 60\u201389; G3a Mildly to "
                "moderately decreased 45\u201359; G3b Moderately to severely decreased 30\u201344; "
                "G4 Severely decreased 15\u201329; G5 Kidney failure <15; A1 Normal to mildly "
                "increased <30 mg/g; A2 Moderately increased 30\u2013300 mg/g; A3 Severely "
                "increased >300 mg/g"
            ),
        ),
        Source(
            id="kdigo-2024-criteria",
            document=KDIGO_2024,
            locator="Figure 3, p. S139; Practice Points 1.1.3.1 and 1.1.3.2, p. S149",
            quote=(
                "GFR <60 ml/min per 1.73 m2 or ACR >=30 mg/g [3 mg/mmol] [...] Proof of "
                "chronicity (duration of a minimum of 3 months) can be established by: (i) review "
                "of past measurements/estimations of GFR; (ii) review of past measurements of "
                "albuminuria [...] Do not assume chronicity based upon a single abnormal level "
                "for eGFR and ACR, as the finding could be the result of a recent acute kidney "
                "injury (AKI) event or acute kidney disease (AKD)."
            ),
            stored="kdigo-2024-ckd",
        ),
        Source(
            id="kdigo-2024-monitoring",
            document=KDIGO_2024,
            locator="Practice Point 2.1.1, pp. S155 and S196",
            quote=(
                "Assess albuminuria in adults, or albuminuria/proteinuria in children, and GFR at "
                "least annually in people with CKD."
            ),
            stored="kdigo-2024-ckd",
        ),
        Source(
            id="kdigo-2012-anaemia-testing",
            document=KDIGO_2012_ANAEMIA,
            locator="Chapter 1 (pp. 288-291), Recommendation 1.1.1 (Not Graded)",
            quote=(
                "For CKD patients without anemia [...], measure Hb concentration when clinically "
                "indicated and (Not Graded): at least annually in patients with CKD 3; at least "
                "twice per year in patients with CKD 4-5ND; at least every 3 months in patients "
                "with CKD 5HD and CKD 5PD"
            ),
            stored="kdigo-2012-anaemia",
        ),
        Source(
            id="kdigo-2012-anaemia-intervals",
            document=KDIGO_2012_ANAEMIA,
            locator="Recommendations 1.1.2 (p. 288) and 3.12.1-3.12.3 (p. 306) (Not Graded)",
            quote=(
                "For CKD patients with anemia not being treated with an ESA, measure Hb "
                "concentration when clinically indicated and (Not Graded): at least every 3 "
                "months in patients with CKD 3-5ND and CKD 5PD [...] During the initiation phase "
                "of ESA therapy, measure Hb concentration at least monthly. [...] For CKD ND "
                "patients, during the maintenance phase of ESA therapy measure Hb concentration "
                "at least every 3 months"
            ),
            stored="kdigo-2012-anaemia",
        ),
        Source(
            id="who-2024-cutoffs",
            document=WHO_2024,
            locator="Normative statement 1.a, Table 2: haemoglobin cutoffs in g/L (executive "
            "summary, p. xi)",
            quote=(
                "Children, 6\u201323 months <105; Children, 24\u201359 months <110; "
                "Children, 5\u201311 years <115; Children, 12\u201314 years, nonpregnant girls "
                "<120; Children, 12\u201314 years, boys <120; "
                "Adults, 15\u201365 years, nonpregnant women <120; "
                "Adults, 15\u201365 years, men <130; Pregnancy First trimester Second trimester "
                "Third trimester <110 <105 <110"
            ),
            stored="who-2024-haemoglobin",
            table="Pregnancy, first trimester <110; second trimester <105; third trimester <110",
        ),
        Source(
            id="who-2024-smoking",
            document=WHO_2024,
            locator="Normative statement 2.a.2 and Table 5: haemoglobin adjustment in g/L (p. xiv)",
            quote=(
                "Haemoglobin concentrations should be adjusted to diagnose anaemia in individuals "
                "and populations to account for the effect of smoking on haemoglobin "
                "concentrations. [...] Smoker, quantity unknown 3 [...] The adjustment "
                "consists in the corresponding value in the table added to the haemoglobin "
                "cutoff defining anaemia"
            ),
            stored="who-2024-haemoglobin",
        ),
        Source(
            id="who-2024-elevation",
            document=WHO_2024,
            locator="Normative statement 2.a.1 and Table 4: elevation in metres above sea "
            "level, adjustment in g/L (p. xiii)",
            quote=(
                "Adjustments of haemoglobin concentrations are recommended to diagnose anaemia in "
                "individuals and populations to account for the effect of elevation of place of "
                "residency on haemoglobin concentrations. [...] 1\u2013499 0"
            ),
            stored="who-2024-haemoglobin",
        ),
        Source(
            id="who-2024-age-gap",
            document=WHO_2024,
            locator="Research gaps, normative statement 1.a (p. 11)",
            quote=(
                "Future studies should prospectively define 5th percentiles in healthy "
                "populations across all geographical regions, including infants, and men and "
                "women aged >65 years."
            ),
            stored="who-2024-haemoglobin",
        ),
        *LABEL_SOURCES,
    )
}


def cite(source_id: str) -> Source:
    """The source with this ID; a KeyError means a rule cites something unregistered."""
    return SOURCES[source_id]
