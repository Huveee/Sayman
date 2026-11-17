"""Faz 5: belge araması.

- Rol süzmesi her aşamada sıralamadan önce yapılır: bir satış müdürü başka bir müdürün müşterisinin sözleşmesini ya da
  yalnızca CFO'ya açık bir belgeyi hiçbir yöntemle göremez.
- Kabul ölçütü (önbellekten, modelleri çalıştırmadan): ayrılmış sette kelime + anlam araması ve yeniden sıralama ile
  doğru parça ilk 5 sonuçta en az %85 oranında bulunur.
"""

import json
from pathlib import Path

import faz5_puanlama as puan
import numpy as np
import pytest

from sayman.belge import kutuphane
from sayman.belge.anlam import AnlamDizini, GommeOnbellegi, anahtar
from sayman.belge.arama import BM25, Arayici, ilk, kucult, rrf, terimler
from sayman.belge.kutuphane import Parca

KOK = Path(__file__).resolve().parents[1]
BELGE = KOK / "data" / "belge"
GOMME = BELGE / "onbellek" / "gomme_bge-m3.npz"
SIRALAMA = BELGE / "onbellek" / "sirala_bge-reranker-v2-m3.json"
SONUC = KOK / "reports" / "faz5_arama.json"


def P(pid, metin, roller):
    return Parca(pid, pid.split("#")[0], "Belge", "Madde", roller, metin, metin)


ORNEK = [
    P("SOZ-M1#m1", "Kuzey Market kredi limiti 100.000 TL", ("cfo", "satis_yurtici")),
    P("SOZ-M2#m1", "Atlas Trading kredi limiti 50.000 USD", ("cfo", "satis_ihracat")),
    P("POL-NAKIT#m2", "Ana TL hesabının en düşük bakiyesi", ("cfo",)),
    P("POL-CEK#m2", "En uzun vadeli çek 120 gün", ("cfo", "satis_yurtici", "satis_ihracat")),
]


class SahteOnbellek(GommeOnbellegi):
    """Her metne sabit bir vektör: bütün parçalar ve sorgu aynı yönde, yani anlam araması hepsini eşit bulur."""

    def __init__(self):
        self.v = {}

    def al(self, metinler):
        return np.ones((len(metinler), 1024), dtype=np.float32) / 32.0


class SahteSiralayici:
    def __init__(self):
        self.gorulen = []

    def skorla(self, sorgu, adaylar):
        self.gorulen += [pid for pid, _ in adaylar]
        return [float(len(m)) for _, m in adaylar]


def test_turkce_kucultme_ve_kok():
    assert kucult("IĞDIR İZMİR") == "ığdır izmir"
    assert terimler("Faturalarını 30 gün içinde ödemeliyiz") == ["fatur", "30", "gün", "içind", "ödeme"]


def test_rrf_sira_birlestirme():
    s = dict(rrf([[("a", 9.0), ("b", 5.0)], [("b", 0.9), ("c", 0.8)]], k=60))
    assert s["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert s["a"] == pytest.approx(1 / 61) and s["c"] == pytest.approx(1 / 62)
    assert ilk(list(s.items()), 1)[0][0] == "b"


def test_kelime_aramasi_rol_disindaki_parcayi_gormez():
    bm = BM25(ORNEK)
    assert {p for p, _ in bm.skorlar("Atlas Trading kredi limiti", "satis_ihracat")} == {"SOZ-M2#m1"}
    gorulen = {p for p, _ in bm.skorlar("Atlas Trading kredi limiti", "satis_yurtici")}
    assert "SOZ-M2#m1" not in gorulen and "SOZ-M1#m1" in gorulen
    assert not {p for p, _ in bm.skorlar("Ana TL hesabının bakiyesi", "satis_yurtici")} & {"POL-NAKIT#m2"}


def test_anlam_aramasi_rol_suzmesi_qdrant_icinde():
    dizin = AnlamDizini(ORNEK, SahteOnbellek())
    try:
        assert {p for p, _ in dizin.skorlar("herhangi", "satis_yurtici")} == {"SOZ-M1#m1", "POL-CEK#m2"}
        assert {p for p, _ in dizin.skorlar("herhangi", "satis_ihracat")} == {"SOZ-M2#m1", "POL-CEK#m2"}
        assert len(dizin.skorlar("herhangi", "cfo")) == 4
    finally:
        dizin.kapat()


def test_siralayici_yalnizca_gorulebilen_adaylari_alir():
    sirala = SahteSiralayici()
    dizin = AnlamDizini(ORNEK, SahteOnbellek())
    try:
        a = Arayici(ORNEK, dizin, sirala)
        sonuc = a.ara("Atlas Trading kredi limiti", "satis_yurtici", k=5)
        assert {p.parca_id for p, _ in sonuc} <= {"SOZ-M1#m1", "POL-CEK#m2"}
        assert set(sirala.gorulen) <= {"SOZ-M1#m1", "POL-CEK#m2"}
    finally:
        dizin.kapat()


def test_onbellek_diske_yazilip_okunur(tmp_path):
    o = GommeOnbellegi(tmp_path / "g.npz")
    n = o.doldur(["a", "b", "a"], gomucu=lambda grup: np.eye(1024, dtype=np.float32)[: len(grup)])
    assert n == 2
    o2 = GommeOnbellegi(tmp_path / "g.npz")
    assert np.array_equal(o2.al(["a", "b"]), np.eye(1024, dtype=np.float32)[:2])
    assert o2.eksikler(["a", "c"]) == ["c"] and anahtar("a") != anahtar("b")


# ------------------------------------------------------------------ gerçek kütüphane

kutuphane_var = pytest.mark.skipif(not (BELGE / "belgeler.jsonl").exists(),
                                   reason="Önce faz5_belge_uret.py çalıştırılmalı")


@pytest.fixture(scope="module")
def parcalar():
    return kutuphane.parcalar(kutuphane.oku(BELGE / "belgeler.jsonl"))


@kutuphane_var
def test_satis_muduru_baska_mudurun_musterisinin_sozlesmesini_goremez(parcalar):
    belgeler = kutuphane.oku(BELGE / "belgeler.jsonl")
    ihracat = next(b for b in belgeler if "satis_ihracat" in b.roller and b.tur == "satis_sozlesmesi")
    yurtici = next(b for b in belgeler if "satis_yurtici" in b.roller and b.tur == "satis_sozlesmesi")
    bm = BM25(parcalar)
    for rol, hedef in (("satis_yurtici", ihracat), ("satis_ihracat", yurtici)):
        sonuc = bm.skorlar(f"{hedef.taraf_ad} kredi limiti ödeme vadesi", rol)
        assert sonuc and not [p for p, _ in sonuc if p.startswith(hedef.belge_id + "#")]
    tedarik = next(b for b in belgeler if b.tur == "tedarik_sozlesmesi")
    for rol in ("satis_yurtici", "satis_ihracat"):
        assert not [p for p, _ in bm.skorlar(tedarik.taraf_ad, rol) if p.startswith(tedarik.belge_id + "#")]
        assert not [p for p, _ in bm.skorlar("Nakit Yönetimi Politikası güvenlik eşiği", rol)
                    if p.startswith("POL-NAKIT#")]


onbellek_var = pytest.mark.skipif(not (GOMME.exists() and SIRALAMA.exists() and SONUC.exists()),
                                  reason="Önce faz5_dizin_kur.py ve faz5_arama_olc.py --ayrilmis çalıştırılmalı")


@kutuphane_var
@onbellek_var
def test_kabul_ayrilmis_sette_ilk_5_isabet_en_az_yuzde_85(parcalar):
    """Modeller çalışmaz: vektörler ve sıralama skorları önbellekten gelir; eksik skor olursa test hata verir."""
    from sayman.belge.siralayici import Siralayici

    rapor = json.loads(SONUC.read_text(encoding="utf-8"))
    sorular = json.loads((BELGE / "sorular.json").read_text(encoding="utf-8"))["ayrilmis"]
    dizin = AnlamDizini(parcalar, GommeOnbellegi(GOMME))
    try:
        a = Arayici(parcalar, dizin, Siralayici(None, SIRALAMA), **rapor["ayarlar"])
        sonuc = puan.puanla(sorular, {s["soru_id"]: ilk(a.skorlar(s["soru"], "cfo"), 10) for s in sorular})
    finally:
        dizin.kapat()
    assert sonuc["isabet@5"] >= 0.85
    assert sonuc["isabet@5"] == rapor["yontemler"]["karma_sirala"]["ayrilmis"]["isabet@5"]
