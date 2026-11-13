"""Faz 1 kabul testi: gerçek UCI verisinden üretilmiş tablolar üzerinde.

Önce scripts/faz1_uci_indir.py ve scripts/faz1_veri_uret.py çalıştırılmış olmalı; yoksa testler atlanır.
Tablolar diskteki CSV'lerden okunur; rapordaki sayılara güvenilmez, her şey yeniden hesaplanır.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from sayman.veri import denetim, kalibrasyon
from sayman.veri.cikti import csv_oku, dosya_hash, veri_hash
from sayman.veri.saat import saat_oku
from sayman.veri.uretici import Ayarlar

KOK = Path(__file__).resolve().parents[1]
URETILEN = KOK / "data" / "uretilen"
RAPOR = KOK / "reports" / "faz1_veri.json"
ANAHTAR = KOK / "data" / "anahtar" / "olaylar.json"

pytestmark = pytest.mark.skipif(not (URETILEN.exists() and RAPOR.exists() and ANAHTAR.exists()),
                                reason="Önce faz1_uci_indir.py ve faz1_veri_uret.py çalıştırılmalı")


@pytest.fixture(scope="module")
def rapor():
    return json.loads(RAPOR.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def T():
    return csv_oku(URETILEN)


@pytest.fixture(scope="module")
def anahtar():
    return json.loads(ANAHTAR.read_text(encoding="utf-8"))


def test_diskteki_tablolarin_hashi_raporla_ayni(T, rapor):
    assert veri_hash(T)[0] == rapor["veri_hash"]


def test_girdi_dosyalari_degismemis(rapor):
    for ad, yol in (("uci_ham", "data/ara/uci_ham.csv.gz"), ("tcmb_kurlar", "data/kaynak/tcmb_kurlar.csv"),
                    ("kalibrasyon", "data/kaynak/kalibrasyon.json")):
        assert dosya_hash(KOK / yol, metin=not yol.endswith(".gz")) == rapor["girdi_sha256"][ad], ad


def test_ayni_tohum_ayri_surecte_ayni_hash(rapor):
    cikti = subprocess.run([sys.executable, "-I", str(KOK / "scripts" / "faz1_veri_uret.py"), "--yalniz-hash"],
                           capture_output=True, text=True, check=True, timeout=900)
    assert cikti.stdout.strip() == rapor["veri_hash"]


def test_temizleme_mutabakati(rapor):
    t = rapor["temizleme"]
    assert t["mutabakat"] and t["girdi"] == sum(t["tutulan"].values()) + sum(t["atilan"].values())
    assert t["tutulan"]["iade"] > 0 and t["tutulan"]["perakende"] > 0


def test_butun_denetimler_gecer(T, anahtar):
    ayar = Ayarlar()
    sonuc = denetim.hepsi(T, anahtar, ayar.olay_sayilari, ayar.simdi, ayar.veri_bitis)
    assert {ad: h for ad, h in sonuc.items() if h} == {}


def test_kalibrasyon_bantlari_icinde(T):
    kaynak = json.loads((KOK / "data" / "kaynak" / "kalibrasyon.json").read_text(encoding="utf-8"))
    satirlar = kalibrasyon.bantlarla_karsilastir(kalibrasyon.olc(T, Ayarlar().veri_bitis, 2025), kaynak)
    assert [s for s in satirlar if not s["tamam"]] == []


def test_saat_ve_anahtar_araclarin_okudugu_klasorde_degil():
    assert saat_oku(KOK / "data" / "saat.json").simdi == Ayarlar().simdi
    assert not any(p.suffix == ".json" for p in URETILEN.iterdir())
    assert ANAHTAR.parent != URETILEN
