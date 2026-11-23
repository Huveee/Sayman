"""Yeniden sıralama: BAAI/bge-reranker-v2-m3 (resmî Hugging Face deposu, Apache-2.0), işlemcide torch ile.

İlk aşama (kelime + anlam araması) aday parçaları bulur; sıralayıcı soruyu ve her adayı birlikte okuyup "bu parça
bu soruyu cevaplıyor mu" diye bir skor verir. Aday sayısı küçük tutulur (ör. 20), çünkü her aday için model bir kez
çalışır.

- Model data/modeller/bge-reranker-v2-m3 altından yüklenir; internetten bir şey indirmez, uzak kod çalıştırmaz.
- Skorlar diskteki önbellekte tutulur (soru özeti + parça kimliği). Aynı çift ikinci kez modele gitmez; testler
  modeli yüklemeden önbellekten çalışır.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL_ADI = "bge-reranker-v2-m3"
EN_UZUN = 512


def anahtar(sorgu: str, parca_id: str) -> str:
    return hashlib.sha256(f"{MODEL_ADI}\n{sorgu}".encode("utf-8")).hexdigest()[:24] + "|" + parca_id


class Siralayici:
    def __init__(self, model_klasoru: Path | None, onbellek_yolu: Path, parti: int = 8):
        self.klasor = model_klasoru
        self.yol = onbellek_yolu
        self.parti = parti
        self.skor: dict[str, float] = (json.loads(onbellek_yolu.read_text(encoding="utf-8"))
                                       if onbellek_yolu.exists() else {})
        self._model = None
        self.degisti = False

    def _yukle(self):
        if self._model is None:
            if self.klasor is None or not (self.klasor / "model.safetensors").exists():
                raise FileNotFoundError(f"Sıralama modeli yok: {self.klasor}")
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            torch.set_num_threads(max(1, torch.get_num_threads()))
            self._tok = AutoTokenizer.from_pretrained(self.klasor, local_files_only=True)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.klasor, local_files_only=True, use_safetensors=True).eval()
            self._torch = torch
        return self._model

    def skorla(self, sorgu: str, adaylar: list[tuple[str, str]]) -> list[float]:
        """adaylar: (parca_id, aranan metin). Her aday için bir skor (büyük = daha ilgili)."""
        eksik = [(pid, metin) for pid, metin in adaylar if anahtar(sorgu, pid) not in self.skor]
        if eksik:
            model = self._yukle()
            for i in range(0, len(eksik), self.parti):
                grup = eksik[i:i + self.parti]
                girdi = self._tok([sorgu] * len(grup), [m for _, m in grup], padding=True, truncation=True,
                                  max_length=EN_UZUN, return_tensors="pt")
                with self._torch.inference_mode():
                    cikti = model(**girdi).logits.view(-1).float().tolist()
                for (pid, _), s in zip(grup, cikti):
                    self.skor[anahtar(sorgu, pid)] = round(s, 6)
            self.degisti = True
        return [self.skor[anahtar(sorgu, pid)] for pid, _ in adaylar]

    def kaydet(self) -> None:
        if self.degisti:
            self.yol.parent.mkdir(parents=True, exist_ok=True)
            self.yol.write_text(json.dumps(self.skor, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self.degisti = False
