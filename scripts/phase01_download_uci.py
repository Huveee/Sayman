"""UCI Online Retail II veri setini indirir ve okunabilir bir ara dosyaya çevirir.

Kaynak: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository.
https://doi.org/10.24432/C5CG6D. Lisans: CC BY 4.0.

Çıktılar (git'e girmez, bu betik yeniden üretir):
    data/ham/online_retail_ii.zip       indirilen dosya
    data/ham/online_retail_II.xlsx      zip içindeki Excel
    data/ara/uci_ham.csv.gz             iki sayfanın bütün satırları, metin olarak
    data/ara/uci_kaynak.json            adres, boyut ve SHA-256 özetleri

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz1_uci_indir.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import time
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.data.table_io import dosya_hash  # noqa: E402
from sayman.data.uci import ham_csv_yaz, xlsx_oku  # noqa: E402

ADRES = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
EXCEL_ADI = "online_retail_II.xlsx"


def indir(adres: str, hedef: Path) -> int:
    hedef.parent.mkdir(parents=True, exist_ok=True)
    gecici = hedef.with_suffix(".part")
    istek = urllib.request.Request(adres, headers={"User-Agent": "sayman-faz1"})
    boyut = 0
    with urllib.request.urlopen(istek, timeout=120) as yanit, open(gecici, "wb") as f:
        while parca := yanit.read(1 << 20):
            f.write(parca)
            boyut += len(parca)
            print(f"\r  {boyut / 1e6:.1f} MB", end="", flush=True)
    print()
    gecici.replace(hedef)
    return boyut


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ham_klasor, ara_klasor = KOK / "data" / "ham", KOK / "data" / "ara"
    zip_yol, excel_yol = ham_klasor / "online_retail_ii.zip", ham_klasor / EXCEL_ADI

    if not zip_yol.exists():
        print(f"İndiriliyor: {ADRES}")
        indir(ADRES, zip_yol)
    with zipfile.ZipFile(zip_yol) as z:
        adlar = z.namelist()
        if EXCEL_ADI not in adlar:
            print(f"Zip içinde {EXCEL_ADI} yok: {adlar}", file=sys.stderr)
            return 1
        with z.open(EXCEL_ADI) as kaynak, open(excel_yol, "wb") as hedef:  # yalnızca bilinen dosya, bilinen yola
            while parca := kaynak.read(1 << 20):
                hedef.write(parca)

    print("Excel okunuyor (birkaç dakika sürebilir)...")
    bas = time.time()
    sayac = Counter()

    def satirlar():
        for s in xlsx_oku(excel_yol):
            sayac[s["sayfa"]] += 1
            yield s

    csv_yol = ara_klasor / "uci_ham.csv.gz"
    toplam = ham_csv_yaz(satirlar(), csv_yol)
    bilgi = {
        "kaynak": "Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5CG6D",
        "lisans": "CC BY 4.0",
        "adres": ADRES,
        "indirme_tarihi": dt.date.today().isoformat(),
        "zip_bayt": zip_yol.stat().st_size,
        "zip_sha256": dosya_hash(zip_yol),
        "excel_sha256": dosya_hash(excel_yol),
        "zip_icerigi": adlar,
        "sayfa_satirlari": dict(sayac),
        "toplam_satir": toplam,
        "ara_dosya_sha256": dosya_hash(csv_yol),
        "okuma_suresi_s": round(time.time() - bas, 1),
    }
    (ara_klasor / "uci_kaynak.json").write_text(json.dumps(bilgi, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(bilgi, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
