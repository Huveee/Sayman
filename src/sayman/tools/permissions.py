"""Yetki: hangi rol hangi aracı çağırabilir ve hangi satırları görebilir.

Rol tablosu veri olarak roles.json'da durur. Yetki iki katmanda uygulanır:
1. Araç: rolün listesinde olmayan araç hiç çalıştırılmaz.
2. Veri: araç, rolün kapsamındaki satırlardan başka satır bulunmayan bir bağlantı üzerinde çalışır
   (kapsam.py). Araçta bir hata olsa bile kapsam dışı satır sonuca giremez.

Bütün roller modele aynı araç listesiyle gösterilir: rol başına ayrı liste, rol her değiştiğinde modelin
araç tanımlarını baştan okumasını gerektirirdi. Faz 0'da 15 taslak araç 2.078 token tuttu ve 9b modeli bunu
~77 saniyede okudu; 13 araçlık bu liste 2.187 token (ölçüm: reports/faz3_araclar.json).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROLLER_DOSYASI = Path(__file__).with_name("roles.json")
KAPSAMLAR = ("hepsi", "yurtici", "ihracat")


@dataclass(frozen=True)
class Rol:
    kimlik: str
    ad: str
    kapsam: str
    araclar: frozenset[str]


@dataclass(frozen=True)
class YetkiTablosu:
    roller: dict[str, Rol]
    kullanicilar: dict[str, str]  # kullanıcı -> rol kimliği

    def izinli_mi(self, rol: str, arac: str) -> bool:
        r = self.roller.get(rol)
        return r is not None and arac in r.araclar


def yetki_oku(yol: Path = ROLLER_DOSYASI) -> YetkiTablosu:
    veri = json.loads(Path(yol).read_text(encoding="utf-8"))
    roller = {}
    for kimlik, r in veri["roller"].items():
        if r["kapsam"] not in KAPSAMLAR:
            raise ValueError(f"bilinmeyen kapsam: {r['kapsam']}")
        roller[kimlik] = Rol(kimlik, r["ad"], r["kapsam"], frozenset(r["araclar"]))
    for kullanici, rol in veri["kullanicilar"].items():
        if rol not in roller:
            raise ValueError(f"{kullanici} için bilinmeyen rol: {rol}")
    return YetkiTablosu(roller, dict(veri["kullanicilar"]))
