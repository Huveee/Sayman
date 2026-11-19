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
KAYNAKLAR = ("src/sayman/asistan/orkestrator.py", "src/sayman/asistan/dogrulayici.py",
             "src/sayman/araclar/araclar.py", "src/sayman/araclar/sunucu.py", "src/sayman/araclar/kapsam.py",
             "src/sayman/araclar/roller.json", "src/sayman/ambar/metrikler.py", "src/sayman/belge/alinti.py",
             "src/sayman/belge/arama.py", "src/sayman/belge/anlam.py")


def _ozet(veri) -> str:
    if not isinstance(veri, (bytes, str)):
        veri = json.dumps(veri, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(veri, str):
        veri = veri.encode("utf-8")
    return hashlib.sha256(veri).hexdigest()


def dosya_ozeti(yol: Path) -> str:
    return _ozet(Path(yol).read_bytes().replace(b"\r\n", b"\n"))


def sistem(belge_yontemi: str = "anlam") -> dict:
    from sayman.araclar import araclar
    from sayman.asistan import orkestrator

    ayar = dataclasses.asdict(orkestrator.Ayar())
    parcalar = {
        "sistem_istemi": _ozet(orkestrator.SISTEM_ISTEMI),
        "metinler": _ozet(orkestrator.METINLER),
        "arac_tanimlari": _ozet(orkestrator.arac_tanimlari()),
        "ayar": ayar,
        "belge": {"yontem": belge_yontemi, "varsayilan": araclar.BELGE_VARSAYILAN, "en_fazla": araclar.BELGE_EN_FAZLA,
                  "metin_siniri": araclar.BELGE_METIN_SINIRI},
        "kaynak_kodu": {k: dosya_ozeti(KOK / k) for k in KAYNAKLAR},
    }
    return {"ozet": _ozet({k: v for k, v in parcalar.items() if k != "kaynak_kodu"}),
            "kod_ozeti": _ozet(parcalar["kaynak_kodu"]), **parcalar}


def model(ollama_ozeti: bool = True) -> dict:
    from sayman.asistan.model import MODEL, SECENEKLER

    m = {"model": MODEL, "secenekler": SECENEKLER, "think": False}
    if ollama_ozeti:
        try:
            import ollama
            liste = ollama.Client(timeout=10).list()
            m["ollama_ozeti"] = next((x.digest for x in liste.models if x.model == MODEL), None)
        except Exception as e:  # Ollama kapalıysa özet yazılmaz
            m["ollama_ozeti"] = f"okunamadi: {type(e).__name__}"
    m["ozet"] = _ozet({k: v for k, v in m.items() if k != "ozet"})
    return m
