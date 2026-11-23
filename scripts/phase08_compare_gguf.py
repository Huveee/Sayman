"""Phase 8 fairness and download check: compares GGUF files tensor by tensor.

Usage (from the project root; do not run while a measurement is running, a 5 GB file is read):
    .venv\\Scripts\\python -I scripts\\phase08_compare_gguf.py taban --kaggle data\\egitim\\kaggle_cikti\\sayman-faz8-birlestir
    .venv\\Scripts\\python -I scripts\\phase08_compare_gguf.py indirilen --gguf <path> --kaggle <folder>

taban: the file of the base model in Ollama (the first FROM line of qwen3.5:9b) is compared with the summary
(taban_q4_ozet.json) of the base model that went through our own path on Kaggle. Report: reports/faz8_gguf_adillik.json
indirilen: is the downloaded fine-tuned model file the same as its summary on Kaggle (ayarli_q4_ozet.json) (sha256, bytes
and tensors)? For the base model file use --ozet taban_q4_ozet.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.training import gguf_summary  # noqa: E402

OLLAMA = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"


def ollama_model_file(model: str = "qwen3.5:9b") -> Path:
    r = subprocess.run([str(OLLAMA), "show", model, "--modelfile"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", check=True)
    froms = [s.split(" ", 1)[1].strip() for s in r.stdout.splitlines() if s.startswith("FROM ")]
    return Path(froms[0])  # the first is the language model, the second the vision encoder


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    return h.hexdigest()


def compare_base(kaggle: Path) -> None:
    own_summary = json.loads((kaggle / "taban_q4_ozet.json").read_text(encoding="utf-8"))
    path = ollama_model_file()
    ollama_summary = gguf_summary.summarize(str(path))
    k = gguf_summary.compare(ollama_summary, own_summary)
    result = {"ollama_dosyasi": path.name, "kaggle_rapor": json.loads((kaggle / "rapor.json").read_text(
        encoding="utf-8")).get("sabitler"), **k,
        "veri_farki": k["veri_farki"][:50], "veri_farki_sayisi": len(k["veri_farki"]),
        "ayni_mi": not (k["kv_farki"] or k["tur_farki"] or k["veri_farki"] or k["yalniz_ilkinde"]
                        or k["yalniz_ikincide"])}
    (ROOT / "reports" / "faz8_gguf_adillik.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                                             encoding="utf-8")
    print(json.dumps({x: result[x] for x in ("tensor", "ayni_tensor", "veri_farki_sayisi", "tur_farki", "kv_farki",
                                            "yalniz_ilkinde", "yalniz_ikincide", "ayni_mi")}, ensure_ascii=False,
                     indent=1))
    print("yazıldı: reports/faz8_gguf_adillik.json")


def verify_download(gguf: Path, kaggle: Path, summary_name: str = "ayarli_q4_ozet.json") -> None:
    expected = json.loads((kaggle / summary_name).read_text(encoding="utf-8"))
    h = sha256(gguf)
    k = gguf_summary.compare(expected, gguf_summary.summarize(str(gguf)))
    byte_count = expected["dosya"].get("bayt", gguf.stat().st_size)
    ok = (h == expected["dosya"]["sha256"] and gguf.stat().st_size == byte_count
             and not (k["veri_farki"] or k["tur_farki"] or k["kv_farki"]))
    print({"sha256": h, "sha256_ayni": h == expected["dosya"]["sha256"], "bayt": gguf.stat().st_size,
           "tensor": k["tensor"], "ayni_tensor": k["ayni_tensor"], "tamam": ok})
    if not ok:
        raise SystemExit("indirilen dosya Kaggle'daki özetle aynı değil")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8: GGUF adillik ve indirme denetimi")
    subparsers = ap.add_subparsers(dest="komut", required=True)
    t = subparsers.add_parser("taban")
    t.add_argument("--kaggle", type=Path, required=True)
    i = subparsers.add_parser("indirilen")
    i.add_argument("--gguf", type=Path, required=True)
    i.add_argument("--kaggle", type=Path, required=True)
    i.add_argument("--ozet", default="ayarli_q4_ozet.json", help="temel model için taban_q4_ozet.json")
    a = ap.parse_args()
    if a.komut == "taban":
        compare_base(a.kaggle)
    else:
        verify_download(a.gguf, a.kaggle, a.ozet)


if __name__ == "__main__":
    main()
