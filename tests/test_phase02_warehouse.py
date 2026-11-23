"""Faz 2: veri ambarı ve metrikler (elle hesaplanmış küçük veri ve örnek veriyle; indirme gerekmez).

Üç ayrı kontrol:
1. Elle hesaplanmış küçük bir şirket: her metriğin beklenen değeri bu dosyada elle yazıldı.
2. DuckDB (SQL) ve pandas: aynı veriyi ayrı ayrı okuyup hesaplar, sonuçlar birebir aynı olmalı.
3. Faz 1 hesapları: banka bakiyeleri, açık fatura tutarları ve açık alacak toplamı.
"""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import duckdb
from support import pandas_reference
import pytest

from sayman.warehouse import metrics
from sayman.warehouse.loader import MAKROLAR, ambar_kur, baglan, satirlar
from sayman.data import calibration
from sayman.data.table_io import SEMA, csv_oku, csv_yaz
from sayman.data.as_of import acik_tutarlar, banka_bakiyeleri, gorunum
from sayman.data.sample_data import ornek_ham_satirlar, ornek_kurlar
from sayman.data.money import kurusa, kurustan
from sayman.data.uci import temizle
from sayman.data.generator import Ayarlar, uret

G = dt.date.fromisoformat


def kur(klasor, T, veri_hash=None):
    csv_yaz(T, klasor)
    return ambar_kur(klasor, veri_hash=veri_hash)


# Varsayılan olmayan parametreler de karşılaştırılır: Faz 3 araçları bunları değiştirecek.
PARAMETRELI = (("dso", {"gun": 90}), ("geciken_alacaklar", {"en_az_gun": 31}), ("yaklasan_odemeler", {"gun": 7}),
               ("nakit_akis_tahmini", {"hafta": 4}))


def karsilastir(con, P, tarih):
    """Yedi metriğin (ve parametreli hâllerinin) SQL ve pandas sonuçları birebir aynı mı?"""
    for ad, parametre in [(ad, {}) for ad in metrics.METRIKLER] + list(PARAMETRELI):
        sql = metrics.METRIKLER[ad](con, tarih, **parametre)
        sql.pop("dahil_olmayanlar", None)
        assert sql == pandas_reference.METRIKLER[ad](P, tarih, **parametre), f"{ad} {parametre} @ {tarih}"


# ---------------------------------------------------------------- yuvarlama


@pytest.mark.parametrize("deger", ["2.5", "3.5", "-2.5", "-3.5", "0.5", "-0.5", "1.4999995", "-1.5000001", "7",
                                   "116171363.8027", "-0.000001"])
def test_yuvarlama_esitlikte_cift_tarafa_ve_python_ile_ayni(deger):
    con = duckdb.connect()
    for makro in MAKROLAR:
        con.execute(makro)
    sql = con.execute(f"SELECT yuvarla_cift(CAST('{deger}' AS DECIMAL(38,7)))").fetchone()[0]
    assert sql == kurusa(Decimal(deger) / 100)


def test_kur_carpimi_python_kurusa_ile_ayni():
    con = duckdb.connect()
    for makro in MAKROLAR:
        con.execute(makro)
    for kurus, kur_ in [(3188779, "36.4313"), (5, "0.5"), (15, "0.5"), (-15, "0.5"), (21000, "37.1234"), (1, "1")]:
        assert con.execute(f"SELECT kur_carp({kurus}, CAST('{kur_}' AS DECIMAL(18,6)))").fetchone()[0] == \
            kurusa(kurustan(kurus) * Decimal(kur_)), (kurus, kur_)


# ---------------------------------------------------------------- elle hesaplanmış küçük şirket
#
# D = 31 Mart 2025 (pazartesi). Her beklenen değerin hesabı yanında yazılı.


def _elle_tablolar() -> dict[str, list[dict]]:
    def fatura(fid, tur, m, ft, vade, pb, tutar, kur_, iliskili=None, uyg=None):
        return {"fatura_id": fid, "kaynak_fatura_no": fid, "tur": tur, "musteri_id": m, "fatura_tarihi": G(ft),
                "vade_tarihi": G(vade), "para_birimi": pb, "tutar_kurus": tutar, "kur": kur_,
                "tutar_try_kurus": kurusa(kurustan(tutar) * Decimal(kur_)), "iliskili_fatura_id": iliskili,
                "iade_eslesme": "stok_kodu" if iliskili else None, "uygulanan_kurus": uyg, "kalem_sayisi": 1}

    def tahsilat(tid, m, fid, tarih, pb, tutar, yontem="havale", cek=None):
        return {"tahsilat_id": tid, "musteri_id": m, "fatura_id": fid, "tarih": G(tarih), "para_birimi": pb,
                "tutar_kurus": tutar, "yontem": yontem, "cek_id": cek, "banka_hesap_id": "A_TRY",
                "eslesme": "eslesti" if fid else "bilerek_eslesmemis"}

    def alis(aid, s, tarih, vade, tutar, kategori="mal"):
        return {"alis_fatura_id": aid, "tedarikci_id": s, "tarih": G(tarih), "vade_tarihi": G(vade),
                "tutar_kurus": tutar, "kategori": kategori}

    def odeme(oid, tur, aid, tarih, tutar, s="S1"):
        return {"odeme_id": oid, "tur": tur, "tedarikci_id": s if aid else None, "alis_fatura_id": aid,
                "tarih": G(tarih), "tutar_kurus": tutar, "yontem": "havale", "banka_hesap_id": "A_TRY", "aciklama": oid}

    def hareket(hid, hesap, tarih, tutar):
        return {"hareket_id": hid, "hesap_id": hesap, "tarih": G(tarih), "tutar_kurus": tutar, "bakiye_kurus": 0,
                "aciklama": hid, "kaynak_tur": "test", "kaynak_id": hid}

    return {
        "musteriler": [
            {"musteri_id": "M1", "ad": "Alfa Ltd.", "ulke": "Türkiye", "yurtici": True, "para_birimi": "TRY",
             "vade_gun": 30, "ilk_islem_tarihi": G("2024-09-01")},
            {"musteri_id": "M2", "ad": "Beta GmbH", "ulke": "Almanya", "yurtici": False, "para_birimi": "EUR",
             "vade_gun": 60, "ilk_islem_tarihi": G("2025-01-10")},
            {"musteri_id": "M3", "ad": "Gama AŞ", "ulke": "Türkiye", "yurtici": True, "para_birimi": "TRY",
             "vade_gun": 30, "ilk_islem_tarihi": G("2025-04-15")},
            {"musteri_id": "M4", "ad": "Delta Ltd.", "ulke": "Türkiye", "yurtici": True, "para_birimi": "TRY",
             "vade_gun": 30, "ilk_islem_tarihi": G("2024-11-01")},
        ],
        "faturalar": [
            fatura("F01", "satis", "M1", "2024-09-01", "2024-10-01", "TRY", 7000, "1"),
            fatura("F02", "satis", "M1", "2025-01-02", "2025-02-01", "TRY", 100000, "1"),
            fatura("F03", "satis", "M1", "2025-01-15", "2025-02-14", "TRY", 20000, "1"),
            fatura("F04", "satis", "M1", "2025-02-10", "2025-03-12", "TRY", 50000, "1"),
            fatura("F05", "satis", "M1", "2025-03-20", "2025-04-19", "TRY", 30000, "1"),
            fatura("F06", "iade", "M1", "2025-03-25", "2025-03-25", "TRY", -10000, "1", "F04", 10000),
            fatura("F07", "satis", "M2", "2025-01-10", "2025-03-11", "EUR", 20000, "36.5"),
            fatura("F08", "satis", "M2", "2025-01-20", "2025-03-21", "EUR", 1000, "36.6"),
            fatura("F09", "satis", "M4", "2024-11-01", "2024-12-01", "TRY", 3000, "1"),
            fatura("F10", "satis", "M4", "2025-03-05", "2025-04-04", "TRY", 12000, "1"),
            fatura("F11", "satis", "M3", "2025-04-15", "2025-05-15", "TRY", 40000, "1"),  # D'den sonra
        ],
        "tahsilatlar": [
            tahsilat("T1", "M1", "F02", "2025-02-05", "TRY", 60000),
            tahsilat("T2", "M1", "F03", "2025-02-24", "TRY", 20000),  # F03 vadeden 10 gün sonra kapandı
            tahsilat("T3", "M2", "F07", "2025-03-15", "EUR", 5000, "swift"),
            tahsilat("T4", "M2", "F08", "2025-03-21", "EUR", 1000, "swift"),  # F08 vadesinde kapandı
            tahsilat("T5", "M1", None, "2025-03-28", "TRY", 5000),  # eşleşmemiş
            tahsilat("T6", "M1", "F02", "2025-04-10", "TRY", 40000),  # D'den sonra
            tahsilat("T7", "M1", "F04", "2025-04-20", "TRY", 20000, "cek", "C1"),  # D'den sonra
        ],
        "cekler": [
            {"cek_id": "C1", "musteri_id": "M1", "cek_no": "1111111", "kesideci_banka": "Banka X",
             "alis_tarihi": G("2025-03-01"), "vade_tarihi": G("2025-04-20"), "tutar_kurus": 20000},
            {"cek_id": "C2", "musteri_id": "M1", "cek_no": "2222222", "kesideci_banka": "Banka Y",
             "alis_tarihi": G("2025-02-01"), "vade_tarihi": G("2025-03-03"), "tutar_kurus": 9999},
        ],
        "cek_hareketleri": [
            {"cek_hareket_id": "K1", "cek_id": "C2", "tarih": G("2025-02-01"), "durum": "portfoyde"},
            {"cek_hareket_id": "K2", "cek_id": "C2", "tarih": G("2025-02-27"), "durum": "tahsilde"},
            {"cek_hareket_id": "K3", "cek_id": "C1", "tarih": G("2025-03-01"), "durum": "portfoyde"},
            {"cek_hareket_id": "K4", "cek_id": "C2", "tarih": G("2025-03-03"), "durum": "karsiliksiz"},
            {"cek_hareket_id": "K5", "cek_id": "C1", "tarih": G("2025-04-15"), "durum": "tahsilde"},
            {"cek_hareket_id": "K6", "cek_id": "C1", "tarih": G("2025-04-20"), "durum": "tahsil_edildi"},
        ],
        "pos_islemleri": [
            {"pos_id": "P1", "kaynak_fatura_no": "x", "islem_tarihi": G("2025-03-31"), "tutar_kurus": 10000,
             "komisyon_kurus": 200, "valor_tarihi": G("2025-04-01")},
            {"pos_id": "P2", "kaynak_fatura_no": "y", "islem_tarihi": G("2025-03-28"), "tutar_kurus": 5000,
             "komisyon_kurus": 100, "valor_tarihi": G("2025-03-31")},
        ],
        "tedarikciler": [
            {"tedarikci_id": "S1", "ad": "Delta Kağıt", "kategori": "mal", "vade_gun": 30},
            {"tedarikci_id": "S2", "ad": "Kira AŞ", "kategori": "kira", "vade_gun": 30},
        ],
        "alis_faturalari": [
            alis("A1", "S1", "2025-03-01", "2025-03-31", 25000),
            alis("A2", "S1", "2025-03-15", "2025-04-14", 12000),  # erken ödendi
            alis("A3", "S2", "2025-03-25", "2025-04-24", 8000, "kira"),
            alis("A4", "S1", "2025-02-01", "2025-03-03", 6000),  # yarısı ödendi, vadesi geçti
            alis("A5", "S1", "2025-03-30", "2025-06-28", 9000),
            alis("A6", "S1", "2025-01-05", "2025-02-04", 4000),  # iki kez ödendi
            alis("A7", "S1", "2025-04-05", "2025-05-05", 7777),  # D'den sonra
        ],
        "odemeler": [
            odeme("O1", "tedarikci", "A2", "2025-03-20", 12000),
            odeme("O2", "tedarikci", "A4", "2025-03-03", 3000),
            odeme("O3", "tedarikci", "A4", "2025-04-02", 3000),  # D'den sonra
            odeme("O4", "tedarikci", "A6", "2025-02-04", 4000),
            odeme("O5", "tedarikci", "A6", "2025-02-05", 4000),  # mükerrer
            odeme("O6", "maas", None, "2025-03-28", 50000),
        ],
        "banka_hesaplari": [
            {"hesap_id": "A_TRY", "banka": "Banka A", "para_birimi": "TRY", "acilis_tarihi": G("2024-09-01"),
             "acilis_bakiye_kurus": 1000000},
            {"hesap_id": "B_EUR", "banka": "Banka B", "para_birimi": "EUR", "acilis_tarihi": G("2024-09-01"),
             "acilis_bakiye_kurus": 0},
            {"hesap_id": "C_USD", "banka": "Banka C", "para_birimi": "USD", "acilis_tarihi": G("2025-04-01"),
             "acilis_bakiye_kurus": 0},
        ],
        "banka_hareketleri": [
            hareket("H01", "A_TRY", "2025-02-04", -4000), hareket("H02", "A_TRY", "2025-02-05", 60000),
            hareket("H03", "A_TRY", "2025-02-05", -4000), hareket("H04", "A_TRY", "2025-02-24", 20000),
            hareket("H05", "A_TRY", "2025-03-03", -3000), hareket("H06", "A_TRY", "2025-03-20", -12000),
            hareket("H07", "A_TRY", "2025-03-28", 5000), hareket("H08", "A_TRY", "2025-03-28", -50000),
            hareket("H09", "A_TRY", "2025-03-31", 4900), hareket("H10", "A_TRY", "2025-04-02", -3000),
            hareket("H11", "A_TRY", "2025-04-10", 40000),
            hareket("H12", "B_EUR", "2025-03-15", 5000), hareket("H13", "B_EUR", "2025-03-21", 1000),
        ],
        "doviz_kurlari": [
            {"kur_id": f"{t}-{pb}", "tarih": G(t), "para_birimi": pb, "alis": a}
            for t, pb, a in [("2025-01-10", "EUR", "36.5"), ("2025-01-20", "EUR", "36.6"), ("2025-03-28", "EUR", "37"),
                             ("2025-03-31", "EUR", "37.1234"), ("2025-03-31", "USD", "34"),
                             ("2025-04-01", "EUR", "37.5"), ("2025-04-01", "USD", "34.1")]
        ],
    }


D = G("2025-03-31")


@pytest.fixture(scope="module")
def elle(tmp_path_factory):
    klasor = tmp_path_factory.mktemp("elle")
    con = kur(klasor, _elle_tablolar())
    return con, pandas_reference.oku(klasor)


@pytest.fixture(scope="module", params=["sql", "pandas"])
def hesap(request, elle):
    """Elle hesaplanmış beklenen değerler hem SQL'e hem pandas'a ayrı ayrı sorulur."""
    con, P = elle
    if request.param == "sql":
        return lambda ad, *a, **k: metrics.METRIKLER[ad](con, *a, **k)
    return lambda ad, *a, **k: pandas_reference.METRIKLER[ad](P, *a, **k)


def test_elle_nakit_pozisyonu(hesap):
    n = hesap("nakit_pozisyonu", D)
    # A_TRY: 1.000.000 - 4.000 + 60.000 - 4.000 + 20.000 - 3.000 - 12.000 + 5.000 - 50.000 + 4.900 = 1.016.900
    # B_EUR: 6.000 euro-kuruş x 37,1234 = 222.740,4 -> 222.740. C_USD 1 Nisan'da açılıyor: görünmez.
    assert [(h["hesap_id"], h["bakiye_kurus"], h["bakiye_try_kurus"]) for h in n["hesaplar"]] == [
        ("A_TRY", 1016900, 1016900), ("B_EUR", 6000, 222740)]
    assert n["toplam_try_kurus"] == 1239640


def test_elle_nakit_pozisyonu_hafta_sonu_cuma_kurunu_kullanir(hesap):
    n = hesap("nakit_pozisyonu", G("2025-03-30"))  # pazar: 31 Mart hareketi ve kuru yok
    assert [(h["hesap_id"], h["bakiye_kurus"], h["kur"], h["bakiye_try_kurus"]) for h in n["hesaplar"]] == [
        ("A_TRY", 1012000, 1, 1012000), ("B_EUR", 6000, Decimal("37"), 222000)]


def test_elle_yaslandirma(hesap):
    y = hesap("yaslandirma", D)
    satir = {m["musteri_id"]: m for m in y["musteriler"]}
    assert [m["musteri_id"] for m in y["musteriler"]] == ["M2", "M1", "M4"]  # toplamı büyükten küçüğe
    # M1: F05 30.000 vadesi gelmedi; F04 50.000-10.000 iade = 40.000, 19 gün; F02 100.000-60.000 = 40.000, 58 gün;
    #     F01 7.000, 181 gün; eşleşmemiş havale -5.000; F03 kapandı.
    assert satir["M1"] == {"musteri_id": "M1", "ad": "Alfa Ltd.", "vadesi_gelmemis_kurus": 30000,
                           "gecikme_1_30_kurus": 40000, "gecikme_31_60_kurus": 40000, "gecikme_61_90_kurus": 0,
                           "gecikme_91_180_kurus": 0, "gecikme_180_ustu_kurus": 7000,
                           "eslesmemis_tahsilat_kurus": -5000, "toplam_kurus": 112000}
    # M2: F07 20.000-5.000 = 15.000 euro-kuruş x 36,5 (fatura kuru) = 547.500; 20 gün. F08 kapandı.
    assert satir["M2"]["gecikme_1_30_kurus"] == 547500 and satir["M2"]["toplam_kurus"] == 547500
    # M4: F10 12.000 vadesi gelmedi; F09 3.000, 120 gün.
    assert (satir["M4"]["vadesi_gelmemis_kurus"], satir["M4"]["gecikme_91_180_kurus"], satir["M4"]["toplam_kurus"]) == (
        12000, 3000, 15000)
    assert y["toplam"]["toplam_kurus"] == 674500


def test_elle_dso(hesap):
    d = hesap("dso", D)
    # Satış (1 Nisan 2024 - 31 Mart 2025): faturalar 7.000+100.000+20.000+50.000+30.000-10.000+730.000+36.600
    # +3.000+12.000 = 978.600; POS 10.000+5.000 = 15.000. DSO = 365 x 674.500 / 993.600 = 247,778... -> 247,78
    assert (d["alacak_kurus"], d["satis_kurus"], d["dso_gun"]) == (674500, 993600, Decimal("247.78"))


def test_elle_geciken_alacaklar(hesap):
    g = hesap("geciken_alacaklar", D)
    assert [(f["fatura_id"], f["gecikme_gun"], f["acik_kurus"], f["acik_try_kurus"]) for f in g["faturalar"]] == [
        ("F07", 20, 15000, 547500), ("F02", 58, 40000, 40000), ("F04", 19, 40000, 40000),
        ("F01", 181, 7000, 7000), ("F09", 120, 3000, 3000)]


def test_elle_yaklasan_odemeler(hesap):
    y = hesap("yaklasan_odemeler", D)
    # A2 ödendi, A6 fazla ödendi (eksi açık), A5'in vadesi 30 günden sonra, A7 henüz kesilmedi.
    assert [(o["alis_fatura_id"], o["kalan_gun"], o["acik_kurus"]) for o in y["odemeler"]] == [
        ("A4", -28, 3000), ("A1", 0, 25000), ("A3", 24, 8000)]


def test_elle_kur_riski(hesap):
    k = hesap("kur_riski", D)
    # EUR: alacak 15.000 + banka 6.000 = 21.000 euro-kuruş; x 37,1234 = 779.591,4 -> 779.591; %10'u 77.959,14 -> 77.959.
    # Kur farkı: 15.000 x 37,1234 = 556.851 - 547.500 (fatura kuruyla) = 9.351. USD hesabı henüz açılmadı.
    assert k["dovizler"] == [{"para_birimi": "EUR", "alacak_doviz_kurus": 15000, "banka_doviz_kurus": 6000,
                              "toplam_doviz_kurus": 21000, "kur": Decimal("37.1234"), "toplam_try_kurus": 779591,
                              "yuzde_10_etki_kurus": 77959, "alacak_kur_farki_kurus": 9351}]


def test_elle_nakit_akis_tahmini(hesap):
    n = hesap("nakit_akis_tahmini", D)
    # Havuz (M1): portföydeki C1 20.000 + eşleşmemiş T5 5.000 = 25.000; C2 karşılıksız, sayılmaz. En eski vadeden:
    #   F01 7.000 tamamen, F02'nin 18.000'i kapanmış sayılır -> F02'den 22.000 kalır.
    # Ortalama gecikme: M1 = 10 gün (F03), M2 = 0 (F08), şirket = (10+0)//2 = 5 -> M4 için 5.
    # Hafta 1 (1-7 Nis): F02 22.000 + F04 40.000 (vade+10 geçmiş -> D+1) + F07 15.000x37,1234 = 556.851
    #   = 618.851; POS P1 9.800; tedarikçi A1 25.000 + A4 3.000 = 28.000.
    # Hafta 2: F10 (4 Nis + 5 = 9 Nis) 12.000. Hafta 3: C1 çeki (20 Nis) 20.000. Hafta 4: A3 (24 Nis) 8.000.
    # Hafta 5: F05 (19 Nis + 10 = 29 Nis) 30.000. Hafta 13: A5 (28 Haz) 9.000. F09 120 gün gecikmeli: şüpheli.
    assert n["acilis_kurus"] == 1239640
    ozet = {h["hafta"]: (h["alacak_girisi_kurus"], h["cek_girisi_kurus"], h["pos_girisi_kurus"],
                         h["tedarikci_cikisi_kurus"]) for h in n["haftalar"] if h["net_kurus"]}
    assert ozet == {1: (618851, 0, 9800, 28000), 2: (12000, 0, 0, 0), 3: (0, 20000, 0, 0), 4: (0, 0, 0, 8000),
                    5: (30000, 0, 0, 0), 13: (0, 0, 0, 9000)}
    assert [h["bilinen_kalemlerle_bakiye_kurus"] for h in n["haftalar"]] == [
        1840291, 1852291, 1872291, 1864291, 1894291, 1894291, 1894291, 1894291, 1894291, 1894291, 1894291, 1894291,
        1885291]
    assert (n["haftalar"][0]["bas"], n["haftalar"][0]["bit"], n["haftalar"][-1]["bit"]) == (
        G("2025-04-01"), G("2025-04-07"), G("2025-06-30"))
    assert (n["supheli_alacak_kurus"], n["ufuk_disi_giris_kurus"], n["ufuk_disi_cikis_kurus"]) == (3000, 0, 0)


def test_elle_tahmin_ufku_kisalinca_disarida_kalanlar_ayri_toplanir(hesap):
    n = hesap("nakit_akis_tahmini", D, hafta=2)
    # 3. haftadan sonrakiler: C1 20.000 + F05 30.000 girişi; A3 8.000 + A5 9.000 çıkışı.
    assert (n["ufuk_disi_giris_kurus"], n["ufuk_disi_cikis_kurus"]) == (50000, 17000)


def test_elle_doviz_eslesmemis_tahsilat(tmp_path):
    """Faz 1 üreticisi yalnızca TL havaleleri eşleşmemiş bırakır; döviz kolu burada ayrıca denenir."""
    T = _elle_tablolar()
    T["tahsilatlar"].append({"tahsilat_id": "T8", "musteri_id": "M2", "fatura_id": None, "tarih": G("2025-03-25"),
                             "para_birimi": "EUR", "tutar_kurus": 2000, "yontem": "swift", "cek_id": None,
                             "banka_hesap_id": "B_EUR", "eslesme": "bilerek_eslesmemis"})
    T["banka_hareketleri"].append({"hareket_id": "H14", "hesap_id": "B_EUR", "tarih": G("2025-03-25"),
                                   "tutar_kurus": 2000, "bakiye_kurus": 0, "aciklama": "H14", "kaynak_tur": "test",
                                   "kaynak_id": "H14"})
    con = kur(tmp_path, T)
    P = pandas_reference.oku(tmp_path)
    for hesap in (lambda ad, *a: metrics.METRIKLER[ad](con, *a), lambda ad, *a: pandas_reference.METRIKLER[ad](P, *a)):
        # 2.000 euro-kuruş, tahsilat günündeki (25 Mart) son kurla, 20 Ocak'ın 36,6'sıyla: 73.200.
        m2 = next(m for m in hesap("yaslandirma", D)["musteriler"] if m["musteri_id"] == "M2")
        assert (m2["eslesmemis_tahsilat_kurus"], m2["toplam_kurus"]) == (-73200, 474300)
        eur = hesap("kur_riski", D)["dovizler"][0]
        assert (eur["alacak_doviz_kurus"], eur["banka_doviz_kurus"], eur["toplam_try_kurus"]) == (13000, 8000, 779591)
        n = hesap("nakit_akis_tahmini", D)
        # F07'den 2.000 havuzla kapanmış sayılır: 13.000 x 37,1234 = 482.604; B_EUR 8.000 x 37,1234 = 296.987.
        assert (n["acilis_kurus"], n["haftalar"][0]["alacak_girisi_kurus"]) == (1016900 + 296987, 22000 + 40000 + 482604)
    for gun in (G("2025-03-24"), G("2025-03-25"), D, G("2025-04-30")):
        karsilastir(con, P, gun)


def test_kur_yoksa_hata_verir_sessizce_atlamaz(elle):
    con, P = elle
    with pytest.raises(duckdb.Error, match="kur bulunamadı"):
        metrics.nakit_pozisyonu(con, G("2025-01-05"))  # B_EUR açık ama ilk EUR kuru 10 Ocak'ta
    with pytest.raises(LookupError):
        pandas_reference.nakit_pozisyonu(P, G("2025-01-05"))


def test_elle_veride_sql_ve_pandas_her_gun_ayni(elle):
    con, P = elle
    gun = G("2025-01-10")
    while gun <= G("2025-07-15"):
        karsilastir(con, P, gun)
        gun += dt.timedelta(days=3)


def test_tarih_datetime_olamaz(elle):
    with pytest.raises(TypeError):
        metrics.yaslandirma(elle[0], dt.datetime(2025, 3, 31))


# ---------------------------------------------------------------- ambarın kendisi


def test_ambar_csv_ile_birebir_ve_yalnizca_sema_tablolari(tmp_path):
    T = _elle_tablolar()
    con = kur(tmp_path, T, veri_hash="abc")
    beklenen = csv_oku(tmp_path)
    for ad, sutunlar in SEMA.items():
        sql = satirlar(con, f"SELECT * FROM {ad} ORDER BY {sutunlar[0]}")
        csv = sorted(beklenen[ad], key=lambda s: s[sutunlar[0]])
        for s in csv:
            for k in ("kur", "alis"):
                if s.get(k) is not None:
                    s[k] = Decimal(s[k])
        assert sql == csv, ad
    tablolar = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert tablolar == set(SEMA) | {"ambar_bilgisi"}
    assert con.execute("SELECT deger FROM ambar_bilgisi WHERE anahtar = 'veri_hash'").fetchone()[0] == "abc"


def test_ambar_dosyasi_salt_okunur_acilir(tmp_path):
    csv_yaz(_elle_tablolar(), tmp_path / "csv")
    ambar_kur(tmp_path / "csv", tmp_path / "ambar.duckdb").close()
    con = baglan(tmp_path / "ambar.duckdb")
    assert metrics.nakit_pozisyonu(con, D)["toplam_try_kurus"] == 1239640  # makrolar dosyada saklı
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM faturalar")
    con.close()


# ---------------------------------------------------------------- örnek veri: üç tohum


@pytest.fixture(scope="module", params=[42, 43, 44], ids=lambda t: f"tohum{t}")
def ornek(request, tmp_path_factory):
    ayar = replace(Ayarlar(), tohum=request.param)
    uretim = uret(temizle(ornek_ham_satirlar()).kalemler, ornek_kurlar(), ayar)
    klasor = tmp_path_factory.mktemp(f"ornek{request.param}")
    con = kur(klasor, uretim.tablolar)
    return uretim, con, pandas_reference.oku(klasor), ayar


def _ornek_gunleri(uretim, ayar) -> list[dt.date]:
    """Her olay türü en az bir günde canlı olsun: olay günleri ve bir hafta sonrası, ayrıca ilk gün, bir hafta
    sonu, şimdi ve son gün."""
    gunler = {uretim.veri_baslangic, ayar.simdi, ayar.veri_bitis}
    for o in uretim.anahtar["olaylar"]:
        for alan in ("tarih", "bitis"):
            if o.get(alan):
                g = G(o[alan])
                gunler |= {g, min(g + dt.timedelta(days=7), ayar.veri_bitis)}
    orta = uretim.veri_baslangic + (ayar.veri_bitis - uretim.veri_baslangic) / 2
    gunler.add(orta + dt.timedelta(days=(5 - orta.weekday()) % 7))  # cumartesi
    return sorted(gunler)


def test_ornek_veride_sql_ve_pandas_ayni(ornek):
    uretim, con, P, ayar = ornek
    for gun in _ornek_gunleri(uretim, ayar):
        karsilastir(con, P, gun)


def test_ornek_veride_faz1_hesaplariyla_ayni(ornek):
    uretim, con, _, ayar = ornek
    T = uretim.tablolar
    for gun in _ornek_gunleri(uretim, ayar):
        n = metrics.nakit_pozisyonu(con, gun)
        assert {h["hesap_id"]: h["bakiye_kurus"] for h in n["hesaplar"]} == banka_bakiyeleri(T, gun)
        assert metrics.yaslandirma(con, gun)["toplam"]["toplam_kurus"] == calibration.acik_alacak_try(T, gun)
        hepsi = metrics.geciken_alacaklar(con, gun, en_az_gun=-10**6)["faturalar"]
        assert {f["fatura_id"]: f["acik_kurus"] for f in hepsi} == {
            k: v for k, v in acik_tutarlar(T, gun).items() if v > 0}


def test_ornek_veride_gelecek_sizmaz(ornek, tmp_path):
    """D gününde tüm veriyle hesaplanan metrik, yalnızca D'ye kadarki veriyle kurulan ambardakiyle aynı."""
    uretim, con, _, ayar = ornek
    gunler = _ornek_gunleri(uretim, ayar)
    for gun in (ayar.simdi, gunler[len(gunler) // 2]):
        kesik = kur(tmp_path / gun.isoformat(), gorunum(uretim.tablolar, gun))
        for ad, fonk in metrics.METRIKLER.items():
            assert fonk(con, gun) == fonk(kesik, gun), f"{ad} @ {gun}"


def test_cek_alan_musteriler_tl_calisir(ornek):
    """Nakit tahmini çekleri müşterinin açık faturalarından düşer; bunun için çek ve fatura aynı para biriminde."""
    uretim, _, _, _ = ornek
    pb = {m["musteri_id"]: m["para_birimi"] for m in uretim.tablolar["musteriler"]}
    assert {pb[c["musteri_id"]] for c in uretim.tablolar["cekler"]} <= {"TRY"}
