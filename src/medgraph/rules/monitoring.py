"""Which analytes a cited guideline uses to assess an in-scope condition.

These links become the graph's ``monitored_by`` edges (condition to analyte). Each one names
the guideline passage it rests on and quotes it: fragments separated by "[...]", each checked
against the stored text of the document named in ``stored``, on the pages the locator names
(``medgraph.rag.quotes``; ``pytest --run-knowledge``). How often a test is due, and whether it
is overdue, is a rule for Phase 3; nothing here raises a flag.

Condition codes are listed one by one, because medgraph has no SNOMED CT hierarchy: a code
that is not listed, such as a more specific anaemia, gets no link. Kidney failure on dialysis
or after a transplant (``46177005`` end-stage renal disease, ``161665007`` history of renal
transplant) and diabetic kidney disease codes are deliberately not linked yet. Whether the
same tests apply to them is settled in Phase 3, with its own citation.
"""

from dataclasses import dataclass

SNOMED = "http://snomed.info/sct"

KDIGO_2024 = (
    "KDIGO 2024 Clinical Practice Guideline for the Evaluation and Management of Chronic Kidney "
    "Disease, executive summary: Kidney Int 2024;105:684-701, doi:10.1016/j.kint.2023.10.016"
)
WHO_2024 = (
    "WHO. Guideline on haemoglobin cutoffs to define anaemia in individuals and populations. "
    "Geneva: World Health Organization; 2024. ISBN 978-92-4-008854-2"
)


@dataclass(frozen=True)
class GuidelineLink:
    analyte: str  # registry key in medgraph.normalize.analytes
    source: str
    locator: str  # where in the source
    quote: str  # verbatim fragments separated by "[...]"
    stored: str  # the knowledge-store document the quote is checked against

    @property
    def citation(self) -> str:
        return f"{self.source}, {self.locator}"


CKD_GFR = GuidelineLink(
    analyte="egfr",
    source=KDIGO_2024,
    locator="Practice Point 2.1.1, p. 688",
    quote=(
        "Assess albuminuria in adults, or albuminuria/proteinuria in children, and GFR at least "
        "annually in people with CKD."
    ),
    stored="kdigo-2024-ckd-summary",
)
CKD_ACR = GuidelineLink(
    analyte="urine_acr",
    source=KDIGO_2024,
    locator="Practice Points 2.1.1 and 1.3.1.1, p. 688",
    quote=(
        "Assess albuminuria in adults, or albuminuria/proteinuria in children, and GFR at least "
        "annually in people with CKD. [...] Use the following measurements for initial testing "
        "of albuminuria (in descending order of preference). [...] (i) urine ACR"
    ),
    stored="kdigo-2024-ckd-summary",
)
CKD_CREATININE = GuidelineLink(
    analyte="creatinine",
    source=KDIGO_2024,
    locator="Practice Point 1.2.2.1, p. 687",
    quote="Use serum creatinine (SCr) and an estimating equation for initial assessment of GFR.",
    stored="kdigo-2024-ckd-summary",
)
ANAEMIA_HEMOGLOBIN = GuidelineLink(
    analyte="hemoglobin",
    source=WHO_2024,
    locator="Objectives, p. 3",
    quote=(
        "The objective of this guideline is to provide updated, locally adaptable, clear, "
        "evidence-informed normative statements on the use of haemoglobin concentrations to "
        "assess anaemia"
    ),
    stored="who-2024-haemoglobin",
)

CKD_LINKS = (CKD_GFR, CKD_ACR, CKD_CREATININE)
# SNOMED CT code -> (display, links). Displays as Synthea writes them.
MONITORING: dict[str, tuple[str, tuple[GuidelineLink, ...]]] = {
    "431855005": ("Chronic kidney disease stage 1 (disorder)", CKD_LINKS),
    "431856006": ("Chronic kidney disease stage 2 (disorder)", CKD_LINKS),
    "433144002": ("Chronic kidney disease stage 3 (disorder)", CKD_LINKS),
    "431857002": ("Chronic kidney disease stage 4 (disorder)", CKD_LINKS),
    "271737000": ("Anemia (disorder)", (ANAEMIA_HEMOGLOBIN,)),
}


def monitoring_links(snomed_code: str | None) -> tuple[GuidelineLink, ...]:
    """The cited analyte links for a condition code; none for codes not in the table."""
    entry = MONITORING.get(snomed_code) if snomed_code else None
    return entry[1] if entry else ()
