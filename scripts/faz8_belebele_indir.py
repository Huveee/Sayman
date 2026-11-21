"""Belebele'nin Türkçe dosyasını indirir (Faz 8 unutma testi).

Kaynak: Bandarkar ve diğerleri (2023). The Belebele Benchmark. Hugging Face facebook/belebele,
data/tur_Latn.jsonl. Lisans: CC BY-SA 4.0. Dosya sabit bir sürümden (commit) indirilir.

Çıktılar (git'e girmez, bu betik yeniden üretir):
    data/ham/belebele/tur_Latn.jsonl    indirilen dosya
    data/ham/belebele/kaynak.json       adres, sürüm, boyut ve SHA-256 özeti

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8_belebele_indir.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
SURUM = "7899cdfa4e1e0d733fd77c848e2c273cb1d32be2"
ADRES = f"https://huggingface.co/datasets/facebook/belebele/resolve/{SURUM}/data/tur_Latn.jsonl"
HEDEF = KOK / "data" / "ham" / "belebele" / "tur_Latn.jsonl"


def main() -> None:
    HEDEF.parent.mkdir(parents=True, exist_ok=True)
    gecici = HEDEF.with_suffix(".part")
    with urllib.request.urlopen(ADRES, timeout=120) as yanit, open(gecici, "wb") as f:
        while parca := yanit.read(1 << 16):
            f.write(parca)
    gecici.replace(HEDEF)
    veri = HEDEF.read_bytes()
    satir = sum(1 for s in veri.decode("utf-8").splitlines() if s.strip())
    kayit = {"adres": ADRES, "surum": SURUM, "lisans": "CC BY-SA 4.0", "boyut": len(veri), "satir": satir,
             "sha256": hashlib.sha256(veri).hexdigest(), "indirme": dt.datetime.now().astimezone().isoformat()}
    (HEDEF.parent / "kaynak.json").write_text(json.dumps(kayit, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(kayit, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
