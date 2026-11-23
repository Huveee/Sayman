"""Anlam araması: bge-m3 gömmeleri (Ollama) ve Qdrant (yerel mod; ayrı bir sunucu gerekmez).

- Her parçanın aranan metni (belge adı + taraf + madde başlığı + metin) bge-m3 ile 1024 boyutlu bir vektöre çevrilir.
  Soru da aynı modelle vektöre çevrilir; en yakın (kosinüs benzerliği en yüksek) parçalar döner.
- Vektörler diskteki önbellekte tutulur (metnin özetiyle anahtarlanır). Aynı metin ikinci kez modele gitmez; testler
  modeli çalıştırmadan önbellekten çalışır.
- Uzun metin sessizce kesilmez: Ollama'ya truncate=False gönderilir; bağlam penceresini aşan metin hata verir.
- Rol süzmesi Qdrant sorgusunun içindedir (roller alanında rol geçmeli); görülemeyen parça sıralamaya girmez.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from sayman.documents.library import Parca

MODEL = "bge-m3"
BOYUT = 1024
BAGLAM = 8192
KOLEKSIYON = "belgeler"


def anahtar(metin: str) -> str:
    return hashlib.sha256(f"{MODEL}\n{metin}".encode("utf-8")).hexdigest()[:32]


class GommeOnbellegi:
    """metin özeti -> normalize edilmiş vektör; .npz dosyasında durur."""

    def __init__(self, yol: Path):
        self.yol = yol
        self.v: dict[str, np.ndarray] = {}
        if yol.exists():
            with np.load(yol) as d:
                self.v = dict(zip(d["anahtarlar"].tolist(), d["vektorler"]))

    def eksikler(self, metinler: list[str]) -> list[str]:
        return list(dict.fromkeys(m for m in metinler if anahtar(m) not in self.v))

    def al(self, metinler: list[str]) -> np.ndarray:
        return np.stack([self.v[anahtar(m)] for m in metinler])

    def doldur(self, metinler: list[str], gomucu=None, parti: int = 16, ilerleme=None) -> int:
        """Önbellekte olmayan metinleri modele gönderir; kaç metin gömüldüğünü döner. Arada diske yazar, yarıda
        kalırsa kaldığı yerden devam eder."""
        eksik = self.eksikler(metinler)
        for i in range(0, len(eksik), parti):
            grup = eksik[i:i + parti]
            for m, v in zip(grup, (gomucu or gom)(grup)):
                self.v[anahtar(m)] = v
            if (i // parti) % 20 == 19:
                self.kaydet()
                if ilerleme:
                    ilerleme(i + len(grup), len(eksik))
        if eksik:
            self.kaydet()
        return len(eksik)

    def kaydet(self) -> None:
        self.yol.parent.mkdir(parents=True, exist_ok=True)
        anahtarlar = sorted(self.v)
        np.savez(self.yol, anahtarlar=np.array(anahtarlar),
                 vektorler=np.stack([self.v[a] for a in anahtarlar]).astype(np.float32))


def gom(metinler: list[str]) -> np.ndarray:
    import ollama
    yanit = ollama.embed(model=MODEL, input=metinler, truncate=False, options={"num_ctx": BAGLAM})
    v = np.asarray(yanit["embeddings"], dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


class AnlamDizini:
    """Qdrant koleksiyonu. yol verilmezse bellekte kurulur (yine Qdrant yerel modu)."""

    def __init__(self, parcalar: list[Parca], onbellek: GommeOnbellegi, yol: Path | None = None, gomucu=None):
        """gomucu: önbellekte olmayan bir soruyu vektöre çeviren işlev (verilmezse Ollama'daki bge-m3). Yeni
        soruların vektörü yalnızca bellekte tutulur; diskteki önbelleğe yazılmaz."""
        from qdrant_client import QdrantClient, models

        self.m = models
        self.onbellek = onbellek
        self.gomucu = gomucu
        self.istemci = QdrantClient(path=str(yol)) if yol else QdrantClient(location=":memory:")
        if self.istemci.collection_exists(KOLEKSIYON):
            self.istemci.delete_collection(KOLEKSIYON)
        self.istemci.create_collection(KOLEKSIYON, vectors_config=models.VectorParams(
            size=BOYUT, distance=models.Distance.COSINE))
        vektorler = onbellek.al([p.arama_metni for p in parcalar])
        self.istemci.upload_points(KOLEKSIYON, [
            models.PointStruct(id=i, vector=v.tolist(),
                               payload={"parca_id": p.parca_id, "belge_id": p.belge_id, "roller": list(p.roller)})
            for i, (p, v) in enumerate(zip(parcalar, vektorler))])

    def skorlar(self, sorgu: str, rol: str, limit: int = 50) -> list[tuple[str, float]]:
        m = self.m
        try:
            v = self.onbellek.al([sorgu])[0]
        except KeyError:  # önbellekte olmayan yeni soru
            self.onbellek.v[anahtar(sorgu)] = (self.gomucu or gom)([sorgu])[0]
            v = self.onbellek.al([sorgu])[0]
        yanit = self.istemci.query_points(
            KOLEKSIYON, query=v.tolist(), limit=limit, with_payload=["parca_id"],
            query_filter=m.Filter(must=[m.FieldCondition(key="roller", match=m.MatchValue(value=rol))]))
        return [(n.payload["parca_id"], float(n.score)) for n in yanit.points]

    def kapat(self) -> None:
        self.istemci.close()
