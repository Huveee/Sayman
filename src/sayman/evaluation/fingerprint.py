"""Ölçülen sistemin parmak izi: sistem istemi, ayarlar, araç tanımları, sabit metinler, roller ve kaynak kodu.

Faz 7 raporu ölçülen sistemin özetini yazar; Faz 8 ince ayarlı modeli aynı sistem özetiyle ölçer. Böylece önce/sonra
farkı yalnızca modelden gelir. Model kısmı (model adı, seçenekler, Ollama'daki model özeti) ayrı tutulur.

Kaynak kodu özetlenirken satır sonları eşitlenir (Windows'ta git CRLF'e çevirebilir).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

KOK = Path(__file__).resolve().parents[3]
KAYNAKLAR = ("src/sayman/assistant/orchestrator.py", "src/sayman/assistant/verifier.py",
             "src/sayman/tools/catalog.py", "src/sayman/tools/server.py", "src/sayman/tools/scope.py",
             "src/sayman/tools/roles.json", "src/sayman/warehouse/metrics.py", "src/sayman/documents/citation.py",
             "src/sayman/documents/search.py", "src/sayman/documents/semantic.py")


def _ozet(veri) -> str:
    if not isinstance(veri, (bytes, str)):
        veri = json.dumps(veri, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(veri, str):
        veri = veri.encode("utf-8")
    return hashlib.sha256(veri).hexdigest()


def dosya_ozeti(yol: Path) -> str:
    return _ozet(Path(yol).read_bytes().replace(b"\r\n", b"\n"))


def sistem(belge_yontemi: str = "anlam") -> dict:
    from sayman.tools import catalog
    from sayman.assistant import orchestrator

    ayar = dataclasses.asdict(orchestrator.Ayar())
    parcalar = {
        "sistem_istemi": _ozet(orchestrator.SISTEM_ISTEMI),
        "metinler": _ozet(orchestrator.METINLER),
        "arac_tanimlari": _ozet(orchestrator.arac_tanimlari()),
        "ayar": ayar,
        "belge": {"yontem": belge_yontemi, "varsayilan": catalog.BELGE_VARSAYILAN, "en_fazla": catalog.BELGE_EN_FAZLA,
                  "metin_siniri": catalog.BELGE_METIN_SINIRI},
        "kaynak_kodu": {k: dosya_ozeti(KOK / k) for k in KAYNAKLAR},
    }
    return {"ozet": _ozet({k: v for k, v in parcalar.items() if k != "kaynak_kodu"}),
            "kod_ozeti": _ozet(parcalar["kaynak_kodu"]), **parcalar}


def model(ollama_ozeti: bool = True, model_adi: str | None = None) -> dict:
    """Model kısmının izi. Faz 8'de ince ayarlı model `model_adi` ile verilir; seçenekler aynı kalır."""
    from sayman.assistant.model import MODEL, SECENEKLER

    ad = model_adi or MODEL
    m = {"model": ad, "secenekler": SECENEKLER, "think": False}
    if ollama_ozeti:
        try:
            import ollama
            liste = ollama.Client(timeout=10).list()
            m["ollama_ozeti"] = next((x.digest for x in liste.models if x.model in (ad, f"{ad}:latest")), None)
        except Exception as e:  # Ollama kapalıysa özet yazılmaz
            m["ollama_ozeti"] = f"okunamadi: {type(e).__name__}"
    m["ozet"] = _ozet({k: v for k, v in m.items() if k != "ozet"})
    return m
