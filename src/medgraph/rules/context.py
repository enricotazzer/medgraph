"""What the rules read from a patient's graph, as of an evaluation date.

Only data dated on or before the evaluation date is used. Lab values come from the timeline's
series, so they are usable values only (status ``ok`` and dated), each measurement once.
Recorded tests of any status are kept separately: a test counts as done for follow-up even if
its value could not be used. Medication requests are kept if authored on or before the date,
procedures if performed on or before it.
"""

import calendar
import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from medgraph.graph.schema import MEASUREMENTS, PatientGraph, graph_info, node_data
from medgraph.graph.timeline import build_timeline
from medgraph.records import Comparator, SourceRef
from medgraph.rules.diagnoses import SMOKING_STATUS, Code, parse_code

Sex = Literal["female", "male"]


@dataclass(frozen=True)
class Value:
    node: str
    label: str
    code: str | None  # "system|code" of the source record, e.g. the LOINC code
    date: dt.date
    value: Decimal  # canonical unit of its analyte
    comparator: Comparator | None
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class Test:
    """A recorded test of an analyte, usable or not."""

    node: str
    date: dt.date
    usable: bool


@dataclass(frozen=True)
class Diagnosis:
    node: str
    code: Code
    label: str
    onset: dt.date | None
    abatement: dt.date | None

    def recorded_by(self, day: dt.date) -> bool:
        return self.onset is None or self.onset <= day

    def ongoing_on(self, day: dt.date) -> bool:
        """Recorded by ``day`` and not abated before it."""
        return self.recorded_by(day) and (self.abatement is None or self.abatement >= day)

    def spans(self, day: dt.date) -> bool:
        """Known to be in progress on ``day`` (needs an onset date)."""
        return (
            self.onset is not None
            and self.onset <= day
            and (self.abatement is None or self.abatement >= day)
        )


@dataclass(frozen=True)
class Medication:
    """One medication request."""

    node: str
    code: Code | None  # (RxNorm, RxCUI) in Synthea
    label: str
    authored: dt.date
    status: str | None  # FHIR MedicationRequest.status, e.g. "active" or "stopped"


@dataclass(frozen=True)
class Procedure:
    node: str
    code: Code | None
    label: str
    date: dt.date  # when it was performed (its start)


@dataclass(frozen=True)
class Context:
    patient_id: str
    as_of: dt.date
    gender: str  # FHIR administrative gender
    birth_date: dt.date | None
    deceased: dt.date | None
    series: dict[str, tuple[Value, ...]] = field(default_factory=dict)
    tests: dict[str, tuple[Test, ...]] = field(default_factory=dict)
    diagnoses: tuple[Diagnosis, ...] = ()
    smoking: tuple[tuple[dt.date, str], ...] = ()  # (date, status text), in time order
    medications: tuple[Medication, ...] = ()  # in time order
    procedures: tuple[Procedure, ...] = ()  # in time order

    @property
    def sex(self) -> Sex | None:
        """Administrative gender as a proxy for sex; ``None`` when other or unknown."""
        return self.gender if self.gender in ("female", "male") else None  # type: ignore[return-value]

    def age_years(self, day: dt.date) -> int | None:
        return completed_years(self.birth_date, day) if self.birth_date else None

    def age_months(self, day: dt.date) -> int | None:
        return completed_months(self.birth_date, day) if self.birth_date else None


def completed_years(birth: dt.date, day: dt.date) -> int:
    return day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))


def completed_months(birth: dt.date, day: dt.date) -> int:
    return (day.year - birth.year) * 12 + day.month - birth.month - (day.day < birth.day)


def months_before(day: dt.date, months: int) -> dt.date:
    """The same day ``months`` months earlier, clamped to the end of a shorter month."""
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def context_from_graph(graph: PatientGraph, as_of: dt.date) -> Context:
    info = graph_info(graph)
    patient = info.patient
    series: dict[str, tuple[Value, ...]] = {}
    for s in build_timeline(graph).series:
        values = tuple(
            Value(
                node=p.node,
                label=node_data(graph, p.node).label,
                code=node_data(graph, p.node).code,
                date=p.time.date,
                value=p.value,
                comparator=p.comparator,
                sources=p.sources,
            )
            for p in s.points
            if p.time.date <= as_of
        )
        if values:
            series[s.analyte] = values
    tests: dict[str, list[Test]] = {}
    diagnoses = []
    smoking = []
    medications = []
    procedures = []
    for node in graph.nodes:
        d = node_data(graph, node)
        day = d.start.date if d.start else None
        if d.kind in MEASUREMENTS and d.analyte and day is not None and day <= as_of:
            tests.setdefault(d.analyte, []).append(Test(node, day, bool(d.usable)))
        elif d.kind == "condition" and (code := parse_code(d.code)) is not None:
            diagnoses.append(
                Diagnosis(
                    node=node,
                    code=code,
                    label=d.label,
                    onset=day,
                    abatement=d.end.date if d.end else None,
                )
            )
        elif d.kind == "observation" and d.code == SMOKING_STATUS and day and day <= as_of:
            smoking.append((day, d.text or ""))
        elif d.kind == "medication_request" and day is not None and day <= as_of:
            medications.append(Medication(node, parse_code(d.code), d.label, day, d.status))
        elif d.kind == "procedure" and day is not None and day <= as_of:
            procedures.append(Procedure(node, parse_code(d.code), d.label, day))
    return Context(
        patient_id=patient.id,
        as_of=as_of,
        gender=patient.gender,
        birth_date=patient.birth_date.date if patient.birth_date else None,
        deceased=patient.deceased.date if patient.deceased else None,
        series=series,
        tests={a: tuple(sorted(t, key=lambda x: (x.date, x.node))) for a, t in tests.items()},
        diagnoses=tuple(diagnoses),
        smoking=tuple(sorted(smoking)),
        medications=tuple(sorted(medications, key=lambda m: (m.authored, m.node))),
        procedures=tuple(sorted(procedures, key=lambda p: (p.date, p.node))),
    )
