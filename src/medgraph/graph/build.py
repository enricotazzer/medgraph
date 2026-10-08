"""Build one patient's graph from their FHIR record and the lab reports attached to it.

Deterministic: the same record and reports give the same nodes and edges in the same order.
Every record becomes a node, including values that can't be used. A measurement is
``usable`` only with status ``ok`` and a date, and only usable values join a series.

A report row that repeats a FHIR lab result becomes a ``same_measurement`` edge rather than a
second value. Rows count as the same measurement when the analyte, the day and the value at
the printed precision all agree (:func:`printed_value_match`).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Any

from medgraph.graph.schema import (
    EdgeData,
    EdgeKind,
    GraphInfo,
    NodeData,
    NodeKind,
    PatientGraph,
    node_data,
    node_id,
    time_key,
)
from medgraph.graph.timeline import series_members
from medgraph.ingest.lab_report import ReportResult, ReportRow
from medgraph.normalize.analytes import BY_KEY
from medgraph.normalize.labs import LOINC, LabResult, normalize_labs
from medgraph.records import (
    CodeableConcept,
    Observation,
    PatientRecord,
    SourceRef,
    Timepoint,
)
from medgraph.rules.monitoring import SNOMED, monitoring_links

# Decimal division (a factor such as 1/88.4) is exact to 28 digits, not exactly: allow that
# much beyond half a unit in the last printed place.
_ARITHMETIC_SLACK = Decimal("1e-12")


@dataclass(frozen=True)
class AttachedReport:
    """A lab report filed in a patient's record, after interpretation."""

    name: str  # file name or upload label, for display
    result: ReportResult


def build_patient_graph(
    record: PatientRecord, reports: Sequence[AttachedReport] = ()
) -> PatientGraph:
    return _Builder(record, reports).build()


def code_of(concept: CodeableConcept | None) -> str | None:
    """``system|code`` of a concept's first coding."""
    if concept is None or not concept.codings:
        return None
    first = concept.codings[0]
    return f"{first.system or ''}|{first.code}"


def printed_value_match(row: NodeData, lab: NodeData) -> str | None:
    """How a report row repeats a FHIR lab result, or ``None`` if it doesn't.

    The analyte and the day must agree, as must the comparator and, for eGFR, any equation
    the report names. The recorded value, converted to the printed unit, must lie within half
    a unit of the last printed digit: ``109 µmol/L`` repeats ``1.2345 mg/dL``, because
    1.2345 mg/dL is 109.13 µmol/L. Both must be usable.
    """
    printed = row.original
    if (
        not (row.usable and lab.usable)
        or row.analyte != lab.analyte
        or row.analyte is None
        or row.start is None
        or lab.start is None
        or row.start.date != lab.start.date
        or row.comparator != lab.comparator
        or printed is None
        or printed.code is None
        or lab.value is None
    ):
        return None
    if row.analyte == "egfr" and not _same_equation(row.method, lab.method or ""):
        return None
    factor = BY_KEY[row.analyte].to_canonical.get(printed.code)
    if factor is None:
        return None
    exponent = printed.value.as_tuple().exponent
    assert isinstance(exponent, int)
    half_unit = Decimal(5).scaleb(exponent - 1)
    in_printed_unit = lab.value / factor
    if abs(in_printed_unit - printed.value) > half_unit * (1 + _ARITHMETIC_SLACK):
        return None
    recorded = lab.original
    as_recorded = (
        f"{recorded.value} {recorded.unit or recorded.code or ''}".strip() if recorded else ""
    )
    shown = in_printed_unit.quantize(Decimal(1).scaleb(exponent - 2))
    return (
        f"printed {printed.value} {printed.unit or ''} on {row.start.date}; recorded "
        f"{as_recorded} = {shown} {printed.code}"
    )


def _same_equation(stated: str | None, recorded: str) -> bool:
    """Whether a report's eGFR equation can be the one a LOINC code names."""
    if stated in (None, "unspecified"):
        return True
    if stated == "MDRD":
        return "MDRD" in recorded
    if stated == "CKD-EPI 2021":
        return recorded.startswith("CKD-EPI 2021")
    if stated == "CKD-EPI (year not stated)":
        return recorded.startswith("CKD-EPI")
    return False


def _measurement_detail(status: str, detail: str | None, dated: bool) -> str | None:
    if status != "ok":
        return f"{status}: {detail}" if detail else status
    return None if dated else "no date"


class _Builder:
    def __init__(self, record: PatientRecord, reports: Sequence[AttachedReport]) -> None:
        self.record = record
        self.reports = reports
        self.graph = PatientGraph()
        self.issues: list[str] = []

    def build(self) -> PatientGraph:
        record = self.record
        self.graph.graph.update(
            GraphInfo(
                patient=record.patient,
                sources=(record.source, *(r.result.source for r in self.reports)),
                ingest_issues=record.issues,
            ).attrs()
        )
        self.encounters()
        self.conditions()
        self.medication_requests()
        self.procedures()
        self.observations()
        self.notes()
        for report in self.reports:
            self.report(report)
        self.same_measurements()
        self.series_order()
        if self.issues:
            self.graph.graph["build_issues"] = list(self.issues)
        return self.graph

    # --- helpers -------------------------------------------------------------------------

    def add_node(self, kind: NodeKind, source_id: str, **fields: Any) -> str:
        node = node_id(kind, source_id)
        if node in self.graph:
            raise ValueError(f"duplicate node {node}")
        self.graph.add_node(node, **NodeData(kind=kind, **fields).attrs())
        return node

    def add_edge(self, u: str, v: str, kind: EdgeKind, basis: str, **fields: Any) -> None:
        missing = [n for n in (u, v) if n not in self.graph]
        if missing:  # never let networkx create a node without attributes
            self.issues.append(f"{kind} edge from {u} to {v} skipped: {missing[0]} not in graph")
            return
        self.graph.add_edge(u, v, key=kind, **EdgeData(kind=kind, basis=basis, **fields).attrs())

    def during(self, node: str, encounter_id: str | None) -> None:
        if encounter_id is not None:
            self.add_edge(
                node, node_id("encounter", encounter_id), "occurred_during", "encounter reference"
            )

    def analyte(self, key: str) -> str:
        node = node_id("analyte", key)
        if node not in self.graph:
            entry = BY_KEY[key]
            self.add_node(
                "analyte",
                key,
                label=entry.name,
                analyte=key,
                unit=entry.canonical_unit,
                detail="medgraph analyte registry (normalize/analytes.py)",
            )
        return node

    # --- FHIR records --------------------------------------------------------------------

    def encounters(self) -> None:
        for enc in self.record.encounters:
            self.add_node(
                "encounter",
                enc.id,
                label=enc.type.label if enc.type else "Encounter",
                code=code_of(enc.type),
                category=enc.class_code,
                start=enc.period.start,
                end=enc.period.end,
                status=enc.status,
                sources=(enc.source,),
            )

    def conditions(self) -> None:
        for cond in self.record.conditions:
            node = self.add_node(
                "condition",
                cond.id,
                label=cond.code.label,
                code=code_of(cond.code),
                start=cond.onset,
                end=cond.abatement,
                status=cond.clinical_status,
                sources=(cond.source,),
            )
            self.during(node, cond.encounter_id)
            for link in monitoring_links(cond.code.code(SNOMED)):
                self.add_edge(
                    node,
                    self.analyte(link.analyte),
                    "monitored_by",
                    "cited guideline (rules/monitoring.py)",
                    citation=link.citation,
                    detail=link.quote,
                )

    def treated_by(self, node: str, reason_ids: Iterable[str], source: SourceRef) -> None:
        for condition_id in reason_ids:
            self.add_edge(
                node_id("condition", condition_id),
                node,
                "treated_by",
                f"reasonReference of {source.resource_type}/{source.resource_id}",
            )

    def medication_requests(self) -> None:
        for med in self.record.medication_requests:
            node = self.add_node(
                "medication_request",
                med.id,
                label=med.medication.label,
                code=code_of(med.medication),
                start=med.authored_on,
                status=med.status,
                text=med.dosage_text,
                sources=(med.source,),
            )
            self.during(node, med.encounter_id)
            self.treated_by(node, med.reason_condition_ids, med.source)

    def procedures(self) -> None:
        for proc in self.record.procedures:
            node = self.add_node(
                "procedure",
                proc.id,
                label=proc.code.label,
                code=code_of(proc.code),
                start=proc.performed.start,
                end=proc.performed.end,
                status=proc.status,
                sources=(proc.source,),
            )
            self.during(node, proc.encounter_id)
            self.treated_by(node, proc.reason_condition_ids, proc.source)

    def observations(self) -> None:
        labs = {lab.observation_id: lab for lab in normalize_labs(self.record)}
        for obs in self.record.observations:
            lab = labs.get(obs.id)
            node = self.lab_result(obs, lab) if lab else self.observation(obs)
            self.during(node, obs.encounter_id)

    def lab_result(self, obs: Observation, lab: LabResult) -> str:
        dated = lab.effective is not None
        node = self.add_node(
            "lab_result",
            obs.id,
            label=obs.code.label,
            code=f"{LOINC}|{lab.loinc}",
            start=lab.effective,
            status=lab.status,
            usable=lab.status == "ok" and dated,
            analyte=lab.analyte,
            method=lab.method,
            value=lab.value,
            unit=lab.unit,
            comparator=lab.comparator,
            original=lab.original,
            text=lab.qualitative,
            detail=_measurement_detail(lab.status, lab.detail, dated),
            sources=(lab.source,),
        )
        self.add_edge(node, self.analyte(lab.analyte), "measures", f"LOINC {lab.loinc}")
        return node

    def observation(self, obs: Observation) -> str:
        q = obs.value_quantity
        text = None
        if obs.value_concept is not None:
            text = obs.value_concept.label
        elif obs.value_text is not None:
            text = obs.value_text
        elif obs.value_boolean is not None:
            text = str(obs.value_boolean).lower()
        elif obs.components:
            text = "; ".join(
                f"{c.code.label}: {c.value_quantity.value} {c.value_quantity.unit or ''}".strip()
                if c.value_quantity
                else f"{c.code.label}: {c.value_concept.label if c.value_concept else c.value_text}"
                for c in obs.components
            )
        return self.add_node(
            "observation",
            obs.id,
            label=obs.code.label,
            code=code_of(obs.code),
            category=obs.categories[0] if obs.categories else None,
            start=obs.effective,
            status=obs.status,
            value=q.value if q else None,
            unit=(q.unit or q.code) if q else None,
            comparator=q.comparator if q else None,
            text=text,
            sources=(obs.source,),
        )

    def notes(self) -> None:
        for note in self.record.notes:
            node = self.add_node(
                "note",
                note.id,
                label=note.kind.label if note.kind else "Note",
                code=code_of(note.kind),
                start=note.date,
                text=note.text,
                sources=note.sources,
            )
            self.during(node, note.encounter_id)

    # --- lab reports ---------------------------------------------------------------------

    def report(self, attached: AttachedReport) -> None:
        result = attached.result
        date = (
            Timepoint(date=result.collection_date, precision="day")
            if result.collection_date
            else None
        )
        separator = result.number_locale or "not proven"
        order = result.date_order or "not proven"
        conventions = (
            f"language {result.language}; decimal separator {separator}; date order {order}"
        )
        report = self.add_node(
            "lab_report",
            result.source,
            label=f"Lab report {attached.name}",
            start=date,
            status="dated" if date else "undated",
            text=conventions,
            detail="; ".join(result.issues) or None,
            sources=(
                SourceRef(
                    source=result.source, resource_type="LabReport", resource_id=attached.name
                ),
            ),
        )
        for row in result.rows:
            self.report_row(report, result.source, row, date)

    def report_row(self, report: str, source: str, row: ReportRow, date: Timepoint | None) -> None:
        printed = " · ".join(
            f"{label} {text}"
            for label, text in (
                ("value", row.value_text),
                ("unit", row.unit_text),
                ("range", row.range_text),
                ("flag", row.flag_text),
            )
            if text
        )
        node = self.add_node(
            "report_row",
            f"{source}:{row.index}",
            label=row.analyte_text,
            start=date,
            status=row.status,
            usable=row.status == "ok" and date is not None,
            analyte=row.analyte,
            method=row.method,
            value=row.value,
            unit=row.unit,
            comparator=row.comparator,
            original=row.original,
            text=printed,
            detail=_measurement_detail(row.status, row.detail, date is not None),
            sources=(row.source,),
        )
        self.add_edge(node, report, "part_of", "printed in the report")
        if row.analyte is not None:
            self.add_edge(
                node,
                self.analyte(row.analyte),
                "measures",
                f"analyte name table: {row.analyte_text}",
            )

    # --- derived edges -------------------------------------------------------------------

    def same_measurements(self) -> None:
        labs: dict[tuple[str, str], list[tuple[str, NodeData]]] = {}
        rows: list[tuple[str, NodeData]] = []
        for node in self.graph.nodes:
            data = node_data(self.graph, node)
            if not data.usable or data.analyte is None or data.start is None:
                continue
            if data.kind == "lab_result":
                key = (data.analyte, data.start.date.isoformat())
                labs.setdefault(key, []).append((node, data))
            elif data.kind == "report_row":
                rows.append((node, data))
        for row, row_data in rows:
            assert row_data.analyte is not None
            assert row_data.start is not None
            for lab, lab_data in labs.get((row_data.analyte, row_data.start.date.isoformat()), []):
                if detail := printed_value_match(row_data, lab_data):
                    self.add_edge(
                        row,
                        lab,
                        "same_measurement",
                        "same analyte, day and value at the printed precision",
                        detail=detail,
                    )

    def series_order(self) -> None:
        for nodes in series_members(self.graph).values():
            for earlier, later in pairwise(nodes):
                self.add_edge(earlier, later, "precedes", "time order of the analyte's values")
        encounters = sorted(
            (
                (node, data)
                for node in self.graph.nodes
                if (data := node_data(self.graph, node)).kind == "encounter"
                and data.start is not None
            ),
            key=lambda item: (time_key(item[1].start), item[0]),
        )
        for (earlier, _), (later, _) in pairwise(encounters):
            self.add_edge(earlier, later, "precedes", "time order of encounters")
