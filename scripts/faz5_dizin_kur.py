"""Faz 5: anlam araması için bütün parçaları ve soruları bge-m3 ile vektöre çevirir, Qdrant dizinini kurar.

Vektörler önbelleğe yazılır (data/belge/onbellek/gomme_bge-m3.npz); betik yarıda kalırsa yeniden çalıştırınca
kaldığı yerden devam eder. Soruların vektörleri de önbelleğe alınır: bu yalnızca vektör hesaplamasıdır, arama
sonucu üretmez.

Girdi:  data/belge/belgeler.jsonl, data/belge/sorular.json (scripts/faz5_belge_uret.py)
        Ollama'da bge-m3 modeli (ollama pull bge-m3)
Çıktı:  data/belge/onbellek/gomme_bge-m3.npz, data/belge/qdrant/ (Qdrant yerel mod dizini)

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz5_dizin_kur.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.belge import kutuphane  # noqa: E402
from sayman.belge.anlam import AnlamDizini, GommeOnbellegi  # noqa: E402

BELGE = KOK / "data" / "belge"
ONBELLEK = BELGE / "onbellek" / "gomme_bge-m3.npz"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    parcalar = kutuphane.parcalar(kutuphane.oku(BELGE / "belgeler.jsonl"))
    sorular = json.loads((BELGE / "sorular.json").read_text(encoding="utf-8"))
    onbellek = GommeOnbellegi(ONBELLEK)
    bas = time.perf_counter()

    def ilerleme(n, toplam):
        print(f"  {n}/{toplam} ({time.perf_counter() - bas:.0f} s)")

    soru_metni = [s["soru"] for set_ in sorular.values() for s in set_]
    print("sorular:", onbellek.doldur(soru_metni, ilerleme=ilerleme), "yeni vektör")
    print("parçalar:", onbellek.doldur([p.arama_metni for p in parcalar], ilerleme=ilerleme), "yeni vektör")
    print(f"toplam süre: {time.perf_counter() - bas:.0f} s")
    dizin = AnlamDizini(parcalar, onbellek, BELGE / "qdrant")
    print("Qdrant dizini:", dizin.istemci.count("belgeler").count, "nokta")
    dizin.kapat()


if __name__ == "__main__":
    main()
