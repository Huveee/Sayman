"""Faz 5 puanlaması: bir aramanın sıraladığı parçalarda doğru parça kaçıncı?

Bu dosya arama kodundan önce yazıldı ve ayar sırasında değiştirilmedi.

- Arama (parca_id, skor) listesi verir. Sıra skora göre büyükten küçüğe; eşit skorlar parça kimliğine göre sıralanır
  (aramanın kendi sırası kullanılmaz, böylece eşitlikler şansa bırakılmaz).
- isabet@k: doğru parça ilk k sonuç içinde mi. Kabul ölçütü isabet@5 ≥ 0,85 (tek doğru parça olduğu için bu,
  recall@5 ile aynıdır).
- MRR@10: doğru parçanın sırasının tersi (ilk 10'da değilse 0); rapor içindir.
- Sonuçlar ayrıca soru türüne ve soru stiline (yakın/uzak kalıp) göre ayrı verilir.
"""

from __future__ import annotations

from collections import defaultdict


def sirala(sonuclar: list[tuple[str, float]]) -> list[str]:
    return [p for p, _ in sorted(sonuclar, key=lambda x: (-x[1], x[0]))]


def sira(sonuclar: list[tuple[str, float]], dogru: str) -> int | None:
    """Doğru parçanın 1'den başlayan sırası; listede yoksa None."""
    s = sirala(sonuclar)
    return s.index(dogru) + 1 if dogru in s else None


def puanla(sorular: list[dict], sonuclar: dict[str, list[tuple[str, float]]], k: int = 5) -> dict:
    """sonuclar: soru_id -> (parca_id, skor) listesi."""
    satirlar, gruplar, stiller = [], defaultdict(list), defaultdict(list)
    for s in sorular:
        r = sira(sonuclar[s["soru_id"]], s["dogru_parca"])
        satirlar.append({"soru_id": s["soru_id"], "sira": r})
        gruplar[s["tur"]].append(r)
        stiller[s.get("stil", "-")].append(r)
    hepsi = [x["sira"] for x in satirlar]

    def ozet(sirlar):
        n = len(sirlar)
        return {"soru": n, f"isabet@{k}": round(sum(1 for r in sirlar if r and r <= k) / n, 4) if n else None,
                "isabet@1": round(sum(1 for r in sirlar if r == 1) / n, 4) if n else None,
                "mrr@10": round(sum(1 / r for r in sirlar if r and r <= 10) / n, 4) if n else None}

    return {**ozet(hepsi), "ture_gore": {t: ozet(v) for t, v in sorted(gruplar.items())},
            "stile_gore": {t: ozet(v) for t, v in sorted(stiller.items())},
            "kacanlar": [x["soru_id"] for x in satirlar if not (x["sira"] and x["sira"] <= k)]}
