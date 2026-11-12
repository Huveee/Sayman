"""Faz 0 kabul testi: kayıtlı ölçüm raporunu (reports/faz0_hiz.json) denetler.

Ollama'yı çağırmaz; yalnızca ölçüm betiğinin yazdığı dosyayı okur. Rapor yoksa
faz kapanmamış demektir ve test başarısız olur.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sayman.faz0.dogrulama import (
    GEREKEN_GECERLI,
    SURE_ESIGI_S,
    beklenen_cagri_mi,
    cevap_gecerli_mi,
    kurali_sagliyor_mu,
    secim_kurali,
)
from sayman.faz0.ornek_sorular import ORNEK_SORULAR

RAPOR = Path(__file__).resolve().parents[1] / "reports" / "faz0_hiz.json"


@pytest.fixture(scope="module")
def rapor() -> dict:
    assert RAPOR.exists(), (
        "Ölçüm raporu yok. Önce şunu çalıştırın: .venv\\Scripts\\python scripts\\faz0_hiz_olcumu.py"
    )
    return json.loads(RAPOR.read_text(encoding="utf-8"))


def test_en_az_iki_aday_olculdu(rapor):
    assert len(rapor["adaylar"]) >= 2


def test_kural_degerleri_faz0_olcutuyle_ayni(rapor):
    assert rapor["kural"]["sure_esigi_s"] == SURE_ESIGI_S == 30.0
    assert rapor["kural"]["gereken_gecerli"] == GEREKEN_GECERLI == 5


def test_her_aday_icin_hizlar_yazildi(rapor):
    for a in rapor["adaylar"]:
        assert a["istem_isleme_tps"] and a["istem_isleme_tps"] > 0, a["model"]
        assert a["uretim_tps"] and a["uretim_tps"] > 0, a["model"]
        assert a["soguk_tur_s"] > 0, a["model"]
        assert a["ozet"], a["model"]


def test_her_aday_bes_soruyla_olculdu(rapor):
    beklenen = [s["kimlik"] for s in ORNEK_SORULAR]
    for a in rapor["adaylar"]:
        assert [t["kimlik"] for t in a["turlar"]] == beklenen, a["model"]
        assert all(t["sure_s"] > 0 for t in a["turlar"]), a["model"]


def test_olcum_kosullari_gecerli(rapor):
    """Düşünme kapalı, istem bağlama sığdı, ön bellek ısındı, bellek tükenmedi."""
    for a in rapor["adaylar"]:
        assert a["dusunme_kapali"], a["model"]
        assert a["baglam_yetti"], a["model"]
        assert a["soguk_istem_token"] < rapor["ayarlar"]["num_ctx"], a["model"]
        assert a["isinma_dogrulandi"], a["model"]
        assert a["en_dusuk_bos_bellek_gb"] >= 0.5, a["model"]


def test_kayitli_gecerlilik_yeniden_hesaplanınca_ayni(rapor):
    for a in rapor["adaylar"]:
        for t in a["turlar"]:
            assert cevap_gecerli_mi(t["cagrilar"])[0] == t["gecerli"], (a["model"], t["kimlik"])
            soru = next(s for s in ORNEK_SORULAR if s["kimlik"] == t["kimlik"])
            dogru = beklenen_cagri_mi(t["cagrilar"], soru["beklenen_arac"], soru["beklenen_argumanlar"])
            assert dogru == t["dogru_cagri"], (a["model"], t["kimlik"])
        assert a["gecerli_sayisi"] == sum(t["gecerli"] for t in a["turlar"])
        assert a["kural_saglandi"] == kurali_sagliyor_mu(a)


def test_secilen_model_kurala_uygun(rapor):
    assert rapor["secilen_model"] == secim_kurali(rapor["adaylar"])


def test_bir_demo_modeli_secildi(rapor):
    assert rapor["secilen_model"] is not None, "Hiçbir aday kuralı sağlamadı; Faz 0 kapanamaz."
