"""Phase 5: converts all chunks and questions to vectors with bge-m3 for semantic search and builds the Qdrant index.

The vectors are written to the cache (data/belge/onbellek/gomme_bge-m3.npz); if the script is interrupted, running it
again continues where it left off. The questions' vectors are cached too: this is only vector computation, it does
not produce search results.

Input:  data/belge/belgeler.jsonl, data/belge/sorular.json (scripts/phase05_generate_documents.py)
        the bge-m3 model in Ollama (ollama pull bge-m3)
Output: data/belge/onbellek/gomme_bge-m3.npz, data/belge/qdrant/ (Qdrant local-mode index directory)

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase05_build_index.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.documents import library  # noqa: E402
from sayman.documents.semantic import SemanticIndex, EmbeddingCache  # noqa: E402

DOCUMENT_DIR = ROOT / "data" / "belge"
CACHE = DOCUMENT_DIR / "onbellek" / "gomme_bge-m3.npz"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    chunks = library.to_chunks(library.read_documents(DOCUMENT_DIR / "belgeler.jsonl"))
    questions = json.loads((DOCUMENT_DIR / "sorular.json").read_text(encoding="utf-8"))
    cache = EmbeddingCache(CACHE)
    start = time.perf_counter()

    def progress(n, total):
        print(f"  {n}/{total} ({time.perf_counter() - start:.0f} s)")

    question_texts = [s["soru"] for set_ in questions.values() for s in set_]
    print("sorular:", cache.fill(question_texts, progress=progress), "yeni vektör")
    print("parçalar:", cache.fill([p.arama_metni for p in chunks], progress=progress), "yeni vektör")
    print(f"toplam süre: {time.perf_counter() - start:.0f} s")
    index = SemanticIndex(chunks, cache, DOCUMENT_DIR / "qdrant")
    print("Qdrant dizini:", index.client.count("belgeler").count, "nokta")
    index.close()


if __name__ == "__main__":
    main()
