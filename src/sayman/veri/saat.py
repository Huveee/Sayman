"""Simülasyon saati: Sayman'ın "bugün" kabul ettiği gün.

Veri, veri bitiş tarihine kadar baştan üretilir; ama araçlar ve izleme yalnızca saatin
gösterdiği güne kadar olanı görür. "Zamanı ileri sar" saati ileri alır ve yeni günlerin
kayıtlarını görünür yapar. Geçmiş hiçbir zaman değişmez.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Saat:
    simdi: dt.date
    veri_bitis: dt.date

    def __post_init__(self):
        if self.simdi > self.veri_bitis:
            raise ValueError("Saat veri bitiş tarihini geçemez")

    def ileri_sar(self, gun: int) -> "Saat":
        if gun < 0:
            raise ValueError("Saat geri alınamaz")
        return Saat(min(self.simdi + dt.timedelta(days=gun), self.veri_bitis), self.veri_bitis)


def saat_oku(yol: Path) -> Saat:
    d = json.loads(Path(yol).read_text(encoding="utf-8"))
    return Saat(dt.date.fromisoformat(d["simdi"]), dt.date.fromisoformat(d["veri_bitis"]))


def saat_yaz(saat: Saat, yol: Path) -> None:
    Path(yol).parent.mkdir(parents=True, exist_ok=True)
    Path(yol).write_text(
        json.dumps({"simdi": saat.simdi.isoformat(), "veri_bitis": saat.veri_bitis.isoformat()}) + "\n",
        encoding="utf-8",
    )
