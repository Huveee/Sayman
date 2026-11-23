"""Belge araması: kelime araması (BM25), anlam araması ile birleştirme ve yeniden sıralama.

Kelime araması (BM25):
- Metin Türkçe kurala göre küçültülür (I→ı, İ→i), harf ve rakam dizilerine bölünür.
- Kelimeler ilk 5 harfine indirilir ("faturalarını" → "fatur"). Türkçe ekler kelimenin sonuna geldiği için bu basit
  kesme, aynı kökten gelen biçimleri çoğu zaman birleştirir. Rakamlar olduğu gibi kalır.

Birleştirme (karma): kelime ve anlam aramasının ilk N sonucu sıra numaralarıyla birleştirilir (reciprocal rank
fusion): bir parçanın skoru, her listedeki sırası r için 1/(k + r) toplamıdır. Skorların ölçeği farklı olduğu için
skor yerine sıra kullanılır.

Yeniden sıralama (karma_sirala): birleşik listenin ilk adayları sıralayıcı modelle yeniden puanlanır.

Rol süzmesi her aşamada skorlamadan önce yapılır: rolün göremediği parça hiçbir listeye girmez.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from sayman.documents.library import Parca, gorebilir

KOK_UZUNLUGU = 5
DURAK = frozenset("""
ve ile bir bu şu o için ne mi mı mu mü da de ki ya veya gibi kadar en çok daha her hangi kaç nasıl neden olarak
olan ise değil var yok biz bize bizi bizim ona onu onun sonra önce
""".split())
_KELIME = re.compile(r"\w+")


def kucult(metin: str) -> str:
    return metin.replace("I", "ı").replace("İ", "i").lower()


def terimler(metin: str) -> list[str]:
    sonuc = []
    for k in _KELIME.findall(kucult(metin)):
        if k in DURAK or k == "_":
            continue
        sonuc.append(k if any(c.isdigit() for c in k) else k[:KOK_UZUNLUGU])
    return sonuc


class BM25:
    def __init__(self, parcalar: list[Parca], k1: float = 1.5, b: float = 0.75):
        self.parcalar = parcalar
        self.k1, self.b = k1, b
        self.tf = [Counter(terimler(p.arama_metni)) for p in parcalar]
        self.uzunluk = [sum(t.values()) for t in self.tf]
        self.ort_uzunluk = sum(self.uzunluk) / len(self.uzunluk)
        df = Counter(t for tf in self.tf for t in tf)
        n = len(parcalar)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def skorlar(self, sorgu: str, rol: str) -> list[tuple[str, float]]:
        """Rolün görebildiği her parça için (parca_id, skor); skoru 0 olanlar dahil değildir."""
        sorgu_t = [t for t in dict.fromkeys(terimler(sorgu)) if t in self.idf]
        sonuc = []
        for p, tf, u in zip(self.parcalar, self.tf, self.uzunluk):
            if not gorebilir(rol, p.roller):
                continue
            s = 0.0
            for t in sorgu_t:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * u / self.ort_uzunluk))
            if s > 0:
                sonuc.append((p.parca_id, s))
        return sonuc


def ilk(sonuclar: list[tuple[str, float]], k: int) -> list[tuple[str, float]]:
    """Skora göre ilk k sonuç; eşit skorlar parça kimliğine göre sıralanır."""
    return sorted(sonuclar, key=lambda x: (-x[1], x[0]))[:k]


def rrf(listeler: list[list[tuple[str, float]]], k: int = 60, derinlik: int = 50) -> list[tuple[str, float]]:
    """Sıra birleştirme: her listenin ilk `derinlik` sonucu, sıra r (1'den) için 1/(k + r) katkı verir."""
    skor: dict[str, float] = {}
    for liste in listeler:
        for r, (pid, _) in enumerate(ilk(liste, derinlik), start=1):
            skor[pid] = skor.get(pid, 0.0) + 1.0 / (k + r)
    return list(skor.items())


YONTEMLER = ("bm25", "anlam", "karma", "karma_sirala")


class Arayici:
    """Bütün aşamaları bir araya getirir. anlam ve siralayici verilmezse yalnızca bm25 kullanılabilir."""

    def __init__(self, parcalar: list[Parca], anlam=None, siralayici=None, rrf_k: int = 60, derinlik: int = 50,
                 aday: int = 20):
        self.parca = {p.parca_id: p for p in parcalar}
        self.bm25 = BM25(parcalar)
        self.anlam, self.siralayici = anlam, siralayici
        self.rrf_k, self.derinlik, self.aday = rrf_k, derinlik, aday

    def skorlar(self, sorgu: str, rol: str, yontem: str = "karma_sirala") -> list[tuple[str, float]]:
        if yontem not in YONTEMLER:
            raise ValueError(f"bilinmeyen yöntem: {yontem}")
        kelime = self.bm25.skorlar(sorgu, rol)
        if yontem == "bm25":
            return kelime
        anlam = self.anlam.skorlar(sorgu, rol, self.derinlik)
        if yontem == "anlam":
            return anlam
        karma = rrf([kelime, anlam], self.rrf_k, self.derinlik)
        if yontem == "karma":
            return karma
        adaylar = ilk(karma, self.aday)
        s = self.siralayici.skorla(sorgu, [(pid, self.parca[pid].arama_metni) for pid, _ in adaylar])
        return list(zip([pid for pid, _ in adaylar], s))

    def ara(self, sorgu: str, rol: str, k: int = 5, yontem: str = "karma_sirala") -> list[tuple[Parca, float]]:
        return [(self.parca[pid], s) for pid, s in ilk(self.skorlar(sorgu, rol, yontem), k)]


def arayici_kur(belge_klasoru, yontem: str = "anlam", model_klasoru=None) -> Arayici:
    """Diskteki kütüphaneden bir arayıcı kurar (asistan ve MCP sunucusu için).

    - Anlam dizini önbellekteki parça vektörlerinden bellekte kurulur (Qdrant yerel mod); yeni sorular Ollama'daki
      bge-m3 ile vektöre çevrilir.
    - Sıralayıcı model yalnızca yöntem "karma_sirala" ise yüklenir. Faz 5 ölçümünde sürecin belleğini 2,7 GB'a
      çıkardı ve soru başına aramayı medyan 9,6 saniyeye uzattı; daha güçlü bir bilgisayar için bir ayardır."""
    from pathlib import Path

    from sayman.documents import library

    if yontem not in YONTEMLER:
        raise ValueError(f"bilinmeyen yöntem: {yontem}")
    klasor = Path(belge_klasoru)
    parcalar = library.parcalar(library.oku(klasor / "belgeler.jsonl"))
    anlam = siralayici = None
    if yontem != "bm25":
        from sayman.documents.semantic import AnlamDizini, GommeOnbellegi
        anlam = AnlamDizini(parcalar, GommeOnbellegi(klasor / "onbellek" / "gomme_bge-m3.npz"))
    if yontem == "karma_sirala":
        from sayman.documents.reranker import Siralayici
        siralayici = Siralayici(Path(model_klasoru) if model_klasoru else None,
                                klasor / "onbellek" / "sirala_bge-reranker-v2-m3.json")
    return Arayici(parcalar, anlam, siralayici)
