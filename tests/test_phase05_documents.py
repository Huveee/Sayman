"""Phase 5: document library, question set and scoring.

- The documents are consistent with the company data, and the same data always yields the same documents.
- The question set matches the digest taken when it was frozen (reports/faz5_soru_seti.json); the document generator or the
  question templates cannot change silently.
- The tuning set and the held-out set share neither a document nor a question template.
"""

import json
from pathlib import Path

import support.phase05_scoring as puan
import support.phase05_questions as fs
import pytest

from sayman.documents import library
from sayman.documents.legislation import LAWS, laws, split_articles
from sayman.documents.synthetic import generate
from sayman.data.table_io import read_csv

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "uretilen"
LEGISLATION = ROOT / "data" / "ham" / "mevzuat"
REPORT = ROOT / "reports" / "faz5_soru_seti.json"

data_required = pytest.mark.skipif(not GENERATED.exists(), reason="Önce phase01_generate_data.py çalıştırılmalı")
law_required = pytest.mark.skipif(not all((LEGISLATION / f"{n}.pdf").exists() for n in LAWS),
                                 reason="Kanun PDF'leri data/ham/mevzuat altında yok")


@pytest.fixture(scope="module")
def tables():
    return read_csv(GENERATED)


@pytest.fixture(scope="module")
def documents(tables):
    return generate(tables)


@pytest.fixture(scope="module")
def questions(documents):
    return fs.question_set(documents)


# ------------------------------------------------------------------ library

@data_required
def test_same_data_gives_same_documents(tables, documents):
    assert generate(tables) == documents


@data_required
def test_documents_written_to_disk_and_read_back_are_identical(documents, tmp_path):
    library.write_documents(documents, tmp_path / "b.jsonl")
    assert library.read_documents(tmp_path / "b.jsonl") == documents


@data_required
def test_chunk_ids_are_unique_and_tied_to_document(documents):
    p = library.to_chunks(documents)
    assert len({x.parca_id for x in p}) == len(p) == 833
    assert all(x.parca_id.startswith(x.belge_id + "#m") for x in p)


@data_required
def test_contracts_are_consistent_with_data(tables, documents):
    suppliers = {t["ad"]: t for t in tables["tedarikciler"]}
    customers = {m["ad"]: m for m in tables["musteriler"]}
    for b in documents:
        due = next((m.deger for m in b.maddeler if m.alan == "odeme_vadesi"), None)
        if b.tur == "tedarik_sozlesmesi":
            assert due == f"{suppliers[b.taraf_ad]['vade_gun']} gün"
        if b.tur == "satis_sozlesmesi":
            m = customers[b.taraf_ad]
            assert due == ("peşin" if m["vade_gun"] == 0 else f"{m['vade_gun']} gün")
            expected = ("cfo", "satis_yurtici") if m["yurtici"] else ("cfo", "satis_ihracat")
            assert b.roller == expected


@data_required
def test_contract_of_cheque_customer_accepts_cheques(tables, documents):
    cheque_customer_ids = {c["musteri_id"] for c in tables["cekler"]}
    name = {m["musteri_id"]: m["ad"] for m in tables["musteriler"]}
    cheque_customer_names = {name[customer_id] for customer_id in cheque_customer_ids}
    for b in documents:
        for m in b.maddeler:
            if m.alan == "cek":
                assert m.deger.startswith("kabul,") == (b.taraf_ad in cheque_customer_names)


LAW_SAMPLE = """TÜRK BORÇLAR KANUNU
BİRİNCİ KISIM
A. Sözleşmenin kurulması
1. Genel olarak
MADDE 1 - Sözleşme, tarafların iradelerini karşılıklı ve
birbirine uygun olarak açıklamalarıyla kurulur.

b. Hazır olmayanlar arasında
MADDE 2- Kabul için süre belirlenmeksizin yapılan öneri, önereni bağlar.
MADDE 2/A- Sonradan eklenen bir madde.
MADDE 3- (Mülga: 1/1/2020-1234/5 md.)
Yürürlük
GEÇİCİ MADDE 1 – Geçiş hükmü.
"""


def test_law_is_split_into_articles_and_title_is_taken():
    m = split_articles(LAW_SAMPLE, "6098")
    assert [x.parca_id for x in m] == ["KANUN-6098#m1", "KANUN-6098#m2"]
    assert m[0].baslik == "1. Genel olarak"
    assert m[0].metin == "Sözleşme, tarafların iradelerini karşılıklı ve birbirine uygun olarak açıklamalarıyla kurulur."
    assert m[1].baslik == "b. Hazır olmayanlar arasında"
    assert m[1].metin == "Kabul için süre belirlenmeksizin yapılan öneri, önereni bağlar."
    assert all(x.alan is None for x in m)


@data_required
@law_required
def test_library_matches_frozen_state(documents):
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    law_documents = laws(LEGISLATION)
    assert fs.documents_digest(documents + law_documents) == report["kutuphane_ozeti"]
    assert fs.question_set(documents + law_documents) == fs.question_set(documents)  # laws take no questions, so they do not change the set
    assert all(b.roller == ("cfo", "satis_yurtici", "satis_ihracat") for b in law_documents)


def test_role_sees_only_its_own_documents():
    assert library.can_see("satis_yurtici", ("cfo", "satis_yurtici"))
    assert not library.can_see("satis_yurtici", ("cfo", "satis_ihracat"))
    assert not library.can_see("satis_ihracat", ("cfo",))
    assert library.can_see("cfo", ("cfo",))


# ------------------------------------------------------------------ question set

@data_required
def test_question_set_matches_frozen_state(documents, questions):
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert fs.documents_digest(documents) == report["belge_ozeti"]
    assert fs.digest(questions) == report["soru_ozeti"]


@data_required
def test_sets_do_not_share_documents_or_templates(documents, questions):
    kind_by_document = {b.belge_id: b.tur for b in documents}

    def template(x):
        return fs.PATTERNS[kind_by_document[x["belge_id"]]][x["alan"]][0 if x["stil"] == "yakin" else 1]

    a, h = questions["ayar"], questions["ayrilmis"]
    assert not {x["belge_id"] for x in a} & {x["belge_id"] for x in h}
    assert not {template(x) for x in a} & {template(x) for x in h}


@data_required
def test_both_sets_have_similar_near_far_mix(questions):
    for s in questions.values():
        ratio = sum(x["stil"] == "yakin" for x in s) / len(s)
        assert 0.35 <= ratio <= 0.65


@data_required
def test_every_question_has_correct_chunk_and_contract_question_names_the_party(documents, questions):
    chunk_by_id = {m.parca_id: (b, m) for b in documents for m in b.maddeler}
    for s in questions["ayar"] + questions["ayrilmis"]:
        b, m = chunk_by_id[s["dogru_parca"]]
        assert m.alan == s["alan"] and m.deger == s["deger"] and b.belge_id == s["belge_id"]
        if b.tur != "politika":
            assert b.kisa_ad in s["soru"]
        assert "{" not in s["soru"]


# ------------------------------------------------------------------ scoring

def test_equal_scores_are_ordered_by_chunk_id():
    assert puan.order_chunks([("B#m2", 1.0), ("A#m1", 1.0), ("C#m1", 2.0)]) == ["C#m1", "A#m1", "B#m2"]
    assert puan.rank_of([("B#m2", 1.0), ("A#m1", 1.0)], "B#m2") == 2
    assert puan.rank_of([("B#m2", 1.0)], "X#m1") is None


def test_scoring_hit_and_mrr():
    questions = [{"soru_id": "q1", "dogru_parca": "a", "tur": "t"}, {"soru_id": "q2", "dogru_parca": "b", "tur": "t"}]
    result = {"q1": [("a", 3.0), ("x", 2.0)], "q2": [(f"x{i}", 10.0 - i) for i in range(6)] + [("b", 1.0)]}
    p = puan.score(questions, result)
    assert p["isabet@5"] == 0.5 and p["isabet@1"] == 0.5 and p["mrr@10"] == round((1 + 1 / 7) / 2, 4)
    assert p["kacanlar"] == ["q2"]
