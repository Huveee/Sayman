"""TCMB döviz kurları (döviz alış). Kaynak: TCMB EVDS, seriler TP.DK.USD.A, TP.DK.EUR.A, TP.DK.GBP.A.

Kurlar indirme betiğiyle bir kez indirilip data/kaynak/tcmb_kurlar.csv dosyasına kaydedilir;
üretici ve testler yalnızca bu dosyayı okur. Hafta sonu ve tatil günlerinde son yayımlanan kur kullanılır.
"""

from __future__ import annotations

import bisect
import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path

SERILER = {"USD": "TP.DK.USD.A", "EUR": "TP.DK.EUR.A", "GBP": "TP.DK.GBP.A"}


class Kurlar:
    def __init__(self, satirlar: list[tuple[dt.date, str, Decimal]]):
        self._tarihler: dict[str, list[dt.date]] = {}
        self._degerler: dict[str, list[Decimal]] = {}
        for tarih, pb, kur in sorted(satirlar):
            self._tarihler.setdefault(pb, []).append(tarih)
            self._degerler.setdefault(pb, []).append(kur)

    @property
    def para_birimleri(self) -> list[str]:
        return sorted(self._tarihler)

    def ilk_tarih(self, pb: str) -> dt.date:
        return self._tarihler[pb][0]

    def son_tarih(self, pb: str) -> dt.date:
        return self._tarihler[pb][-1]

    def kur(self, pb: str, tarih: dt.date) -> Decimal:
        """Verilen günde ya da öncesinde yayımlanan son kur. TRY için 1."""
        if pb == "TRY":
            return Decimal(1)
        tarihler = self._tarihler[pb]
        i = bisect.bisect_right(tarihler, tarih) - 1
        if i < 0:
            raise KeyError(f"{pb} için {tarih} tarihinde ya da öncesinde kur yok")
        return self._degerler[pb][i]

    def satirlar(self, baslangic: dt.date, bitis: dt.date) -> list[tuple[dt.date, str, Decimal]]:
        sonuc = []
        for pb in self.para_birimleri:
            for t, k in zip(self._tarihler[pb], self._degerler[pb]):
                if baslangic <= t <= bitis:
                    sonuc.append((t, pb, k))
        return sorted(sonuc)


def kurlar_oku(yol: Path) -> Kurlar:
    with open(yol, encoding="utf-8", newline="") as f:
        satirlar = [
            (dt.date.fromisoformat(s["tarih"]), s["para_birimi"], Decimal(s["alis"]))
            for s in csv.DictReader(f)
        ]
    return Kurlar(satirlar)


def kurlar_yaz(satirlar: list[tuple[dt.date, str, Decimal]], yol: Path) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    with open(yol, "w", encoding="utf-8", newline="") as f:
        yazici = csv.writer(f, lineterminator="\n")
        yazici.writerow(["tarih", "para_birimi", "alis"])
        for tarih, pb, kur in sorted(satirlar):
            yazici.writerow([tarih.isoformat(), pb, str(kur)])
