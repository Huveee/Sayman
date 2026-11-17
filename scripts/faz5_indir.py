"""Faz 5 için dışarıdan gelen dosyaları indirir: üç kanunun PDF metni ve sıralayıcı modeli.

Kaynaklar:
- mevzuat.gov.tr/MevzuatMetin/1.5.<no>.pdf: 5941 Çek Kanunu, 6098 Türk Borçlar Kanunu, 6102 Türk Ticaret Kanunu.
  Kanun metinleri telif kapsamı dışındadır (5846 sayılı Kanun md. 31). Kanunlar değiştikçe PDF de değişir; indirilen
  dosyaların SHA-256 özetleri reports/faz5_soru_seti.json'a yazılır (scripts/faz5_belge_uret.py).
- Hugging Face, BAAI/bge-reranker-v2-m3 (Apache-2.0), sabit bir sürümde (commit). Yalnızca safetensors ağırlıkları,
  yapılandırma ve tokenizer dosyaları indirilir; çalıştırılabilir kod indirilmez.

Anlam modeli (bge-m3) Ollama ile indirilir: `ollama pull bge-m3`.

Var olan dosyalar yeniden indirilmez.

Çıktılar (git'e girmez):
    data/ham/mevzuat/5941.pdf, 6098.pdf, 6102.pdf
    data/modeller/bge-reranker-v2-m3/

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz5_indir.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.belge.mevzuat import KANUNLAR  # noqa: E402

MEVZUAT = KOK / "data" / "ham" / "mevzuat"
MODEL = KOK / "data" / "modeller" / "bge-reranker-v2-m3"
MODEL_DEPOSU = "BAAI/bge-reranker-v2-m3"
MODEL_SURUMU = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
MODEL_DOSYALARI = ["model.safetensors", "config.json", "tokenizer.json", "tokenizer_config.json",
                   "special_tokens_map.json", "sentencepiece.bpe.model"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    MEVZUAT.mkdir(parents=True, exist_ok=True)
    for no, (ad, _) in sorted(KANUNLAR.items()):
        yol = MEVZUAT / f"{no}.pdf"
        if yol.exists():
            print(f"var: {yol.name} ({ad})")
            continue
        istek = urllib.request.Request(f"https://www.mevzuat.gov.tr/MevzuatMetin/1.5.{no}.pdf",
                                       headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(istek, timeout=120) as yanit:
            veri = yanit.read()
        if not veri.startswith(b"%PDF"):
            raise SystemExit(f"{no}: PDF gelmedi")
        yol.write_bytes(veri)
        print(f"indirildi: {yol.name} ({ad}), {len(veri):,} bayt")

    if all((MODEL / d).exists() for d in MODEL_DOSYALARI):
        print(f"var: {MODEL}")
        return
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=MODEL_DEPOSU, revision=MODEL_SURUMU, allow_patterns=MODEL_DOSYALARI,
                      local_dir=str(MODEL))
    print(f"indirildi: {MODEL_DEPOSU}@{MODEL_SURUMU[:10]} -> {MODEL}")


if __name__ == "__main__":
    main()
