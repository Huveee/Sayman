"""Kayıt numarası düzeltmesinin uçtan uca kabul testi: gerçek veri, demo arka ucu, Faz 3 kapısı, orkestratör ve
doğrulayıcı.

Canlı modelin yazış biçimi denetlenemiyor; canlı denemede model numarayı hiç düzeltmenin hedeflediği biçimde yazmadı
(README). Burada betikli model /api/sor'dan gelen soruda fatura_ara'yı çağırır ve aracın döndürdüğü kaynak fatura
numarasını tam o biçimde yazar: "564713'tür (fatura tarihi ...)". Eski doğrulayıcı bu cevabı durduruyordu (sayı
ardındaki "fatura" yüzünden adet, kaynak numarası tutar sayılıyordu). Numarayı yanlış yazan model yine durdurulur.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import support.authorization_trials as fd
import pytest
from fastapi.testclient import TestClient

from sayman.tools.permissions import yetki_oku
from sayman.assistant.orchestrator import ModelCevabi
from sayman.demo.app import Demo, uygulama_kur
from sayman.data.table_io import csv_oku
from sayman.data.clock import saat_oku

KOK = Path(__file__).resolve().parents[1]
GERCEK_AMBAR, GERCEK_SAAT, URETILEN = (KOK / "data" / "ambar" / "sayman.duckdb", KOK / "data" / "saat.json",
                                       KOK / "data" / "uretilen")
MUSTERI = "Akın Doğa Oyuncak San. ve Tic. Ltd. Şti."
SORU = f"{MUSTERI} firmasına kestiğimiz son açık faturanın numarası ne?"

pytestmark = pytest.mark.skipif(not (GERCEK_AMBAR.exists() and GERCEK_SAAT.exists() and URETILEN.exists()),
                                reason="Önce Faz 1-2 betikleri çalıştırılmalı")


class NumaraYazanModel:
    """İlk turda fatura_ara'yı çağırır; sonra son araç sonucundaki ilk faturanın kaynak numarasını (kaydir kadar
    kaydırarak) "…'tür (fatura tarihi …)" biçiminde yazar. Düzeltme istense de aynı cevabı yazar."""

    def __init__(self, kaydir: int = 0):
        self.kaydir = kaydir
        self.yazilan: list[str] = []

    def __call__(self, mesajlar, araclar, zaman_asimi):
        sonuclar = [m for m in mesajlar if m["role"] == "tool"]
        if not sonuclar:
            return ModelCevabi(arac_cagrilari=[("fatura_ara", {"musteri_adi": MUSTERI, "durum": "acik"})],
                               istem_token=100, uretim_token=10)
        f = json.loads(sonuclar[-1]["content"])["sonuc"]["faturalar"][0]
        metin = f"Son açık faturanın numarası {int(f['kaynak_fatura_no']) + self.kaydir}'tür (fatura tarihi " \
                f"{f['fatura_tarihi']})."
        self.yazilan.append(metin)
        return ModelCevabi(icerik=metin, istem_token=100, uretim_token=10)


def _sor(tmp_path: Path, model: NumaraYazanModel) -> dict:
    demo = Demo(ambar=GERCEK_AMBAR, durum_klasoru=tmp_path / "demo", baslangic=saat_oku(GERCEK_SAAT),
                anahtar=bytes(range(32)), yetki=yetki_oku(), tablolar=csv_oku(URETILEN), model=model,
                zaman=lambda: 1_750_000_000.0)
    try:
        c = TestClient(uygulama_kur(demo))
        assert c.post("/api/giris", json={"kullanici": fd.KULLANICILAR["cfo"]}).status_code == 200
        r = c.post("/api/sor", json={"soru": SORU})
        assert r.status_code == 200
        return r.json()
    finally:
        demo.kapat()


def test_kayit_numarasi_dogru_yazilinca_cevap_gosterilir(tmp_path):
    model = NumaraYazanModel()
    j = _sor(tmp_path, model)
    assert [a["arac"] for a in j["araclar"]] == ["fatura_ara"]
    assert j["durum"] == "tamam", j
    m = re.search(r"(\d{6})'tür \(fatura tarihi (\d{4}-\d{2}-\d{2})\)", j["cevap"])
    assert m, j["cevap"]
    assert model.yazilan[0] in j["cevap"]   # ilk taslak durdurulmadan gösterildi
    assert len(model.yazilan) == 1


def test_kayit_numarasi_yanlis_yazilinca_durdurulur(tmp_path):
    model = NumaraYazanModel(kaydir=2)
    j = _sor(tmp_path, model)
    assert j["durum"] != "tamam", j
    yanlis = re.search(r"\d{6}", model.yazilan[0]).group(0)
    assert yanlis not in j["cevap"]
