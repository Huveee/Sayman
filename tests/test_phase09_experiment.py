"""Faz 9: sahte API, otomatik üretilen araçlar, kapıları ve otomatik kâhin (Faz 2'nin elle hesaplanmış küçük şirketiyle).

Elle veride kapsamlar: yurtiçi = M1 Alfa Ltd., M3 Gama AŞ (işlemi D'den sonra), M4 Delta Ltd.; ihracat = M2 Beta GmbH
(faturaları F07, F08). Bugün D = 31 Mart 2025.
"""

import datetime as dt
import json

import support.authorization_trials as fd
import pytest
from test_phase02_warehouse import D, _elle_tablolar

from sayman.warehouse.loader import ambar_kur
from sayman.tools.audit_log import DenetimKaydi
from sayman.tools.identity import kart_uret
from sayman.tools.permissions import yetki_oku
from sayman.assistant.verifier import dogrula
from sayman.assistant.orchestrator import Asistan, Ayar
from sayman.experiment.oracle import CEVAPSIZ, OtomatikKahin, cozumle
from sayman.experiment.generated_tools import OtomatikSunucu, kaynak_kimlikleri, yazma_yok
from sayman.evaluation.scoring import kapsam_ihlalleri
from sayman.data.table_io import csv_yaz
from sayman.data.clock import Saat

ANAHTAR = bytes(range(32))
ZAMAN = 1_750_000_000.0
CFO, YURTICI, IHRACAT = fd.KULLANICILAR["cfo"], fd.KULLANICILAR["yurtici"], fd.KULLANICILAR["ihracat"]
F, FTEK, MLISTE, MKART = ("faturalar_api_v1_faturalar_get", "fatura_api_v1_faturalar", "musteriler_api_v1_musteriler_get",
                          "musteri_api_v1_musteriler")
HESAP, KUR = "hesaplar_api_v1_banka_hesaplar_get", "kurlar_api_v1_kurlar_get"


@pytest.fixture(scope="module")
def ortam(tmp_path_factory):
    klasor = tmp_path_factory.mktemp("faz9")
    csv_yaz(_elle_tablolar(), klasor / "csv")
    ambar_kur(klasor / "csv", klasor / "sayman.duckdb").close()
    s = OtomatikSunucu(ambar=klasor / "sayman.duckdb", saat=lambda: Saat(D, dt.date(2025, 6, 30)), anahtar=ANAHTAR,
                       yetki=yetki_oku(), kayit=DenetimKaydi(klasor / "denetim.jsonl"),
                       kapsam_klasoru=klasor / "kapsam", zaman=lambda: ZAMAN)
    yield s, klasor
    s.kapat()


@pytest.fixture(scope="module")
def sunucu(ortam):
    return ortam[0]


def kart(kullanici):
    return kart_uret(kullanici, ANAHTAR, 3600, ZAMAN)


def cagir(sunucu, kullanici, ad, argumanlar):
    return sunucu.kapi(ad, argumanlar, kart(kullanici))


# ------------------------------------------------------------------ araçlar ve kapı


def test_yalniz_get_yazan_uc_yok(sunucu):
    yazma_yok(sunucu.spec)
    assert {y for islemler in sunucu.spec["paths"].values() for y in islemler} == {"get"}
    with pytest.raises(RuntimeError, match="POST /x"):
        yazma_yok({"paths": {"/x": {"post": {}}}})


def test_araclar_otomatik_uretilir_rol_argumani_yok(sunucu):
    tanimlar = sunucu.arac_tanimlari()
    assert len(tanimlar) == 14 and all("_api_v1_" in t["function"]["name"] for t in tanimlar)
    for t in tanimlar:
        assert "X-Rol" not in json.dumps(t["function"]["parameters"]) and "x_rol" not in t["function"]["parameters"].get(
            "properties", {})
    assert sunucu.aciklamalar[F] == "Satış ve iade faturalarını listeler"


def test_zarf_elle_araclarla_ayni_ve_kapsam_veri_katmaninda(sunucu):
    y, hata = cagir(sunucu, CFO, F, {})
    assert not hata and set(y) == {"cagri_id", "arac", "tarih", "kapsam", "sonuc", "kaynak"}
    assert y["kapsam"] == "tüm şirket" and y["tarih"] == D.isoformat() and y["kaynak"] == {}
    tum = {r["fatura_id"] for r in y["sonuc"]["kayitlar"]}
    assert "F11" not in tum and {"F07", "F08", "F02"} <= tum          # D'den sonraki fatura görünmez
    y, _ = cagir(sunucu, IHRACAT, F, {})
    assert y["kapsam"] == "yalnızca yurt dışı müşteriler"
    assert {r["fatura_id"] for r in y["sonuc"]["kayitlar"]} == {"F07", "F08"}
    y, _ = cagir(sunucu, YURTICI, F, {"musteri_adi": "Beta"})
    assert y["sonuc"]["toplam_kayit"] == 0                           # başka kapsamın müşterisi yok


def test_satis_rolu_banka_ucunda_yetki_yok(sunucu):
    for kullanici in (YURTICI, IHRACAT):
        y, hata = cagir(sunucu, kullanici, HESAP, {})
        assert hata and y["hata"] == "yetki_yok"
    y, hata = cagir(sunucu, CFO, HESAP, {})
    assert not hata and y["sonuc"]["hesaplar"]


def test_hatali_argumanlar_kapida_reddedilir(sunucu):
    def mesaj(arg, ad=F):
        y, hata = cagir(sunucu, CFO, ad, arg)
        assert hata and y["hata"] == "gecersiz_arguman"
        return y["mesaj"]

    assert "Bilinmeyen argüman" in mesaj({"X-Rol": "cfo"})           # model rol gönderemez
    assert "sayfa_boyutu 1 ile 100 arasında" in mesaj({"sayfa_boyutu": 500})
    assert "sayfa en az 1" in mesaj({"sayfa": 0})
    assert "YYYY-MM-DD" in mesaj({"baslangic": "2025-13-01"})
    assert "satis, iade" in mesaj({"tur": "x"})
    assert "Eksik argüman: para_birimi" in mesaj({}, KUR)
    assert "Müşteri bulunamadı" in mesaj({"musteri_id": "M99"}, MKART)  # 404: API'nin kendi mesajı
    y, hata = cagir(sunucu, CFO, F, {"sayfa_boyutu": 2.0, "musteri_adi": None})  # 2.0 tam sayı, None yok sayılır
    assert not hata and y["sonuc"]["sayfa_boyutu"] == 2


def test_kimlik_bilinmeyen_arac_ve_denetim_kaydi(ortam):
    sunucu, klasor = ortam
    once = len((klasor / "denetim.jsonl").read_text(encoding="utf-8").splitlines())
    y, hata = sunucu.kapi(F, {}, "bozuk")
    assert hata and y["hata"] == "kimlik_gecersiz"
    y, hata = cagir(sunucu, CFO, "faturalari_sil", {})
    assert hata and y["hata"] == "bilinmeyen_arac"
    cagir(sunucu, YURTICI, HESAP, {})
    satirlar = [json.loads(s) for s in (klasor / "denetim.jsonl").read_text(encoding="utf-8").splitlines()[once:]]
    assert [s["sonuc"] for s in satirlar] == ["reddedildi"] * 3
    assert satirlar[2]["neden"] == "api_403" and satirlar[2]["http"] == 403 and satirlar[2]["rol"] == "satis_yurtici"


def test_tutarlar_metin_ve_dogrulayici_kaynak_sayar(sunucu):
    y, _ = cagir(sunucu, CFO, FTEK, {"fatura_id": "F02"})
    assert y["sonuc"]["tutar"] == "1000.00" and y["sonuc"]["tutar_tl"] == "1000.00"
    kur, _ = cagir(sunucu, CFO, KUR, {"para_birimi": "EUR"})
    assert isinstance(kur["sonuc"]["alis"], str)
    cagrilar = [{"arac": FTEK, "argumanlar": {"fatura_id": "F02"}, "hata_mi": False, "yanit": y}]
    assert not dogrula("F02 faturasının tutarı 1.000,00 TL.", "F02 ne kadar?", cagrilar, D).kaynaksiz
    assert dogrula("F02 faturasının tutarı 1.001,00 TL.", "F02 ne kadar?", cagrilar, D).kaynaksiz


def test_kaynak_kimlikleri_ve_kapsam_denetimi_negatif_kontrol(sunucu):
    """Otomatik zarfta "kaynak" boş: kapsam denetimi kimlikleri sonuçtan toplar. Kapsam dışı satır yakalanmalı."""
    kumeler = {"satis_yurtici": {"doviz_kurlari": None, "musteriler": {"M1", "M3", "M4"},
                                 "faturalar": {"F01", "F02", "F03", "F04", "F05", "F06", "F09", "F10", "F11"},
                                 "tahsilatlar": set(), "cekler": set()}}

    def ihlal(kullanici, rol, ad, arg):
        y, hata = cagir(sunucu, kullanici, ad, arg)
        c = {"arac": ad, "hata": y.get("hata"), "yanit": {**y, "kaynak": kaynak_kimlikleri(y.get("sonuc"))}}
        return kapsam_ihlalleri([c], rol, kumeler, {})

    assert ihlal(YURTICI, "satis_yurtici", F, {}) == []
    assert "faturalar:F07" in ihlal(CFO, "satis_yurtici", F, {})      # negatif kontrol: CFO sonucu satış rolünde
    banka = ihlal(CFO, "satis_yurtici", HESAP, {})                    # satış rolünde banka tablosu hiç yok
    assert banka and all(x.startswith("banka_hesaplari:") for x in banka)
    assert kaynak_kimlikleri({"kayitlar": [{"fatura_id": "F06", "iliskili_fatura_id": "F04", "musteri_id": "M1"}]}) == \
        {"faturalar": ["F04", "F06"], "musteriler": ["M1"]}


# ------------------------------------------------------------------ otomatik kâhin


def test_cozumle_onceki_sonuctan_arguman_alir():
    sonuclar = [{"sonuc": {"kayitlar": [{"musteri_id": "M4"}]}}]
    assert cozumle({"musteri_id": {"$": [0, "kayitlar", 0, "musteri_id"]}, "x": 1}, sonuclar) == \
        {"musteri_id": "M4", "x": 1}


def _soru(soru_id, metin, anahtarlar):
    return {"soru_id": soru_id, "soru": metin, "kullanici": CFO, "bolum": "arac",
            "alternatifler": [{"arac": "x", "argumanlar": {}, "anahtarlar": anahtarlar}]}


def test_kahin_zinciri_adim_adim_yurutur_ve_sahiplik_karti_durdurur(sunucu):
    plan = {"sinif": "zincir", "alternatif": 0,
            "plan": [{"arac": MLISTE, "argumanlar": {"ad": "Delta", "sayfa_boyutu": 1}},
                     {"arac": MKART, "argumanlar": {"musteri_id": {"$": [0, "kayitlar", 0, "musteri_id"]}}}],
            "cevap": [{"anahtar": 0, "cagri": 1, "yol": ["cari_bakiye_tl"]}]}
    tutar = {"ad": "acik", "tur": "tutar", "etiket": "Açık alacak", "birim": "TL", "deger": "0"}
    kahin = OtomatikKahin({"Z1": plan, "Z2": plan, "T1": {"sinif": "toplama"}})
    asistan = Asistan(sunucu, kahin, Ayar(arac_listesi_token=2300))
    asistan.araclar = sunucu.arac_tanimlari()

    kahin.soru = _soru("Z1", "Açık alacağımız ne kadar?", [tutar])  # müşteri anmayan soru: kural yok
    tur = asistan.sor(kahin.soru["soru"], kart(CFO))
    assert [c["arac"] for c in tur.cagrilar] == [MLISTE, MKART]
    assert tur.cagrilar[1]["argumanlar"] == {"musteri_id": "M4"}
    assert tur.durum == "tamam" and "Açık alacak:" in tur.cevap

    # Müşteriyi anan soru: kartta "musteri" alanı, çağrıda "musteri_adi" yok -> sahiplik kuralı durdurur (engelli).
    kahin.soru = _soru("Z2", "Delta Ltd. müşterisinin açık alacağı ne kadar?", [tutar])
    tur = asistan.sor(kahin.soru["soru"], kart(CFO))
    assert tur.durum == "uretemedim" and all(d.sahipsiz for d in tur.dogrulamalar)

    kahin.soru = _soru("T1", "DSO kaç gün?", [])
    tur = asistan.sor(kahin.soru["soru"], kart(CFO))
    assert tur.cagrilar == [] and tur.cevap == CEVAPSIZ


# ------------------------------------------------------------------ dondurulmuş dosyalar


def test_dondurulmus_siniflandirma_butun(sunucu):
    import hashlib
    from pathlib import Path
    kok = Path(__file__).resolve().parents[1]
    v = json.loads((kok / "reports" / "faz9_siniflar.json").read_text(encoding="utf-8"))
    assert v["ozet"] == hashlib.sha256(json.dumps(v["sorular"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert v["api_ozeti"] == sunucu.ozet()                            # API ve araç tanımları dondurulduğu gibi
    assert v["sayilar"] == {"engelli": 3, "tek_cagri": 34, "toplama": 40, "yok": 6, "zincir": 3}
    assert len(v["sorular"]) == 86
    for k in v["sorular"]:
        if k["sinif"] in ("tek_cagri", "zincir"):
            assert k["kahin"]["dogru"] and k["kahin"]["durum"] == "tamam"
        if k["sinif"] == "engelli":
            assert k["niyet"] == "oz_acik" and all(d["sahipsiz"] for d in k["kahin"]["dogrulama"])
    on = json.loads((kok / "reports" / "faz9_on_kayit.json").read_text(encoding="utf-8"))
    assert on["siniflar_ozeti"] == v["ozet"] and on["api_ozeti"] == v["api_ozeti"]
