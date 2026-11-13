"""Faz 1: UCI temizleme kuralları, para birimi yardımcıları ve simülasyon saati (örnek veriyle, indirme gerekmez)."""

import datetime as dt
from collections import Counter
from decimal import Decimal

import pytest

from sayman.veri.ornek import OZEL_SATIRLAR, SAYFA_1_SON, SAYFA_2, ornek_ham_satirlar
from sayman.veri.para import kurusa, kurustan, ondalik, oran_uygula
from sayman.veri.saat import Saat, saat_oku, saat_yaz
from sayman.veri.uci import ATILMA_KURALLARI, TUTULMA_KATEGORILERI, ham_csv_oku, ham_csv_yaz, temizle


@pytest.fixture(scope="module")
def ham():
    return ornek_ham_satirlar()


@pytest.fixture(scope="module")
def sonuc(ham):
    return temizle(ham)


def test_her_satir_ya_tutulur_ya_adi_konmus_kuralla_atilir(sonuc):
    assert sonuc.mutabakat_tutuyor_mu()
    assert set(sonuc.atilan) <= set(ATILMA_KURALLARI)
    assert set(sonuc.tutulan) <= set(TUTULMA_KATEGORILERI)
    assert len(sonuc.kalemler) == sum(sonuc.tutulan.values())


def test_ozel_satirlar_dogru_kuralla_atilir(sonuc):
    for kural, adet in OZEL_SATIRLAR.items():
        assert sonuc.atilan[kural] == adet, kural


def test_sayfa_tekrarlari_atilir(ham, sonuc):
    beklenen = sum(1 for s in ham if s["sayfa"] == SAYFA_2 and s["InvoiceDate"] <= SAYFA_1_SON.isoformat(sep=" ")
                   and s["InvoiceDate"][:4].isdigit())
    assert beklenen > 0
    assert sonuc.atilan["sayfa_tekrari"] == beklenen


def test_sayfa_tekrari_coklu_kume_gibi_eslesir():
    satir = {"Invoice": "500000", "StockCode": "85123A", "Description": "X", "Quantity": "1",
             "InvoiceDate": "2010-12-02 10:00:00", "Price": "2.55", "Customer ID": "12346", "Country": "United Kingdom"}
    ham = [dict(satir, sayfa="Year 2009-2010"), dict(satir, sayfa="Year 2009-2010"),
           dict(satir, sayfa="Year 2010-2011"), dict(satir, sayfa="Year 2010-2011"), dict(satir, sayfa="Year 2010-2011")]
    s = temizle(ham)
    assert s.atilan["sayfa_tekrari"] == 2  # ikinci sayfadaki üç kopyadan ikisinin ilk sayfada karşılığı var
    assert s.tutulan["satis"] == 3


def test_c_faturalari_iade_ve_miktar_eksi(sonuc):
    for k in sonuc.kalemler:
        iptal = k.fatura_no.startswith("C")
        assert (k.kategori in ("iade", "perakende_iade")) == iptal
        assert (k.miktar < 0) == iptal
        assert k.fiyat > 0
        assert isinstance(k.tutar, Decimal)
        assert (k.musteri_no is None) == k.kategori.startswith("perakende")


def test_eksi_miktarli_c_olmayan_ve_arti_miktarli_c_atilir():
    taban = {"sayfa": "S", "StockCode": "1", "Description": "", "InvoiceDate": "2011-01-03 10:00:00",
             "Price": "1.00", "Customer ID": "12346", "Country": "United Kingdom"}
    s = temizle([dict(taban, Invoice="1", Quantity="-5"), dict(taban, Invoice="C2", Quantity="5"),
                 dict(taban, Invoice="C3", Quantity="-5"), dict(taban, Invoice="4", Quantity="5")])
    assert s.atilan == Counter({"stok_duzeltmesi": 1, "tutarsiz_iade": 1})
    assert s.tutulan == Counter({"iade": 1, "satis": 1})


def test_musteri_numarasi_ondalik_bicimden_temizlenir():
    taban = {"sayfa": "S", "Invoice": "1", "StockCode": "1", "Description": "", "Quantity": "1",
             "InvoiceDate": "2011-01-03 10:00:00", "Price": "1.00", "Country": "United Kingdom"}
    s = temizle([dict(taban, **{"Customer ID": "12346.0"}), dict(taban, **{"Customer ID": "nan"})])
    assert [k.musteri_no for k in s.kalemler] == ["12346", None]


def test_ham_csv_gz_gidis_donus(ham, tmp_path):
    yol = tmp_path / "ham.csv.gz"
    assert ham_csv_yaz(ham, yol) == len(ham)
    geri = list(ham_csv_oku(yol))
    assert geri == ham
    assert temizle(geri).kalemler == temizle(ham).kalemler


def test_para_float_kabul_etmez_ve_kurusa_yuvarlar():
    with pytest.raises(TypeError):
        ondalik(1.5)
    assert kurusa(Decimal("1.005")) == 100  # eşitlikte çift tarafa
    assert kurusa(Decimal("1.015")) == 102
    assert kurustan(12345) == Decimal("123.45")
    assert oran_uygula(10_000, Decimal("0.02")) == 200


def test_saat_ileri_sarilir_ama_veri_bitisini_gecmez(tmp_path):
    saat = Saat(dt.date(2026, 9, 1), dt.date(2026, 11, 6))
    assert saat.ileri_sar(30).simdi == dt.date(2026, 10, 1)
    assert saat.ileri_sar(365).simdi == dt.date(2026, 11, 6)
    with pytest.raises(ValueError):
        saat.ileri_sar(-1)
    with pytest.raises(ValueError):
        Saat(dt.date(2026, 12, 1), dt.date(2026, 11, 6))
    saat_yaz(saat, tmp_path / "saat.json")
    assert saat_oku(tmp_path / "saat.json") == saat


def test_metin_dosyasi_ozeti_satir_sonundan_bagimsiz(tmp_path):
    from sayman.veri.cikti import dosya_hash

    (tmp_path / "lf.csv").write_bytes(b"a,b\n1,2\n")
    (tmp_path / "crlf.csv").write_bytes(b"a,b\r\n1,2\r\n")
    assert dosya_hash(tmp_path / "lf.csv", metin=True) == dosya_hash(tmp_path / "crlf.csv", metin=True)
    assert dosya_hash(tmp_path / "lf.csv") != dosya_hash(tmp_path / "crlf.csv")
