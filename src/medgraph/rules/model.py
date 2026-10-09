"""What the rules return: assessments of criteria and the follow-up flags raised from them.

Everything here is JSON-serializable, so a result can be stored next to the graph and shown in
the viewer. Every assessment and flag names the rule and version that produced it, the
evidence it rests on (graph nodes, values, dates), the sources it cites
(:mod:`medgraph.rules.sources`) and its limitations. A flag says that criteria are met or that
a test is due, and suggests a conversation with a doctor; it never states a diagnosis.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict

from medgraph.records import SourceRef

# rules-v2 (Phase 4): medication rules from drug labels, and dialysis recognized from procedures.
RULESET = "rules-v2"

Status = Literal["met", "not_met", "not_assessable"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Evidence(_Model):
    node: str  # graph node the value comes from
    label: str
    date: dt.date
    value: str | None = None  # as used by the rule, with its unit
    note: str | None = None  # how it was used, e.g. "first abnormal value"
    sources: tuple[SourceRef, ...] = ()


class Assessment(_Model):
    """One criterion checked for one patient."""

    rule: str
    status: Status
    summary: str  # one sentence: what was found
    reason: str | None = None  # why not assessable, or what is missing
    category: str | None = None  # e.g. "G3a A2"
    evidence: tuple[Evidence, ...] = ()
    sources: tuple[str, ...] = ()  # rules.sources IDs
    limitations: tuple[str, ...] = ()


FlagKind = Literal[
    "criteria_met_no_diagnosis",
    "follow_up_due",
    "medication_threshold",  # an active drug, and a value past a threshold its label states
    "medication_test_due",  # an active drug, and no test in an interval its label states
]


class Flag(_Model):
    rule: str
    kind: FlagKind
    title: str
    statement: str  # neutral wording; ends by suggesting a conversation with a doctor
    evidence: tuple[Evidence, ...] = ()
    sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class Note(_Model):
    """Something a source says about a situation the record shows, without a threshold or an
    interval to check. A note is not a flag: it is shown, never counted as one."""

    rule: str
    title: str
    statement: str
    evidence: tuple[Evidence, ...] = ()
    sources: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class ComputedEgfr(_Model):
    """eGFR computed by medgraph from one usable creatinine value (CKD-EPI 2021)."""

    node: str  # the creatinine value's node
    date: dt.date
    creatinine: str  # mg/dL, as used
    age: int  # completed years on the sample date
    sex: Literal["female", "male"]  # administrative gender, used as a proxy
    value: str  # mL/min/1.73 m2, unrounded
    whole: int  # rounded to a whole number; categories use this


class PatientRules(_Model):
    patient_id: str
    ruleset: str = RULESET
    as_of: dt.date
    evaluated: bool  # False for a deceased patient: no flags
    note: str | None = None
    computed_egfr: tuple[ComputedEgfr, ...] = ()
    assessments: tuple[Assessment, ...] = ()
    flags: tuple[Flag, ...] = ()
    notes: tuple[Note, ...] = ()
