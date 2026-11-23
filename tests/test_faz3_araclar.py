"""Faz 3: araçlar, kimlik, yetki ve denetim kaydı (Faz 2'nin elle hesaplanmış küçük şirketiyle; indirme gerekmez).

Bütün çağrılar gerçek bir MCP istemci oturumundan, kimlik kartı isteğin üst bilgisinde (_meta) gönderilir.
Elle veride kapsamlar: yurtiçi = M1 Alfa Ltd., M3 Gama AŞ (henüz işlemi yok), M4 Delta Ltd.;
ihracat = M2 Beta GmbH (faturaları F07, F08). Bugün D = 31 Mart 2025.
"""

import datetime as dt
import json
import re
from dataclasses import replace
from pathlib import Path

import duckdb
import faz3_denemeler as fd
import jsonschema
import pytest
from test_faz2_ambar import D, _elle_tablolar

from sayman.ambar.kurulum import ambar_kur, satirlar
from sayman.araclar import sunucu as sunucu_modulu
from sayman.araclar.araclar import ARACLAR, TOPLU_ARACLAR
from sayman.araclar.kayit import DenetimKaydi, dogrula
from sayman.araclar.kimlik import anahtar_oku, kart_uret
from sayman.araclar.sunucu import SaymanSunucusu
from sayman.araclar.yetki import yetki_oku
from sayman.veri.cikti import SEMA, csv_yaz
from sayman.veri.saat import Saat

G = dt.date.fromisoformat
ANAHTAR = bytes(range(32))
ZAMAN = 1_750_000_000.0
VERI_BITIS = G("2025-06-30")
CFO, YURTICI, IHRACAT = fd.KULLANICILAR["cfo"], fd.KULLANICILAR["yurtici"], fd.KULLANICILAR["ihracat"]
KAYNAK_SUTUNLARI = {"banka_hesaplari": "hesap_id", "banka_hareketleri": "hareket_id", "musteriler": "musteri_id",
                    "faturalar": "fatura_id", "tahsilatlar": "tahsilat_id", "alis_faturalari": "alis_fatura_id",
                    "cekler": "cek_id", "doviz_kurlari": "kur_id"}


@pytest.fixture(scope="module")
def T():
    return _elle_tablolar()


@pytest.fixture(scope="module")
def ortam(tmp_path_factory, T):
    klasor = tmp_path_factory.mktemp("faz3")
    csv_yaz(T, klasor / "csv")
    ambar_kur(klasor / "csv", klasor / "sayman.duckdb").close()
    s = SaymanSunucusu(ambar=klasor / "sayman.duckdb", saat=lambda: Saat(D, VERI_BITIS), anahtar=ANAHTAR,
                       yetki=yetki_oku(), kayit=DenetimKaydi(klasor / "denetim" / "kayit.jsonl"),
                       kapsam_klasoru=klasor / "kapsam", zaman=lambda: ZAMAN)
    yield s, klasor
    s.kapat()


@pytest.fixture(scope="module")
def sunucu(ortam):
    return ortam[0]


@pytest.fixture(scope="module")
def hakem(T, tmp_path_factory):
    h = fd.Hakem(T, D, tmp_path_factory.mktemp("hakem"))
    yield h
    h.kapat()


def kart(kullanici):
    return kart_uret(kullanici, ANAHTAR, 3600, ZAMAN)


def cagir(sunucu, kullanici, arac, **args):
    """Tek çağrı; (yanıt, hata_mı)."""
    return fd.mcp_cagir(sunucu, [(arac, args, kullanici is not None, kart(kullanici) if kullanici else None)])[0]


def sonuc(sunucu, kullanici, arac, **args):
    yanit, hata = cagir(sunucu, kullanici, arac, **args)
    assert not hata, yanit
    return yanit["sonuc"]


def ret(sunucu, kullanici, arac, **args):
    yanit, hata = cagir(sunucu, kullanici, arac, **args)
    assert hata and set(yanit) == {"cagri_id", "hata", "mesaj"}, yanit
    return yanit["hata"]


# ---------------------------------------------------------------- araç listesi: salt okunur


def test_arac_listesi_salt_okunur_ve_rol_tablosuyla_ayni(sunucu):
    araclar = fd.anyio.run(_listele, sunucu)
    adlar = {a.name for a in araclar}
    assert len(araclar) == 14 and adlar == {a.ad for a in ARACLAR}
    yetki = yetki_oku()
    assert yetki.roller["cfo"].araclar == adlar
    assert all(r.araclar <= adlar for r in yetki.roller.values()), "rol tablosunda olmayan araç"
    for a in araclar:
        assert a.annotations.readOnlyHint is True and a.annotations.destructiveHint is False, a.name
        jsonschema.Draft202012Validator.check_schema(a.inputSchema)
        assert a.inputSchema["additionalProperties"] is False, a.name


async def _listele(sunucu):
    async with fd.create_connected_server_and_client_session(sunucu.mcp) as oturum:
        return (await oturum.list_tools()).tools


def test_araclarda_ve_metriklerde_yazan_sql_yok():
    kok = Path(__file__).resolve().parents[1] / "src" / "sayman"
    yazan = re.compile(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|"
                       r"PRAGMA|CHECKPOINT|VACUUM|TRUNCATE)\b")
    for dosya in (kok / "araclar" / "araclar.py", kok / "ambar" / "metrikler.py"):
        assert not yazan.findall(dosya.read_text(encoding="utf-8")), dosya.name


@pytest.mark.parametrize("sql", ["INSERT INTO musteriler (musteri_id) VALUES ('X')", "DELETE FROM faturalar",
                                 "UPDATE banka_hesaplari SET acilis_bakiye_kurus = 0", "CREATE TABLE x (a INTEGER)",
                                 "DROP TABLE faturalar"])
def test_her_rolun_baglantisi_yazmaya_kapali(sunucu, sql):
    assert set(sunucu.baglantilar) == {"hepsi", "yurtici", "ihracat"}
    for kapsam, con in sunucu.baglantilar.items():
        with pytest.raises(duckdb.Error, match="read-only|read only"):
            con.execute(sql)


# ---------------------------------------------------------------- kimlik


def test_kimliksiz_her_arac_reddedilir_ve_kaydedilir(sunucu):
    once = len(sunucu.kayit.satirlar)
    cevaplar = fd.mcp_cagir(sunucu, [(a.ad, {}, False, None) for a in ARACLAR])
    for a, (yanit, hata) in zip(ARACLAR, cevaplar):
        assert hata and yanit["hata"] == "kimlik_gecersiz" and set(yanit) == {"cagri_id", "hata", "mesaj"}, a.ad
    kayit = sunucu.kayit.satirlar[once:]
    assert [s["arac"] for s in kayit] == [a.ad for a in ARACLAR]
    assert all(s["sonuc"] == "reddedildi" and s["neden"] == "kart_yok" and s["kullanici"] is None for s in kayit)


@pytest.mark.parametrize("saldiri", list(fd.KIMLIK_SALDIRILARI))
def test_kimlik_saldirilari(sunucu, saldiri):
    kullanici = YURTICI if saldiri in ("degistirilmis", "rol_iddiasi") else CFO
    gonder, k = fd.kart_hazirla(saldiri, kullanici, ANAHTAR, ZAMAN)
    once = len(sunucu.kayit.satirlar)
    yanit, hata = fd.mcp_cagir(sunucu, [("nakit_pozisyonu", {}, gonder, k)])[0]
    satir = sunucu.kayit.satirlar[once]
    assert hata and "sonuc" not in yanit
    assert satir["neden"] == fd.KIMLIK_SALDIRILARI[saldiri]
    if saldiri == "rol_iddiasi":  # kart geçerli; rol karttan değil kullanıcı listesinden okunur
        assert (yanit["hata"], satir["kullanici"], satir["rol"]) == ("yetki_yok", YURTICI, "satis_yurtici")
    else:
        assert yanit["hata"] == "kimlik_gecersiz" and satir["kullanici"] is None
        assert yanit["mesaj"] == "Kimlik doğrulanamadı."  # nedeni kullanıcıya söylenmez


def test_gecerli_kart_kabul_edilir_ve_kart_kayda_yazilmaz(ortam):
    s, klasor = ortam
    k = kart(CFO)
    yanit, hata = fd.mcp_cagir(s, [("nakit_pozisyonu", {}, True, k)])[0]
    assert not hata and yanit["kapsam"] == "tüm şirket"
    assert s.kayit.satirlar[-1]["kullanici"] == CFO
    assert k.split(".")[1] not in (klasor / "denetim" / "kayit.jsonl").read_text(encoding="utf-8")


def test_anahtarsiz_sunucu_acilmaz(tmp_path):
    for ortam_ in ({}, {"SAYMAN_KIMLIK_ANAHTARI": ""}, {"SAYMAN_KIMLIK_ANAHTARI": "zz" * 32},
                   {"SAYMAN_KIMLIK_ANAHTARI": "ab" * 16}):
        with pytest.raises(RuntimeError):
            anahtar_oku(ortam_)
    assert anahtar_oku({"SAYMAN_KIMLIK_ANAHTARI": ANAHTAR.hex()}) == ANAHTAR
    with pytest.raises(RuntimeError, match="anahtar"):
        SaymanSunucusu(ambar=tmp_path / "yok.duckdb", saat=lambda: Saat(D, VERI_BITIS), anahtar=b"",
                       yetki=yetki_oku(), kayit=DenetimKaydi())


def test_kart_en_cok_12_saat():
    with pytest.raises(ValueError):
        kart_uret(CFO, ANAHTAR, 13 * 3600, ZAMAN)


# ---------------------------------------------------------------- yetki ve kapsam


def test_satis_mudurleri_yalnizca_kendi_musterilerini_gorur(sunucu):
    # Yurtiçi: M1 1.120,00 + M4 150,00 = 1.270,00. İhracat: M2 15.000 euro-kuruş x 36,5 = 5.475,00.
    assert sonuc(sunucu, YURTICI, "alacak_yaslandirma")["toplam_tl"] == "1270.00"
    assert sonuc(sunucu, IHRACAT, "alacak_yaslandirma")["toplam_tl"] == "5475.00"
    assert sonuc(sunucu, CFO, "alacak_yaslandirma")["toplam_tl"] == "6745.00"
    assert [f["fatura_id"] for f in sonuc(sunucu, IHRACAT, "geciken_alacaklar", min_gun=1)["faturalar"]] == ["F07"]
    assert sonuc(sunucu, IHRACAT, "cek_portfoyu", durum="hepsi")["cek_sayisi"] == 0
    assert sonuc(sunucu, YURTICI, "cek_portfoyu", durum="hepsi")["cek_sayisi"] == 2


@pytest.mark.parametrize("kullanici", [YURTICI, IHRACAT])
def test_satis_muduru_yasak_araclari_cagiramaz(sunucu, kullanici):
    for arac in fd.YASAK_ARACLAR:
        assert ret(sunucu, kullanici, arac, **fd._gecerli_args(arac, D)) == "yetki_yok", arac


def test_yok_ile_senin_degil_ayni_cevabi_alir(sunucu):
    def ayni(kullanici, arac, var, yok):
        (a, _), (b, _) = cagir(sunucu, kullanici, arac, **var), cagir(sunucu, kullanici, arac, **yok)
        a.pop("cagri_id"), b.pop("cagri_id")
        assert a == b, (var, a, b)

    ayni(YURTICI, "musteri_ozeti", {"musteri_adi": "Beta GmbH"}, {"musteri_adi": "Zeta GmbH"})
    ayni(YURTICI, "alacak_yaslandirma", {"musteri_adi": "M2"}, {"musteri_adi": "M9"})
    assert sonuc(sunucu, YURTICI, "alacak_yaslandirma", musteri_adi="m1")["musteri_id"] == "M1"  # kendi müşterisi
    ayni(YURTICI, "fatura_ara", {"fatura_no": "F07"}, {"fatura_no": "F99"})
    ayni(IHRACAT, "fatura_ara", {"fatura_no": "f01"}, {"fatura_no": "f98"})
    ayni(IHRACAT, "musteri_ozeti", {"musteri_adi": "Alfa Ltd."}, {"musteri_adi": "Omega Ltd."})


def test_kapsam_ambarlari_python_suzgeciyle_birebir_ayni(sunucu, hakem):
    for kapsam in fd.KAPSAMLAR:
        for ad in SEMA:
            sql = f"SELECT * FROM {ad} ORDER BY ALL"
            assert sunucu.baglantilar[kapsam].execute(sql).fetchall() == \
                hakem.referans[kapsam].execute(sql).fetchall(), (kapsam, ad)


# ---------------------------------------------------------------- 60 deneme


def test_60_yetkisiz_deneme_sifir_sizinti(sunucu, T, hakem):
    liste = fd.denemeler(T, D, VERI_BITIS)
    assert [sum(d["grup"] == g for d in liste) for g in "ABCDE"] == [12, 12, 24, 6, 6]
    sonuclar = fd.denemeleri_calistir(sunucu, liste, ANAHTAR, ZAMAN, hakem)
    sorunlu = [(r["no"], r["arac"], r["argumanlar"], r["sorunlar"]) for r in sonuclar if r["sorunlar"]]
    assert not sorunlu
    # Yurtiçi müdürü çeklerin hepsini görür (ihracat müşterilerinin çeki yok): o deneme saklanacak veri bulamaz.
    bos = [(r["kullanici"], r["arac"]) for r in sonuclar if r["grup"] == "C" and not r["gizledi"]]
    assert bos == [(YURTICI, "cek_portfoyu")]


def test_ret_cevaplari_kisa_ve_girdiyi_yansitmaz(sunucu):
    uzun = "Z" * 50_000
    for arac, args in [("musteri_ozeti", {"musteri_adi": uzun}), ("fatura_ara", {uzun: 1}),
                       ("fatura_ara", {"fatura_no": uzun}), ("dso_hesapla", {"donem": uzun}),
                       ("geciken_alacaklar", {"min_gun": uzun}), ("geciken_alacaklar", {}),
                       ("nakit_pozisyonu", {"tarih": uzun})]:
        yanit, hata = cagir(sunucu, CFO, arac, **args)
        metin = json.dumps(yanit, ensure_ascii=False)
        assert hata and yanit["hata"] == "gecersiz_arguman" and len(metin) < 300 and "ZZZ" not in metin, (arac, metin)
    yanit, hata = fd.mcp_cagir(sunucu, [(uzun, {}, True, kart(CFO))])[0]
    assert hata and yanit["hata"] == "bilinmeyen_arac" and len(sunucu.kayit.satirlar[-1]["arac"]) == 100
    assert len(json.dumps(sunucu.kayit.satirlar[-2]["argumanlar"])) < 2100  # kayıtta da kesilir
    assert "Eksik argüman: min_gun." in fd.mcp_cagir(sunucu, [("geciken_alacaklar", {}, True, kart(CFO))])[0][0]["mesaj"]


def test_hakem_sizintiyi_yakalar(sunucu, T, hakem):
    """Hakemin kendisi de sınanır: kapsam dışı bir cevap verilirse yakalamalı."""
    deneme = {"arac": "fatura_ara", "argumanlar": {"fatura_no": "F07"}, "beklenen": ("kapsamli",)}
    cfo_cevabi, _ = cagir(sunucu, CFO, "fatura_ara", fatura_no="F07")
    sorunlar = hakem.degerlendir(deneme, "yurtici", cfo_cevabi, False)
    assert any("farklı" in s for s in sorunlar) and any("kapsam dışı değer" in s for s in sorunlar)


# ---------------------------------------------------------------- tarih


def test_gelecek_tarih_her_tarih_alaninda_reddedilir(sunucu):
    yarin = (D + dt.timedelta(days=1)).isoformat()
    bugun = D.isoformat()
    for a in ARACLAR:
        for alan in a.tarih_alanlari:
            args = {"baslangic": bugun, "bitis": bugun} if "baslangic" in a.ozellikler else {}
            args.update({k: v for k, v in {"musteri_adi": "Alfa", "min_gun": 1, "donem": "son_30_gun",
                                            "para_birimi": "EUR"}.items() if k in a.zorunlu})
            assert not cagir(sunucu, CFO, a.ad, **{**args, alan: bugun})[1], (a.ad, alan)
            assert ret(sunucu, CFO, a.ad, **{**args, alan: yarin}) == "gelecek_tarih", (a.ad, alan)


def test_saat_ilerleyince_yeni_gun_gorunur(sunucu, monkeypatch):
    yarin = (D + dt.timedelta(days=1)).isoformat()
    assert ret(sunucu, CFO, "nakit_pozisyonu", tarih=yarin) == "gelecek_tarih"
    monkeypatch.setattr(sunucu, "saat", lambda: Saat(D + dt.timedelta(days=1), VERI_BITIS))
    # 1 Nisan: A_TRY değişmedi (H10 2 Nisan'da); C_USD açıldı, bakiyesi 0; EUR 6.000 x 37,5 = 225.000.
    assert sonuc(sunucu, CFO, "nakit_pozisyonu", tarih=yarin)["toplam_tl"] == "12419.00"


@pytest.mark.parametrize("args", [{"tarih": "20250331"}, {"tarih": "2025-02-30"}, {"tarih": "31.03.2025"},
                                  {"tarih": "2025-03-31T00:00:00"}, {"tarih": 20250331}, {"en_fazla": 0},
                                  {"en_fazla": 11}, {"en_fazla": True}, {"en_fazla": "5"},
                                  {"musteri_adi": "a" * 101}, {"bilinmeyen": 1}])
def test_gecersiz_argumanlar_reddedilir(sunucu, args):
    assert ret(sunucu, CFO, "alacak_yaslandirma", **args) == "gecersiz_arguman"


def test_tam_sayi_ondalikli_yazilsa_da_calisir(sunucu):
    s = sonuc(sunucu, CFO, "geciken_alacaklar", min_gun=30.0, en_fazla=2.0)
    assert s["min_gun"] == 30 and isinstance(s["min_gun"], int) and len(s["faturalar"]) == 2
    assert ret(sunucu, CFO, "geciken_alacaklar", min_gun=30.5) == "gecersiz_arguman"


def test_aralik_ters_ise_anlasilir_hata(sunucu):
    yanit, hata = cagir(sunucu, CFO, "banka_hareketleri", baslangic="2025-03-31", bitis="2025-03-01")
    assert hata and yanit["hata"] == "gecersiz_arguman" and "sonra" in yanit["mesaj"]


# ---------------------------------------------------------------- araçların değerleri (elle hesaplandı)


def test_nakit_pozisyonu(sunucu):
    s = sonuc(sunucu, CFO, "nakit_pozisyonu")
    assert [(h["hesap_id"], h["bakiye"], h["kur"], h["bakiye_tl"]) for h in s["hesaplar"]] == [
        ("A_TRY", "10169.00", "1", "10169.00"), ("B_EUR", "60.00", "37.1234", "2227.40")]
    assert s["toplam_tl"] == "12396.40"


def test_banka_hareketleri(sunucu):
    s = sonuc(sunucu, CFO, "banka_hareketleri", baslangic="2025-03-01", bitis="2025-03-31", en_fazla=3)
    # TRY: H05 -30, H06 -120, H07 +50, H08 -500, H09 +49. EUR: H12 +50, H13 +10.
    assert s["toplamlar"] == [{"para_birimi": "EUR", "adet": 2, "giris": "60.00", "cikis": "0.00"},
                              {"para_birimi": "TRY", "adet": 5, "giris": "99.00", "cikis": "-650.00"}]
    assert (s["hareket_sayisi"], s["gosterilen"]) == (7, 3)
    assert [h["hareket_id"] for h in s["hareketler"]] == ["H09", "H08", "H07"]
    giris = sonuc(sunucu, CFO, "banka_hareketleri", baslangic="2025-03-01", bitis="2025-03-31", yon="giris")
    assert [h["hareket_id"] for h in giris["hareketler"]] == ["H09", "H07", "H13", "H12"]
    tek = sonuc(sunucu, CFO, "banka_hareketleri", baslangic="2025-03-01", bitis="2025-03-31", hesap_id="B_EUR")
    assert [h["hareket_id"] for h in tek["hareketler"]] == ["H13", "H12"]


def test_alacak_yaslandirma(sunucu):
    s = sonuc(sunucu, CFO, "alacak_yaslandirma")
    assert s["dilimler"] == {"vadesi_gelmemis": "420.00", "gecikme_1_30": "5875.00", "gecikme_31_60": "400.00",
                             "gecikme_61_90": "0.00", "gecikme_91_180": "30.00", "gecikme_180_ustu": "70.00",
                             "eslesmemis_tahsilat": "-50.00"}
    assert (s["toplam_tl"], s["vadesi_gecmis_tl"], s["musteri_sayisi"]) == ("6745.00", "6375.00", 3)
    assert [(m["musteri_id"], m["toplam_tl"], m["vadesi_gecmis_tl"]) for m in s["en_buyuk_alacaklar"]] == [
        ("M2", "5475.00", "5475.00"), ("M1", "1120.00", "870.00"), ("M4", "150.00", "30.00")]
    tek = sonuc(sunucu, CFO, "alacak_yaslandirma", musteri_adi="delta")
    assert (tek["musteri_id"], tek["toplam_tl"], tek["dilimler"]["vadesi_gelmemis"]) == ("M4", "150.00", "120.00")
    assert sonuc(sunucu, CFO, "alacak_yaslandirma", musteri_adi="Ltd")["birden_fazla"] is True


def test_geciken_alacaklar(sunucu):
    s = sonuc(sunucu, CFO, "geciken_alacaklar", min_gun=1)
    assert [(f["fatura_id"], f["gecikme_gun"], f["acik_tutar"], f["acik_tutar_tl"]) for f in s["faturalar"]] == [
        ("F07", 20, "150.00", "5475.00"), ("F02", 58, "400.00", "400.00"), ("F04", 19, "400.00", "400.00"),
        ("F01", 181, "70.00", "70.00"), ("F09", 120, "30.00", "30.00")]
    assert (s["fatura_sayisi"], s["toplam_tl"]) == (5, "6375.00")
    s30 = sonuc(sunucu, CFO, "geciken_alacaklar", min_gun=30, en_fazla=2)
    assert (s30["fatura_sayisi"], s30["toplam_tl"], [f["fatura_id"] for f in s30["faturalar"]]) == (
        3, "500.00", ["F02", "F01"])


def test_musteri_ozeti(sunucu):
    s = sonuc(sunucu, CFO, "musteri_ozeti", musteri_adi="alfa")
    assert (s["musteri_id"], s["acik_alacak_tl"], s["acik_fatura_sayisi"], s["en_eski_acik_vade"]) == (
        "M1", "1120.00", 4, "2024-10-01")
    assert [t["tahsilat_id"] for t in s["son_tahsilatlar"]] == ["T5", "T2", "T1"]
    assert (s["bekleyen_cek_sayisi"], s["bekleyen_cek_tutari"]) == (1, "200.00")  # C1; C2 karşılıksız
    # Son 12 ayda kapanan: yalnızca F03 (vadeden 10 gün sonra).
    assert (s["son_12_ay_kapanan_fatura"], s["son_12_ay_gec_kapanan_fatura"], s["son_12_ay_ortalama_gecikme_gun"]) \
        == (1, 1, 10)
    # Gama AŞ'nin ilk işlemi 15 Nisan'da: bugün henüz görünmez.
    assert sonuc(sunucu, CFO, "musteri_ozeti", musteri_adi="Gama AŞ")["bulunamadi"] is True
    assert ret(sunucu, CFO, "musteri_ozeti", musteri_adi="al") == "gecersiz_arguman"


def test_dso(sunucu):
    s = sonuc(sunucu, CFO, "dso_hesapla", donem="son_12_ay")
    assert (s["dso_gun"], s["acik_alacak_tl"], s["donem_satisi_tl"], s["pencere_baslangic"]) == (
        "247.78", "6745.00", "9936.00", "2024-04-01")


def test_yaklasan_odemeler(sunucu):
    s = sonuc(sunucu, CFO, "yaklasan_odemeler", gun_sayisi=30)
    assert [(o["alis_fatura_id"], o["kalan_gun"], o["tutar_tl"], o["tedarikci"]) for o in s["odemeler"]] == [
        ("A4", -28, "30.00", "Delta Kağıt"), ("A1", 0, "250.00", "Delta Kağıt"), ("A3", 24, "80.00", "Kira AŞ")]
    assert (s["toplam_tl"], s["vadesi_gecmis_tl"]) == ("360.00", "30.00")


def test_nakit_akis_tahmini(sunucu):
    s = sonuc(sunucu, CFO, "nakit_akis_tahmini", hafta_sayisi=13)
    h1 = s["haftalar"][0]
    # Hafta 1: alacak 618.851 + POS 9.800 giriş, tedarikçi 28.000 çıkış.
    assert (s["acilis_tl"], h1["giris_tl"], h1["cikis_tl"], h1["net_tl"], h1["bilinen_kalemlerle_bakiye_tl"]) == (
        "12396.40", "6286.51", "280.00", "6006.51", "18402.91")
    assert s["haftalar"][-1]["bilinen_kalemlerle_bakiye_tl"] == "18852.91" and s["supheli_alacak_tl"] == "30.00"


def test_kur_riski_ve_doviz_kuru(sunucu):
    assert sonuc(sunucu, CFO, "kur_riski")["dovizler"] == [
        {"para_birimi": "EUR", "alacak": "150.00", "banka": "60.00", "toplam": "210.00", "kur": "37.1234",
         "toplam_tl": "7795.91", "kur_yuzde_10_degisirse_etki_tl": "779.59", "alacak_kur_farki_tl": "93.51"}]
    assert sonuc(sunucu, CFO, "kur_riski", para_birimi="USD")["dovizler"] == []
    usd, hata = cagir(sunucu, YURTICI, "doviz_kuru", para_birimi="USD")
    assert not hata and (usd["sonuc"]["alis"], usd["kaynak"]) == ("34", {"doviz_kurlari": ["2025-03-31-USD"]})
    pazar = sonuc(sunucu, YURTICI, "doviz_kuru", para_birimi="EUR", tarih="2025-03-30")
    assert (pazar["kur_tarihi"], pazar["alis"]) == ("2025-03-28", "37")
    assert sonuc(sunucu, IHRACAT, "doviz_kuru", para_birimi="GBP")["bulunamadi"] is True


def test_cek_portfoyu(sunucu):
    bekleyen = sonuc(sunucu, CFO, "cek_portfoyu")
    assert (bekleyen["cek_sayisi"], bekleyen["toplam_tl"], [c["cek_id"] for c in bekleyen["cekler"]]) == (
        1, "200.00", ["C1"])
    hepsi = sonuc(sunucu, CFO, "cek_portfoyu", durum="hepsi")
    assert hepsi["durumlara_gore"] == [{"durum": "karsiliksiz", "adet": 1, "toplam_tl": "99.99"},
                                       {"durum": "portfoyde", "adet": 1, "toplam_tl": "200.00"}]
    assert [c["cek_id"] for c in hepsi["cekler"]] == ["C2", "C1"]
    assert sonuc(sunucu, CFO, "cek_portfoyu", vade_gun=10)["cek_sayisi"] == 0  # C1'in vadesi 20 gün sonra
    assert sonuc(sunucu, CFO, "cek_portfoyu", vade_gun=20)["cek_sayisi"] == 1


def test_cek_portfoyu_musteri_adiyla(sunucu):
    """Faz 9 üçüncü kol: çekler müşteri adıyla süzülür (iki çek de M1 Alfa'nın)."""
    assert sonuc(sunucu, CFO, "cek_portfoyu", musteri_adi="Alfa Ltd.", durum="hepsi")["cek_sayisi"] == 2
    assert sonuc(sunucu, CFO, "cek_portfoyu", musteri_adi="Delta Ltd.", durum="hepsi")["cek_sayisi"] == 0
    # Müşteri ve vade süzgeci birlikte (vade koşulu müşteri koşulunun üstüne yazılmaz, eklenir).
    assert sonuc(sunucu, CFO, "cek_portfoyu", musteri_adi="Alfa Ltd.", vade_gun=20)["cek_sayisi"] == 1
    assert sonuc(sunucu, CFO, "cek_portfoyu", musteri_adi="Alfa Ltd.", vade_gun=10)["cek_sayisi"] == 0
    assert sonuc(sunucu, CFO, "cek_portfoyu", musteri_adi="Zeta Ltd.")["bulunamadi"] is True
    # Kapsam: ihracat müdürü yurtiçi müşterinin çeklerini göremez; cevap "böyle müşteri yok" ile aynı.
    assert sonuc(sunucu, IHRACAT, "cek_portfoyu", musteri_adi="Alfa Ltd.") == \
        sonuc(sunucu, IHRACAT, "cek_portfoyu", musteri_adi="Zeta Ltd.")
    assert sonuc(sunucu, YURTICI, "cek_portfoyu", musteri_adi="Alfa Ltd.", durum="hepsi")["cek_sayisi"] == 2


def test_en_fazla_siniri_asilinca_ne_yapilacagini_soyler(sunucu):
    """Faz 9 üçüncü kol: sınırı aşan model hangi değerle yeniden deneyeceğini mesajdan öğrenir."""
    yanit, hata = cagir(sunucu, CFO, "cek_portfoyu", en_fazla=11)
    assert hata and yanit["mesaj"] == "Argüman hatası: en_fazla en çok 10 olabilir; 10 ile yeniden deneyin."
    yanit, hata = cagir(sunucu, CFO, "cek_portfoyu", en_fazla=0)
    assert hata and yanit["mesaj"] == "Argüman hatası: en_fazla 1 ile 10 arasında olmalı."


def test_pos_tahsilatlari(sunucu):
    s = sonuc(sunucu, CFO, "pos_tahsilatlari", baslangic="2025-03-01", bitis="2025-03-31")
    assert s == {"baslangic": "2025-03-01", "bitis": "2025-03-31", "islem_sayisi": 2, "iade_sayisi": 0,
                 "satis_tl": "150.00", "iade_tl": "0.00", "komisyon_tl": "3.00", "net_tl": "147.00",
                 "valoru_gelmemis_net_tl": "98.00"}


def test_fatura_ara(sunucu):
    s = sonuc(sunucu, CFO, "fatura_ara", fatura_no=" f07 ")
    assert s["bulunan"] == 1 and s["faturalar"][0] == {
        "fatura_id": "F07", "kaynak_fatura_no": "F07", "tur": "satis", "musteri": "Beta GmbH",
        "fatura_tarihi": "2025-01-10", "vade_tarihi": "2025-03-11", "tutar": "200.00", "para_birimi": "EUR",
        "tutar_tl": "7300.00", "durum": "acik", "acik_tutar": "150.00"}
    iade = sonuc(sunucu, CFO, "fatura_ara", durum="iade")["faturalar"]
    assert [(f["fatura_id"], f["iliskili_fatura_id"]) for f in iade] == [("F06", "F04")]
    delta = sonuc(sunucu, CFO, "fatura_ara", musteri_adi="delta", durum="acik")
    assert [f["fatura_id"] for f in delta["faturalar"]] == ["F10", "F09"]
    assert sonuc(sunucu, CFO, "fatura_ara", fatura_no="F11")["bulunan"] == 0  # 15 Nisan'da kesilecek
    assert sonuc(sunucu, CFO, "fatura_ara", en_fazla=3)["bulunan"] == 10


# ---------------------------------------------------------------- kaynak kimlikleri ve çıktı sınırları


def _gosterilen_kimlikler(nesne):
    """Cevaptaki her satırın birincil kimliği (önce en özel olanı)."""
    sirali = ("hareket_id", "tahsilat_id", "cek_id", "alis_fatura_id", "fatura_id", "kur_id", "hesap_id",
              "musteri_id")
    if isinstance(nesne, dict):
        anahtar = next((a for a in sirali if a in nesne), None)
        bulunan = {nesne[anahtar]} if anahtar else set()
        return bulunan.union(*(_gosterilen_kimlikler(v) for v in nesne.values()))
    if isinstance(nesne, list):
        return set().union(*(_gosterilen_kimlikler(v) for v in nesne))
    return set()


GENIS_CAGRILAR = [
    ("nakit_pozisyonu", {}), ("banka_hareketleri", {"baslangic": "2025-01-01", "bitis": "2025-03-31", "en_fazla": 10}),
    ("alacak_yaslandirma", {}), ("alacak_yaslandirma", {"musteri_adi": "alfa"}),
    ("geciken_alacaklar", {"min_gun": 1, "en_fazla": 10}), ("musteri_ozeti", {"musteri_adi": "Alfa Ltd."}),
    ("dso_hesapla", {"donem": "son_90_gun"}), ("yaklasan_odemeler", {"gun_sayisi": 90, "en_fazla": 10}),
    ("nakit_akis_tahmini", {"hafta_sayisi": 13}), ("kur_riski", {}), ("doviz_kuru", {"para_birimi": "EUR"}),
    ("cek_portfoyu", {"durum": "hepsi", "en_fazla": 10}),
    ("pos_tahsilatlari", {"baslangic": "2025-01-01", "bitis": "2025-03-31"}),
    ("fatura_ara", {"durum": "hepsi", "en_fazla": 10}),
]


@pytest.mark.parametrize("kullanici", [CFO, YURTICI, IHRACAT])
def test_her_cevap_kaynak_kimlikleri_tasir_ve_kimlikler_gercek(sunucu, kullanici):
    rol = sunucu.yetki.roller[sunucu.yetki.kullanicilar[kullanici]]
    con = sunucu.baglantilar[rol.kapsam]
    cagrilar = [(a, args) for a, args in GENIS_CAGRILAR if a in rol.araclar]
    cevaplar = fd.mcp_cagir(sunucu, [(a, args, True, kart(kullanici)) for a, args in cagrilar])
    for (arac, args), (yanit, hata) in zip(cagrilar, cevaplar):
        assert not hata, (arac, yanit)
        assert set(yanit) == {"cagri_id", "arac", "tarih", "kapsam", "sonuc", "kaynak"}
        assert re.fullmatch(r"C[0-9a-f]{12}", yanit["cagri_id"])
        kaynak = yanit["kaynak"]
        hepsi = set().union(*kaynak.values()) if kaynak else set()
        if arac in TOPLU_ARACLAR or {"bulunamadi", "birden_fazla"} & set(yanit["sonuc"]):
            assert kaynak == {}, arac  # yalnızca toplam (cagri_id ile anılır) ya da hiç satır yok
            continue
        assert kaynak, (arac, "kaynak boş")
        gosterilen = _gosterilen_kimlikler(yanit["sonuc"])
        if arac == "doviz_kuru":  # cevapta kur satırının kimliği ayrıca yazılmaz
            assert len(hepsi) == 1 and not gosterilen
        else:
            assert gosterilen == hepsi, (arac, "kaynak, gösterilen satırlarla birebir aynı değil")
        for tablo, kimlikler in kaynak.items():
            sutun = KAYNAK_SUTUNLARI[tablo]
            var = {s[sutun] for s in satirlar(con, f"SELECT {sutun} FROM {tablo}")}
            assert set(kimlikler) <= var, (arac, tablo)


SATIR_LISTELERI = ("hareketler", "en_buyuk_alacaklar", "faturalar", "odemeler", "cekler")


def test_listeler_en_fazla_sinirina_uyar(sunucu):
    for arac, args in GENIS_CAGRILAR:
        for n in (1, 10):
            if "en_fazla" not in dict((a.ad, a.ozellikler) for a in ARACLAR)[arac] or "musteri_adi" in args:
                continue
            s = sonuc(sunucu, CFO, arac, **{**args, "en_fazla": n})
            listeler = [v for k, v in s.items() if k in SATIR_LISTELERI]
            assert listeler and all(len(v) <= n for v in listeler), (arac, n)


# ---------------------------------------------------------------- denetim kaydı


def test_her_cagri_denetim_kaydinda_ve_zincir_saglam(ortam):
    s, klasor = ortam
    once = len(s.kayit.satirlar)
    cevaplar = fd.mcp_cagir(s, [("nakit_pozisyonu", {}, True, kart(CFO)), ("nakit_pozisyonu", {}, True, kart(YURTICI)),
                                ("yok_boyle", {}, True, kart(CFO)), ("dso_hesapla", {"donem": "x"}, True, kart(CFO)),
                                ("kur_riski", {}, False, None)])
    yeni = s.kayit.satirlar[once:]
    assert [r["cagri_id"] for r in yeni] == [c["cagri_id"] for c, _ in cevaplar]
    assert [(r["sonuc"], r.get("neden")) for r in yeni] == [
        ("tamam", None), ("reddedildi", "yetki_yok"), ("reddedildi", "bilinmeyen_arac"),
        ("reddedildi", "gecersiz_arguman"), ("reddedildi", "kart_yok")]
    assert yeni[0]["kayit_sayisi"] == 2 and all(isinstance(r["sure_ms"], float) for r in yeni)
    dosyadaki = DenetimKaydi.oku(klasor / "denetim" / "kayit.jsonl")
    assert len(dosyadaki) == len(s.kayit.satirlar) and dogrula(dosyadaki) == []


def test_kayit_degistirilir_ya_da_silinirse_zincir_bozulur(tmp_path):
    k = DenetimKaydi(tmp_path / "k.jsonl")
    for i in range(4):
        k.yaz(arac="nakit_pozisyonu", sonuc="tamam", i=i)
    satirlar_ = DenetimKaydi.oku(tmp_path / "k.jsonl")
    assert dogrula(satirlar_) == []
    degismis = [dict(s) for s in satirlar_]
    degismis[1]["sonuc"] = "reddedildi"
    assert dogrula(degismis)
    assert dogrula(satirlar_[:1] + satirlar_[2:])
    # Dosya yeniden açılınca zincir kaldığı yerden devam eder.
    DenetimKaydi(tmp_path / "k.jsonl").yaz(arac="kur_riski", sonuc="tamam")
    tum = DenetimKaydi.oku(tmp_path / "k.jsonl")
    assert len(tum) == 5 and dogrula(tum) == []
    with pytest.raises(ValueError):
        k.yaz(kart="x.y")


def test_beklenmeyen_hatanin_ayrintisi_yalnizca_kayda_gider(sunucu, monkeypatch):
    def patla(b, a):
        raise RuntimeError("gizli ayrıntı: /yol/dosya")

    arac = sunucu_modulu.ARACLAR_ADA_GORE["doviz_kuru"]
    monkeypatch.setitem(sunucu_modulu.ARACLAR_ADA_GORE, "doviz_kuru", replace(arac, calistir=patla))
    yanit, hata = cagir(sunucu, CFO, "doviz_kuru", para_birimi="USD")
    assert hata and yanit["hata"] == "hata" and "gizli" not in json.dumps(yanit, ensure_ascii=False)
    assert sunucu.kayit.satirlar[-1]["sonuc"] == "hata" and "gizli" in sunucu.kayit.satirlar[-1]["neden"]


# ---------------------------------------------------------------- ayrı süreçte stdio sunucusu


async def _stdio_oturumu(parametreler, kart_):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(parametreler) as (oku, yaz), ClientSession(oku, yaz) as oturum:
        await oturum.initialize()
        araclar = (await oturum.list_tools()).tools
        kartli = await oturum.call_tool("nakit_pozisyonu", {}, meta={"sayman_kimlik": kart_})
        kartsiz = await oturum.call_tool("nakit_pozisyonu", {})
        return araclar, kartli, kartsiz


def test_stdio_sunucusu_ayri_surecte_calisir(ortam, tmp_path):
    import sys

    from mcp import StdioServerParameters

    from sayman.veri.saat import saat_yaz

    _, klasor = ortam
    kok = Path(__file__).resolve().parents[1]
    saat_yaz(Saat(D, VERI_BITIS), tmp_path / "saat.json")
    parametreler = StdioServerParameters(
        command=sys.executable, cwd=str(kok),
        args=["-I", str(kok / "scripts" / "faz3_sunucu.py"), "--ambar", str(klasor / "sayman.duckdb"),
              "--saat", str(tmp_path / "saat.json"), "--kayit", str(tmp_path / "kayit.jsonl"),
              "--kapsam-klasoru", str(tmp_path / "kapsam")],
        env={"SAYMAN_KIMLIK_ANAHTARI": ANAHTAR.hex()})
    # Ayrı süreç gerçek saati kullanır: kart da şimdiki zamanla üretilir.
    araclar, kartli, kartsiz = fd.anyio.run(_stdio_oturumu, parametreler, kart_uret(CFO, ANAHTAR, 300))
    assert len(araclar) == 14
    assert not kartli.isError and kartli.structuredContent["sonuc"]["toplam_tl"] == "12396.40"
    assert kartsiz.isError and kartsiz.structuredContent["hata"] == "kimlik_gecersiz"
    kayit = DenetimKaydi.oku(tmp_path / "kayit.jsonl")
    assert [(s["sonuc"], s["kullanici"]) for s in kayit] == [("tamam", CFO), ("reddedildi", None)]
    assert dogrula(kayit) == []


def test_toplu_cevaplar_denetim_kaydindan_aynen_yeniden_uretilir(sunucu):
    """Satır kimliği olmayan dört aracın cevabı yalnızca denetim kaydındaki satırdan yeniden üretilebilmeli:
    araç, argümanlar, rol (-> kapsam) ve simülasyon günü."""
    from sayman.araclar.araclar import ARACLAR_ADA_GORE, Baglam

    cagrilar = [(CFO, "dso_hesapla", {"donem": "son_90_gun"}), (CFO, "nakit_akis_tahmini", {"hafta_sayisi": 4}),
                (CFO, "kur_riski", {}), (CFO, "pos_tahsilatlari", {"baslangic": "2025-03-01", "bitis": "2025-03-31"}),
                (IHRACAT, "dso_hesapla", {"donem": "son_12_ay"})]
    once = len(sunucu.kayit.satirlar)
    cevaplar = fd.mcp_cagir(sunucu, [(a, args, True, kart(k)) for k, a, args in cagrilar])
    for (yanit, hata), satir in zip(cevaplar, sunucu.kayit.satirlar[once:]):
        assert not hata and satir["cagri_id"] == yanit["cagri_id"] and satir["arac"] in TOPLU_ARACLAR
        assert (satir["simdi"], satir["tarih"]) == (D.isoformat(), yanit["tarih"])
        arac = ARACLAR_ADA_GORE[satir["arac"]]
        con = sunucu.baglantilar[sunucu.yetki.roller[satir["rol"]].kapsam]
        tarih, s, kaynak = arac.calistir(Baglam(con, G(satir["simdi"])), satir["argumanlar"])
        assert json.loads(json.dumps({"tarih": tarih.isoformat(), "sonuc": s, "kaynak": kaynak}, ensure_ascii=False)) \
            == {k: yanit[k] for k in ("tarih", "sonuc", "kaynak")}, satir["arac"]
    # Gün gerçekten gerekli: bir gün sonra P1'in valörü gelmiş olur.
    pos = ARACLAR_ADA_GORE["pos_tahsilatlari"].calistir(Baglam(sunucu.baglantilar["hepsi"], D + dt.timedelta(days=1)),
                                                        cagrilar[3][2])[1]
    assert pos["valoru_gelmemis_net_tl"] == "0.00" != cevaplar[3][0]["sonuc"]["valoru_gelmemis_net_tl"]
