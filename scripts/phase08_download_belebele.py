"""Downloads the Turkish file of Belebele (Phase 8 forgetting test).

Source: Bandarkar et al. (2023). The Belebele Benchmark. Hugging Face facebook/belebele,
data/tur_Latn.jsonl. License: CC BY-SA 4.0. The file is downloaded from a fixed revision (commit).

Outputs (not committed to git, this script regenerates them):
    data/ham/belebele/tur_Latn.jsonl    the downloaded file
    data/ham/belebele/kaynak.json       address, revision, size and SHA-256 digest

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08_download_belebele.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "7899cdfa4e1e0d733fd77c848e2c273cb1d32be2"
URL = f"https://huggingface.co/datasets/facebook/belebele/resolve/{REVISION}/data/tur_Latn.jsonl"
TARGET = ROOT / "data" / "ham" / "belebele" / "tur_Latn.jsonl"


def main() -> None:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    temp_path = TARGET.with_suffix(".part")
    with urllib.request.urlopen(URL, timeout=120) as response, open(temp_path, "wb") as f:
        while chunk := response.read(1 << 16):
            f.write(chunk)
    temp_path.replace(TARGET)
    data = TARGET.read_bytes()
    line_count = sum(1 for s in data.decode("utf-8").splitlines() if s.strip())
    record = {"adres": URL, "surum": REVISION, "lisans": "CC BY-SA 4.0", "boyut": len(data), "satir": line_count,
             "sha256": hashlib.sha256(data).hexdigest(), "indirme": dt.datetime.now().astimezone().isoformat()}
    (TARGET.parent / "kaynak.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
