"""Belge kütüphanesi: belgeleri diske yazar, geri okur ve aranacak parçalara ayırır.

- Her madde bir parçadır; parça kimliği "BELGE#mN" biçimindedir ve belge yeniden üretilse de değişmez.
- Her parça, belgesinin rollerini taşır. Bir rol yalnızca rollerinde kendisi bulunan belgelerin parçalarını görür;
  süzme sıralamadan önce yapılır, görülmeyen parça hiçbir skora girmez.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sayman.documents.synthetic import Belge, Madde, arama_metni


@dataclass(frozen=True)
class Parca:
    parca_id: str
    belge_id: str
    belge_baslik: str
    madde_baslik: str
    roller: tuple[str, ...]
    metin: str          # maddenin kendi metni; alıntı kontrolü buna bakar
    arama_metni: str    # belge adı + taraf + madde başlığı + metin; arama buna bakar


def parcalar(belgeler: list[Belge]) -> list[Parca]:
    return [Parca(m.parca_id, b.belge_id, b.baslik, m.baslik, tuple(b.roller), m.metin, arama_metni(b, m))
            for b in belgeler for m in b.maddeler]


def gorebilir(rol: str, roller: tuple[str, ...]) -> bool:
    return rol in roller


def yaz(belgeler: list[Belge], yol: Path) -> None:
    yol.parent.mkdir(parents=True, exist_ok=True)
    with open(yol, "w", encoding="utf-8", newline="\n") as f:
        for b in belgeler:
            f.write(json.dumps(asdict(b), ensure_ascii=False) + "\n")


def oku(yol: Path) -> list[Belge]:
    belgeler = []
    with open(yol, encoding="utf-8") as f:
        for satir in f:
            d = json.loads(satir)
            d["roller"] = tuple(d["roller"])
            d["maddeler"] = tuple(Madde(**m) for m in d["maddeler"])
            belgeler.append(Belge(**d))
    return belgeler
