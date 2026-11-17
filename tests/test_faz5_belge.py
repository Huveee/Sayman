"""Faz 5: belge kütüphanesi, soru seti ve puanlama.

- Belgeler şirket verisiyle tutarlıdır ve aynı veriden aynı belgeler çıkar.
- Soru seti dondurulduğu andaki özetle aynıdır (reports/faz5_soru_seti.json); belge üreticisi ya da soru kalıpları
  sessizce değişemez.
- Ayar ve ayrılmış set aynı belgeyi ya da aynı soru kalıbını paylaşmaz.
"""

import json
from pathlib import Path

import faz5_puanlama as puan
import faz5_sorular as fs
import pytest

from sayman.belge import kutuphane
from sayman.belge.mevzuat import KANUNLAR, kanunlar, maddelere_bol
from sayman.belge.sentetik import uret
from sayman.veri.cikti import csv_oku

KOK = Path(__file__).resolve().parents[1]
URETILEN = KOK / "data" / "uretilen"
MEVZUAT = KOK / "data" / "ham" / "mevzuat"
RAPOR = KOK / "reports" / "faz5_soru_seti.json"

veri_gerek = pytest.mark.skipif(not URETILEN.exists(), reason="Önce faz1_veri_uret.py çalıştırılmalı")
kanun_gerek = pytest.mark.skipif(not all((MEVZUAT / f"{n}.pdf").exists() for n in KANUNLAR),
                                 reason="Kanun PDF'leri data/ham/mevzuat altında yok")


@pytest.fixture(scope="module")
def T():
    return csv_oku(URETILEN)


@pytest.fixture(scope="module")
def belgeler(T):
    return uret(T)


@pytest.fixture(scope="module")
def sorular(belgeler):
    return fs.soru_seti(belgeler)


# ------------------------------------------------------------------ kütüphane

@veri_gerek
def test_ayni_veri_ayni_belgeler(T, belgeler):
    assert uret(T) == belgeler


@veri_gerek
def test_diske_yazilip_okunan_belgeler_ayni(belgeler, tmp_path):
    kutuphane.yaz(belgeler, tmp_path / "b.jsonl")
    assert kutuphane.oku(tmp_path / "b.jsonl") == belgeler


@veri_gerek
def test_parca_kimlikleri_tekil_ve_belgeye_bagli(belgeler):
    p = kutuphane.parcalar(belgeler)
    assert len({x.parca_id for x in p}) == len(p) == 833
    assert all(x.parca_id.startswith(x.belge_id + "#m") for x in p)


@veri_gerek
def test_sozlesmeler_veriyle_tutarli(T, belgeler):
    tedarikci = {t["ad"]: t for t in T["tedarikciler"]}
    musteri = {m["ad"]: m for m in T["musteriler"]}
    for b in belgeler:
        vade = next((m.deger for m in b.maddeler if m.alan == "odeme_vadesi"), None)
        if b.tur == "tedarik_sozlesmesi":
            assert vade == f"{tedarikci[b.taraf_ad]['vade_gun']} gün"
        if b.tur == "satis_sozlesmesi":
            m = musteri[b.taraf_ad]
            assert vade == ("peşin" if m["vade_gun"] == 0 else f"{m['vade_gun']} gün")
            beklenen = ("cfo", "satis_yurtici") if m["yurtici"] else ("cfo", "satis_ihracat")
            assert b.roller == beklenen


@veri_gerek
def test_cekli_musterinin_sozlesmesi_cek_kabul_eder(T, belgeler):
    cekli = {c["musteri_id"] for c in T["cekler"]}
    ad = {m["musteri_id"]: m["ad"] for m in T["musteriler"]}
    cekli_ad = {ad[mid] for mid in cekli}
    for b in belgeler:
        for m in b.maddeler:
            if m.alan == "cek":
                assert m.deger.startswith("kabul,") == (b.taraf_ad in cekli_ad)


KANUN_ORNEGI = """TÜRK BORÇLAR KANUNU
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


def test_kanun_maddelere_bolunur_ve_baslik_alinir():
    m = maddelere_bol(KANUN_ORNEGI, "6098")
    assert [x.parca_id for x in m] == ["KANUN-6098#m1", "KANUN-6098#m2"]
    assert m[0].baslik == "1. Genel olarak"
    assert m[0].metin == "Sözleşme, tarafların iradelerini karşılıklı ve birbirine uygun olarak açıklamalarıyla kurulur."
    assert m[1].baslik == "b. Hazır olmayanlar arasında"
    assert m[1].metin == "Kabul için süre belirlenmeksizin yapılan öneri, önereni bağlar."
    assert all(x.alan is None for x in m)


@veri_gerek
@kanun_gerek
def test_kutuphane_dondurulan_halde(belgeler):
    rapor = json.loads(RAPOR.read_text(encoding="utf-8"))
    kanun = kanunlar(MEVZUAT)
    assert fs.belge_ozeti(belgeler + kanun) == rapor["kutuphane_ozeti"]
    assert fs.soru_seti(belgeler + kanun) == fs.soru_seti(belgeler)  # kanunlar soru almaz, seti değiştirmez
    assert all(b.roller == ("cfo", "satis_yurtici", "satis_ihracat") for b in kanun)


def test_rol_yalnizca_kendi_belgelerini_gorur():
    assert kutuphane.gorebilir("satis_yurtici", ("cfo", "satis_yurtici"))
    assert not kutuphane.gorebilir("satis_yurtici", ("cfo", "satis_ihracat"))
    assert not kutuphane.gorebilir("satis_ihracat", ("cfo",))
    assert kutuphane.gorebilir("cfo", ("cfo",))


# ------------------------------------------------------------------ soru seti

@veri_gerek
def test_soru_seti_dondurulan_halde(belgeler, sorular):
    rapor = json.loads(RAPOR.read_text(encoding="utf-8"))
    assert fs.belge_ozeti(belgeler) == rapor["belge_ozeti"]
    assert fs.ozet(sorular) == rapor["soru_ozeti"]


@veri_gerek
def test_setler_belge_ve_kalip_paylasmaz(belgeler, sorular):
    tur = {b.belge_id: b.tur for b in belgeler}

    def kalip(x):
        return fs.KALIPLAR[tur[x["belge_id"]]][x["alan"]][0 if x["stil"] == "yakin" else 1]

    a, h = sorular["ayar"], sorular["ayrilmis"]
    assert not {x["belge_id"] for x in a} & {x["belge_id"] for x in h}
    assert not {kalip(x) for x in a} & {kalip(x) for x in h}


@veri_gerek
def test_iki_setin_yakin_uzak_karisimi_benzer(sorular):
    for s in sorular.values():
        oran = sum(x["stil"] == "yakin" for x in s) / len(s)
        assert 0.35 <= oran <= 0.65


@veri_gerek
def test_her_sorunun_dogru_parcasi_var_ve_sozlesme_sorusu_tarafi_anar(belgeler, sorular):
    parca = {m.parca_id: (b, m) for b in belgeler for m in b.maddeler}
    for s in sorular["ayar"] + sorular["ayrilmis"]:
        b, m = parca[s["dogru_parca"]]
        assert m.alan == s["alan"] and m.deger == s["deger"] and b.belge_id == s["belge_id"]
        if b.tur != "politika":
            assert b.kisa_ad in s["soru"]
        assert "{" not in s["soru"]


# ------------------------------------------------------------------ puanlama

def test_esit_skorlar_parca_kimligine_gore_siralanir():
    assert puan.sirala([("B#m2", 1.0), ("A#m1", 1.0), ("C#m1", 2.0)]) == ["C#m1", "A#m1", "B#m2"]
    assert puan.sira([("B#m2", 1.0), ("A#m1", 1.0)], "B#m2") == 2
    assert puan.sira([("B#m2", 1.0)], "X#m1") is None


def test_puanla_isabet_ve_mrr():
    sorular = [{"soru_id": "q1", "dogru_parca": "a", "tur": "t"}, {"soru_id": "q2", "dogru_parca": "b", "tur": "t"}]
    sonuc = {"q1": [("a", 3.0), ("x", 2.0)], "q2": [(f"x{i}", 10.0 - i) for i in range(6)] + [("b", 1.0)]}
    p = puan.puanla(sorular, sonuc)
    assert p["isabet@5"] == 0.5 and p["isabet@1"] == 0.5 and p["mrr@10"] == round((1 + 1 / 7) / 2, 4)
    assert p["kacanlar"] == ["q2"]
