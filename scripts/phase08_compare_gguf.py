"""Faz 8 adillik ve indirme denetimi: GGUF dosyalarını tensör tensör karşılaştırır.

Kullanım (proje kökünden; ölçüm sürerken çalıştırmayın, 5 GB'lık dosya okunur):
    .venv\\Scripts\\python -I scripts\\faz8_gguf_karsilastir.py taban --kaggle data\\egitim\\kaggle_cikti\\sayman-faz8-birlestir
    .venv\\Scripts\\python -I scripts\\faz8_gguf_karsilastir.py indirilen --gguf <yol> --kaggle <klasör>

taban: Ollama'daki temel modelin dosyası (qwen3.5:9b'nin ilk FROM satırı), Kaggle'da kendi yolumuzdan geçen temel modelin
özetiyle (taban_q4_ozet.json) karşılaştırılır. Rapor: reports/faz8_gguf_adillik.json
indirilen: indirilen ince ayarlı model dosyası, Kaggle'daki özetiyle (ayarli_q4_ozet.json) aynı mı (sha256, bayt ve
tensörler)? Temel model dosyası için --ozet taban_q4_ozet.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.training import gguf_summary  # noqa: E402

OLLAMA = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"


def ollama_dosyasi(model: str = "qwen3.5:9b") -> Path:
    r = subprocess.run([str(OLLAMA), "show", model, "--modelfile"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", check=True)
    froms = [s.split(" ", 1)[1].strip() for s in r.stdout.splitlines() if s.startswith("FROM ")]
    return Path(froms[0])  # ilki dil modeli, ikincisi görsel kodlayıcı


def sha256(yol: Path) -> str:
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for parca in iter(lambda: f.read(1 << 24), b""):
            h.update(parca)
    return h.hexdigest()


def taban(kaggle: Path) -> None:
    kendi = json.loads((kaggle / "taban_q4_ozet.json").read_text(encoding="utf-8"))
    yol = ollama_dosyasi()
    ollama_o = gguf_summary.ozet(str(yol))
    k = gguf_summary.karsilastir(ollama_o, kendi)
    sonuc = {"ollama_dosyasi": yol.name, "kaggle_rapor": json.loads((kaggle / "rapor.json").read_text(
        encoding="utf-8")).get("sabitler"), **k,
        "veri_farki": k["veri_farki"][:50], "veri_farki_sayisi": len(k["veri_farki"]),
        "ayni_mi": not (k["kv_farki"] or k["tur_farki"] or k["veri_farki"] or k["yalniz_ilkinde"]
                        or k["yalniz_ikincide"])}
    (KOK / "reports" / "faz8_gguf_adillik.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1),
                                                             encoding="utf-8")
    print(json.dumps({x: sonuc[x] for x in ("tensor", "ayni_tensor", "veri_farki_sayisi", "tur_farki", "kv_farki",
                                            "yalniz_ilkinde", "yalniz_ikincide", "ayni_mi")}, ensure_ascii=False,
                     indent=1))
    print("yazıldı: reports/faz8_gguf_adillik.json")


def indirilen(gguf: Path, kaggle: Path, ozet_adi: str = "ayarli_q4_ozet.json") -> None:
    beklenen = json.loads((kaggle / ozet_adi).read_text(encoding="utf-8"))
    h = sha256(gguf)
    k = gguf_summary.karsilastir(beklenen, gguf_summary.ozet(str(gguf)))
    bayt = beklenen["dosya"].get("bayt", gguf.stat().st_size)
    tamam = (h == beklenen["dosya"]["sha256"] and gguf.stat().st_size == bayt
             and not (k["veri_farki"] or k["tur_farki"] or k["kv_farki"]))
    print({"sha256": h, "sha256_ayni": h == beklenen["dosya"]["sha256"], "bayt": gguf.stat().st_size,
           "tensor": k["tensor"], "ayni_tensor": k["ayni_tensor"], "tamam": tamam})
    if not tamam:
        raise SystemExit("indirilen dosya Kaggle'daki özetle aynı değil")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8: GGUF adillik ve indirme denetimi")
    alt = ap.add_subparsers(dest="komut", required=True)
    t = alt.add_parser("taban")
    t.add_argument("--kaggle", type=Path, required=True)
    i = alt.add_parser("indirilen")
    i.add_argument("--gguf", type=Path, required=True)
    i.add_argument("--kaggle", type=Path, required=True)
    i.add_argument("--ozet", default="ayarli_q4_ozet.json", help="temel model için taban_q4_ozet.json")
    a = ap.parse_args()
    if a.komut == "taban":
        taban(a.kaggle)
    else:
        indirilen(a.gguf, a.kaggle, a.ozet)


if __name__ == "__main__":
    main()
