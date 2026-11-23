"""Reranking: BAAI/bge-reranker-v2-m3 (official Hugging Face repository, Apache-2.0), on the CPU with torch.

The first stage (keyword + semantic search) finds candidate chunks; the reranker reads the question and each candidate
together and gives a score for "does this chunk answer this question". The number of candidates is kept small (e.g.
20), because the model runs once for every candidate.

- The model is loaded from data/modeller/bge-reranker-v2-m3; it downloads nothing from the internet and runs no remote
  code.
- Scores are kept in an on-disk cache (question digest + chunk identifier). The same pair never goes to the model a
  second time; tests run from the cache without loading the model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

MODEL_NAME = "bge-reranker-v2-m3"
MAX_LENGTH = 512


def cache_key(query: str, chunk_id: str) -> str:
    return hashlib.sha256(f"{MODEL_NAME}\n{query}".encode("utf-8")).hexdigest()[:24] + "|" + chunk_id


class Reranker:
    def __init__(self, model_directory: Path | None, cache_path: Path, batch_size: int = 8):
        self.directory = model_directory
        self.path = cache_path
        self.batch_size = batch_size
        self.score_cache: dict[str, float] = (json.loads(cache_path.read_text(encoding="utf-8"))
                                       if cache_path.exists() else {})
        self._model = None
        self.changed = False

    def _load(self):
        if self._model is None:
            if self.directory is None or not (self.directory / "model.safetensors").exists():
                raise FileNotFoundError(f"Sıralama modeli yok: {self.directory}")
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            torch.set_num_threads(max(1, torch.get_num_threads()))
            self._tok = AutoTokenizer.from_pretrained(self.directory, local_files_only=True)
            self._model = AutoModelForSequenceClassification.from_pretrained(
                self.directory, local_files_only=True, use_safetensors=True).eval()
            self._torch = torch
        return self._model

    def score(self, query: str, candidates: list[tuple[str, str]]) -> list[float]:
        """candidates: (chunk_id, search text). One score per candidate (larger = more relevant)."""
        missing = [(chunk_id, text) for chunk_id, text in candidates if cache_key(query, chunk_id) not in self.score_cache]
        if missing:
            model = self._load()
            for i in range(0, len(missing), self.batch_size):
                group = missing[i:i + self.batch_size]
                inputs = self._tok([query] * len(group), [m for _, m in group], padding=True, truncation=True,
                                  max_length=MAX_LENGTH, return_tensors="pt")
                with self._torch.inference_mode():
                    logits = model(**inputs).logits.view(-1).float().tolist()
                for (chunk_id, _), s in zip(group, logits):
                    self.score_cache[cache_key(query, chunk_id)] = round(s, 6)
            self.changed = True
        return [self.score_cache[cache_key(query, chunk_id)] for chunk_id, _ in candidates]

    def kaydet(self) -> None:
        if self.changed:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.score_cache, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            self.changed = False
