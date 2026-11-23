"""Faz 8 unutma testinin araçları: alt küme sabit, cevap okuma, karşılaştırma ve hedef."""

import json

import pytest

from sayman.evaluation import forgetting


def _satir(link, no, dogru=2):
    return {"link": link, "question_number": no, "flores_passage": f"Metin {link}", "question": f"Soru {no}?",
            "mc_answer1": "a", "mc_answer2": "b", "mc_answer3": "c", "mc_answer4": "d",
            "correct_answer_num": str(dogru), "dialect": "tur_Latn"}


@pytest.fixture
def kaynak(tmp_path):
    p = tmp_path / "tur_Latn.jsonl"
    satirlar = [_satir(f"https://ornek/{i}", 1 + i % 2) for i in range(300)]
    p.write_text("\n".join(json.dumps(s) for s in reversed(satirlar)) + "\n", encoding="utf-8")
    return p


def test_alt_kume_sabit_ve_dosya_sirasindan_bagimsiz(kaynak, tmp_path):
    sorular = forgetting.sorulari_oku(kaynak)
    ilk = [s["soru_id"] for s in forgetting.alt_kume(sorular)]
    assert len(ilk) == forgetting.SORU_SAYISI == len(set(ilk))
    assert ilk == [s["soru_id"] for s in forgetting.alt_kume(forgetting.sorulari_oku(kaynak))]
    ters = tmp_path / "ters.jsonl"
    ters.write_text("\n".join(reversed(kaynak.read_text(encoding="utf-8").splitlines())), encoding="utf-8")
    assert ilk == [s["soru_id"] for s in forgetting.alt_kume(forgetting.sorulari_oku(ters))]
    assert sorular[0]["dogru"] == 2 and sorular[0]["siklar"] == ["a", "b", "c", "d"]


def test_istem_metni_soruyu_ve_siklari_numarali_tasir(kaynak):
    s = forgetting.sorulari_oku(kaynak)[0]
    t = forgetting.istem(s)
    assert s["metin"] in t and s["soru"] in t and "1) a\n2) b\n3) c\n4) d" in t
    assert t.endswith("(1, 2, 3 ya da 4).")


@pytest.mark.parametrize("metin, beklenen", [
    ("2", 2), ("Cevap: 3", 3), ("**4**", 4), ("1) ilk şık", 1), ("Doğru seçenek 2'dir.", 2),
    ("", None), ("Bilmiyorum", None), ("12", None), ("5", None), (None, None),
])
def test_cevap_numarasi(metin, beklenen):
    assert forgetting.cevap_numarasi(metin) == beklenen


def _kayit(sid, dogru, secilen):
    return {"soru_id": sid, "dogru": dogru, "secilen": secilen}


def test_ozet_ve_karsilastirma():
    temel = [_kayit(f"s{i}", 1, 1 if i < 80 else 2) for i in range(100)]          # %80
    ayarli = [_kayit(f"s{i}", 1, 1 if 4 <= i < 80 else None) for i in range(100)]  # %76
    assert forgetting.ozetle(temel) == {"soru": 100, "dogru": 80, "dogruluk": 80.0, "okunamayan": 0}
    assert forgetting.ozetle(ayarli)["okunamayan"] == 24
    k = forgetting.karsilastir(temel, ayarli)
    assert k["fark"] == -4.0 and k["yalniz_temel_dogru"] == 4 and k["yalniz_ayarli_dogru"] == 0 and k["gecti"]
    assert k["p"] == 0.125  # 4 soru yalnız temelde doğru, 0 yalnız ayarlıda: 2 × (1/2)⁴
    kotu = [_kayit(f"s{i}", 1, 1 if 6 <= i < 80 else 2) for i in range(100)]      # %74, düşüş 6 puan
    assert not forgetting.karsilastir(temel, kotu)["gecti"]


def test_karsilastirma_yalniz_ortak_sorularda():
    temel = [_kayit("a", 1, 1), _kayit("b", 1, 2)]
    ayarli = [_kayit("b", 1, 1), _kayit("c", 1, 1)]
    k = forgetting.karsilastir(temel, ayarli)
    assert k["soru"] == 1 and k["temel_dogruluk"] == 0.0 and k["ayarli_dogruluk"] == 100.0
    with pytest.raises(ValueError):
        forgetting.karsilastir([_kayit("a", 1, 1)], [_kayit("z", 1, 1)])
