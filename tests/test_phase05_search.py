"""Phase 5: document search.

- The role filter is applied at every stage before ranking: a sales manager cannot see another manager's customer contract
  or a document open only to the CFO by any method.
- Acceptance criterion (from the cache, without running the models): on the held-out set, with keyword + semantic search and
  reranking, the correct chunk is found in the top 5 results at least 85% of the time.
"""

import json
from pathlib import Path

import support.phase05_scoring as puan
import numpy as np
import pytest

from sayman.documents import library
from sayman.documents.semantic import SemanticIndex, EmbeddingCache, cache_key
from sayman.documents.search import BM25, Searcher, top_k, lower_tr, rrf, terms
from sayman.documents.library import Chunk

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS_DIR = ROOT / "data" / "belge"
EMBEDDING_CACHE = DOCUMENTS_DIR / "onbellek" / "gomme_bge-m3.npz"
RERANK_CACHE = DOCUMENTS_DIR / "onbellek" / "sirala_bge-reranker-v2-m3.json"
SEARCH_REPORT = ROOT / "reports" / "faz5_arama.json"


def make_chunk(chunk_id, text, roles):
    return Chunk(chunk_id, chunk_id.split("#")[0], "Belge", "Madde", roles, text, text)


SAMPLE_CHUNKS = [
    make_chunk("SOZ-M1#m1", "Kuzey Market kredi limiti 100.000 TL", ("cfo", "satis_yurtici")),
    make_chunk("SOZ-M2#m1", "Atlas Trading kredi limiti 50.000 USD", ("cfo", "satis_ihracat")),
    make_chunk("POL-NAKIT#m2", "Ana TL hesabının en düşük bakiyesi", ("cfo",)),
    make_chunk("POL-CEK#m2", "En uzun vadeli çek 120 gün", ("cfo", "satis_yurtici", "satis_ihracat")),
]


class FakeCache(EmbeddingCache):
    """One constant vector for every text: chunks and query point the same way, so semantic search rates all equal."""

    def __init__(self):
        self.v = {}

    def fetch(self, texts):
        return np.ones((len(texts), 1024), dtype=np.float32) / 32.0


class FakeReranker:
    def __init__(self):
        self.seen_ids = []

    def score(self, query, candidates):
        self.seen_ids += [chunk_id for chunk_id, _ in candidates]
        return [float(len(m)) for _, m in candidates]


def test_turkish_lowercasing_and_stemming():
    assert lower_tr("IĞDIR İZMİR") == "ığdır izmir"
    assert terms("Faturalarını 30 gün içinde ödemeliyiz") == ["fatur", "30", "gün", "içind", "ödeme"]


def test_rrf_rank_fusion():
    s = dict(rrf([[("a", 9.0), ("b", 5.0)], [("b", 0.9), ("c", 0.8)]], k=60))
    assert s["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert s["a"] == pytest.approx(1 / 61) and s["c"] == pytest.approx(1 / 62)
    assert top_k(list(s.items()), 1)[0][0] == "b"


def test_keyword_search_does_not_see_chunk_outside_role():
    bm = BM25(SAMPLE_CHUNKS)
    assert {p for p, _ in bm.scores("Atlas Trading kredi limiti", "satis_ihracat")} == {"SOZ-M2#m1"}
    seen_ids = {p for p, _ in bm.scores("Atlas Trading kredi limiti", "satis_yurtici")}
    assert "SOZ-M2#m1" not in seen_ids and "SOZ-M1#m1" in seen_ids
    assert not {p for p, _ in bm.scores("Ana TL hesabının bakiyesi", "satis_yurtici")} & {"POL-NAKIT#m2"}


def test_semantic_search_filters_by_role_inside_qdrant():
    index = SemanticIndex(SAMPLE_CHUNKS, FakeCache())
    try:
        assert {p for p, _ in index.scores("herhangi", "satis_yurtici")} == {"SOZ-M1#m1", "POL-CEK#m2"}
        assert {p for p, _ in index.scores("herhangi", "satis_ihracat")} == {"SOZ-M2#m1", "POL-CEK#m2"}
        assert len(index.scores("herhangi", "cfo")) == 4
    finally:
        index.close()


def test_reranker_receives_only_visible_candidates():
    reranker = FakeReranker()
    index = SemanticIndex(SAMPLE_CHUNKS, FakeCache())
    try:
        a = Searcher(SAMPLE_CHUNKS, index, reranker)
        result = a.search("Atlas Trading kredi limiti", "satis_yurtici", k=5)
        assert {p.parca_id for p, _ in result} <= {"SOZ-M1#m1", "POL-CEK#m2"}
        assert set(reranker.seen_ids) <= {"SOZ-M1#m1", "POL-CEK#m2"}
    finally:
        index.close()


def test_cache_is_written_to_disk_and_read_back(tmp_path):
    o = EmbeddingCache(tmp_path / "g.npz")
    n = o.fill(["a", "b", "a"], embedder=lambda group: np.eye(1024, dtype=np.float32)[: len(group)])
    assert n == 2
    o2 = EmbeddingCache(tmp_path / "g.npz")
    assert np.array_equal(o2.fetch(["a", "b"]), np.eye(1024, dtype=np.float32)[:2])
    assert o2.missing_texts(["a", "c"]) == ["c"] and cache_key("a") != cache_key("b")


# ------------------------------------------------------------------ real library

library_available = pytest.mark.skipif(not (DOCUMENTS_DIR / "belgeler.jsonl").exists(),
                                   reason="Önce phase05_generate_documents.py çalıştırılmalı")


@pytest.fixture(scope="module")
def chunks():
    return library.to_chunks(library.read_documents(DOCUMENTS_DIR / "belgeler.jsonl"))


@library_available
def test_sales_manager_cannot_see_other_managers_customer_contract(chunks):
    documents = library.read_documents(DOCUMENTS_DIR / "belgeler.jsonl")
    export_document = next(b for b in documents if "satis_ihracat" in b.roller and b.tur == "satis_sozlesmesi")
    domestic_document = next(b for b in documents if "satis_yurtici" in b.roller and b.tur == "satis_sozlesmesi")
    bm = BM25(chunks)
    for role, target in (("satis_yurtici", export_document), ("satis_ihracat", domestic_document)):
        result = bm.scores(f"{target.taraf_ad} kredi limiti ödeme vadesi", role)
        assert result and not [p for p, _ in result if p.startswith(target.belge_id + "#")]
    supplier_document = next(b for b in documents if b.tur == "tedarik_sozlesmesi")
    for role in ("satis_yurtici", "satis_ihracat"):
        assert not [p for p, _ in bm.scores(supplier_document.taraf_ad, role) if p.startswith(supplier_document.belge_id + "#")]
        assert not [p for p, _ in bm.scores("Nakit Yönetimi Politikası güvenlik eşiği", role)
                    if p.startswith("POL-NAKIT#")]


cache_available = pytest.mark.skipif(not (EMBEDDING_CACHE.exists() and RERANK_CACHE.exists() and SEARCH_REPORT.exists()),
                                  reason="Önce phase05_build_index.py ve phase05_evaluate_search.py --ayrilmis çalıştırılmalı")


@library_available
@cache_available
def test_acceptance_held_out_set_top_5_hit_rate_at_least_85_percent(chunks):
    """The models do not run: vectors and reranking scores come from the cache; a missing score makes the test fail."""
    from sayman.documents.reranker import Reranker

    report = json.loads(SEARCH_REPORT.read_text(encoding="utf-8"))
    questions = json.loads((DOCUMENTS_DIR / "sorular.json").read_text(encoding="utf-8"))["ayrilmis"]
    index = SemanticIndex(chunks, EmbeddingCache(EMBEDDING_CACHE))
    try:
        a = Searcher(chunks, index, Reranker(None, RERANK_CACHE), **report["ayarlar"])
        result = puan.score(questions, {s["soru_id"]: top_k(a.scores(s["soru"], "cfo"), 10) for s in questions})
    finally:
        index.close()
    assert result["isabet@5"] >= 0.85
    assert result["isabet@5"] == report["yontemler"]["karma_sirala"]["ayrilmis"]["isabet@5"]
