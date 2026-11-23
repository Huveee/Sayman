"""Document search: keyword search (BM25), fusion with semantic search, and reranking.

Keyword search (BM25):
- The text is lowercased by the Turkish rule (I→ı, İ→i) and split into runs of letters and digits.
- Words are cut down to their first 5 letters ("faturalarını" → "fatur"). Turkish suffixes come at the end of the
  word, so this simple cut usually merges forms of the same root. Digits stay as they are.

Fusion (hybrid): the top N results of keyword search and semantic search are merged by rank (reciprocal rank fusion):
the score of a chunk is the sum of 1/(k + r) over every list, where r is its rank in that list. The scores have
different scales, so rank is used instead of score.

Reranking ("karma_sirala"): the first candidates of the fused list are rescored with the reranker model.

Role filtering is done before scoring at every stage: a chunk the role cannot see enters no list.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from sayman.documents.library import Chunk, can_see

STEM_LENGTH = 5
STOPWORDS = frozenset("""
ve ile bir bu şu o için ne mi mı mu mü da de ki ya veya gibi kadar en çok daha her hangi kaç nasıl neden olarak
olan ise değil var yok biz bize bizi bizim ona onu onun sonra önce
""".split())
_WORD = re.compile(r"\w+")


def lower_tr(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def terms(text: str) -> list[str]:
    result = []
    for k in _WORD.findall(lower_tr(text)):
        if k in STOPWORDS or k == "_":
            continue
        result.append(k if any(c.isdigit() for c in k) else k[:STEM_LENGTH])
    return result


class BM25:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tf = [Counter(terms(p.arama_metni)) for p in chunks]
        self.lengths = [sum(t.values()) for t in self.tf]
        self.avg_length = sum(self.lengths) / len(self.lengths)
        df = Counter(t for tf in self.tf for t in tf)
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def scores(self, query: str, role: str) -> list[tuple[str, float]]:
        """For every chunk the role can see: (chunk_id, score); chunks with a score of 0 are not included."""
        query_terms = [t for t in dict.fromkeys(terms(query)) if t in self.idf]
        result = []
        for p, tf, u in zip(self.chunks, self.tf, self.lengths):
            if not can_see(role, p.roller):
                continue
            s = 0.0
            for t in query_terms:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * u / self.avg_length))
            if s > 0:
                result.append((p.parca_id, s))
        return result


def top_k(results: list[tuple[str, float]], k: int) -> list[tuple[str, float]]:
    """Top k results by score; equal scores are ordered by chunk identifier."""
    return sorted(results, key=lambda x: (-x[1], x[0]))[:k]


def rrf(rankings: list[list[tuple[str, float]]], k: int = 60, derinlik: int = 50) -> list[tuple[str, float]]:
    """Rank fusion: the first `derinlik` (depth) results of every list contribute 1/(k + r) for rank r (starting from 1)."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for r, (chunk_id, _) in enumerate(top_k(ranking, derinlik), start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + r)
    return list(fused.items())


METHODS = ("bm25", "anlam", "karma", "karma_sirala")


class Searcher:
    """Brings all the stages together. If `semantic` and `reranker` are not given, only bm25 can be used."""

    def __init__(self, chunks: list[Chunk], semantic=None, reranker=None, rrf_k: int = 60, derinlik: int = 50,
                 aday: int = 20):
        self.chunk_by_id = {p.parca_id: p for p in chunks}
        self.bm25 = BM25(chunks)
        self.semantic, self.reranker = semantic, reranker
        self.rrf_k, self.depth, self.candidate_count = rrf_k, derinlik, aday

    def scores(self, query: str, role: str, method: str = "karma_sirala") -> list[tuple[str, float]]:
        if method not in METHODS:
            raise ValueError(f"bilinmeyen yöntem: {method}")
        lexical = self.bm25.scores(query, role)
        if method == "bm25":
            return lexical
        semantic = self.semantic.scores(query, role, self.depth)
        if method == "anlam":
            return semantic
        hybrid = rrf([lexical, semantic], self.rrf_k, self.depth)
        if method == "karma":
            return hybrid
        candidates = top_k(hybrid, self.candidate_count)
        s = self.reranker.score(query, [(chunk_id, self.chunk_by_id[chunk_id].arama_metni) for chunk_id, _ in candidates])
        return list(zip([chunk_id for chunk_id, _ in candidates], s))

    def search(self, query: str, role: str, k: int = 5, method: str = "karma_sirala") -> list[tuple[Chunk, float]]:
        return [(self.chunk_by_id[chunk_id], s) for chunk_id, s in top_k(self.scores(query, role, method), k)]


def build_searcher(document_directory, method: str = "anlam", model_directory=None) -> Searcher:
    """Builds a searcher from the library on disk (for the assistant and the MCP server).

    - The semantic index is built in memory from the cached chunk vectors (Qdrant local mode); new questions are
      turned into vectors with bge-m3 in Ollama.
    - The reranker model is loaded only if the method is "karma_sirala". In the Phase 5 measurement it raised the
      memory of the process to 2.7 GB and the median search time per question to 9.6 seconds; it is a setting for a
      more powerful computer."""
    from pathlib import Path

    from sayman.documents import library

    if method not in METHODS:
        raise ValueError(f"bilinmeyen yöntem: {method}")
    directory = Path(document_directory)
    chunks = library.to_chunks(library.read_documents(directory / "belgeler.jsonl"))
    semantic = reranker = None
    if method != "bm25":
        from sayman.documents.semantic import SemanticIndex, EmbeddingCache
        semantic = SemanticIndex(chunks, EmbeddingCache(directory / "onbellek" / "gomme_bge-m3.npz"))
    if method == "karma_sirala":
        from sayman.documents.reranker import Reranker
        reranker = Reranker(Path(model_directory) if model_directory else None,
                                directory / "onbellek" / "sirala_bge-reranker-v2-m3.json")
    return Searcher(chunks, semantic, reranker)
