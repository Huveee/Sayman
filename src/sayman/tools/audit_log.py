"""Denetim kaydı: her araç çağrısı (reddedilenler ve hatalar dahil) bir satır olarak yazılır.

Kayıt yalnızca sona eklenir (JSON satırları). Her satır bir öncekinin özetini taşır (zincir): aradan bir
satır silinir ya da değiştirilirse `dogrula` bunu bulur. Kimlik kartının kendisi asla yazılmaz.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

BASLANGIC = "0" * 64


def _ozet(satir: dict) -> str:
    return hashlib.sha256(json.dumps(satir, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


class DenetimKaydi:
    """yol None ise kayıt yalnızca bellekte tutulur (testler için)."""

    def __init__(self, yol: Path | None = None, saat=None):
        self.yol = Path(yol) if yol else None
        self.saat = saat or (lambda: dt.datetime.now(dt.timezone.utc))
        self.satirlar: list[dict] = []
        self._son_ozet, self._sira = BASLANGIC, 0
        if self.yol and self.yol.exists():
            onceki = self.oku(self.yol)
            if onceki:
                self._son_ozet, self._sira = onceki[-1]["ozet"], onceki[-1]["sira"]

    def yaz(self, **alanlar) -> dict:
        if "kart" in alanlar:
            raise ValueError("kimlik kartı denetim kaydına yazılmaz")
        self._sira += 1
        satir = {"sira": self._sira, "zaman": self.saat().isoformat(timespec="seconds"), **alanlar,
                 "onceki_ozet": self._son_ozet}
        satir["ozet"] = _ozet(satir)
        self._son_ozet = satir["ozet"]
        self.satirlar.append(satir)
        if self.yol:
            self.yol.parent.mkdir(parents=True, exist_ok=True)
            with open(self.yol, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(satir, ensure_ascii=False, default=str) + "\n")
        return satir

    @staticmethod
    def oku(yol: Path) -> list[dict]:
        return [json.loads(s) for s in Path(yol).read_text(encoding="utf-8").splitlines() if s.strip()]


def dogrula(satirlar: list[dict]) -> list[str]:
    """Zinciri kontrol eder; bozuk satırların açıklamasını döner (boş liste = sağlam)."""
    hatalar, onceki = [], BASLANGIC
    for i, satir in enumerate(satirlar, start=1):
        govde = {k: v for k, v in satir.items() if k != "ozet"}
        if satir.get("sira") != i:
            hatalar.append(f"{i}. satırın sırası {satir.get('sira')}")
        if satir.get("onceki_ozet") != onceki:
            hatalar.append(f"{i}. satır bir önceki satıra bağlı değil")
        if _ozet(govde) != satir.get("ozet"):
            hatalar.append(f"{i}. satırın içeriği değişmiş")
        onceki = satir.get("ozet")
    return hatalar
