"""People's records in the local app (Phase 5c, ADR 0013).

A record is added by uploading a FHIR R4 bundle, or by entering a birth date and sex for
someone who has only lab reports; the form then writes a minimal bundle, marked as entered in
the app. Either way it goes through the steps of ``make graphs``: parse, build, check the
invariants, store.

Each person has one folder, ``$MEDGRAPH_DATA_DIR/people/<id>/``:

- ``person.json``: the label chosen in the app, how and when the record was added, the
  record's SHA-256 and its own patient ID;
- ``record.json``: the bundle, byte for byte as uploaded, or as the form wrote it;
- ``graph.sqlite``: the patient graph (``graph/store.py``).

``<id>`` is random. It names the folder, the page and log lines; the record's own patient ID,
which may identify the person, stays inside the folder. Everything derived from a record lives
in its folder, so deleting the folder deletes the person. An upload waiting for a choice
(replace the record already here?) is kept in ``people/.pending/`` until the choice is made,
for an hour at most.

Uploads may be real records: they are processed on this machine only, and never opened in an
assistant session (CLAUDE.md).
"""

import datetime as dt
import hashlib
import json
import logging
import re
import secrets
import threading
import uuid
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from medgraph.graph.build import build_patient_graph
from medgraph.graph.check import check_coverage, check_graph
from medgraph.graph.schema import PatientGraph
from medgraph.graph.store import GraphStore
from medgraph.ingest.fhir import BundleError, parse_bundle
from medgraph.ingest.files import iter_data_files, remove_tree
from medgraph.rag.fetch import write_atomic
from medgraph.records import PatientRecord
from medgraph.settings import Settings

logger = logging.getLogger(__name__)

MAX_RECORD_BYTES = 100 * 1024 * 1024  # the largest dev-1000 bundle is 68 MB
MAX_LABEL = 60
PENDING_FOR = dt.timedelta(hours=1)
GENDERS = ("female", "male", "other", "unknown")  # FHIR administrative gender
EARLIEST_BIRTH = dt.date(1900, 1, 1)
ENTERED = "medgraph:entered-in-app"  # Patient.meta.source of a record entered in the app
PERSON_ID = re.compile(r"[0-9a-f]{12}")
_PENDING_ID = re.compile(r"[0-9a-f]{32}")
Source = Literal["upload", "entered"]
# One writer at a time: the app runs requests in threads.
_lock = threading.Lock()


class AddError(ValueError):
    """A record that can't be added. ``key`` names the message for the person adding it
    (``app.err.<key>`` in the string table), with plain-text ``values``."""

    def __init__(self, key: str, **values: str) -> None:
        super().__init__(key, values)
        self.key = key
        self.values = values


@dataclass(frozen=True)
class Person:
    id: str
    label: str
    source: Source
    added: dt.datetime
    record_sha256: str
    patient_id: str  # the record's own, kept inside the folder
    folder: Path

    @property
    def store(self) -> Path:
        return self.folder / "graph.sqlite"

    def load_graph(self) -> PatientGraph:
        with GraphStore(self.store, read_only=True) as store:
            return store.load(self.patient_id)


@dataclass(frozen=True)
class Checked:
    """A record read and built, ready to store."""

    data: bytes
    sha256: str
    record: PatientRecord
    graph: PatientGraph

    def issues(self) -> Counter[str]:
        """The parser's issues by code: data it could not read, reported instead of guessed."""
        return Counter(i.code for i in self.record.issues)


def clean_label(label: str) -> str:
    text = " ".join(label.split())
    if not text:
        raise AddError("label")
    if len(text) > MAX_LABEL:
        raise AddError("label.long", n=str(MAX_LABEL))
    return text


def check_record(data: bytes) -> Checked:
    """Read ``data`` as one patient's FHIR bundle and build its graph, checking every
    invariant (as ``make graphs`` does)."""
    if len(data) > MAX_RECORD_BYTES:
        raise AddError("large", mb=str(MAX_RECORD_BYTES // 2**20))
    digest = _sha256(data)
    try:
        bundle = json.loads(data, parse_float=Decimal)  # values exactly as written
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise AddError("json") from exc
    if not isinstance(bundle, dict):
        raise AddError("bundle", why="not a FHIR Bundle")
    try:
        record = parse_bundle(bundle, source=f"sha256:{digest}")
    except BundleError as exc:
        raise AddError("bundle", why=str(exc)) from exc
    graph = build_patient_graph(record)
    problems = check_graph(graph) + check_coverage(graph, record, ())
    if problems:
        logger.error("a record failed %d graph checks; not added", len(problems))
        raise AddError("graph")
    return Checked(data, digest, record, graph)


def entered_record(birth_date: dt.date, gender: str, today: dt.date, now: dt.datetime) -> bytes:
    """A minimal bundle for someone with no FHIR record: a Patient with a birth date and an
    administrative gender (the eGFR equation needs age and sex), marked as entered here."""
    if gender not in GENDERS:
        raise AddError("sex")
    if not EARLIEST_BIRTH <= birth_date <= today:
        raise AddError("birth", earliest=EARLIEST_BIRTH.isoformat())
    patient = str(uuid.uuid4())
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {
                "fullUrl": f"urn:uuid:{patient}",
                "resource": {
                    "resourceType": "Patient",
                    "id": patient,
                    "meta": {"source": ENTERED, "lastUpdated": now.isoformat()},
                    "gender": gender,
                    "birthDate": birth_date.isoformat(),
                },
            }
        ],
    }
    return (json.dumps(bundle, indent=1) + "\n").encode()


def people(settings: Settings) -> list[Person]:
    """Everyone added in the app, by label."""
    root = settings.people_dir
    if not root.is_dir():
        return []
    found = [
        _load(path.parent)
        for path in iter_data_files(root, "*/person.json")
        if path.parent.parent == root and PERSON_ID.fullmatch(path.parent.name)
    ]
    return sorted(found, key=lambda p: (p.label.lower(), p.id))


def person(settings: Settings, person_id: str) -> Person | None:
    if not PERSON_ID.fullmatch(person_id):
        return None
    folder = settings.people_dir / person_id
    return _load(folder) if (folder / "person.json").is_file() else None


def same_patient(settings: Settings, checked: Checked) -> Person | None:
    """Someone already here whose record has the same patient ID."""
    patient = checked.record.patient.id
    return next((p for p in people(settings) if p.patient_id == patient), None)


def add(
    settings: Settings, label: str, checked: Checked, source: Source, now: dt.datetime
) -> Person:
    root = settings.people_dir
    with _lock:
        person_id = secrets.token_hex(6)
        while (root / person_id).exists():
            person_id = secrets.token_hex(6)
        staging = root / f".new-{person_id}"
        _write(staging, checked)
        _write_manifest(staging, person_id, label, source, now, checked)
        staging.rename(root / person_id)
    logger.info("added person %s", person_id)
    return _load(root / person_id)


def replace(settings: Settings, who: Person, checked: Checked, now: dt.datetime) -> Person:
    """Replace ``who``'s record with ``checked``; the label stays."""
    with _lock:
        staging = settings.people_dir / f".new-{who.id}"
        _write(staging, checked)
        _write_manifest(staging, who.id, who.label, "upload", now, checked)
        old = settings.people_dir / f".old-{who.id}"
        who.folder.rename(old)
        staging.rename(who.folder)
        remove_tree(old)
    logger.info("replaced the record of person %s", who.id)
    return _load(who.folder)


def delete(settings: Settings, who: Person) -> None:
    """Delete ``who`` and everything derived from their record: the whole folder."""
    with _lock:
        remove_tree(who.folder)
    logger.info("deleted person %s", who.id)


def stash(settings: Settings, label: str, data: bytes, now: dt.datetime) -> str:
    """Keep an upload until its replace-or-not choice; returns its key."""
    clean_pending(settings, now)
    key = secrets.token_hex(16)
    folder = settings.people_dir / ".pending" / key
    folder.mkdir(parents=True)
    (folder / "record.json").write_bytes(data)
    meta = {"label": label, "at": now.isoformat()}
    (folder / "pending.json").write_text(json.dumps(meta), encoding="utf-8")
    return key


def take(settings: Settings, key: str) -> tuple[str, bytes] | None:
    """The stashed upload ``key`` (its label and bytes), removed from the stash."""
    if not _PENDING_ID.fullmatch(key):
        return None
    folder = settings.people_dir / ".pending" / key
    if not (folder / "pending.json").is_file():
        return None
    label = str(json.loads((folder / "pending.json").read_text(encoding="utf-8"))["label"])
    data = (folder / "record.json").read_bytes()
    remove_tree(folder)
    return label, data


def discard(settings: Settings, key: str) -> None:
    take(settings, key)


def clean_pending(settings: Settings, now: dt.datetime) -> None:
    """Remove stashed uploads older than :data:`PENDING_FOR`."""
    pending = settings.people_dir / ".pending"
    if pending.is_dir():
        for meta in iter_data_files(pending, "*/pending.json"):
            at = dt.datetime.fromisoformat(json.loads(meta.read_text(encoding="utf-8"))["at"])
            if now - at > PENDING_FOR:
                remove_tree(meta.parent)


def recover(settings: Settings) -> None:
    """Finish or undo writes a stopped server left half done: a record being added
    (``.new-<id>``) is dropped; a record being replaced is restored from ``.old-<id>`` unless
    its replacement is already in place."""
    root = settings.people_dir
    if not root.is_dir():
        return
    with _lock:
        for path in sorted(root.iterdir()):
            if not path.is_dir() or not path.name.startswith((".new-", ".old-")):
                continue
            target = root / path.name[5:]
            if path.name.startswith(".old-") and not target.exists():
                path.rename(target)
                logger.warning(
                    "restored the record of person %s after an interrupted write", target.name
                )
            else:
                remove_tree(path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(folder: Path, checked: Checked) -> None:
    if folder.exists():
        remove_tree(folder)
    folder.mkdir(parents=True)
    (folder / "record.json").write_bytes(checked.data)
    with GraphStore(folder / "graph.sqlite") as store:
        store.save(checked.graph)


def _write_manifest(
    folder: Path, person_id: str, label: str, source: Source, now: dt.datetime, checked: Checked
) -> None:
    manifest: dict[str, Any] = {
        "id": person_id,
        "label": label,
        "source": source,
        "added": now.isoformat(),
        "record_sha256": checked.sha256,
        "patient_id": checked.record.patient.id,
    }
    write_atomic(folder / "person.json", (json.dumps(manifest, indent=1) + "\n").encode())


def _load(folder: Path) -> Person:
    m = json.loads((folder / "person.json").read_text(encoding="utf-8"))
    return Person(
        id=m["id"],
        label=m["label"],
        source=m["source"],
        added=dt.datetime.fromisoformat(m["added"]),
        record_sha256=m["record_sha256"],
        patient_id=m["patient_id"],
        folder=folder,
    )
