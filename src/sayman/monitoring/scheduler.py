"""Zamanlayıcı: saatin gösterdiği güne kadar kuralları çalıştırır, yeni uyarıları uyarı deposuna ekler.

Her çalışmada kurallar o güne kadar görünen veriyle baştan hesaplanır; depo yalnızca daha önce bildirilmiş
uyarıları ve en son hangi güne kadar bakıldığını tutar. Uyarının günü veriden gelir, depodan gelmez. Bu yüzden:
- saat aynıyken ikinci çalışma hiçbir şey eklemez,
- saat gün gün ilerletilse de tek seferde ilerletilse de depoda aynı uyarılar olur.
Kayıtlarda duvar saati (çalıştırıldığı an) yoktur; aynı veri ve aynı saat her zaman aynı dosyayı verir.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from sayman.monitoring.rules import KURALLAR, Ayarlar, Veri
from sayman.data.as_of import gorunum
from sayman.data.clock import Saat

GUN = dt.timedelta(days=1)


def baslangic_gunu(tablolar: dict[str, list[dict]]) -> dt.date:
    """Verinin ilk günü: ilk satış faturası."""
    return min(f["fatura_tarihi"] for f in tablolar["faturalar"])


def izle(tablolar: dict[str, list[dict]], bitis: dt.date, ayarlar: Ayarlar | None = None,
         bas: dt.date | None = None) -> list[dict]:
    """bas..bitis arasında başlayan uyarılar; yalnızca bitis gününe kadar görünen kayıtlar kullanılır."""
    a = ayarlar or Ayarlar()
    g = gorunum(tablolar, bitis)
    v = Veri(g, baslangic_gunu(g), bitis)
    bas = bas or v.baslangic
    sonuc = []
    for kural in KURALLAR.values():
        sonuc.extend(kural(v, a, bas, bitis))
    return sorted(sonuc, key=lambda u: (u["gun"], u["kural"], u["varlik"]["id"], u["uyari_id"]))


class DepoHatasi(RuntimeError):
    pass


@dataclass
class Depo:
    """data/izleme/: uyarilar.jsonl (her satır bir uyarı, yalnızca eklenir) ve durum.json (son bakılan gün)."""

    klasor: Path

    @property
    def uyari_yolu(self) -> Path:
        return Path(self.klasor) / "uyarilar.jsonl"

    @property
    def durum_yolu(self) -> Path:
        return Path(self.klasor) / "durum.json"

    def durum(self) -> dict | None:
        if not self.durum_yolu.exists():
            return None
        return json.loads(self.durum_yolu.read_text(encoding="utf-8"))

    def uyarilar(self) -> list[dict]:
        if not self.uyari_yolu.exists():
            return []
        with open(self.uyari_yolu, encoding="utf-8") as f:
            return [json.loads(satir) for satir in f if satir.strip()]

    def ekle(self, uyarilar: list[dict], son_gun: dt.date, ayarlar: Ayarlar) -> int:
        Path(self.klasor).mkdir(parents=True, exist_ok=True)
        var = {u["uyari_id"] for u in self.uyarilar()}
        yeni = [u for u in uyarilar if u["uyari_id"] not in var]
        with open(self.uyari_yolu, "a", encoding="utf-8", newline="\n") as f:
            for u in yeni:
                f.write(json.dumps(u, ensure_ascii=False, sort_keys=True) + "\n")
        self.durum_yolu.write_text(json.dumps({"son_gun": son_gun.isoformat(), "ayarlar": ayarlar.sozluk()},
                                              ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return len(yeni)


def calistir(tablolar: dict[str, list[dict]], saat: Saat, depo: Depo, ayarlar: Ayarlar | None = None) -> dict:
    """Son bakılan günden sonraki günleri saatin gösterdiği güne kadar tarar, yeni uyarıları depoya ekler."""
    a = ayarlar or Ayarlar()
    durum = depo.durum()
    if durum and durum["ayarlar"] != a.sozluk():
        raise DepoHatasi("Depo başka kural ayarlarıyla oluşturulmuş; yeni ayarlarla baştan başlamak için depoyu silin.")
    son = dt.date.fromisoformat(durum["son_gun"]) if durum else None
    if son is not None and saat.simdi < son:
        raise DepoHatasi(f"Saat ({saat.simdi}) depodaki son günün ({son}) gerisinde; saat geri alınamaz.")
    if son is not None and saat.simdi == son:
        return {"onceki_son_gun": son.isoformat(), "son_gun": son.isoformat(), "yeni": [], "toplam": len(depo.uyarilar())}
    bas = son + GUN if son else None
    uyarilar = izle(tablolar, saat.simdi, a, bas)
    depo.ekle(uyarilar, saat.simdi, a)
    return {"onceki_son_gun": son.isoformat() if son else None, "son_gun": saat.simdi.isoformat(),
            "yeni": uyarilar, "toplam": len(depo.uyarilar())}
