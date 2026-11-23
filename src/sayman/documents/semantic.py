"""Semantic search: bge-m3 embeddings (Ollama) and Qdrant (local mode; no separate server is needed).

- The search text of every chunk (document title + party + article title + text) is turned into a 1024-dimensional
  vector with bge-m3. The question is turned into a vector with the same model; the nearest chunks (highest cosine
  similarity) are returned.
- Vectors are kept in an on-disk cache (keyed by the digest of the text). The same text never goes to the model a
  second time; tests run from the cache without running the model.
- Long text is not silently truncated: truncate=False is sent to Ollama; a text that exceeds the context window raises
  an error.
- The role filter is inside the Qdrant query (the role must occur in the roles field); a chunk that cannot be seen
  does not enter the ranking.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from sayman.documents.library import Chunk

MODEL = "bge-m3"
DIMENSIONS = 1024
CONTEXT_SIZE = 8192
COLLECTION = "belgeler"


def cache_key(text: str) -> str:
    return hashlib.sha256(f"{MODEL}\n{text}".encode("utf-8")).hexdigest()[:32]


class EmbeddingCache:
    """text digest -> normalized vector; stored in an .npz file."""

    def __init__(self, path: Path):
        self.path = path
        self.v: dict[str, np.ndarray] = {}
        if path.exists():
            with np.load(path) as d:
                self.v = dict(zip(d["anahtarlar"].tolist(), d["vektorler"]))

    def missing_texts(self, texts: list[str]) -> list[str]:
        return list(dict.fromkeys(m for m in texts if cache_key(m) not in self.v))

    def fetch(self, texts: list[str]) -> np.ndarray:
        return np.stack([self.v[cache_key(m)] for m in texts])

    def fill(self, texts: list[str], embedder=None, batch_size: int = 16, progress=None) -> int:
        """Sends the texts missing from the cache to the model and returns how many texts were embedded. Writes to
        disk now and then, so an interrupted run continues where it stopped."""
        missing = self.missing_texts(texts)
        for i in range(0, len(missing), batch_size):
            group = missing[i:i + batch_size]
            for m, v in zip(group, (embedder or embed)(group)):
                self.v[cache_key(m)] = v
            if (i // batch_size) % 20 == 19:
                self.kaydet()
                if progress:
                    progress(i + len(group), len(missing))
        if missing:
            self.kaydet()
        return len(missing)

    def kaydet(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        keys = sorted(self.v)
        np.savez(self.path, anahtarlar=np.array(keys),
                 vektorler=np.stack([self.v[a] for a in keys]).astype(np.float32))


def embed(texts: list[str]) -> np.ndarray:
    import ollama
    response = ollama.embed(model=MODEL, input=texts, truncate=False, options={"num_ctx": CONTEXT_SIZE})
    v = np.asarray(response["embeddings"], dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


class SemanticIndex:
    """A Qdrant collection. If `path` is not given it is built in memory (still Qdrant local mode)."""

    def __init__(self, chunks: list[Chunk], cache: EmbeddingCache, path: Path | None = None, embedder=None):
        """embedder: function that turns a question that is not in the cache into a vector (if not given, bge-m3 in
        Ollama). The vectors of new questions are kept only in memory; they are not written to the on-disk cache."""
        from qdrant_client import QdrantClient, models

        self.m = models
        self.cache = cache
        self.embedder = embedder
        self.client = QdrantClient(path=str(path)) if path else QdrantClient(location=":memory:")
        if self.client.collection_exists(COLLECTION):
            self.client.delete_collection(COLLECTION)
        self.client.create_collection(COLLECTION, vectors_config=models.VectorParams(
            size=DIMENSIONS, distance=models.Distance.COSINE))
        vectors = cache.fetch([p.arama_metni for p in chunks])
        self.client.upload_points(COLLECTION, [
            models.PointStruct(id=i, vector=v.tolist(),
                               payload={"parca_id": p.parca_id, "belge_id": p.belge_id, "roller": list(p.roller)})
            for i, (p, v) in enumerate(zip(chunks, vectors))])

    def scores(self, query: str, role: str, limit: int = 50) -> list[tuple[str, float]]:
        m = self.m
        try:
            v = self.cache.fetch([query])[0]
        except KeyError:  # a new question that is not in the cache
            self.cache.v[cache_key(query)] = (self.embedder or embed)([query])[0]
            v = self.cache.fetch([query])[0]
        response = self.client.query_points(
            COLLECTION, query=v.tolist(), limit=limit, with_payload=["parca_id"],
            query_filter=m.Filter(must=[m.FieldCondition(key="roller", match=m.MatchValue(value=role))]))
        return [(n.payload["parca_id"], float(n.score)) for n in response.points]

    def close(self) -> None:
        self.client.close()
