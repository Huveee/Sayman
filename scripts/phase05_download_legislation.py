"""Downloads the externally sourced files for Phase 5: the PDF text of three laws and the reranker model.

Sources:
- mevzuat.gov.tr/MevzuatMetin/1.5.<no>.pdf: Law 5941 (Cheque Law), 6098 (Turkish Code of Obligations), 6102 (Turkish
  Commercial Code). Legal texts are outside copyright (Law no. 5846, art. 31). The PDF changes as the laws change; the
  SHA-256 digests of the downloaded files are written to reports/faz5_soru_seti.json
  (scripts/phase05_generate_documents.py).
- Hugging Face, BAAI/bge-reranker-v2-m3 (Apache-2.0), at a pinned version (commit). Only the safetensors weights, the
  configuration and the tokenizer files are downloaded; no executable code is downloaded.

The semantic model (bge-m3) is downloaded with Ollama: `ollama pull bge-m3`.

Existing files are not downloaded again.

Outputs (not committed to git):
    data/ham/mevzuat/5941.pdf, 6098.pdf, 6102.pdf
    data/modeller/bge-reranker-v2-m3/

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase05_download_legislation.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.documents.legislation import LAWS  # noqa: E402

LEGISLATION = ROOT / "data" / "ham" / "mevzuat"
MODEL = ROOT / "data" / "modeller" / "bge-reranker-v2-m3"
MODEL_REPO = "BAAI/bge-reranker-v2-m3"
MODEL_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
MODEL_FILES = ["model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json",
                   "special_tokens_map.json", "sentencepiece.bpe.model"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    LEGISLATION.mkdir(parents=True, exist_ok=True)
    for law_no, (name, _) in sorted(LAWS.items()):
        path = LEGISLATION / f"{law_no}.pdf"
        if path.exists():
            print(f"var: {path.name} ({name})")
            continue
        request = urllib.request.Request(f"https://www.mevzuat.gov.tr/MevzuatMetin/1.5.{law_no}.pdf",
                                       headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
        if not data.startswith(b"%PDF"):
            raise SystemExit(f"{law_no}: PDF gelmedi")
        path.write_bytes(data)
        print(f"indirildi: {path.name} ({name}), {len(data):,} bayt")

    if all((MODEL / d).exists() for d in MODEL_FILES):
        print(f"var: {MODEL}")
        return
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=MODEL_REPO, revision=MODEL_REVISION, allow_patterns=MODEL_FILES,
                      local_dir=str(MODEL))
    print(f"indirildi: {MODEL_REPO}@{MODEL_REVISION[:10]} -> {MODEL}")


if __name__ == "__main__":
    main()
