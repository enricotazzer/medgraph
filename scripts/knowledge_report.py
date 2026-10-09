"""Report on a cohort's knowledge store: documents, labels, verified quotes, index, retrieval.

    OLLAMA_MODELS=/Volumes/T7/ollama-models scripts/dev/llm_run.sh knowledge-report \
        uv run python scripts/knowledge_report.py dev-1000

Writes ``docs/data/knowledge-<cohort>.md``. Everything is recomputed from the stored files:
- the documents and the cohort's labels, checked against their pins;
- every quote that a rule, a graph link or a label statement cites, checked against the stored
  text (``medgraph.rag.quotes``);
- the retrieval index;
- the retrieval smoke checks (``configs/knowledge/retrieval-checks.yaml``) with BM25, dense and
  hybrid search, in English and Italian. Dense search embeds the questions with the local
  embedding model; without an Ollama server those columns say "not run".
"""

import argparse
import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from build_index import EMBEDDING_MODEL
from evaluate_extraction import code_digest
from fetch_knowledge import CONFIG_DIR, lock_path
from generate_synthea import git_state
from medgraph.agent.llm import OllamaClient
from medgraph.rag.dailymed import LabelLock, spl_meta
from medgraph.rag.documents import extract_pages, load_documents, reference_pages
from medgraph.rag.fetch import sha256_file
from medgraph.rag.index import Hit, KnowledgeIndex
from medgraph.rag.quotes import check_quote, fragments
from medgraph.rag.spl import section_pages, sections
from medgraph.rag.text import Page, find
from medgraph.rules.monitoring import MONITORING
from medgraph.rules.sources import SOURCES
from medgraph.settings import Settings
from profile_cohort import REPO_ROOT, table

SCRIPTS = ("knowledge_report.py", "build_index.py", "fetch_knowledge.py")
RANKS = 10


@dataclass(frozen=True)
class Check:
    id: str
    en: str
    it: str
    expect: str
    sources: tuple[str, ...]


def load_checks(path: Path) -> list[Check]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Check(**{**c, "sources": tuple(c["sources"])}) for c in raw["checks"]]


def first_rank(index: KnowledgeIndex, hits: list[Hit], expect: str) -> int | None:
    for hit in hits:
        if find(expect, [Page(1, None, index.chunk(hit.chunk).text)]):
            return hit.rank
    return None


def rank_text(rank: int | None) -> str:
    return "-" if rank is None else str(rank)


def recall(ranks: list[int | None], k: int) -> str:
    return f"{sum(1 for r in ranks if r is not None and r <= k)}/{len(ranks)}"


# Label statement census: sentences that state an eGFR threshold, a creatinine-clearance
# threshold, or a lab-monitoring interval, outside the sections that describe trials,
# pharmacology, adverse reactions, the product or its packaging.
CENSUS_SKIPPED = frozenset(
    {"34069-5", "34089-3", "34092-7", "34084-4", "43682-4", "43681-6", "43679-0", "34083-6"}
)
SENTENCE = re.compile(r"(?<=[.;])\s+(?=[A-Z•(\[])|\n")
NUMBER = re.compile(
    r"(<|>|≤|≥|\bbelow\b|\bless than\b|\bgreater than\b|\bbetween\b|\bunder\b|\bat least\b)\s*\d+"
)
EGFR = re.compile(r"\beGFR\b")
CRCL = re.compile(r"creatinine clearance|\bCrCl\b|\bCLcr\b", re.I)
VERB = re.compile(r"\b(monitor|measure|assess|check|obtain|evaluate|determine)", re.I)
LAB = re.compile(
    r"hemoglobin|haemoglobin|\beGFR\b|renal function|kidney function|serum creatinine", re.I
)
WHEN = re.compile(
    r"\b(every|at least|weekly|monthly|annually|yearly|periodically|before initiat"
    r"|prior to initiat|regularly|months)\b",
    re.I,
)


def census(lock: LabelLock, root: Path) -> list[list[str]]:
    sentences: Counter[str] = Counter()
    labels: dict[str, set[str]] = {
        "eGFR threshold": set(),
        "creatinine-clearance threshold": set(),
        "lab monitoring with a time word": set(),
    }
    for label in lock.labels().values():
        for section in sections((root / "labels" / label.path).read_bytes()):
            if section.code in CENSUS_SKIPPED:
                continue
            for sentence in SENTENCE.split(section.text):
                kind = None
                if EGFR.search(sentence) and NUMBER.search(sentence):
                    kind = "eGFR threshold"
                elif CRCL.search(sentence) and NUMBER.search(sentence):
                    kind = "creatinine-clearance threshold"
                elif VERB.search(sentence) and LAB.search(sentence) and WHEN.search(sentence):
                    kind = "lab monitoring with a time word"
                if kind:
                    sentences[kind] += 1
                    labels[kind].add(label.setid)
    return [[k, f"{sentences[k]:,}", f"{len(v):,}"] for k, v in labels.items()]


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report on the knowledge store.")
    parser.add_argument("cohort")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    settings = Settings()
    root = settings.knowledge_dir
    lock = LabelLock.model_validate_json(lock_path(args.cohort).read_text(encoding="utf-8"))
    labels = lock.labels()

    # Documents.
    pages: dict[str, tuple[Page, ...]] = {}
    doc_rows = []
    for spec in load_documents(CONFIG_DIR / "documents.yaml"):
        path = root / "documents" / spec.filename
        pinned = sha256_file(path) == spec.sha256
        pages[spec.id] = extract_pages(path, spec)
        skipped = reference_pages(pages[spec.id], spec)
        licence = "none expected"
        if spec.licence_quote:
            found = find(spec.licence_quote, pages[spec.id])
            licence = found[0].first.cite if found else "NOT FOUND"
        doc_rows.append(
            [
                f"`{spec.id}`",
                spec.format.upper(),
                f"{len(pages[spec.id])}",
                f"{sum(p.label is not None for p in pages[spec.id])}",
                f"{len(skipped)}",
                spec.licence,
                licence,
                f"`{spec.sha256[:12]}`" + ("" if pinned else " MISMATCH"),
            ]
        )

    # Labels.
    tiers = Counter(d.tier or "no label" for d in lock.drugs)
    years = Counter(
        "before 2016"
        if x.effective.year < 2016
        else "2016-2020"
        if x.effective.year <= 2020
        else "2021-2024"
        if x.effective.year <= 2024
        else "2025 or later"
        for x in labels.values()
    )
    mismatched = [
        x.setid for x in labels.values() if sha256_file(root / "labels" / x.path) != x.sha256
    ]
    old = sorted((x for x in labels.values() if x.effective.year < 2016), key=lambda x: x.effective)

    # Quotes.
    def stored_pages(stored: str) -> tuple[Page, ...]:
        if stored.startswith("label:"):
            setid = stored.removeprefix("label:").split("@")[0]
            return section_pages((root / "labels" / labels[setid].path).read_bytes())
        return pages[stored]

    quote_rows = []
    failures = 0
    for s in SOURCES.values():
        problems = check_quote(s.quote, stored_pages(s.stored), locator=s.locator, table=s.table)
        failures += bool(problems)
        kind = "label" if s.stored.startswith("label:") else "document"
        quote_rows.append(
            [
                f"`{s.id}`",
                kind,
                str(len(fragments(s.quote))),
                "yes (row check)" if s.table else "",
                "verified" if not problems else "; ".join(problems),
            ]
        )
    links = {
        f"{a}: {x.locator}": x for _, ls in MONITORING.values() for x in ls for a in [x.analyte]
    }
    for name, link in sorted(links.items()):
        problems = check_quote(link.quote, stored_pages(link.stored), locator=link.locator)
        failures += bool(problems)
        quote_rows.append(
            [
                f"monitoring link {name}",
                "document",
                str(len(fragments(link.quote))),
                "",
                "verified" if not problems else "; ".join(problems),
            ]
        )

    # Index and retrieval.
    index_path = root / f"index-{args.cohort}.sqlite"
    checks = load_checks(CONFIG_DIR / "retrieval-checks.yaml")
    guideline_ids = set(pages)
    with KnowledgeIndex(index_path) as index:
        chunks = index.chunks()
        kinds = Counter(
            "drug labels" if c.source.startswith("label:") else c.source for c in chunks
        )
        digests = sorted(index.embedding_digests(EMBEDDING_MODEL))
        embedded = len(chunks) - len(index.without_embedding(EMBEDDING_MODEL))
        client: OllamaClient | None = OllamaClient(settings, model=EMBEDDING_MODEL)
        try:
            assert client is not None
            live = client.model_digest()
            if live is None or live not in digests:
                client = None
        except httpx.HTTPError:
            client = None
        results: dict[str, dict[str, list[int | None]]] = {
            lang: {"bm25": [], "dense": [], "hybrid": []} for lang in ("en", "it")
        }
        check_rows = []
        for check in checks:
            sources = {s for s in check.sources if s != "guidelines"}
            if "guidelines" in check.sources:
                sources |= guideline_ids
            row = [f"`{check.id}`"]
            for lang in ("en", "it"):
                question = getattr(check, lang)
                vector = client.embed([question])[0] if client else None
                bm25 = first_rank(index, index.bm25(question, RANKS, sources), check.expect)
                dense = (
                    first_rank(
                        index,
                        index.dense(vector, EMBEDDING_MODEL, RANKS, sources),
                        check.expect,
                    )
                    if vector
                    else None
                )
                hybrid = first_rank(
                    index,
                    index.hybrid(question, vector, EMBEDDING_MODEL, limit=RANKS, sources=sources),
                    check.expect,
                )
                for method, rank in (("bm25", bm25), ("dense", dense), ("hybrid", hybrid)):
                    results[lang][method].append(rank)
                row += [
                    rank_text(bm25),
                    rank_text(dense) if vector else "not run",
                    rank_text(hybrid),
                ]
            check_rows.append(row)
        meta: dict[str, Any] = index.meta()

    provenance: dict[str, Any] = {
        "documents_config": file_digest(CONFIG_DIR / "documents.yaml")[:12],
        "lock": file_digest(lock_path(args.cohort))[:12],
        "code_digest": code_digest(scripts=SCRIPTS),
        "git": git_state(REPO_ROOT),
    }
    summary_rows = [
        [
            lang,
            method,
            recall(results[lang][method], 1),
            recall(results[lang][method], 5),
            recall(results[lang][method], RANKS),
        ]
        for lang in ("en", "it")
        for method in ("bm25", "dense", "hybrid")
        if method != "dense" or client is not None
    ]
    lines = [
        f"# Knowledge store: {args.cohort}",
        "",
        "> Generated by `scripts/knowledge_report.py` from the files in "
        "`$MEDGRAPH_DATA_DIR/knowledge` (`make knowledge`, `scripts/build_index.py`). The "
        "documents and labels themselves are never committed.",
        "",
        f"- Documents config `{provenance['documents_config']}`; label lock "
        f"`{provenance['lock']}` (rule `{lock.rule}`, chosen {lock.selected_on}); code digest "
        f"`{provenance['code_digest']}`; git commit `{provenance['git']['commit']}`"
        + (" with uncommitted changes" if provenance["git"]["dirty"] else "")
        + ".",
        "",
        "## Guideline documents",
        "",
        table(
            [
                "document",
                "format",
                "pages",
                "with printed number",
                "reference pages left out",
                "licence",
                "licence statement found on",
                "SHA-256",
            ],
            doc_rows,
        ),
        "",
        "## Drug labels (DailyMed)",
        "",
        f"- {len(lock.drugs)} RxNorm codes prescribed in the cohort; {len(labels)} distinct "
        f"labels ({sum(x.bytes for x in labels.values()) / 1e6:.1f} MB); stored files that "
        f"differ from the lock: {len(mismatched)}.",
        "",
        table(["chosen from", "RxNorm codes"], [[k, str(v)] for k, v in tiers.most_common()]),
        "",
        table(
            ["label effective date", "labels"],
            [
                [k, str(years[k])]
                for k in ("before 2016", "2016-2020", "2021-2024", "2025 or later")
            ],
        ),
        "",
        "What the labels state about kidney function and haemoglobin (sentences, and the "
        "labels they occur in; a heuristic census, not a reading of each label):",
        "",
        table(["statement", "sentences", "labels"], census(lock, root)),
        "",
        "The medication rules use only eGFR thresholds and monitoring intervals that a label "
        "states and the record can check (`rules/medications.py`). Creatinine-clearance "
        "thresholds are not used: medgraph does not compute creatinine clearance.",
        "",
        "The rule takes the most recently published label of the best tier, so some choices are "
        "old copies by repackagers. Labels effective before 2016:",
        "",
        table(
            ["label", "labeler", "effective", "set ID"],
            [
                [
                    spl_meta((root / "labels" / x.path).read_bytes()).name or x.title[:60],
                    x.labeler,
                    str(x.effective),
                    f"`{x.setid}`",
                ]
                for x in old
            ],
        ),
        "",
        "RxNorm codes with no DailyMed label:",
        "",
        table(
            ["RxCUI", "as recorded", "patients"],
            [[d.rxcui, d.name[:80], str(d.patients)] for d in lock.drugs if d.label is None],
        ),
        "",
        "## Quotes checked against the stored text",
        "",
        f"{len(quote_rows) - failures} of {len(quote_rows)} cited quotes verified. Each fragment "
        "occurs in the stored text (up to case, spacing and punctuation) on a page or in the "
        "label section the locator names. A table transcription is checked row by row: every "
        "word and number of a row occurs on the quote's page.",
        "",
        table(["quote", "stored in", "fragments", "table transcription", "result"], quote_rows),
        "",
        "## Retrieval index",
        "",
        f"- {len(chunks):,} passages of at most {meta.get('max_words')} words; "
        f"{embedded:,} embedded with `{EMBEDDING_MODEL}` (digest "
        + (", ".join(f"`{d[:12]}`" for d in digests) or "none")
        + ").",
        "",
        table(["source", "passages"], [[k, f"{v:,}"] for k, v in sorted(kinds.items())]),
        "",
        "## Retrieval smoke checks",
        "",
        "Rank of the first passage containing the expected text, among the first "
        f'{RANKS} ("-": not found). The questions were written by the developer, who knows '
        "the documents: this shows the right passage is reachable, it does not measure "
        "retrieval quality. Each search is limited to the guidelines plus, where named, one "
        "label, as explanations will be.",
        "",
        table(
            [
                "check",
                "EN BM25",
                "EN dense",
                "EN hybrid",
                "IT BM25",
                "IT dense",
                "IT hybrid",
            ],
            check_rows,
        ),
        "",
        table(["language", "method", "rank 1", "top 5", f"top {RANKS}"], summary_rows),
        "",
    ]
    out = args.out or REPO_ROOT / "docs" / "data" / f"knowledge-{args.cohort}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out}; quotes verified {len(quote_rows) - failures}/{len(quote_rows)}")
    return 1 if failures or mismatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
