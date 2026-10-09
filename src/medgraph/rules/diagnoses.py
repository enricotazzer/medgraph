"""Which recorded codes count as a matching diagnosis, a kidney-failure state, dialysis, a
pregnancy or a current smoker.

These lists are data matching, not guideline content: they say which codes in a record
correspond to the conditions the rules talk about. medgraph has no SNOMED CT hierarchy, so
every code is listed, with the display it has in the data. A code that is not listed is never
matched. The lists were built from the dev-1000 cohort's own codes (2026-10-08) and must be
extended, with a test, for other data.
"""

SNOMED = "http://snomed.info/sct"
ICD10 = "http://hl7.org/fhir/sid/icd-10"

Code = tuple[str, str]  # (system, code)

CKD_STAGES: dict[Code, int] = {
    (SNOMED, "431855005"): 1,  # Chronic kidney disease stage 1 (disorder)
    (SNOMED, "431856006"): 2,  # Chronic kidney disease stage 2 (disorder)
    (SNOMED, "433144002"): 3,  # Chronic kidney disease stage 3 (disorder)
    (SNOMED, "431857002"): 4,  # Chronic kidney disease stage 4 (disorder)
}
# Kidney failure, dialysis or transplant: the monitoring rules of KDIGO 2024 PP 2.1.1 and KDIGO
# 2012 Rec 1.1.1 (CKD not on dialysis) are not applied to them.
KIDNEY_FAILURE: dict[Code, str] = {
    (SNOMED, "46177005"): "End-stage renal disease (disorder)",
    (SNOMED, "698306007"): "Awaiting transplantation of kidney (situation)",
    (SNOMED, "161665007"): "History of renal transplant (situation)",
    (SNOMED, "213150003"): "Kidney transplant failure and rejection (disorder)",
}
# Any of these means kidney disease is already recorded, so "criteria met, no diagnosis" does
# not apply. Albuminuria and proteinuria due to diabetes are included because the CKD criteria
# include albuminuria.
KIDNEY_DIAGNOSES: dict[Code, str] = {
    **dict.fromkeys(CKD_STAGES, "chronic kidney disease stage"),
    **KIDNEY_FAILURE,
    (SNOMED, "127013003"): "Disorder of kidney due to diabetes mellitus (disorder)",
    (SNOMED, "90781000119102"): "Microalbuminuria due to type 2 diabetes mellitus (disorder)",
    (SNOMED, "157141000119108"): "Proteinuria due to type 2 diabetes mellitus (disorder)",
    (SNOMED, "204949001"): "Renal dysplasia (disorder)",
}
# Dialysis, from procedures. A patient counts as on dialysis when a session is recorded in the
# DIALYSIS_WINDOW_DAYS before the evaluation date. That window is medgraph's definition, not a
# guideline's: maintenance dialysis runs several times a week, so a month without a session is
# taken to mean it has stopped (recovery, transplant, or a record that ends). In dev-1000 every
# dialysis patient's last session was either within 7 days of the evaluation date or more than
# 250 days before it, so any window from 7 to 250 days gives the same result.
DIALYSIS_PROCEDURES: dict[Code, str] = {
    (SNOMED, "265764009"): "Renal dialysis (procedure)",
}
DIALYSIS_WINDOW_DAYS = 30
ANAEMIA_DIAGNOSES: dict[Code, str] = {
    (SNOMED, "271737000"): "Anemia (disorder)",
    (ICD10, "D46.4"): "Refractory anemia, unspecified",
}
PREGNANCY: dict[Code, str] = {
    (SNOMED, "72892002"): "Normal pregnancy (finding)",
    (SNOMED, "79586000"): "Tubal pregnancy (disorder)",
}

SMOKING_STATUS = "http://loinc.org|72166-2"  # Tobacco smoking status
# Values of that observation, as their display text (the graph keeps the value's text, not its
# code). Any other text is treated as unknown: no adjustment, and the result says so.
CURRENT_SMOKER = frozenset({"Smokes tobacco daily (finding)"})
NOT_SMOKING = frozenset({"Never smoked tobacco (finding)", "Ex-smoker (finding)"})


def parse_code(code: str | None) -> Code | None:
    """``"system|code"`` (the graph's form) to ``(system, code)``."""
    if not code or "|" not in code:
        return None
    system, _, value = code.rpartition("|")
    return system, value
