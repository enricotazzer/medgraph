"""medgraph's internal record model: typed, provenance-carrying views of source records.

Every record keeps a :class:`SourceRef` naming the exact source resource it came from, so a
flag or explanation built on it can cite the resource, value and date. Values are kept as
recorded (``Decimal`` preserves the digits). Normalization into canonical units happens in
``medgraph.normalize`` and never overwrites the original.
"""

import datetime as dt
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceRef(_Record):
    """Where a record came from: a source document and one resource inside it."""

    source: str  # "sha256:<hex>" of the source file, or an upload identifier
    resource_type: str
    resource_id: str


class Coding(_Record):
    system: str | None
    code: str
    display: str | None = None


class CodeableConcept(_Record):
    codings: tuple[Coding, ...] = ()
    text: str | None = None

    def code(self, system: str) -> str | None:
        """First code from ``system``, if any."""
        return next((c.code for c in self.codings if c.system == system), None)

    @property
    def label(self) -> str:
        """Best human-readable label: text, else the first display, else the first code."""
        if self.text:
            return self.text
        display = next((c.display for c in self.codings if c.display), None)
        return display or (self.codings[0].code if self.codings else "")


Precision = Literal["year", "month", "day", "instant"]


class Timepoint(_Record):
    """A recorded time.

    ``date`` is the calendar date as written in the source. ``instant`` is set only when the
    source gives a time of day with a UTC offset; ``precision`` says how much was recorded
    (``"2025-03"`` has month precision and ``date`` 2025-03-01).
    """

    date: dt.date
    precision: Precision
    instant: dt.datetime | None = None

    @model_validator(mode="after")
    def _instant_matches_precision(self) -> Self:
        if (self.precision == "instant") != (self.instant is not None):
            raise ValueError("instant is required exactly when precision is 'instant'")
        if self.instant is not None and self.instant.utcoffset() is None:
            raise ValueError("instant must be timezone-aware")
        return self


class Period(_Record):
    start: Timepoint | None = None
    end: Timepoint | None = None


Comparator = Literal["<", "<=", ">=", ">"]


class Quantity(_Record):
    value: Decimal
    unit: str | None = None  # unit as written in the source
    code: str | None = None  # UCUM code, when the source provides one
    comparator: Comparator | None = None


class ReferenceRange(_Record):
    low: Quantity | None = None
    high: Quantity | None = None
    text: str | None = None


class Patient(_Record):
    """``gender`` is FHIR administrative gender. Rules that need biological sex use it as a
    proxy and must say so."""

    id: str
    source: SourceRef
    gender: Literal["male", "female", "other", "unknown"]
    birth_date: Timepoint | None = None
    deceased: Timepoint | None = None


class Encounter(_Record):
    id: str
    source: SourceRef
    status: str | None = None
    class_code: str | None = None
    type: CodeableConcept | None = None
    period: Period = Period()


class Condition(_Record):
    id: str
    source: SourceRef
    code: CodeableConcept
    clinical_status: str | None = None
    verification_status: str | None = None
    categories: tuple[str, ...] = ()
    onset: Timepoint | None = None
    abatement: Timepoint | None = None
    recorded: Timepoint | None = None
    encounter_id: str | None = None


class MedicationRequest(_Record):
    id: str
    source: SourceRef
    medication: CodeableConcept
    status: str | None = None
    intent: str | None = None
    authored_on: Timepoint | None = None
    dosage_text: str | None = None
    as_needed: bool | None = None
    encounter_id: str | None = None


class ObservationComponent(_Record):
    code: CodeableConcept
    value_quantity: Quantity | None = None
    value_concept: CodeableConcept | None = None
    value_text: str | None = None


class Observation(_Record):
    id: str
    source: SourceRef
    code: CodeableConcept
    status: str | None = None
    categories: tuple[str, ...] = ()
    effective: Timepoint | None = None
    issued: Timepoint | None = None
    value_quantity: Quantity | None = None
    value_concept: CodeableConcept | None = None
    value_text: str | None = None
    value_boolean: bool | None = None
    components: tuple[ObservationComponent, ...] = ()
    reference_ranges: tuple[ReferenceRange, ...] = ()
    interpretations: tuple[str, ...] = ()
    encounter_id: str | None = None


class Procedure(_Record):
    id: str
    source: SourceRef
    code: CodeableConcept
    status: str | None = None
    performed: Period = Period()
    encounter_id: str | None = None


class Note(_Record):
    """Free text attached to the record. Identical text in the same encounter is one note,
    however many resources carry it."""

    id: str
    sources: tuple[SourceRef, ...]
    text: str
    kind: CodeableConcept | None = None
    date: Timepoint | None = None
    encounter_id: str | None = None


class DiagnosticReport(_Record):
    """A report as issued: lab panels group their observations through ``result_ids``."""

    id: str
    source: SourceRef
    code: CodeableConcept
    status: str | None = None
    categories: tuple[str, ...] = ()
    effective: Timepoint | None = None
    issued: Timepoint | None = None
    result_ids: tuple[str, ...] = ()
    note_id: str | None = None
    encounter_id: str | None = None


IssueCode = Literal[
    "invalid_resource",
    "invalid_time",
    "unresolved_reference",
    "unexpected_reference_type",
    "event_after_death",
    "unsupported_value",
    "unsupported_attachment",
]
Severity = Literal["info", "warning", "error"]


class IngestIssue(_Record):
    """A data problem found while reading a source. Issues are reported, not fatal."""

    code: IssueCode
    severity: Severity
    message: str
    source: SourceRef | None = None


class PatientRecord(_Record):
    """One patient's record as read from one source document."""

    source: str
    patient: Patient
    encounters: tuple[Encounter, ...] = ()
    conditions: tuple[Condition, ...] = ()
    medication_requests: tuple[MedicationRequest, ...] = ()
    observations: tuple[Observation, ...] = ()
    procedures: tuple[Procedure, ...] = ()
    diagnostic_reports: tuple[DiagnosticReport, ...] = ()
    notes: tuple[Note, ...] = ()
    issues: tuple[IngestIssue, ...] = ()
    skipped_resources: dict[str, int] = {}
