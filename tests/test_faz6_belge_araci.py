"""Faz 6: belge_ara aracı (14. araç) Faz 3'ün tek kapısından geçer.

Küçük bir belge kütüphanesiyle, modelleri çalıştırmadan: anlam araması sahte vektörlerle (her metne kelimelerinden
türeyen bir vektör), kelime araması gerçek BM25 ile. Elle ambar Faz 3 testlerindekiyle aynıdır.
"""

import datetime as dt
import zlib

import numpy as np
import pytest
from test_faz2_ambar import D, _elle_tablolar

from sayman.ambar.kurulum import ambar_kur
from sayman.araclar import araclar as araclar_modulu
from sayman.araclar.kayit import DenetimKaydi
from sayman.araclar.kimlik import kart_uret
from sayman.araclar.sunucu import SaymanSunucusu
from sayman.araclar.yetki import yetki_oku
from sayman.belge.anlam import AnlamDizini, GommeOnbellegi, anahtar
from sayman.belge.arama import Arayici, terimler
from sayman.belge.kutuphane import Parca
from sayman.veri.cikti import csv_yaz
from sayman.veri.saat import Saat

ANAHTAR = bytes(range(32))
ZAMAN = 1_750_000_000.0
CFO, YURTICI, IHRACAT = "selin.aydin", "murat.kaya", "deniz.arslan"
UZUN = "Madde metni " + "uzun bir açıklama cümlesi " * 120  # ~3.100 karakter


def P(pid, baslik, metin, roller):
    return Parca(pid, pid.split("#")[0], baslik, "Ödeme Koşulları", roller, metin, f"{baslik} {metin}")


PARCALAR = [
    P("SOZ-M1#m3", "Satış Sözleşmesi — Alfa Ltd.", "Alıcı faturayı 30 gün içinde öder.", ("cfo", "satis_yurtici")),
    P("SOZ-M2#m3", "Satış Sözleşmesi — Beta GmbH", "Der Käufer zahlt binnen 60 Tagen; alıcı 60 gün içinde öder.",
      ("cfo", "satis_ihracat")),
    P("POL-NAKIT#m2", "Nakit Politikası", "Ana TL hesabının en düşük bakiyesi 5 milyon TL'dir.", ("cfo",)),
    P("POL-CEK#m2", "Çek Politikası", "En uzun vadeli çek 120 gün olabilir.", ("cfo", "satis_yurtici", "satis_ihracat")),
    P("KANUN-6102#m21", "Türk Ticaret Kanunu", UZUN, ("cfo", "satis_yurtici", "satis_ihracat")),
]


def _vektor(metin: str) -> np.ndarray:
    """Kelime köklerinden türeyen sahte gömme: ortak kelimesi çok olan metinler birbirine yakın düşer."""
    v = np.zeros(1024, dtype=np.float32)
    for t in terimler(metin):
        v[zlib.crc32(t.encode()) % 1024] += 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


class SahteOnbellek(GommeOnbellegi):
    def __init__(self):
        self.v = {}

    def al(self, metinler):
        return np.stack([_vektor(m) for m in metinler])


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    klasor = tmp_path_factory.mktemp("faz6_belge")
    csv_yaz(_elle_tablolar(), klasor / "csv")
    ambar_kur(klasor / "csv", klasor / "sayman.duckdb").close()
    dizin = AnlamDizini(PARCALAR, SahteOnbellek())

    def kur(ad, belge, yontem="anlam"):
        return SaymanSunucusu(ambar=klasor / "sayman.duckdb", saat=lambda: Saat(D, dt.date(2025, 6, 30)),
                              anahtar=ANAHTAR, yetki=yetki_oku(), kayit=DenetimKaydi(klasor / f"kayit_{ad}.jsonl"),
                              kapsam_klasoru=klasor / f"kapsam_{ad}", zaman=lambda: ZAMAN, belge=belge,
                              belge_yontemi=yontem)

    sunucular = {"anlam": kur("anlam", Arayici(PARCALAR, dizin)), "bm25": kur("bm25", Arayici(PARCALAR), "bm25"),
                 "yok": kur("yok", None)}
    yield sunucular
    for s in sunucular.values():
        s.kapat()
    dizin.kapat()


def ara(sunucu, kullanici, **args):
    return sunucu.kapi("belge_ara", args, kart_uret(kullanici, ANAHTAR, 3600, ZAMAN))


def kimlikler(yanit):
    return [s["parca_id"] for s in yanit["sonuc"]["sonuclar"]]


def test_her_rol_belge_ara_cagirabilir():
    yetki = yetki_oku()
    assert all("belge_ara" in r.araclar for r in yetki.roller.values())
    assert "belge_ara" in araclar_modulu.ARACLAR_ADA_GORE and len(araclar_modulu.ARACLAR) == 14


def test_sonuc_bicimi_ve_kaynak(ortam):
    yanit, hata = ara(ortam["anlam"], CFO, sorgu="Alfa Ltd. faturayı kaç günde öder")
    assert not hata
    assert set(yanit) == {"cagri_id", "arac", "tarih", "kapsam", "sonuc", "kaynak"}
    assert yanit["tarih"] == D.isoformat() and yanit["arac"] == "belge_ara"
    ilk = yanit["sonuc"]["sonuclar"][0]
    assert ilk == {"parca_id": "SOZ-M1#m3", "belge": "Satış Sözleşmesi — Alfa Ltd.", "madde": "Ödeme Koşulları",
                   "metin": "Alıcı faturayı 30 gün içinde öder."}
    assert yanit["kaynak"] == {"belge_parcalari": kimlikler(yanit)}
    assert len(kimlikler(yanit)) == 3  # varsayılan


@pytest.mark.parametrize("yontem", ["anlam", "bm25"])
def test_rolun_goremedigi_belge_hicbir_yontemle_donmez(ortam, yontem):
    s = ortam[yontem]
    yurtici = kimlikler(ara(s, YURTICI, sorgu="Beta GmbH ödeme süresi 60 gün", en_fazla=3)[0])
    ihracat = kimlikler(ara(s, IHRACAT, sorgu="Beta GmbH ödeme süresi 60 gün", en_fazla=3)[0])
    assert "SOZ-M2#m3" not in yurtici and "POL-NAKIT#m2" not in yurtici
    assert "SOZ-M1#m3" not in ihracat and "POL-NAKIT#m2" not in ihracat
    assert ihracat[0] == "SOZ-M2#m3"
    assert "POL-NAKIT#m2" in kimlikler(ara(s, CFO, sorgu="Ana TL hesabının en düşük bakiyesi", en_fazla=3)[0])


def test_uzun_madde_kelime_sinirinda_kesilir(ortam):
    yanit, _ = ara(ortam["anlam"], CFO, sorgu="uzun bir açıklama cümlesi", en_fazla=1)
    [s] = yanit["sonuc"]["sonuclar"]
    assert s["parca_id"] == "KANUN-6102#m21" and s["metin_kesildi"] is True
    assert len(s["metin"]) <= araclar_modulu.BELGE_METIN_SINIRI + 2 and s["metin"].endswith(" …")
    assert UZUN.startswith(s["metin"][:-2])


def test_denetim_kaydi_ve_sema(ortam):
    s = ortam["anlam"]
    once = len(s.kayit.satirlar)
    _, hata = ara(s, YURTICI, sorgu="çek vadesi")
    assert not hata
    r1, h1 = ara(s, YURTICI, sorgu="ab")
    r2, h2 = ara(s, YURTICI, sorgu="x" * 301)
    r3, h3 = ara(s, YURTICI, sorgu="çek", en_fazla=4)
    r4, h4 = ara(s, YURTICI)
    assert h1 and r1["mesaj"] == "Argüman hatası: sorgu en az 3 karakter olmalı."
    assert h2 and r2["mesaj"] == "Argüman hatası: sorgu en çok 300 karakter olabilir."
    assert h3 and r3["mesaj"] == "Argüman hatası: en_fazla 1 ile 3 arasında olmalı."
    assert h4 and r4["mesaj"] == "Argüman hatası: Eksik argüman: sorgu."
    kayit = s.kayit.satirlar[once:]
    assert [(k["arac"], k["kullanici"], k["rol"], k["sonuc"]) for k in kayit] == \
        [("belge_ara", YURTICI, "satis_yurtici", "tamam")] + [("belge_ara", YURTICI, "satis_yurtici", "reddedildi")] * 4
    assert kayit[0]["kayit_sayisi"] == 3


def test_kimliksiz_cagri_reddedilir(ortam):
    yanit, hata = ortam["anlam"].kapi("belge_ara", {"sorgu": "çek vadesi"}, None)
    assert hata and yanit["hata"] == "kimlik_gecersiz"


def test_kutuphane_yoksa_kullanilamaz(ortam):
    s = ortam["yok"]
    yanit, hata = ara(s, CFO, sorgu="çek vadesi")
    assert hata and yanit == {"cagri_id": yanit["cagri_id"], "hata": "kullanilamaz",
                              "mesaj": "Belge araması şu an kullanılamıyor."}
    assert s.kayit.satirlar[-1]["neden"] == "kullanilamaz"


def test_yeni_soru_vektoru_yalnizca_bellekte(tmp_path):
    gorulen = []

    def gomucu(metinler):
        gorulen.extend(metinler)
        return np.stack([_vektor(m) for m in metinler])

    onbellek = GommeOnbellegi(tmp_path / "yok.npz")
    for p in PARCALAR:
        onbellek.v[anahtar(p.arama_metni)] = _vektor(p.arama_metni)
    dizin = AnlamDizini(PARCALAR, onbellek, gomucu=gomucu)
    try:
        assert dizin.skorlar("çek vadesi", "cfo")
        assert dizin.skorlar("çek vadesi", "cfo")
        assert gorulen == ["çek vadesi"]  # ikinci kez modele gitmedi
        assert not (tmp_path / "yok.npz").exists()  # diske yazılmadı
    finally:
        dizin.kapat()
