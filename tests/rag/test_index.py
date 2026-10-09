"""The retrieval index: BM25, dense ranking, fusion, and keeping embeddings in step."""

from pathlib import Path

import pytest

from medgraph.rag.chunks import Chunk
from medgraph.rag.index import Hit, KnowledgeIndex, fts_query, rrf


def chunk(n: int, text: str, source: str = "doc") -> Chunk:
    return Chunk(
        id=f"{source}@v1/{n}.1", source=source, version="v1", title="T", where=f"p. {n}", text=text
    )


CHUNKS = [
    chunk(1, "Assess albuminuria and GFR at least annually in people with CKD."),
    chunk(2, "Haemoglobin cutoffs define anaemia in adults."),
    chunk(3, "Metformin is contraindicated below an eGFR of 30.", source="label:x"),
]


@pytest.fixture
def index(tmp_path: Path) -> KnowledgeIndex:
    ix = KnowledgeIndex(tmp_path / "index.sqlite")
    ix.replace_chunks(CHUNKS, {"cohort": "test"})
    return ix


def test_fts_query_quotes_every_word() -> None:
    assert fts_query('GFR "OR" NEAR(x) <60') == '"60" OR "gfr" OR "near" OR "or" OR "x"'
    assert fts_query("< >=") == ""


def test_bm25_ranks_the_matching_passage_first(index: KnowledgeIndex) -> None:
    hits = index.bm25("how often to assess GFR in CKD")
    assert hits[0].chunk == CHUNKS[0].id
    assert hits[0].rank == 1
    assert index.bm25("") == []
    assert index.chunk(CHUNKS[2].id) == CHUNKS[2]
    assert index.meta() == {"cohort": "test"}


def test_dense_ranking_uses_cosine_and_breaks_ties_by_id(index: KnowledgeIndex) -> None:
    index.save_embeddings("m", "d1", [c.id for c in CHUNKS], [[1, 0], [0, 2], [0, 5]])
    hits = index.dense([0, 1], "m")
    # Vectors are normalized, so passages 2 and 3 tie at 1.0; the lower ID comes first.
    assert [h.chunk for h in hits] == [CHUNKS[1].id, CHUNKS[2].id, CHUNKS[0].id]
    assert hits[0].score == pytest.approx(1.0)
    with pytest.raises(ValueError, match="zero"):
        index.save_embeddings("m", "d1", [CHUNKS[0].id], [[0, 0]])


def test_rrf_fuses_by_rank_deterministically() -> None:
    a = [Hit("x", 1, 9.0), Hit("y", 2, 5.0)]
    b = [Hit("y", 1, 0.9), Hit("z", 2, 0.8)]
    fused = rrf([a, b])
    assert [h.chunk for h in fused] == ["y", "x", "z"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert [h.chunk for h in rrf([[Hit("b", 1, 0)], [Hit("a", 1, 0)]])] == ["a", "b"]


def test_changed_passages_lose_their_embeddings(index: KnowledgeIndex) -> None:
    index.save_embeddings("m", "d1", [c.id for c in CHUNKS], [[1, 0], [0, 1], [1, 1]])
    changed = [CHUNKS[0], CHUNKS[1].model_copy(update={"text": "New text."})]
    index.replace_chunks(changed, {"cohort": "test"})
    assert [c.id for c in index.chunks()] == [CHUNKS[0].id, CHUNKS[1].id]
    assert [c.id for c in index.without_embedding("m")] == [CHUNKS[1].id]
    assert index.bm25("new text")[0].chunk == CHUNKS[1].id
    assert index.bm25("metformin") == []
    assert index.embedding_digests("m") == {"d1"}


def test_search_can_be_limited_to_sources(index: KnowledgeIndex) -> None:
    index.save_embeddings("m", "d1", [c.id for c in CHUNKS], [[1, 0], [0, 1], [0, 1]])
    assert [h.chunk for h in index.bm25("metformin eGFR GFR", sources={"doc"})] == [CHUNKS[0].id]
    assert index.bm25("metformin", sources=set()) == []
    assert [h.chunk for h in index.dense([0, 1], "m", sources={"label:x"})] == [CHUNKS[2].id]
    fused = index.hybrid("metformin", [0, 1], "m", sources={"label:x"})
    assert [h.chunk for h in fused] == [CHUNKS[2].id]
