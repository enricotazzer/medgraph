"""Fetch and verify the knowledge store: guideline documents and the cohort's drug labels.

    uv run python scripts/fetch_knowledge.py dev-1000 [--lock] [--offline]

Documents (``configs/knowledge/documents.yaml``):
- each is downloaded once into ``$MEDGRAPH_DATA_DIR/knowledge/documents/`` and must match its
  pinned SHA-256; stored files are re-verified on every run;
- the document's own licence statement must occur in its extracted text.

Drug labels (``configs/knowledge/labels-<cohort>.lock.json``):
- ``--lock`` chooses a DailyMed label for every RxNorm code the cohort prescribes (the rule is
  in ``medgraph.rag.dailymed``), downloads it and writes the lock file with set ID, version and
  SHA-256;
- without ``--lock``, the pinned labels are verified, and missing ones are downloaded if
  DailyMed still serves the pinned version;
- ``--offline`` only verifies what is stored.

Only Synthea cohorts: choosing labels sends the cohort's drug codes to NLM's public APIs, one
code per request and never with a patient. Check the data use agreement before doing this for
credentialed data such as MIMIC-IV.
"""

import argparse
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path

from medgraph.graph.store import GraphStore
from medgraph.rag.dailymed import (
    RXNORM,
    SELECTION_RULE,
    LabelLock,
    LockedDrug,
    LockedLabel,
    rxnorm_info,
    select_label,
    spl_meta,
    spl_url,
)
from medgraph.rag.documents import DocumentSpec, extract_pages, load_documents
from medgraph.rag.fetch import Fetcher, FetchError, sha256_bytes, sha256_file, write_atomic
from medgraph.rag.store import KnowledgeStore, ManifestEntry
from medgraph.rag.text import find
from medgraph.settings import Settings
from profile_cohort import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "configs" / "knowledge"
LABEL_LICENCE = "not stated; NLM cannot guarantee the copyright status of DailyMed labels"


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def lock_path(cohort: str) -> Path:
    return CONFIG_DIR / f"labels-{cohort}.lock.json"


# --- documents ----------------------------------------------------------------------------


def fetch_documents(
    specs: tuple[DocumentSpec, ...],
    kstore: KnowledgeStore,
    manifest: dict[str, ManifestEntry],
    fetcher: Fetcher | None,
) -> list[str]:
    problems = []
    for spec in specs:
        path = kstore.document_path(spec.filename)
        try:
            if path.exists():
                digest = sha256_file(path)
                if digest != spec.sha256:
                    raise FetchError(f"stored file {digest} differs from the pinned {spec.sha256}")
            elif fetcher is None:
                raise FetchError("not stored (offline)")
            else:
                digest = fetcher.download(spec.url, path, spec.sha256)
        except FetchError as exc:
            problems.append(f"{spec.id}: {exc}")
            continue
        kstore.record(
            manifest,
            kstore.relative(path),
            ManifestEntry(
                kind="document",
                url=spec.url,
                sha256=digest,
                bytes=path.stat().st_size,
                licence=spec.licence,
                retrieved=now(),
            ),
        )
        pages = extract_pages(path, spec)
        labelled = sum(p.label is not None for p in pages)
        licence = "no statement expected"
        if spec.licence_quote:
            found = find(spec.licence_quote, pages)
            if not found:
                problems.append(f"{spec.id}: licence statement not found in the text")
                continue
            licence = f"licence statement on {found[0].first.cite}"
        print(f"  {spec.id}: {len(pages)} pages ({labelled} with a printed number); {licence}")
    return problems


# --- labels -------------------------------------------------------------------------------


def cohort_drugs(db_path: Path) -> dict[str, tuple[str, int]]:
    """RxCUI -> (name as recorded, patients prescribed it at any time)."""
    patients: dict[str, set[str]] = defaultdict(set)
    names: dict[str, str] = {}
    with GraphStore(db_path) as store:
        for patient, _, attrs in store.nodes_of_kind("medication_request"):
            system, _, code = str(attrs.get("code", "")).partition("|")
            if system == RXNORM and code:
                patients[code].add(patient)
                names.setdefault(code, str(attrs.get("label", "")))
    return {code: (names[code], len(patients[code])) for code in patients}


def store_label(
    xml: bytes, kstore: KnowledgeStore, manifest: dict[str, ManifestEntry]
) -> tuple[Path, str]:
    meta = spl_meta(xml)
    path = kstore.label_path(f"{meta.setid}/v{meta.version}.xml")
    digest = sha256_bytes(xml)
    if path.exists() and sha256_file(path) != digest:
        raise FetchError(f"{path.name} of {meta.setid} is stored with different content")
    write_atomic(path, xml)
    kstore.record(
        manifest,
        kstore.relative(path),
        ManifestEntry(
            kind="label",
            url=spl_url(meta.setid),
            sha256=digest,
            bytes=len(xml),
            licence=LABEL_LICENCE,
            retrieved=now(),
        ),
    )
    return path, digest


def lock_one(
    rxcui: str,
    name: str,
    patients: int,
    fetcher: Fetcher,
    kstore: KnowledgeStore,
    manifest: dict[str, ManifestEntry],
    labels: dict[str, LockedLabel],
) -> LockedDrug:
    """Choose and store the label for one RxCUI; ``labels`` holds those already stored."""
    info = rxnorm_info(fetcher, rxcui)
    selection = select_label(fetcher, rxcui)
    label = None
    if selection is not None:
        setid = selection.listing.setid
        if setid not in labels:
            xml = fetcher.get(spl_url(setid)).content
            meta = spl_meta(xml)
            if meta.setid != setid:
                raise FetchError(f"DailyMed returned {meta.setid} for {setid}")
            _, digest = store_label(xml, kstore, manifest)
            labels[setid] = LockedLabel(
                setid=setid,
                version=meta.version,
                published=selection.listing.published,
                effective=meta.effective,
                title=meta.title,
                labeler=meta.labeler,
                document_type=meta.document_type,
                approvals=meta.approvals,
                sha256=digest,
                bytes=len(xml),
            )
        label = labels[setid]
    return LockedDrug(
        rxcui=rxcui,
        name=name,
        patients=patients,
        rxnorm_status=info.status,
        tty=info.tty,
        ingredients=info.ingredients,
        label=label,
        tier=selection.tier if selection else None,
        candidates=selection.candidates if selection else 0,
        reason=None if selection else "DailyMed maps no label to this RxCUI",
    )


def lock_labels(
    cohort: str,
    drugs: dict[str, tuple[str, int]],
    fetcher: Fetcher,
    kstore: KnowledgeStore,
    manifest: dict[str, ManifestEntry],
) -> tuple[LabelLock | None, list[str]]:
    """Choose a label for every RxCUI. The lock is returned only when all succeeded.

    Each result is appended to a progress file named for the day, so a run cut short by the
    network resumes where it stopped on the same day; a later day starts afresh.
    """
    today = now().date()
    progress = kstore.root / f"labels-{cohort}-{today.isoformat()}.progress.jsonl"
    done: dict[str, LockedDrug] = {}
    if progress.exists():
        for line in progress.read_text(encoding="utf-8").splitlines():
            drug = LockedDrug.model_validate_json(line)
            done[drug.rxcui] = drug
    labels = {d.label.setid: d.label for d in done.values() if d.label is not None}
    problems = []
    progress.parent.mkdir(parents=True, exist_ok=True)
    with progress.open("a", encoding="utf-8") as out:
        for n, rxcui in enumerate(sorted(drugs, key=int), start=1):
            if rxcui in done:
                continue
            name, patients = drugs[rxcui]
            try:
                drug = lock_one(rxcui, name, patients, fetcher, kstore, manifest, labels)
            except FetchError as exc:
                problems.append(f"RxCUI {rxcui}: {exc}")
                continue
            done[rxcui] = drug
            out.write(drug.model_dump_json() + "\n")
            out.flush()
            setid = drug.label.setid if drug.label else "no label"
            print(f"  [{n}/{len(drugs)}] {rxcui} {name[:60]}: {setid}", flush=True)
    if problems:
        return None, problems
    lock = LabelLock(
        rule=SELECTION_RULE,
        selected_on=today,
        cohort=cohort,
        drugs=tuple(done[r] for r in sorted(drugs, key=int)),
    )
    progress.unlink()
    return lock, []


def verify_labels(
    lock: LabelLock,
    kstore: KnowledgeStore,
    manifest: dict[str, ManifestEntry],
    fetcher: Fetcher | None,
) -> list[str]:
    problems = []
    for label in lock.labels().values():
        path = kstore.label_path(label.path)
        try:
            if path.exists():
                if sha256_file(path) != label.sha256:
                    raise FetchError("stored file differs from the lock")
                continue
            if fetcher is None:
                raise FetchError("not stored (offline)")
            xml = fetcher.get(spl_url(label.setid)).content
            meta = spl_meta(xml)
            if meta.version != label.version:
                raise FetchError(
                    f"DailyMed now serves version {meta.version}, the lock pins "
                    f"{label.version}; re-run with --lock to choose again"
                )
            if sha256_bytes(xml) != label.sha256:
                raise FetchError("same version, different content than the lock")
            store_label(xml, kstore, manifest)
        except FetchError as exc:
            problems.append(f"label {label.setid} v{label.version}: {exc}")
    return problems


def summarize_lock(lock: LabelLock) -> str:
    tiers = Counter(d.tier or "no label" for d in lock.drugs)
    no_label = [d for d in lock.drugs if d.label is None]
    labels = lock.labels()
    lines = [
        f"labels ({lock.rule}, chosen {lock.selected_on}): {len(lock.drugs)} RxCUIs, "
        f"{len(labels)} distinct labels, {sum(x.bytes for x in labels.values()) / 1e6:.1f} MB",
        *(f"  {tier}: {count}" for tier, count in tiers.most_common()),
    ]
    lines += [f"  no label: {d.rxcui} {d.name} ({d.patients} patients)" for d in no_label]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and verify the knowledge store.")
    parser.add_argument("cohort")
    parser.add_argument("--lock", action="store_true", help="choose the labels again")
    parser.add_argument("--offline", action="store_true", help="verify stored files only")
    args = parser.parse_args(argv)
    if args.lock and args.offline:
        parser.error("--lock needs the network")
    settings = Settings()
    if not (settings.synthea_dir / args.cohort / "MANIFEST.json").exists():
        parser.error(
            f"{args.cohort} is not a Synthea cohort; label selection sends its drug codes to "
            "NLM, so only synthetic cohorts are allowed"
        )
    kstore = KnowledgeStore(settings.knowledge_dir)
    manifest = kstore.load_manifest()
    fetcher = None if args.offline else Fetcher(retries=5)
    try:
        print("documents:")
        problems = fetch_documents(
            load_documents(CONFIG_DIR / "documents.yaml"), kstore, manifest, fetcher
        )
        lock: LabelLock | None
        if args.lock:
            assert fetcher is not None
            db_path = settings.graphs_dir / f"{args.cohort}.sqlite"
            if not db_path.exists():
                parser.error(f"{db_path} not found; run make graphs first")
            lock, failed = lock_labels(
                args.cohort, cohort_drugs(db_path), fetcher, kstore, manifest
            )
            problems += failed
            if lock is None:
                problems.append("lock not written: run again with --lock to resume")
            else:
                write_atomic(
                    lock_path(args.cohort),
                    (json.dumps(lock.model_dump(mode="json"), indent=1) + "\n").encode(),
                )
        else:
            path = lock_path(args.cohort)
            if not path.exists():
                parser.error(f"{path} not found; run with --lock to choose labels")
            lock = LabelLock.model_validate_json(path.read_text(encoding="utf-8"))
            problems += verify_labels(lock, kstore, manifest, fetcher)
        if lock is not None:
            print(summarize_lock(lock))
    finally:
        kstore.save_manifest(manifest)
        if fetcher is not None:
            fetcher.close()
    for problem in problems:
        print(f"PROBLEM {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
