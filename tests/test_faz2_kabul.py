"""Faz 2 kabul testi: gerçek UCI verisinden üretilmiş tablolar üzerinde.

Önce Faz 1 betikleri ve scripts/faz2_ambar_kur.py çalıştırılmış olmalı. Ambar bu testte CSV'lerden
yeniden kurulur; betiğin kurduğu dosya ayrıca kontrol edilir.

Kabul ölçütü: her metrik, aynı veri üzerinde bağımsız bir pandas hesabıyla birebir aynı;
yaşlandırma dilimlerinin toplamı toplam alacağa eşit. Toplam alacak, Faz 1'in ayrı yazılmış hesabıyla
(kalibrasyon.acik_alacak_try) karşılaştırılır; aynı sayıların toplamı kendisiyle karşılaştırılmaz.
"""

import datetime as dt
import json
import time
from pathlib import Path

import pandas_referans
import pytest

from sayman.ambar import metrikler
from sayman.ambar.kurulum import ambar_kur, baglan, satirlar
from sayman.veri import kalibrasyon
from sayman.veri.cikti import SEMA, csv_oku, csv_yaz
from sayman.veri.gorunum import acik_tutarlar, banka_bakiyeleri, gorunum
from sayman.veri.saat import saat_oku

KOK = Path(__file__).resolve().parents[1]
URETILEN = KOK / "data" / "uretilen"
RAPOR1 = KOK / "reports" / "faz1_veri.json"
ANAHTAR = KOK / "data" / "anahtar" / "olaylar.json"
AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"

pytestmark = pytest.mark.skipif(not (URETILEN.exists() and RAPOR1.exists() and ANAHTAR.exists()),
                                reason="Önce faz1_uci_indir.py ve faz1_veri_uret.py çalıştırılmalı")
G = dt.date.fromisoformat


@pytest.fixture(scope="module")
def rapor1():
    return json.loads(RAPOR1.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def T():
    return csv_oku(URETILEN)


@pytest.fixture(scope="module")
def con():
    return ambar_kur(URETILEN)


@pytest.fixture(scope="module")
def P():
    return pandas_referans.oku(URETILEN)


@pytest.fixture(scope="module")
def gunler(rapor1):
    """Her olay türü en az bir günde canlı: olay günleri, sıkışma bitişleri ve bir hafta sonraları; ayrıca ilk
    gün, bir cumartesi, 2025 yıl sonu, şimdi ve son gün."""
    anahtar = json.loads(ANAHTAR.read_text(encoding="utf-8"))
    bitis = G(rapor1["ayarlar"]["veri_bitis"])
    gunler = {G(rapor1["veri_baslangic"]), G("2025-06-14"), G("2025-12-31"), G(rapor1["ayarlar"]["simdi"]), bitis}
    for o in anahtar["olaylar"]:
        for alan in ("tarih", "bitis"):
            if o.get(alan):
                gunler |= {G(o[alan]), min(G(o[alan]) + dt.timedelta(days=7), bitis)}
    assert {o["tur"] for o in anahtar["olaylar"]} == set(dict(rapor1["ayarlar"]["olay_sayilari"]))
    return sorted(gunler)


def test_ambar_dosyasi_faz1_verisinden_kuruldu(rapor1):
    assert AMBAR.exists(), "Önce scripts/faz2_ambar_kur.py çalıştırılmalı"
    dosya = baglan(AMBAR)
    try:
        assert dosya.execute("SELECT deger FROM ambar_bilgisi WHERE anahtar = 'veri_hash'").fetchone()[0] == \
            rapor1["veri_hash"]
        for ad, sutunlar in SEMA.items():
            assert dosya.execute(f"SELECT count(*) FROM {ad}").fetchone()[0] == rapor1["tablo_satirlari"][ad], ad
    finally:
        dosya.close()


def test_ambar_csv_ile_birebir(con, T):
    from decimal import Decimal

    for ad, sutunlar in SEMA.items():
        csv = sorted((dict(s) for s in T[ad]), key=lambda s: s[sutunlar[0]])  # kopya: T'yi değiştirmez
        for s in csv:
            for k in ("kur", "alis"):
                if s.get(k) is not None:
                    s[k] = Decimal(s[k])
        assert satirlar(con, f"SELECT * FROM {ad} ORDER BY {sutunlar[0]}") == csv, ad


PARAMETRELI = (("dso", {"gun": 90}), ("geciken_alacaklar", {"en_az_gun": 31}), ("yaklasan_odemeler", {"gun": 7}),
               ("nakit_akis_tahmini", {"hafta": 4}))


def test_sql_ve_pandas_her_metrikte_birebir_ayni(con, P, gunler):
    for gun in gunler:
        for ad, parametre in [(ad, {}) for ad in metrikler.METRIKLER] + list(PARAMETRELI):
            sql = metrikler.METRIKLER[ad](con, gun, **parametre)
            sql.pop("dahil_olmayanlar", None)
            assert sql == pandas_referans.METRIKLER[ad](P, gun, **parametre), f"{ad} {parametre} @ {gun}"


def test_faz1_hesaplariyla_ayni(con, T, gunler):
    son_bakiye = {}
    for h in sorted(T["banka_hareketleri"], key=lambda h: h["hareket_id"]):
        son_bakiye.setdefault(h["tarih"], {})[h["hesap_id"]] = h["bakiye_kurus"]
    for gun in gunler:
        hesaplar = {h["hesap_id"]: h["bakiye_kurus"] for h in metrikler.nakit_pozisyonu(con, gun)["hesaplar"]}
        assert hesaplar == banka_bakiyeleri(T, gun), gun
        y = metrikler.yaslandirma(con, gun)
        assert y["toplam"]["toplam_kurus"] == kalibrasyon.acik_alacak_try(T, gun), gun
        dilimler = sum(v for k, v in y["toplam"].items() if k != "toplam_kurus")
        assert dilimler == y["toplam"]["toplam_kurus"], gun
        hepsi = metrikler.geciken_alacaklar(con, gun, en_az_gun=-10**6)["faturalar"]
        assert {f["fatura_id"]: f["acik_kurus"] for f in hepsi} == {
            k: v for k, v in acik_tutarlar(T, gun).items() if v > 0}, gun
    # Banka satırlarındaki "bakiye" sütunu (Faz 1 defteri) ile son gün sonu bakiyeleri de aynı.
    son = {}
    for gun in sorted(son_bakiye):
        son.update(son_bakiye[gun])
    bitis = max(son_bakiye)
    assert {h["hesap_id"]: h["bakiye_kurus"] for h in metrikler.nakit_pozisyonu(con, bitis)["hesaplar"]} == son


def test_dso_2025_faz1_kalibrasyonuyla_ayni(con, T, rapor1):
    d = metrikler.dso(con, G("2025-12-31"))
    assert d["alacak_kurus"] == kalibrasyon.acik_alacak_try(T, G("2025-12-31"))
    assert d["satis_kurus"] == kalibrasyon.net_satis_try(T, G("2025-01-01"), G("2025-12-31"))
    assert abs(float(d["dso_gun"]) - rapor1["kalibrasyon"]["olcum"]["dso_gun"]) < 0.006


def test_gelecek_sizmaz(con, T, rapor1, tmp_path):
    """D gününde tüm veriyle hesaplanan metrik, yalnızca D'ye kadarki kayıtlarla kurulan ambardakiyle aynı."""
    anahtar = json.loads(ANAHTAR.read_text(encoding="utf-8"))
    sikisma = next(G(o["tarih"]) + dt.timedelta(days=3) for o in anahtar["olaylar"] if o["tur"] == "nakit_sikismasi")
    for gun in (G(rapor1["ayarlar"]["simdi"]), sikisma):
        kesik = ambar_kur(_yaz(gorunum(T, gun), tmp_path / gun.isoformat()))
        for ad, fonk in metrikler.METRIKLER.items():
            assert fonk(con, gun) == fonk(kesik, gun), f"{ad} @ {gun}"


def _yaz(tablolar, klasor):
    csv_yaz(tablolar, klasor)
    return klasor


def test_cek_alan_musteriler_tl_calisir(T):
    pb = {m["musteri_id"]: m["para_birimi"] for m in T["musteriler"]}
    assert {pb[c["musteri_id"]] for c in T["cekler"]} == {"TRY"}


def test_metrikler_hizli(rapor1):
    """Araçlar bu metrikleri soru başına çağıracak. Sınır cömert: model belleği doldururken de geçmeli."""
    con = baglan(AMBAR)
    try:
        simdi = saat_oku(KOK / "data" / "saat.json").simdi
        for ad, fonk in metrikler.METRIKLER.items():
            bas = time.perf_counter()
            fonk(con, simdi)
            assert time.perf_counter() - bas < 5.0, ad
    finally:
        con.close()
