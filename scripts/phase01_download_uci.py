"""Downloads the UCI Online Retail II dataset and converts it to a readable intermediate file.

Source: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository.
https://doi.org/10.24432/C5CG6D. License: CC BY 4.0.

Outputs (not committed to git, this script regenerates them):
    data/ham/online_retail_ii.zip       the downloaded file
    data/ham/online_retail_II.xlsx      the Excel file inside the zip
    data/ara/uci_ham.csv.gz             all rows of both sheets, as text
    data/ara/uci_kaynak.json            URL, size and SHA-256 digests

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase01_download_uci.py
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.data.table_io import file_digest  # noqa: E402
from sayman.data.uci import write_raw_csv, read_xlsx  # noqa: E402

URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
EXCEL_NAME = "online_retail_II.xlsx"


def download(url: str, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(".part")
    request = urllib.request.Request(url, headers={"User-Agent": "sayman-faz1"})
    size = 0
    with urllib.request.urlopen(request, timeout=120) as response, open(temp_path, "wb") as f:
        while chunk := response.read(1 << 20):
            f.write(chunk)
            size += len(chunk)
            print(f"\r  {size / 1e6:.1f} MB", end="", flush=True)
    print()
    temp_path.replace(target)
    return size


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    raw_dir, interim_dir = ROOT / "data" / "ham", ROOT / "data" / "ara"
    zip_path, excel_path = raw_dir / "online_retail_ii.zip", raw_dir / EXCEL_NAME

    if not zip_path.exists():
        print(f"İndiriliyor: {URL}")
        download(URL, zip_path)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if EXCEL_NAME not in names:
            print(f"Zip içinde {EXCEL_NAME} yok: {names}", file=sys.stderr)
            return 1
        with z.open(EXCEL_NAME) as source, open(excel_path, "wb") as target:  # only the known file, to the known path
            while chunk := source.read(1 << 20):
                target.write(chunk)

    print("Excel okunuyor (birkaç dakika sürebilir)...")
    start = time.time()
    counter = Counter()

    def rows():
        for s in read_xlsx(excel_path):
            counter[s["sayfa"]] += 1
            yield s

    csv_path = interim_dir / "uci_ham.csv.gz"
    total = write_raw_csv(rows(), csv_path)
    info = {
        "kaynak": "Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C5CG6D",
        "lisans": "CC BY 4.0",
        "adres": URL,
        "indirme_tarihi": dt.date.today().isoformat(),
        "zip_bayt": zip_path.stat().st_size,
        "zip_sha256": file_digest(zip_path),
        "excel_sha256": file_digest(excel_path),
        "zip_icerigi": names,
        "sayfa_satirlari": dict(counter),
        "toplam_satir": total,
        "ara_dosya_sha256": file_digest(csv_path),
        "okuma_suresi_s": round(time.time() - start, 1),
    }
    (interim_dir / "uci_kaynak.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
