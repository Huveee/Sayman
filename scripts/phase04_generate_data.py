"""Faz 4: izleme kurallarını ölçmek için ek veri setleri üretir.

- tohum42_temiz: projenin şirketiyle (tohum 42) birebir aynı, yalnızca olaylar kapalı. Yanlış alarm burada sayılır.
- tohum43, tohum44 (olaylı) ve tohum43_temiz, tohum44_temiz: kurallar tohum 42'de dondurulduktan sonra yalnızca
  bir kez çalıştırılan, hiç bakılmamış veri setleri.

Tohum 42'nin olaylı hâli zaten data/uretilen'dedir; bu betik onu yeniden üretip özetinin (hash) Faz 1 raporuyla
aynı olduğunu kontrol eder, kopyalamaz.

Girdiler: data/ara/uci_ham.csv.gz, data/kaynak/tcmb_kurlar.csv, reports/faz1_veri.json
Çıktı:    data/izleme_veri/<ad>/*.csv, olaylı setlerde ayrıca olaylar.json (cevap anahtarı; izleme kodu okumaz)
          data/izleme_veri/setler.json (her setin tohumu, olay durumu, özeti, satır sayıları)

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz4_veri_uret.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.data.table_io import csv_yaz, veri_hash  # noqa: E402
from sayman.data.exchange_rates import kurlar_oku  # noqa: E402
from sayman.data.uci import ham_csv_oku, temizle  # noqa: E402
from sayman.data.generator import Ayarlar, uret  # noqa: E402

CIKTI = KOK / "data" / "izleme_veri"
SETLER = [("tohum42_temiz", 42, False), ("tohum43", 43, True), ("tohum43_temiz", 43, False),
          ("tohum44", 44, True), ("tohum44_temiz", 44, False)]


def _json(deger):
    if isinstance(deger, (dt.date, Decimal)):
        return str(deger)
    raise TypeError(type(deger))


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    bas = time.time()
    kalemler = temizle(ham_csv_oku(KOK / "data" / "ara" / "uci_ham.csv.gz")).kalemler
    kurlar = kurlar_oku(KOK / "data" / "kaynak" / "tcmb_kurlar.csv")
    rapor1 = json.loads((KOK / "reports" / "faz1_veri.json").read_text(encoding="utf-8"))

    ana = uret(kalemler, kurlar, Ayarlar(tohum=42))
    ozet = veri_hash(ana.tablolar)[0]
    if ozet != rapor1["veri_hash"]:
        print(f"Tohum 42 Faz 1'deki veriyi vermedi ({ozet[:12]} != {rapor1['veri_hash'][:12]}); durduruldu.")
        return 1
    print(f"tohum42 (olaylı): özet Faz 1 ile aynı ({ozet[:12]}), data/uretilen kullanılır")

    setler = {"tohum42": {"tohum": 42, "olaylar": True, "klasor": "data/uretilen", "veri_hash": ozet,
                          "olay_sayisi": len(ana.anahtar["olaylar"])}}
    for ad, tohum, olayli in SETLER:
        u = uret(kalemler, kurlar, Ayarlar(tohum=tohum, olaylar_acik=olayli))
        klasor = CIKTI / ad
        klasor.mkdir(parents=True, exist_ok=True)
        csv_yaz(u.tablolar, klasor)
        if olayli:
            (klasor / "olaylar.json").write_text(
                json.dumps(u.anahtar, ensure_ascii=False, indent=1, default=_json) + "\n", encoding="utf-8")
        setler[ad] = {"tohum": tohum, "olaylar": olayli, "klasor": f"data/izleme_veri/{ad}",
                      "veri_hash": veri_hash(u.tablolar)[0], "veri_baslangic": u.veri_baslangic,
                      "tablo_satirlari": {t: len(v) for t, v in u.tablolar.items()},
                      "olay_sayisi": len(u.anahtar["olaylar"]) if olayli else 0}
        print(f"{ad}: {setler[ad]['veri_hash'][:12]}, {setler[ad]['olay_sayisi']} olay")
    (CIKTI / "setler.json").write_text(json.dumps(setler, ensure_ascii=False, indent=2, default=_json) + "\n",
                                       encoding="utf-8")
    print(f"Süre: {time.time() - bas:.0f} s. Yazıldı: {CIKTI.relative_to(KOK)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
