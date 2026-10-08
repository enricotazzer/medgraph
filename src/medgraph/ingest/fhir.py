"""FHIR R4 bundles to :class:`~medgraph.records.PatientRecord`.

One bundle holds one patient's record (Synthea's layout, and the usual shape of an export).
Problems inside individual resources become :class:`~medgraph.records.IngestIssue` entries on
the record instead of aborting the bundle: real records are messy, and a flag must be able to
say "this value could not be read" rather than silently lose it.
"""

import base64
import binascii
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from medgraph.normalize.time import TimeParseError, parse_fhir_time
from medgraph.records import (
    CodeableConcept,
    Coding,
    Condition,
    DiagnosticReport,
    Encounter,
    IngestIssue,
    IssueCode,
    MedicationRequest,
    Note,
    Observation,
    ObservationComponent,
    Patient,
    PatientRecord,
    Period,
    Procedure,
    Quantity,
    ReferenceRange,
    Severity,
    SourceRef,
    Timepoint,
)

UCUM = "http://unitsofmeasure.org"
Resource = dict[str, Any]
# Read only when another resource references them.
SUPPORTING_TYPES = frozenset({"Medication"})
GENDERS = frozenset({"male", "female", "other", "unknown"})


class BundleError(ValueError):
    """The document cannot be read as one patient's record."""


def read_bundle_file(path: Path) -> PatientRecord:
    """Read a bundle file. The record's source is the SHA-256 of the file's bytes."""
    raw = path.read_bytes()
    bundle = json.loads(raw, parse_float=Decimal)  # keep values exactly as written
    return parse_bundle(bundle, source=f"sha256:{hashlib.sha256(raw).hexdigest()}")


def parse_bundle(bundle: Mapping[str, Any], source: str) -> PatientRecord:
    return _BundleParser(bundle, source).parse()


def concept(raw: Mapping[str, Any] | None) -> CodeableConcept | None:
    if not raw:
        return None
    codings = tuple(
        Coding(system=c.get("system"), code=str(c["code"]), display=c.get("display"))
        for c in raw.get("coding", [])
        if c.get("code") is not None
    )
    return CodeableConcept(codings=codings, text=raw.get("text"))


def category_codes(resource: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(c["code"])
        for category in resource.get("category", [])
        for c in category.get("coding", [])
        if c.get("code") is not None
    )


def quantity(raw: Mapping[str, Any] | None) -> Quantity | None:
    if not raw or raw.get("value") is None or isinstance(raw["value"], bool):
        return None
    return Quantity(
        value=Decimal(str(raw["value"])),
        unit=raw.get("unit"),
        code=raw.get("code") if raw.get("system") == UCUM else None,
        comparator=raw.get("comparator"),
    )


class _BundleParser:
    def __init__(self, bundle: Mapping[str, Any], source: str) -> None:
        if bundle.get("resourceType") != "Bundle":
            raise BundleError("not a FHIR Bundle")
        self.source = source
        self.resources: list[Resource] = []
        self.by_reference: dict[str, Resource] = {}
        for entry in bundle.get("entry", []):
            resource = entry.get("resource")
            if not isinstance(resource, dict) or "resourceType" not in resource:
                continue
            self.resources.append(resource)
            if entry.get("fullUrl"):
                self.by_reference[str(entry["fullUrl"])] = resource
            if resource.get("id"):
                self.by_reference[f"{resource['resourceType']}/{resource['id']}"] = resource
        self.issues: list[IngestIssue] = []
        self.skipped: Counter[str] = Counter()
        self.patient_id = ""
        # (encounter id, text) -> note fields; merges notes carried by several resources.
        self.note_drafts: dict[tuple[str | None, str], dict[str, Any]] = {}

    # --- helpers ---------------------------------------------------------------------

    def ref(self, resource: Mapping[str, Any]) -> SourceRef:
        return SourceRef(
            source=self.source,
            resource_type=str(resource["resourceType"]),
            resource_id=str(resource.get("id", "")),
        )

    def issue(
        self,
        code: IssueCode,
        severity: Severity,
        message: str,
        resource: Mapping[str, Any] | None = None,
    ) -> None:
        source = self.ref(resource) if resource is not None else None
        self.issues.append(
            IngestIssue(code=code, severity=severity, message=message, source=source)
        )

    def time(self, value: object, resource: Mapping[str, Any], field: str) -> Timepoint | None:
        if value is None:
            return None
        try:
            return parse_fhir_time(str(value))
        except TimeParseError as exc:
            self.issue("invalid_time", "warning", f"{field}: {exc}", resource)
            return None

    def period(self, raw: Mapping[str, Any] | None, resource: Mapping[str, Any]) -> Period:
        raw = raw or {}
        return Period(
            start=self.time(raw.get("start"), resource, "period.start"),
            end=self.time(raw.get("end"), resource, "period.end"),
        )

    def resolve(
        self, reference: Mapping[str, Any] | None, resource: Mapping[str, Any], expected: str
    ) -> Resource | None:
        """The bundle resource a reference points to, or ``None``.

        Conditional references (``Practitioner?identifier=…``) point outside the patient's
        bundle and are left unresolved without an issue.
        """
        target = (reference or {}).get("reference")
        if not isinstance(target, str) or "?" in target:
            return None
        found = self.by_reference.get(target)
        if found is None:
            self.issue("unresolved_reference", "warning", f"{target} not in bundle", resource)
            return None
        if found["resourceType"] != expected:
            self.issue(
                "unexpected_reference_type",
                "warning",
                f"{target} is a {found['resourceType']}, expected {expected}",
                resource,
            )
            return None
        return found

    def resolve_id(
        self, reference: Mapping[str, Any] | None, resource: Mapping[str, Any], expected: str
    ) -> str | None:
        found = self.resolve(reference, resource, expected)
        return str(found["id"]) if found is not None else None

    def encounter_id(self, resource: Mapping[str, Any]) -> str | None:
        return self.resolve_id(resource.get("encounter"), resource, "Encounter")

    def reason_condition_ids(self, resource: Mapping[str, Any]) -> tuple[str, ...]:
        """Conditions a resource gives as its reason (``reasonReference``).

        FHIR also allows an Observation, DiagnosticReport or DocumentReference as a reason;
        those are reported and not read, since only a condition can be treated.
        """
        ids = []
        for reference in resource.get("reasonReference", []):
            target = reference.get("reference") if isinstance(reference, Mapping) else None
            found = self.by_reference.get(target) if isinstance(target, str) else None
            if found is not None and found["resourceType"] != "Condition":
                self.issue(
                    "unsupported_value",
                    "info",
                    f"reasonReference to a {found['resourceType']} not read",
                    resource,
                )
            elif (rid := self.resolve_id(reference, resource, "Condition")) is not None:
                ids.append(rid)
        return tuple(dict.fromkeys(ids))

    def check_subject(self, resource: Mapping[str, Any]) -> None:
        """A subject must be the bundle's patient (the only Patient a bundle may hold)."""
        subject = resource.get("subject") or resource.get("patient")
        if subject:
            self.resolve(subject, resource, "Patient")

    def add_note(
        self,
        text: str,
        resource: Mapping[str, Any],
        kind: CodeableConcept | None,
        date: Timepoint | None,
        encounter_id: str | None,
    ) -> str:
        key = (encounter_id, text)
        draft = self.note_drafts.get(key)
        if draft is None:
            draft = self.note_drafts[key] = {
                "id": str(resource.get("id", "")),
                "sources": [],
                "text": text,
                "kind": kind,
                "date": date,
                "encounter_id": encounter_id,
            }
        draft["sources"].append(self.ref(resource))
        return str(draft["id"])

    def attachment_texts(
        self, attachments: list[Mapping[str, Any]], resource: Mapping[str, Any]
    ) -> list[str]:
        texts = []
        for attachment in attachments:
            content_type = str(attachment.get("contentType", ""))
            data = attachment.get("data")
            if not content_type.startswith("text/plain") or data is None:
                self.issue(
                    "unsupported_attachment",
                    "info",
                    f"attachment of type {content_type or 'unknown'} without inline text skipped",
                    resource,
                )
                continue
            try:
                texts.append(base64.b64decode(data, validate=True).decode("utf-8"))
            except (binascii.Error, UnicodeDecodeError) as exc:
                self.issue("unsupported_attachment", "warning", f"undecodable: {exc}", resource)
        return texts

    # --- resources -------------------------------------------------------------------

    def parse(self) -> PatientRecord:
        patients = [r for r in self.resources if r["resourceType"] == "Patient"]
        if len(patients) != 1:
            raise BundleError(f"expected one Patient resource, found {len(patients)}")
        self.patient_id = str(patients[0].get("id", ""))
        patient = self.patient(patients[0])

        handlers: dict[str, Callable[[Resource], Any]] = {
            "Encounter": self.encounter,
            "Condition": self.condition,
            "MedicationRequest": self.medication_request,
            "Observation": self.observation,
            "Procedure": self.procedure,
            "DiagnosticReport": self.diagnostic_report,
            "DocumentReference": self.document_reference,
        }
        parsed: dict[str, list[Any]] = {name: [] for name in handlers}
        for resource in self.resources:
            rtype = resource["resourceType"]
            handler = handlers.get(rtype)
            if handler is None:
                if rtype not in SUPPORTING_TYPES and rtype != "Patient":
                    self.skipped[rtype] += 1
                continue
            try:
                self.check_subject(resource)
                result = handler(resource)
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                self.issue("invalid_resource", "error", f"{type(exc).__name__}: {exc}", resource)
                continue
            if result is not None:
                parsed[rtype].append(result)

        encounters: list[Encounter] = parsed["Encounter"]
        self.check_events_after_death(patient, encounters)
        notes = tuple(
            Note(**{**draft, "sources": tuple(draft["sources"])})
            for draft in self.note_drafts.values()
        )
        return PatientRecord(
            source=self.source,
            patient=patient,
            encounters=tuple(encounters),
            conditions=tuple(parsed["Condition"]),
            medication_requests=tuple(parsed["MedicationRequest"]),
            observations=tuple(parsed["Observation"]),
            procedures=tuple(parsed["Procedure"]),
            diagnostic_reports=tuple(parsed["DiagnosticReport"]),
            notes=notes,
            issues=tuple(self.issues),
            skipped_resources=dict(sorted(self.skipped.items())),
        )

    def patient(self, r: Resource) -> Patient:
        gender = r.get("gender", "unknown")
        if gender not in GENDERS:
            self.issue("unsupported_value", "warning", f"gender {gender!r} read as unknown", r)
            gender = "unknown"
        deceased = self.time(r.get("deceasedDateTime"), r, "deceasedDateTime")
        if deceased is None and r.get("deceasedBoolean") is True:
            self.issue("unsupported_value", "info", "deceased without a date", r)
        return Patient(
            id=self.patient_id,
            source=self.ref(r),
            gender=gender,
            birth_date=self.time(r.get("birthDate"), r, "birthDate"),
            deceased=deceased,
        )

    def encounter(self, r: Resource) -> Encounter:
        klass = r.get("class") or {}
        return Encounter(
            id=str(r["id"]),
            source=self.ref(r),
            status=r.get("status"),
            class_code=klass.get("code"),
            type=concept((r.get("type") or [None])[0]),
            period=self.period(r.get("period"), r),
        )

    def condition(self, r: Resource) -> Condition:
        return Condition(
            id=str(r["id"]),
            source=self.ref(r),
            code=self.required_concept(r, "code"),
            clinical_status=self.first_code(r.get("clinicalStatus")),
            verification_status=self.first_code(r.get("verificationStatus")),
            categories=category_codes(r),
            onset=self.time(
                r.get("onsetDateTime") or (r.get("onsetPeriod") or {}).get("start"), r, "onset"
            ),
            abatement=self.time(
                r.get("abatementDateTime") or (r.get("abatementPeriod") or {}).get("start"),
                r,
                "abatement",
            ),
            recorded=self.time(r.get("recordedDate"), r, "recordedDate"),
            encounter_id=self.encounter_id(r),
        )

    def medication_request(self, r: Resource) -> MedicationRequest:
        medication = concept(r.get("medicationCodeableConcept"))
        if medication is None:
            target = self.resolve(r.get("medicationReference"), r, "Medication")
            medication = concept((target or {}).get("code"))
        if medication is None:
            raise ValueError("no medication code")
        dosage = (r.get("dosageInstruction") or [{}])[0]
        return MedicationRequest(
            id=str(r["id"]),
            source=self.ref(r),
            medication=medication,
            status=r.get("status"),
            intent=r.get("intent"),
            authored_on=self.time(r.get("authoredOn"), r, "authoredOn"),
            dosage_text=dosage.get("text"),
            as_needed=dosage.get("asNeededBoolean"),
            encounter_id=self.encounter_id(r),
            reason_condition_ids=self.reason_condition_ids(r),
        )

    def observation(self, r: Resource) -> Observation:
        value_quantity = quantity(r.get("valueQuantity"))
        if value_quantity is None and r.get("valueInteger") is not None:
            value_quantity = Quantity(value=Decimal(int(r["valueInteger"])))
        handled = {"valueQuantity", "valueCodeableConcept", "valueString", "valueBoolean"}
        unsupported = [
            k for k in r if k.startswith("value") and k not in handled | {"valueInteger"}
        ]
        if unsupported:
            self.issue("unsupported_value", "info", f"{unsupported[0]} not read", r)
        return Observation(
            id=str(r["id"]),
            source=self.ref(r),
            code=self.required_concept(r, "code"),
            status=r.get("status"),
            categories=category_codes(r),
            effective=self.time(
                r.get("effectiveDateTime") or (r.get("effectivePeriod") or {}).get("start"),
                r,
                "effective",
            ),
            issued=self.time(r.get("issued"), r, "issued"),
            value_quantity=value_quantity,
            value_concept=concept(r.get("valueCodeableConcept")),
            value_text=r.get("valueString"),
            value_boolean=r.get("valueBoolean"),
            components=tuple(
                ObservationComponent(
                    code=self.required_concept(c, "code"),
                    value_quantity=quantity(c.get("valueQuantity")),
                    value_concept=concept(c.get("valueCodeableConcept")),
                    value_text=c.get("valueString"),
                )
                for c in r.get("component", [])
            ),
            reference_ranges=tuple(
                ReferenceRange(
                    low=quantity(rr.get("low")), high=quantity(rr.get("high")), text=rr.get("text")
                )
                for rr in r.get("referenceRange", [])
            ),
            interpretations=tuple(
                code
                for interpretation in r.get("interpretation", [])
                if (code := self.first_code(interpretation)) is not None
            ),
            encounter_id=self.encounter_id(r),
        )

    def procedure(self, r: Resource) -> Procedure:
        if "performedDateTime" in r:
            when = self.time(r["performedDateTime"], r, "performedDateTime")
            performed = Period(start=when, end=when)
        else:
            performed = self.period(r.get("performedPeriod"), r)
        return Procedure(
            id=str(r["id"]),
            source=self.ref(r),
            code=self.required_concept(r, "code"),
            status=r.get("status"),
            performed=performed,
            encounter_id=self.encounter_id(r),
            reason_condition_ids=self.reason_condition_ids(r),
        )

    def diagnostic_report(self, r: Resource) -> DiagnosticReport:
        code = self.required_concept(r, "code")
        effective = self.time(r.get("effectiveDateTime"), r, "effectiveDateTime")
        encounter_id = self.encounter_id(r)
        note_id = None
        for text in self.attachment_texts(r.get("presentedForm", []), r):
            note_id = self.add_note(text, r, code, effective, encounter_id)
        return DiagnosticReport(
            id=str(r["id"]),
            source=self.ref(r),
            code=code,
            status=r.get("status"),
            categories=category_codes(r),
            effective=effective,
            issued=self.time(r.get("issued"), r, "issued"),
            result_ids=tuple(
                rid
                for result in r.get("result", [])
                if (rid := self.resolve_id(result, r, "Observation")) is not None
            ),
            note_id=note_id,
            encounter_id=encounter_id,
        )

    def document_reference(self, r: Resource) -> None:
        context = r.get("context") or {}
        encounters = context.get("encounter") or [None]
        encounter_id = self.resolve_id(encounters[0], r, "Encounter")
        attachments = [c.get("attachment") or {} for c in r.get("content", [])]
        kind = concept(r.get("type"))
        date = self.time(r.get("date"), r, "date")
        for text in self.attachment_texts(attachments, r):
            self.add_note(text, r, kind, date, encounter_id)

    def check_events_after_death(self, patient: Patient, encounters: list[Encounter]) -> None:
        if patient.deceased is None:
            return
        for enc in encounters:
            start = enc.period.start
            if start is not None and start.date > patient.deceased.date:
                self.issues.append(
                    IngestIssue(
                        code="event_after_death",
                        severity="warning",
                        message=f"encounter on {start.date} after death on {patient.deceased.date}",
                        source=enc.source,
                    )
                )

    @staticmethod
    def first_code(raw: Mapping[str, Any] | None) -> str | None:
        c = concept(raw)
        return c.codings[0].code if c and c.codings else None

    @staticmethod
    def required_concept(r: Mapping[str, Any], field: str) -> CodeableConcept:
        c = concept(r.get(field))
        if c is None or not (c.codings or c.text):
            raise ValueError(f"missing {field}")
        return c
