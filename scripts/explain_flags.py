"""Explain a sample of a cohort's follow-up flags with the local model, and check notes.

    OLLAMA_MODELS=/Volumes/T7/ollama-models scripts/dev/llm_run.sh explain-dev-1000 \
        uv run python -u scripts/explain_flags.py dev-1000
    uv run python scripts/explain_flags.py dev-1000 --report-only

The sample is stratified by rule (``--per-rule`` flags of each, chosen by a seeded hash of the
patient and rule, so it does not depend on the order of the store). Flags read while developing
the prompt and the checks (``DEVELOPMENT``) are never in it; ``--dev N`` runs N flags per rule
ranked after the sample instead, for that development, and writes only ``review-dev.md``.
For each flag:

1. the evidence pack (``medgraph.agent.evidence``): the query is embedded with bge-m3 and the
   passages retrieved, before the chat model is loaded;
2. four explanations, clinician or personal, English or Italian (``medgraph.agent.explain``),
   each checked sentence by sentence, with the rule's template when too little passes;
3. the note check (``medgraph.agent.notes``).

Everything is cached under ``$MEDGRAPH_DATA_DIR/runs/explanations/<cohort>/<patient>/<rule>.json``
and reused while the flag, the pack's inputs, the model digests and the prompt versions are
unchanged, so an interrupted run resumes. The report goes to
``docs/results/explanations-<cohort>.md`` (aggregates only); a review file with every variant of
some flags side by side goes to the run folder, outside the repository.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any

from build_index import EMBEDDING_MODEL
from build_knowledge_graph import load_current
from evaluate_extraction import code_digest
from evaluate_flags import evaluation_date
from generate_synthea import git_state
from medgraph.agent import explain as explainer
from medgraph.agent import notes as note_check
from medgraph.agent.evidence import (
    PACK_VERSION,
    EvidencePack,
    build_pack,
    document_citations,
    flag_hash,
    query_for,
    quoted_passages,
    retrieve,
    searched_sources,
)
from medgraph.agent.explain import VARIANTS, Explanation, explain
from medgraph.agent.llm import OllamaClient
from medgraph.agent.notes import NoteCheck, check_notes
from medgraph.graph.schema import PatientGraph
from medgraph.graph.store import GraphStore
from medgraph.rag.fetch import write_atomic
from medgraph.rag.index import KnowledgeIndex
from medgraph.rules.model import RULESET, Flag, PatientRules
from medgraph.settings import Settings
from profile_cohort import REPO_ROOT, load_meta, pct, table

SCRIPTS = ("explain_flags.py", "build_index.py", "build_knowledge_graph.py", "evaluate_flags.py")
SEED = "explain-sample-v1"
PER_RULE = 6
REVIEW_PER_RULE = 2
# Flags read while the prompt and the checks were developed: never in the evaluation sample, so
# that the sample's results are not tuned to it. (patient, rule), with the run that read them.
DEVELOPMENT: frozenset[tuple[str, str]] = frozenset(
    {
        # explain-v1 smoke run, 2026-10-19
        ("5e225d85-906f-e95b-e299-ad0a5d103124", "anaemia-criteria-no-diagnosis"),
        # --dev 1 with explain-v2 and explain-v3, 2026-10-19
        ("f75caf9c-ba51-5e74-1461-a54b3d07b53f", "anaemia-criteria-no-diagnosis"),
        ("70596951-cfc1-c707-3352-daf5a3a1fddc", "ckd-albuminuria-follow-up"),
        ("c10cc155-32a9-6303-3673-f64986b27faa", "ckd-criteria-no-diagnosis"),
        ("337d2cd0-ed14-1231-4bad-09ba5bd25734", "ckd-gfr-follow-up"),
        ("63f4d267-4d83-647e-99ed-6a3af4e4f812", "ckd-haemoglobin-follow-up"),
        ("7c9b01e3-39e9-3690-4099-4a5babdf8b25", "metformin-egfr-below-30"),
    }
)


def variant_key(audience: str, language: str) -> str:
    return f"{audience}-{language}"


def sample_flags(
    rows: Sequence[tuple[str, PatientRules]],
    per_rule: int,
    seed: str = SEED,
    *,
    skip: int = 0,
    exclude: Collection[tuple[str, str]] = DEVELOPMENT,
) -> list[tuple[str, Flag]]:
    """Up to ``per_rule`` flags of each rule, in a seeded order that ignores the store's order,
    after the first ``skip`` of each rule. Flags in ``exclude`` are never chosen."""
    by_rule: dict[str, list[tuple[str, Flag]]] = defaultdict(list)
    for patient, rules in rows:
        for flag in rules.flags:
            if (patient, flag.rule) not in exclude:
                by_rule[flag.rule].append((patient, flag))

    def key(item: tuple[str, Flag]) -> str:
        return hashlib.sha256(f"{seed}|{item[0]}|{item[1].rule}".encode()).hexdigest()

    sample = []
    for rule in sorted(by_rule):
        sample += sorted(by_rule[rule], key=key)[skip : skip + per_rule]
    return sample


def record_path(run_dir: Path, patient: str, rule: str) -> Path:
    return run_dir / patient / f"{rule}.json"


def load_record(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def save_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, (json.dumps(record, indent=1, sort_keys=True) + "\n").encode())


def digest_of(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


# --- the run ------------------------------------------------------------------------------


def run(
    sample: Sequence[tuple[str, Flag]],
    *,
    run_dir: Path,
    as_of: dt.date,
    store: GraphStore,
    index: KnowledgeIndex,
    knowledge: Any,
    embedder: OllamaClient,
    chat: OllamaClient,
) -> dict[str, str]:
    """Packs, then explanations, then note checks, reusing what the cache still holds."""
    embedding_digest = embedder.model_digest()
    chat_digest = chat.model_digest()
    if embedding_digest is None or chat_digest is None:
        raise SystemExit(f"{EMBEDDING_MODEL} or {chat.model} is not installed on the server")
    pack_inputs = digest_of(
        {
            "knowledge": knowledge.digest(),
            "index": index.meta(),
            "embedding": embedding_digest,
            "pack": PACK_VERSION,
        }
    )
    citations = document_citations(knowledge)
    graphs: dict[str, PatientGraph] = {}

    def graph_of(patient: str) -> PatientGraph:
        if patient not in graphs:
            graphs[patient] = store.load(patient)
        return graphs[patient]

    n = len(sample)
    print(f"{n} flags; packs", flush=True)
    for i, (patient, flag) in enumerate(sample, 1):
        path = record_path(run_dir, patient, flag.rule)
        record = load_record(path)
        if record.get("flag_hash") == flag_hash(flag) and record.get("pack_inputs") == pack_inputs:
            continue
        graph = graph_of(patient)
        query = query_for(flag)
        sources = searched_sources(flag, graph, knowledge)
        (vector,) = embedder.embed([query])
        passages = retrieve(
            index,
            query,
            vector,
            EMBEDDING_MODEL,
            sources=sources,
            exclude=quoted_passages(flag, knowledge),
        )
        pack = build_pack(
            patient, flag, passages, query=query, searched=sources, citations=citations
        )
        save_record(
            path,
            {
                "patient": patient,
                "rule": flag.rule,
                "as_of": as_of.isoformat(),
                "flag_hash": pack.flag_hash,
                "flag": flag.model_dump(mode="json"),
                "pack_inputs": pack_inputs,
                "pack": pack.model_dump(mode="json"),
                "explanations": {},
                "note_check": None,
            },
        )
        print(f"  [{i}/{n}] pack {patient[:8]} {flag.rule}: {len(passages)} passages", flush=True)

    print("explanations", flush=True)
    for i, (patient, flag) in enumerate(sample, 1):
        path = record_path(run_dir, patient, flag.rule)
        record = load_record(path)
        pack = EvidencePack.model_validate(record["pack"])
        for audience, language in VARIANTS:
            key = variant_key(audience, language)
            done = record["explanations"].get(key)
            if (
                done
                and done["pack_digest"] == pack.digest()
                and done["model_digest"] == chat_digest
                and done["prompt_version"] == explainer.PROMPT_VERSION
            ):
                continue
            result = explain(flag, pack, audience, language, chat, chat_digest)
            record["explanations"][key] = result.model_dump(mode="json")
            save_record(path, record)
            print(
                f"  [{i}/{n}] {patient[:8]} {flag.rule} {key}: {result.origin}"
                f"{f' ({result.fallback})' if result.fallback else ''}, {result.seconds:.0f} s",
                flush=True,
            )

    print("note checks", flush=True)
    for i, (patient, flag) in enumerate(sample, 1):
        path = record_path(run_dir, patient, flag.rule)
        record = load_record(path)
        done = record.get("note_check")
        if (
            done
            and done["model_digest"] == chat_digest
            and done["prompt_version"] == note_check.PROMPT_VERSION
        ):
            continue
        checked = check_notes(graph_of(patient), flag, as_of, chat, chat_digest)
        record["note_check"] = checked.model_dump(mode="json")
        save_record(path, record)
        found = "mention kept" if checked.mention else checked.rejected or "no mention"
        print(f"  [{i}/{n}] notes {patient[:8]} {flag.rule}: {found}", flush=True)
    return {"chat_digest": chat_digest, "embedding_digest": embedding_digest}


# --- the report ---------------------------------------------------------------------------

# Reasons that can carry text the model wrote (a cited "ID", a note's ID, an error): the report
# shows only their fixed part, since the model's text can quote the record. Any other reason that
# is not a short plain phrase is shown as "other". (The evaluation run's first report printed
# whole notes the model had returned as a note's ID.)
_VARIABLE_REASONS = ("unknown citation", "unknown note", "unknown stance", "model call failed")
_PLAIN_REASON = re.compile(r"[A-Za-z0-9 ,;()'-]{1,60}")


def reported(reason: str) -> str:
    """What a report may show of a reason."""
    for fixed in _VARIABLE_REASONS:
        if reason.startswith(fixed):
            return fixed
    return reason if _PLAIN_REASON.fullmatch(reason) else "other"


def summarize(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    variants: dict[str, dict[str, Any]] = {}
    for audience, language in VARIANTS:
        key = variant_key(audience, language)
        results = [
            Explanation.model_validate(r["explanations"][key])
            for r in records
            if key in r.get("explanations", {})
        ]
        # The model's sentences: not the closing sentence code adds, nor the template's.
        kept = [s for e in results if e.origin == "model" for s in e.sentences if not s.by_code]
        unused = sum(len(e.unused) for e in results)
        dropped = sum(len(e.dropped) for e in results)
        reasons = Counter(
            reported(reason) for e in results for d in e.dropped for reason in d.reasons
        )
        variants[key] = {
            "flags": len(results),
            "model": sum(e.origin == "model" for e in results),
            "template": sum(e.origin == "template" for e in results),
            "fallbacks": Counter(
                reported(e.fallback or "") for e in results if e.origin == "template"
            ),
            "returned": len(kept) + unused + dropped,
            "kept": len(kept),
            "unused": unused,
            "dropped": dropped,
            "reasons": reasons,
            "cites_per_sentence": (statistics.mean(len(s.cites) for s in kept) if kept else 0.0),
            "cite_passage": sum(any(c.startswith("P") for c in s.cites) for s in kept),
            "seconds": statistics.median(e.seconds for e in results) if results else 0.0,
            "output_tokens": statistics.median(e.output_tokens for e in results) if results else 0,
        }
    notes: dict[str, dict[str, Any]] = {}
    for r in records:
        if not r.get("note_check"):
            continue
        check = NoteCheck.model_validate(r["note_check"])
        row = notes.setdefault(
            r["rule"],
            {
                "flags": 0,
                "with_notes": 0,
                "quoted": 0,
                "kept": 0,
                "rejected": Counter(),
                "stance": Counter(),
            },
        )
        row["flags"] += 1
        row["with_notes"] += bool(check.notes)
        row["quoted"] += bool(check.mention or check.rejected)
        row["kept"] += bool(check.mention)
        if check.rejected:
            row["rejected"][reported(check.rejected)] += 1
        if check.mention:
            row["stance"][check.mention.stance] += 1
    return {"variants": variants, "notes": notes, "flags": len(records)}


def _rewritten(provenance: dict[str, Any]) -> list[str]:
    lines = []
    if note := provenance.get("reconstructed"):
        lines.append(f"- The run's provenance was {note}.")
    if report := provenance.get("report"):
        lines.append(
            f"- This report was rewritten from the cache on {report['date']} by code "
            f"`{report['code_digest'][:12]}`; the explanations come from the run above."
        )
    return lines


def _counts(counter: Counter[str]) -> str:
    return "; ".join(f"{k}: {v}" for k, v in counter.most_common()) or "none"


def report_markdown(
    summary: dict[str, Any], provenance: dict[str, Any], by_rule: Counter[str]
) -> str:
    v = summary["variants"]
    lines = [
        f"# Flag explanations and note check: {provenance['cohort']} (sample)",
        "",
        "Generated by `scripts/explain_flags.py`. Aggregates only; the explanations themselves "
        "stay in `$MEDGRAPH_DATA_DIR/runs/explanations/`.",
        "",
        "## Provenance",
        "",
        f"- Chat model `{provenance['model']}` (digest `{provenance['chat_digest'][:12]}`), "
        f"prompts `{explainer.PROMPT_VERSION}` and `{note_check.PROMPT_VERSION}`; embeddings "
        f"`{EMBEDDING_MODEL}` (digest `{provenance['embedding_digest'][:12]}`).",
        f"- Knowledge graph `{provenance['knowledge'][:12]}`; ruleset `{RULESET}`, flags as of "
        f"{provenance['as_of']}.",
        f"- Sample: up to {provenance['per_rule']} flags per rule, seed `{provenance['seed']}`; "
        f"{summary['flags']} flags. The {len(DEVELOPMENT)} flag(s) read while the prompt and the "
        "checks were developed are never sampled (`DEVELOPMENT` in the script). Code digest "
        f"`{provenance['code_digest'][:12]}`; git commit "
        f"`{provenance['git']['commit']}`"
        + (" with uncommitted changes." if provenance["git"]["dirty"] else "."),
        *_rewritten(provenance),
        "",
        table(["rule", "flags in the sample"], [[r, n] for r, n in sorted(by_rule.items())]),
        "",
        "## Explanations",
        "",
        "Each variant is the model's text when at least two sentences pass every check and one "
        "of them cites the flag or its evidence; otherwise the rule's template. Either way it "
        "ends with the flag's own suggestion of a doctor, added by code and not counted here. "
        "Sentences that passed in a variant that fell back to the template are counted as "
        "unused.",
        "",
        table(
            [
                "variant",
                "flags",
                "model text",
                "template",
                "sentences returned",
                "kept",
                "unused",
                "dropped",
                "citations per kept sentence",
                "kept citing a passage",
                "median s",
                "median output tokens",
            ],
            [
                [
                    key,
                    row["flags"],
                    f"{row['model']} ({pct(row['model'], row['flags'])})",
                    row["template"],
                    row["returned"],
                    row["kept"],
                    row["unused"],
                    f"{row['dropped']} ({pct(row['dropped'], row['returned'])})",
                    f"{row['cites_per_sentence']:.1f}",
                    row["cite_passage"],
                    f"{row['seconds']:.0f}",
                    f"{row['output_tokens']:.0f}",
                ]
                for key, row in v.items()
            ],
        ),
        "",
        "Why sentences were dropped (a sentence can fail more than one check):",
        "",
        table(["variant", "reasons"], [[key, _counts(row["reasons"])] for key, row in v.items()]),
        "",
        "Why the template was used:",
        "",
        table(["variant", "reasons"], [[key, _counts(row["fallbacks"])] for key, row in v.items()]),
        "",
        "## Note check",
        "",
        "On Synthea the notes are written from the coded record, so this tests the plumbing, not "
        "the check: a finding that is not coded is never mentioned.",
        "",
        table(
            [
                "rule",
                "flags",
                "with notes in the period",
                "model quoted a note",
                "quote kept",
                "quote refused",
                "stance of kept quotes",
            ],
            [
                [
                    rule,
                    row["flags"],
                    row["with_notes"],
                    row["quoted"],
                    row["kept"],
                    _counts(row["rejected"]),
                    _counts(row["stance"]),
                ]
                for rule, row in sorted(summary["notes"].items())
            ],
        ),
        "",
        "## What this does not measure",
        "",
        "- Whether a kept sentence is right: the checks catch invented numbers and dates, missing "
        "citations, diagnostic and treatment wording, and the wrong language. A sentence can "
        "cite the right item and still misread it, and numbers written as words are not checked.",
        "- Quality of the Italian and of the plain-language register: the review file in the run "
        "folder shows every variant of some flags side by side, for a person to judge.",
        "- The note check on real notes: that needs MIMIC-IV-Note, run locally.",
        "",
    ]
    return "\n".join(lines)


def review_markdown(records: Sequence[dict[str, Any]], per_rule: int = REVIEW_PER_RULE) -> str:
    """Every variant of up to ``per_rule`` flags per rule, with the pack they rest on."""
    shown: Counter[str] = Counter()
    lines = [
        "# Explanations to review",
        "",
        "Synthetic patients. For each flag: the evidence pack, the four variants (kept "
        "sentences with their citations; dropped ones with the reason) and the note check.",
        "",
    ]
    for r in records:
        if shown[r["rule"]] >= per_rule:
            continue
        shown[r["rule"]] += 1
        pack = EvidencePack.model_validate(r["pack"])
        lines += [f"## {r['rule']}, patient {r['patient'][:8]}", "", "### Evidence pack", ""]
        lines += [f"- **[{item.id}]** {item.text}" for item in pack.items]
        for audience, language in VARIANTS:
            key = variant_key(audience, language)
            if key not in r["explanations"]:
                continue
            e = Explanation.model_validate(r["explanations"][key])
            label = "model text" if e.origin == "model" else f"template ({e.fallback})"
            lines += ["", f"### {key}: {label}", ""]
            lines += [f"{s.text} `[{', '.join(s.cites)}]`" for s in e.sentences]
            if e.unused:
                lines += ["", "Passed, but not used (the template was):"]
                lines += [f"- {s.text} `[{', '.join(s.cites)}]`" for s in e.unused]
            if e.dropped:
                lines += ["", "Dropped:"]
                lines += [
                    f"- ~~{d.text}~~ `[{', '.join(d.cites)}]`: {'; '.join(d.reasons)}"
                    for d in e.dropped
                ]
        if r.get("note_check"):
            check = NoteCheck.model_validate(r["note_check"])
            lines += ["", "### Note check", ""]
            if check.mention:
                m = check.mention
                lines.append(f'Note of {m.date}: "{m.quote}" (model reads it as: {m.stance})')
            elif check.rejected:
                lines.append(f'Refused ({check.rejected}): "{check.model_quote}"')
            else:
                lines.append(f"No mention in {len(check.notes)} note(s) read.")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain a sample of follow-up flags.")
    parser.add_argument("cohort")
    parser.add_argument("--per-rule", type=int, default=PER_RULE)
    parser.add_argument("--seed", default=SEED)
    parser.add_argument("--limit", type=int, help="only the first N flags of the sample")
    parser.add_argument(
        "--dev",
        type=int,
        metavar="N",
        help="instead of the sample, N flags per rule ranked after it, to develop the prompt and "
        "the checks on; never reported (add the ones read to DEVELOPMENT)",
    )
    parser.add_argument("--report-only", action="store_true", help="report from the cache")
    args = parser.parse_args(argv)
    settings = Settings()
    knowledge = load_current(settings, args.cohort)
    if knowledge is None:
        parser.error("the knowledge graph is needed: run make knowledge-graph")
    as_of = evaluation_date(load_meta(settings.synthea_dir / args.cohort), None)
    run_dir = settings.runs_dir / "explanations" / args.cohort
    started = time.perf_counter()
    with (
        GraphStore(settings.graphs_dir / f"{args.cohort}.sqlite") as store,
        KnowledgeIndex(settings.knowledge_dir / f"index-{args.cohort}.sqlite") as index,
    ):
        rows = [
            (patient, PatientRules.model_validate_json(payload))
            for patient, payload in store.db.execute(
                "SELECT patient_id, payload FROM rules WHERE ruleset = ? AND as_of = ? "
                "ORDER BY patient_id",
                (RULESET, as_of.isoformat()),
            )
        ]
        if not rows:
            parser.error(f"no {RULESET} results as of {as_of}: run make flags")
        # A mistyped patient ID would leave a development flag in the sample, silently.
        flagged = {(patient, flag.rule) for patient, rules in rows for flag in rules.flags}
        if unmatched := sorted(DEVELOPMENT - flagged):
            print(f"warning: {len(unmatched)} DEVELOPMENT flag(s) match no flag here: {unmatched}")
        if args.dev:
            sample = sample_flags(rows, args.dev, args.seed, skip=args.per_rule)
        else:
            sample = sample_flags(rows, args.per_rule, args.seed)
        sample = sample[: args.limit or None]
        digests = {"chat_digest": "", "embedding_digest": ""}
        if not args.report_only:
            embedder = OllamaClient(settings, model=EMBEDDING_MODEL)
            chat = OllamaClient(settings)
            digests = run(
                sample,
                run_dir=run_dir,
                as_of=as_of,
                store=store,
                index=index,
                knowledge=knowledge,
                embedder=embedder,
                chat=chat,
            )
    records = [r for p, f in sample if (r := load_record(record_path(run_dir, p, f.rule)))]
    if args.report_only and records:
        first: dict[str, Any] = next(iter(records[0]["explanations"].values()), {})
        digests["chat_digest"] = first.get("model_digest") or ""
    provenance = {
        "cohort": args.cohort,
        "model": settings.llm_model,
        "knowledge": knowledge.digest(),
        "as_of": as_of.isoformat(),
        "per_rule": args.per_rule,
        "seed": args.seed,
        "code_digest": code_digest(scripts=SCRIPTS),
        "git": git_state(REPO_ROOT),
        **digests,
    }
    summary = summarize(records)
    by_rule = Counter(f.rule for _, f in sample)
    out = REPO_ROOT / "docs" / "results" / f"explanations-{args.cohort}.md"
    full = args.limit is None and not args.dev
    # A full run records its provenance; a report rewritten from the cache keeps the run's and
    # says which code rewrote it.
    run_file = run_dir / "run.json"
    if full and not args.report_only:
        save_record(run_file, provenance)
    elif args.report_only and run_file.exists():
        rewritten = {"code_digest": provenance["code_digest"], "git": provenance["git"]}
        provenance = {
            **load_record(run_file),
            "report": {**rewritten, "date": str(dt.datetime.now(dt.UTC).date())},
        }
    if full:
        out.write_text(report_markdown(summary, provenance, by_rule), encoding="utf-8")
    review = run_dir / ("review-dev.md" if args.dev else "review.md")
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text(
        review_markdown(records, per_rule=args.dev or REVIEW_PER_RULE), encoding="utf-8"
    )
    print(
        f"{len(records)} flags reported; "
        + (f"wrote {out} and {review}" if full else f"wrote {review} (no report for a part)")
        + f" in {time.perf_counter() - started:.0f} s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
