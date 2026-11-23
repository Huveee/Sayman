"""Faz 7 soru setini üretir ve dondurur: araç soruları, yetkisiz denemeler, belge soruları, hedefler.

Beklenen değerler araç fonksiyonları soran kişinin kapsamında doğrudan çalıştırılarak bulunur (kapı, model ve
doğrulayıcı kullanılmaz). Aynı ambar ve aynı Faz 5 soru setiyle her çalıştırmada aynı dosya üretilir.

Girdi:  data/ambar/sayman.duckdb, data/saat.json, data/belge/sorular.json
Çıktı:  reports/faz7_soru_seti.json

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\phase07_generate_questions.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK))

from sayman.data.clock import saat_oku  # noqa: E402
from tests import faz7_sorular as fs  # noqa: E402

CIKTI = KOK / "reports" / "faz7_soru_seti.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    saat = saat_oku(KOK / "data" / "saat.json")
    faz5 = json.loads((KOK / "data" / "belge" / "sorular.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as gecici:
        o = fs.Ornekleyici(KOK / "data" / "ambar" / "sayman.duckdb", saat.simdi, Path(gecici))
        try:
            sorular = fs.soru_seti(o, faz5)
            varliklar = {k: sorted(v) for k, v in o.varliklar.items()}
        finally:
            o.kapat()
    kimlikler = [s["soru_id"] for s in sorular]
    assert len(kimlikler) == len(set(kimlikler)), "soru kimlikleri tekil olmalı"
    sayilar = Counter((s["set"], s["bolum"]) for s in sorular)
    veri = {
        "aciklama": "Faz 7 soru seti. Gerçek modelle hiçbir çalıştırmadan önce üretildi ve donduruldu; hedefler ve "
                    "hata türleri aynı anda yazıldı. Üretim: scripts/phase07_generate_questions.py, şablonlar: tests/phase07_questions.py.",
        "yazilma_tarihi": fs.YAZILMA_TARIHI, "simulasyon_gunu": saat.simdi.isoformat(),
        "ozet": fs.ozet(sorular),
        "sayilar": {f"{s}/{b}": n for (s, b), n in sorted(sayilar.items())},
        "sablonlar": {sb.ad: {"set": sb.set, "adet": sb.adet, "kaliplar": list(sb.kaliplar),
                              "araclar": sorted({a["arac"] for s in sorular if s.get("sablon") == sb.ad
                                                 for a in s["alternatifler"]})} for sb in fs.SABLONLAR},
        "hedefler": fs.HEDEFLER, "hata_turleri": fs.HATA_TURLERI,
        "varliklar": varliklar,
        "sorular": sorular,
    }
    CIKTI.write_text(json.dumps(veri, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(veri["sayilar"], ensure_ascii=False, indent=1))
    print("özet", veri["ozet"])
    print("araç dağılımı (saklanan):", dict(Counter(s["alternatifler"][0]["arac"] for s in sorular
                                                     if s["bolum"] == "arac" and s["set"] == "saklanan")))
    print("rol dağılımı:", dict(Counter((s["set"], s["bolum"], s["rol"]) for s in sorular)))


if __name__ == "__main__":
    main()
