"""Duman testleri: ortam doğru kurulmuş mu, temel dosyalar yerinde mi?

Bu testler internete ve Ollama sunucusuna gitmez.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from sayman.faz0.arac_taslaklari import ARACLAR, ARACLAR_ADA_GORE
from sayman.faz0.dogrulama import arac_cagrisi_gecerli_mi
from sayman.faz0.ornek_sorular import ORNEK_SORULAR

KOK = Path(__file__).resolve().parents[1]


def test_python_surumu_3_11():
    assert sys.version_info[:2] == (3, 11)


def test_paketler_yuklenir():
    import dotenv  # noqa: F401
    import ollama  # noqa: F401
    import sayman

    assert sayman.__version__


def test_gitignore_gizli_dosyalari_disarida_tutar():
    satirlar = (KOK / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in satirlar
    assert ".venv/" in satirlar


def test_env_dosyasi_gitte_izlenmiyor():
    sonuc = subprocess.run(
        ["git", "ls-files", "--", ".env"], cwd=KOK, capture_output=True, text=True, check=True
    )
    assert sonuc.stdout.strip() == ""


def test_on_beş_arac_tanimi_var_ve_adlar_tekil():
    adlar = [a["function"]["name"] for a in ARACLAR]
    assert len(adlar) == 15
    assert len(set(adlar)) == 15


def test_arac_tanimlari_duzgun():
    for arac in ARACLAR:
        assert arac["type"] == "function"
        fonk = arac["function"]
        assert fonk["description"].strip()
        sema = fonk["parameters"]
        assert sema["type"] == "object"
        assert sema["additionalProperties"] is False
        assert set(sema["required"]) <= set(sema["properties"]), fonk["name"]
        for alan, alt in sema["properties"].items():
            assert alt["type"] in {"string", "integer", "number", "boolean"}, (fonk["name"], alan)
            assert alt.get("description", "").strip(), (fonk["name"], alan)
            if "enum" in alt:
                assert alt["enum"], (fonk["name"], alan)


def test_ornek_sorular_bes_tane_ve_beklenen_araclar_var():
    assert len(ORNEK_SORULAR) == 5
    for s in ORNEK_SORULAR:
        assert s["beklenen_arac"] in ARACLAR_ADA_GORE, s["kimlik"]
    argumanli = [s for s in ORNEK_SORULAR if s["beklenen_argumanlar"]]
    assert len(argumanli) >= 2


def test_beklenen_argumanlar_semaya_uygun():
    """Beklenen argümanlar (eksik zorunlular örnek değerle tamamlanınca) geçerli olmalı."""
    ornek_deger = {"string": "ornek", "integer": 1, "number": 1.0, "boolean": True}
    for s in ORNEK_SORULAR:
        sema = ARACLAR_ADA_GORE[s["beklenen_arac"]]["function"]["parameters"]
        argumanlar = dict(s["beklenen_argumanlar"])
        for zorunlu in sema["required"]:
            if zorunlu not in argumanlar:
                alt = sema["properties"][zorunlu]
                argumanlar[zorunlu] = alt["enum"][0] if "enum" in alt else ornek_deger[alt["type"]]
        gecerli, neden = arac_cagrisi_gecerli_mi({"ad": s["beklenen_arac"], "argumanlar": argumanlar})
        assert gecerli, (s["kimlik"], neden)
