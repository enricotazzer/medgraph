"""The retrieval index: passages, BM25 and embeddings in one SQLite file.

``$MEDGRAPH_DATA_DIR/knowledge/index-<cohort>.sqlite`` (``scripts/build_index.py``):
- ``chunks``: every passage (``medgraph.rag.chunks``) with its stable ID and where it came from;
- ``fts``: an FTS5 table over the passages, ranked with BM25. The tokenizer stems English
  (Porter), which suits the English sources; queries in other languages rely on the dense
  side;
- ``embeddings``: one vector per passage and embedding model, with the model's digest. Vectors
  are L2-normalized, so a dot product is the cosine similarity.

Ranking is deterministic:
- ties are broken by passage ID;
- hybrid search fuses the BM25 and dense rankings with reciprocal rank fusion (RRF,
  k = 60), which uses ranks only, so the two score scales never need calibrating.

The text that is embedded is ``title``, ``where`` and ``text`` on separate lines, so a label
passage carries its drug and section.
"""

import sqlite3
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Self

import numpy as np

from medgraph.rag.chunks import Chunk
from medgraph.rag.text import tokens

RRF_K = 60
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (
    n INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    version TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    code TEXT,
    text TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    text, content='chunks', content_rowid='n', tokenize='porter unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS embeddings (
    chunk TEXT NOT NULL REFERENCES chunks (id),
    model TEXT NOT NULL,
    digest TEXT NOT NULL,
    vector BLOB NOT NULL,
    PRIMARY KEY (chunk, model)
) WITHOUT ROWID;
"""


@dataclass(frozen=True)
class Hit:
    chunk: str  # passage ID
    rank: int  # from 1
    score: float  # BM25 (higher is better here), cosine, or fused RRF score


def embedding_text(chunk: Chunk) -> str:
    return f"{chunk.title}\n{chunk.where}\n{chunk.text}"


def fts_query(query: str) -> str:
    """Every word of the query as a quoted FTS5 term, OR-ed: no FTS5 syntax gets through."""
    words = sorted({t for t in tokens(query) if t.replace(".", "").replace("-", "").isalnum()})
    return " OR ".join(f'"{w}"' for w in words)


def rrf(rankings: Iterable[Sequence[Hit]], k: int = RRF_K, limit: int = 10) -> list[Hit]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for hit in ranking:
            scores[hit.chunk] = scores.get(hit.chunk, 0.0) + 1.0 / (k + hit.rank)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [Hit(chunk, rank, score) for rank, (chunk, score) in enumerate(ordered, start=1)]


class KnowledgeIndex:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self._cache: dict[str, tuple[list[str], list[str], np.ndarray]] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.db.close()

    # --- writing --------------------------------------------------------------------------

    def replace_chunks(self, chunks: Sequence[Chunk], meta: dict[str, str]) -> None:
        """Store these passages instead of the current ones. A passage whose ID and text are
        unchanged keeps its row and embeddings; any other is replaced."""
        ids = [c.id for c in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate passage IDs")
        self._cache.clear()
        stored = dict(self.db.execute("SELECT id, text FROM chunks").fetchall())
        keep = {c.id for c in chunks if stored.get(c.id) == c.text}
        drop = [(i,) for i in stored if i not in keep]
        with self.db:
            self.db.executemany("DELETE FROM embeddings WHERE chunk = ?", drop)
            self.db.executemany("DELETE FROM chunks WHERE id = ?", drop)
            self.db.executemany(
                "INSERT INTO chunks (id, source, version, title, location, code, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (c.id, c.source, c.version, c.title, c.where, c.code, c.text)
                    for c in chunks
                    if c.id not in keep
                ],
            )
            self.db.execute("INSERT INTO fts (fts) VALUES ('rebuild')")
            self.db.execute("DELETE FROM meta")
            self.db.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))

    def save_embeddings(
        self, model: str, digest: str, ids: Sequence[str], vectors: Sequence[Sequence[float]]
    ) -> None:
        array = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        if np.any(norms == 0):
            raise ValueError("a zero embedding cannot be normalized")
        array = array / norms
        self._cache.clear()
        with self.db:
            self.db.executemany(
                "INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?)",
                [(i, model, digest, v.tobytes()) for i, v in zip(ids, array, strict=True)],
            )

    # --- reading --------------------------------------------------------------------------

    def meta(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT key, value FROM meta").fetchall())

    def chunks(self, ids: Iterable[str] | None = None) -> list[Chunk]:
        rows = self.db.execute(
            "SELECT id, source, version, title, location, code, text FROM chunks ORDER BY n"
        ).fetchall()
        wanted = None if ids is None else set(ids)
        return [
            Chunk(id=r[0], source=r[1], version=r[2], title=r[3], where=r[4], code=r[5], text=r[6])
            for r in rows
            if wanted is None or r[0] in wanted
        ]

    def chunk(self, chunk_id: str) -> Chunk:
        found = self.chunks([chunk_id])
        if not found:
            raise KeyError(chunk_id)
        return found[0]

    def without_embedding(self, model: str) -> list[Chunk]:
        done = {
            r[0] for r in self.db.execute("SELECT chunk FROM embeddings WHERE model = ?", (model,))
        }
        return [c for c in self.chunks() if c.id not in done]

    def embedding_digests(self, model: str) -> set[str]:
        return {
            r[0]
            for r in self.db.execute(
                "SELECT DISTINCT digest FROM embeddings WHERE model = ?", (model,)
            )
        }

    def bm25(
        self, query: str, limit: int = 50, sources: Collection[str] | None = None
    ) -> list[Hit]:
        """BM25 ranking; ``sources`` keeps passages of these sources only (document IDs, or
        ``label:<set id>``)."""
        match = fts_query(query)
        if not match or (sources is not None and not sources):
            return []
        where, params = "", []
        if sources is not None:
            where = f" AND c.source IN ({','.join('?' * len(sources))})"
            params = sorted(sources)
        rows = self.db.execute(
            "SELECT c.id, -bm25(fts) AS score FROM fts JOIN chunks AS c ON c.n = fts.rowid "
            f"WHERE fts MATCH ?{where} ORDER BY score DESC, c.id LIMIT ?",
            (match, *params, limit),
        ).fetchall()
        return [Hit(r[0], rank, float(r[1])) for rank, r in enumerate(rows, start=1)]

    def _vectors(self, model: str) -> tuple[list[str], list[str], np.ndarray]:
        """Passage IDs, their sources and their vectors (cached until the index changes)."""
        if model not in self._cache:
            rows = self.db.execute(
                "SELECT e.chunk, c.source, e.vector FROM embeddings AS e "
                "JOIN chunks AS c ON c.id = e.chunk WHERE e.model = ? ORDER BY e.chunk",
                (model,),
            ).fetchall()
            matrix = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
            self._cache[model] = (
                [r[0] for r in rows],
                [r[1] for r in rows],
                matrix.reshape(len(rows), -1) if rows else matrix.reshape(0, 0),
            )
        return self._cache[model]

    def dense(
        self,
        vector: Sequence[float],
        model: str,
        limit: int = 50,
        sources: Collection[str] | None = None,
    ) -> list[Hit]:
        ids, of, matrix = self._vectors(model)
        if not ids:
            return []
        query = np.asarray(vector, dtype=np.float32)
        scores = matrix @ (query / np.linalg.norm(query))
        keep = [i for i in range(len(ids)) if sources is None or of[i] in sources]
        order = sorted(keep, key=lambda i: (-float(scores[i]), ids[i]))[:limit]
        return [Hit(ids[i], rank, float(scores[i])) for rank, i in enumerate(order, start=1)]

    def hybrid(
        self,
        query: str,
        vector: Sequence[float] | None,
        model: str,
        *,
        limit: int = 10,
        pool: int = 50,
        sources: Collection[str] | None = None,
    ) -> list[Hit]:
        rankings = [self.bm25(query, pool, sources)]
        if vector is not None:
            rankings.append(self.dense(vector, model, pool, sources))
        return rrf(rankings, limit=limit)
