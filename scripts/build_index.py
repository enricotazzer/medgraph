"""Build the retrieval index of a cohort's knowledge store; with ``--embed``, embed it too.

    uv run python scripts/build_index.py dev-1000
    OLLAMA_MODELS=/Volumes/T7/ollama-models scripts/dev/llm_run.sh index-embed \
        uv run python scripts/build_index.py dev-1000 --embed

Passages come from the pinned documents (``configs/knowledge/documents.yaml``) and the
cohort's pinned labels (``configs/knowledge/labels-<cohort>.lock.json``). Every file is checked
against its pin first; a changed file stops the build. The index goes to
``$MEDGRAPH_DATA_DIR/knowledge/index-<cohort>.sqlite``.

``--embed`` adds a vector for every passage that has none, from a local Ollama embedding model
(``--model``, default bge-m3). Passages embedded with another digest of the same model are
embedded again, so the index never mixes model versions.
"""

import argparse
import hashlib
from collections import Counter
from pathlib import Path

from fetch_knowledge import CONFIG_DIR, lock_path
from medgraph.agent.llm import OllamaClient
from medgraph.rag.chunks import MAX_WORDS, Chunk, document_chunks, label_chunks, words
from medgraph.rag.dailymed import LabelLock, spl_meta
from medgraph.rag.documents import extract_pages, load_documents, reference_pages
from medgraph.rag.fetch import sha256_file
from medgraph.rag.index import KnowledgeIndex, embedding_text
from medgraph.rag.spl import sections
from medgraph.rag.store import KnowledgeStore
from medgraph.settings import Settings

EMBEDDING_MODEL = "bge-m3"
BATCH = 16


class PinError(RuntimeError):
    """A stored file does not match its pin."""


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def all_chunks(kstore: KnowledgeStore, lock: LabelLock) -> list[Chunk]:
    chunks: list[Chunk] = []
    for spec in load_documents(CONFIG_DIR / "documents.yaml"):
        path = kstore.document_path(spec.filename)
        if sha256_file(path) != spec.sha256:
            raise PinError(f"{path} does not match its pinned SHA-256; run make knowledge")
        pages = extract_pages(path, spec)
        skipped = reference_pages(pages, spec)
        pages = tuple(p for p in pages if p.number not in skipped)
        chunks += document_chunks(
            spec.id, spec.sha256[:12], spec.title, pages, running=spec.is_running
        )
    for label in lock.labels().values():
        path = kstore.label_path(label.path)
        if sha256_file(path) != label.sha256:
            raise PinError(f"{path} does not match the lock; run make knowledge")
        xml = path.read_bytes()
        name = spl_meta(xml).name or label.title
        chunks += label_chunks(label.setid, label.version, name, sections(xml))
    return chunks


def embed(index: KnowledgeIndex, client: OllamaClient, model: str) -> int:
    digest = client.model_digest()
    if digest is None:
        raise SystemExit(f"{model} is not installed on the Ollama server")
    stale = index.embedding_digests(model) - {digest}
    if stale:
        print(f"re-embedding: stored vectors come from other digests of {model}: {sorted(stale)}")
        with index.db:
            index.db.execute("DELETE FROM embeddings WHERE model = ?", (model,))
    todo = index.without_embedding(model)
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        vectors = client.embed([embedding_text(c) for c in batch])
        index.save_embeddings(model, digest, [c.id for c in batch], vectors)
        done = start + len(batch)
        if done % (BATCH * 25) == 0 or done == len(todo):
            print(f"  embedded {done}/{len(todo)}", flush=True)
    return len(todo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the knowledge store's retrieval index.")
    parser.add_argument("cohort")
    parser.add_argument("--embed", action="store_true", help="add embeddings (local Ollama)")
    parser.add_argument("--model", default=EMBEDDING_MODEL)
    args = parser.parse_args(argv)
    settings = Settings()
    kstore = KnowledgeStore(settings.knowledge_dir)
    lock_file = lock_path(args.cohort)
    lock = LabelLock.model_validate_json(lock_file.read_text(encoding="utf-8"))
    chunks = all_chunks(kstore, lock)
    meta = {
        "cohort": args.cohort,
        "documents_config_sha256": file_digest(CONFIG_DIR / "documents.yaml"),
        "labels_lock_sha256": file_digest(lock_file),
        "max_words": str(MAX_WORDS),
        "passages": str(len(chunks)),
    }
    path = settings.knowledge_dir / f"index-{args.cohort}.sqlite"
    with KnowledgeIndex(path) as index:
        index.replace_chunks(chunks, meta)
        kinds = Counter("label" if c.source.startswith("label:") else c.source for c in chunks)
        print(f"index {path}: {len(chunks)} passages, {sum(words(c.text) for c in chunks):,} words")
        for kind, n in sorted(kinds.items()):
            print(f"  {kind}: {n}")
        if args.embed:
            client = OllamaClient(settings, model=args.model)
            print(f"embedded {embed(index, client, args.model)} passages with {args.model}")
        missing = len(index.without_embedding(args.model))
        print(f"passages without a {args.model} embedding: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
