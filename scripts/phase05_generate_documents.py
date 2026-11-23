"""Faz 5: belge kütüphanesini ve soru setini üretir, özetlerini rapora yazar.

Sentetik belgeler şirket verisinden (data/uretilen) türetilir: tedarikçi ve müşteri sözleşmelerindeki vade, kredi
limiti, çek gibi bilgiler veriyle tutarlıdır. Sorular tests/faz5_sorular.py'deki kalıplardan üretilir. Kütüphaneye
ayrıca üç kanun karıştırıcı belge olarak eklenir (soru almazlar).

Rapordaki özetler (belge_ozeti: sentetik belgeler, soru_ozeti, kutuphane_ozeti: kanunlar dahil) soru seti
dondurulduğunda kaydedildi; testler yeniden üretilen setin özetini bununla karşılaştırır. Belge üreticisi ya da soru
kalıpları değişirse bu rapor da değişir ve fark git'te görünür.

Girdi:  data/uretilen/*.csv (scripts/faz1_veri_uret.py)
        data/ham/mevzuat/5941.pdf, 6098.pdf, 6102.pdf (scripts/faz5_indir.py)
Çıktı:  data/belge/belgeler.jsonl (kanunlar dahil), data/belge/sorular.json, reports/faz5_soru_seti.json

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz5_belge_uret.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "tests"))

import support.phase05_questions as fs  # noqa: E402

from sayman.documents import library  # noqa: E402
from sayman.documents.legislation import KANUNLAR, kanunlar  # noqa: E402
from sayman.documents.synthetic import uret  # noqa: E402
from sayman.data.table_io import csv_oku  # noqa: E402

URETILEN = KOK / "data" / "uretilen"
MEVZUAT = KOK / "data" / "ham" / "mevzuat"
CIKTI = KOK / "data" / "belge"
RAPOR = KOK / "reports" / "faz5_soru_seti.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    belgeler = uret(csv_oku(URETILEN))
    kanun = kanunlar(MEVZUAT)
    if len(kanun) != len(KANUNLAR):
        raise SystemExit(f"{MEVZUAT} içinde {len(KANUNLAR)} kanun PDF'i bekleniyordu, {len(kanun)} bulundu")
    library.yaz(belgeler + kanun, CIKTI / "belgeler.jsonl")
    sorular = fs.soru_seti(belgeler)
    (CIKTI / "sorular.json").write_text(json.dumps(sorular, ensure_ascii=False, indent=1), encoding="utf-8")

    def set_ozeti(s):
        return {"soru": len(s), "ture_gore": dict(sorted(Counter(x["tur"] for x in s).items())),
                "stile_gore": dict(sorted(Counter(x["stil"] for x in s).items())),
                "belge": len({x["belge_id"] for x in s}),
                "sans_tabani_isabet@5": fs.sans_tabani(s, belgeler)}

    rapor = {
        "aciklama": "Faz 5 belge kütüphanesi ve soru seti. Özetler soru seti dondurulduğunda kaydedildi.",
        "belge": len(belgeler), "madde": sum(len(b.maddeler) for b in belgeler),
        "ture_gore": dict(sorted(Counter(b.tur for b in belgeler).items())),
        "belge_ozeti": fs.belge_ozeti(belgeler), "soru_ozeti": fs.ozet(sorular),
        "karistirici_kanunlar": {b.belge_id: {"ad": b.baslik, "madde": len(b.maddeler), "pdf_sha256": hashlib.sha256(
            (MEVZUAT / f"{b.belge_id.split('-')[1]}.pdf").read_bytes()).hexdigest()} for b in kanun},
        "kutuphane": {"belge": len(belgeler) + len(kanun),
                      "madde": sum(len(b.maddeler) for b in belgeler + kanun)},
        "kutuphane_ozeti": fs.belge_ozeti(belgeler + kanun),
        "setler": {ad: set_ozeti(s) for ad, s in sorular.items()},
        "sans_tabani_aciklama": "Sözleşme sorularında arama yalnızca doğru sözleşmeyi bulup maddelerini rastgele "
                                "sıralasa beklenen isabet@5. Politika soruları dahil değildir.",
    }
    RAPOR.write_text(json.dumps(rapor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(rapor, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
