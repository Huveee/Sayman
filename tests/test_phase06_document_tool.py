"""Phase 6: the `belge_ara` tool (the 14th tool) goes through the single gate of Phase 3.

With a small document library and without running the models: semantic search uses fake vectors (each text gets a vector
derived from its words), keyword search uses the real BM25. The hand-built warehouse is the same as in the Phase 3 tests.
"""

import datetime as dt
import zlib

import numpy as np
import pytest
from test_phase02_warehouse import AS_OF, _hand_built_tables

from sayman.warehouse.loader import build_warehouse
from sayman.tools import catalog as araclar_modulu
from sayman.tools.audit_log import AuditLog
from sayman.tools.identity import generate_card
from sayman.tools.server import SaymanServer
from sayman.tools.permissions import read_permissions
from sayman.documents.semantic import SemanticIndex, EmbeddingCache, cache_key
from sayman.documents.search import Searcher, terms
from sayman.documents.library import Chunk
from sayman.data.table_io import write_csv
from sayman.data.clock import Clock

SIGNING_KEY = bytes(range(32))
NOW_EPOCH = 1_750_000_000.0
CFO, DOMESTIC, EXPORT = "selin.aydin", "murat.kaya", "deniz.arslan"
LONG_TEXT = "Madde metni " + "uzun bir açıklama cümlesi " * 120  # ~3,100 characters


def make_chunk(chunk_id, title, text, roles):
    return Chunk(chunk_id, chunk_id.split("#")[0], title, "Ödeme Koşulları", roles, text, f"{title} {text}")


CHUNKS = [
    make_chunk("SOZ-M1#m3", "Satış Sözleşmesi — Alfa Ltd.", "Alıcı faturayı 30 gün içinde öder.", ("cfo", "satis_yurtici")),
    make_chunk("SOZ-M2#m3", "Satış Sözleşmesi — Beta GmbH", "Der Käufer zahlt binnen 60 Tagen; alıcı 60 gün içinde öder.",
      ("cfo", "satis_ihracat")),
    make_chunk("POL-NAKIT#m2", "Nakit Politikası", "Ana TL hesabının en düşük bakiyesi 5 milyon TL'dir.", ("cfo",)),
    make_chunk("POL-CEK#m2", "Çek Politikası", "En uzun vadeli çek 120 gün olabilir.", ("cfo", "satis_yurtici", "satis_ihracat")),
    make_chunk("KANUN-6102#m21", "Türk Ticaret Kanunu", LONG_TEXT, ("cfo", "satis_yurtici", "satis_ihracat")),
]


def _vector(text: str) -> np.ndarray:
    """Fake embedding derived from word stems: texts that share many words land close to each other."""
    v = np.zeros(1024, dtype=np.float32)
    for t in terms(text):
        v[zlib.crc32(t.encode()) % 1024] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


class FakeCache(EmbeddingCache):
    def __init__(self):
        self.v = {}

    def fetch(self, texts):
        return np.stack([_vector(m) for m in texts])


@pytest.fixture(scope="module")
def server_env(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faz6_belge")
    write_csv(_hand_built_tables(), folder / "csv")
    build_warehouse(folder / "csv", folder / "sayman.duckdb").close()
    index = SemanticIndex(CHUNKS, FakeCache())

    def build_server(name, document, method="anlam"):
        return SaymanServer(warehouse=folder / "sayman.duckdb", clock=lambda: Clock(AS_OF, dt.date(2025, 6, 30)),
                              key=SIGNING_KEY, permissions=read_permissions(), audit_log=AuditLog(folder / f"kayit_{name}.jsonl"),
                              scope_dir=folder / f"kapsam_{name}", time_fn=lambda: NOW_EPOCH, belge=document,
                              search_method=method)

    servers = {"anlam": build_server("anlam", Searcher(CHUNKS, index)), "bm25": build_server("bm25", Searcher(CHUNKS), "bm25"),
                 "yok": build_server("yok", None)}
    yield servers
    for s in servers.values():
        s.close()
    index.close()


def search_documents(server, user, **args):
    return server.gate("belge_ara", args, generate_card(user, SIGNING_KEY, 3600, NOW_EPOCH))


def chunk_ids(response):
    return [s["parca_id"] for s in response["sonuc"]["sonuclar"]]


def test_every_role_can_call_document_search():
    permissions = read_permissions()
    assert all("belge_ara" in r.tools for r in permissions.roller.values())
    assert "belge_ara" in araclar_modulu.TOOLS_BY_NAME and len(araclar_modulu.TOOLS) == 14


def test_result_format_and_source(server_env):
    response, error = search_documents(server_env["anlam"], CFO, sorgu="Alfa Ltd. faturayı kaç günde öder")
    assert not error
    assert set(response) == {"cagri_id", "arac", "tarih", "kapsam", "sonuc", "kaynak"}
    assert response["tarih"] == AS_OF.isoformat() and response["arac"] == "belge_ara"
    first = response["sonuc"]["sonuclar"][0]
    assert first == {"parca_id": "SOZ-M1#m3", "belge": "Satış Sözleşmesi — Alfa Ltd.", "madde": "Ödeme Koşulları",
                   "metin": "Alıcı faturayı 30 gün içinde öder."}
    assert response["kaynak"] == {"belge_parcalari": chunk_ids(response)}
    assert len(chunk_ids(response)) == 3  # default


@pytest.mark.parametrize("method", ["anlam", "bm25"])
def test_document_hidden_from_role_is_never_returned_by_any_method(server_env, method):
    s = server_env[method]
    domestic_ids = chunk_ids(search_documents(s, DOMESTIC, sorgu="Beta GmbH ödeme süresi 60 gün", en_fazla=3)[0])
    export_ids = chunk_ids(search_documents(s, EXPORT, sorgu="Beta GmbH ödeme süresi 60 gün", en_fazla=3)[0])
    assert "SOZ-M2#m3" not in domestic_ids and "POL-NAKIT#m2" not in domestic_ids
    assert "SOZ-M1#m3" not in export_ids and "POL-NAKIT#m2" not in export_ids
    assert export_ids[0] == "SOZ-M2#m3"
    assert "POL-NAKIT#m2" in chunk_ids(search_documents(s, CFO, sorgu="Ana TL hesabının en düşük bakiyesi", en_fazla=3)[0])


def test_long_article_is_cut_at_word_boundary(server_env):
    response, _ = search_documents(server_env["anlam"], CFO, sorgu="uzun bir açıklama cümlesi", en_fazla=1)
    [s] = response["sonuc"]["sonuclar"]
    assert s["parca_id"] == "KANUN-6102#m21" and s["metin_kesildi"] is True
    assert len(s["metin"]) <= araclar_modulu.DOCUMENT_TEXT_LIMIT + 2 and s["metin"].endswith(" …")
    assert LONG_TEXT.startswith(s["metin"][:-2])


def test_audit_log_and_schema(server_env):
    s = server_env["anlam"]
    before = len(s.audit_log.rows)
    _, error = search_documents(s, DOMESTIC, sorgu="çek vadesi")
    assert not error
    r1, err1 = search_documents(s, DOMESTIC, sorgu="ab")
    r2, err2 = search_documents(s, DOMESTIC, sorgu="x" * 301)
    r3, err3 = search_documents(s, DOMESTIC, sorgu="çek", en_fazla=4)
    r4, err4 = search_documents(s, DOMESTIC)
    assert err1 and r1["mesaj"] == "Argüman hatası: sorgu en az 3 karakter olmalı."
    assert err2 and r2["mesaj"] == "Argüman hatası: sorgu en çok 300 karakter olabilir."
    assert err3 and r3["mesaj"] == "Argüman hatası: en_fazla en çok 3 olabilir; 3 ile yeniden deneyin."
    assert err4 and r4["mesaj"] == "Argüman hatası: Eksik argüman: sorgu."
    log_rows = s.audit_log.rows[before:]
    assert [(k["arac"], k["kullanici"], k["rol"], k["sonuc"]) for k in log_rows] == \
        [("belge_ara", DOMESTIC, "satis_yurtici", "tamam")] + [("belge_ara", DOMESTIC, "satis_yurtici", "reddedildi")] * 4
    assert log_rows[0]["kayit_sayisi"] == 3


def test_call_without_identity_is_rejected(server_env):
    response, error = server_env["anlam"].gate("belge_ara", {"sorgu": "çek vadesi"}, None)
    assert error and response["hata"] == "kimlik_gecersiz"


def test_unavailable_when_library_is_missing(server_env):
    s = server_env["yok"]
    response, error = search_documents(s, CFO, sorgu="çek vadesi")
    assert error and response == {"cagri_id": response["cagri_id"], "hata": "kullanilamaz",
                              "mesaj": "Belge araması şu an kullanılamıyor."}
    assert s.audit_log.rows[-1]["neden"] == "kullanilamaz"


def test_new_question_vector_stays_in_memory_only(tmp_path):
    seen_texts = []

    def embedder(texts):
        seen_texts.extend(texts)
        return np.stack([_vector(m) for m in texts])

    cache = EmbeddingCache(tmp_path / "yok.npz")
    for p in CHUNKS:
        cache.v[cache_key(p.arama_metni)] = _vector(p.arama_metni)
    index = SemanticIndex(CHUNKS, cache, embedder=embedder)
    try:
        assert index.scores("çek vadesi", "cfo")
        assert index.scores("çek vadesi", "cfo")
        assert seen_texts == ["çek vadesi"]  # the second time it did not go to the model
        assert not (tmp_path / "yok.npz").exists()  # not written to disk
    finally:
        index.close()
