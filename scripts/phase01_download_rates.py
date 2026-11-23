"""TCMB EVDS'den günlük döviz alış kurlarını indirir: data/kaynak/tcmb_kurlar.csv

Downloads the daily foreign-exchange buying rates from the CBRT (TCMB) EVDS: data/kaynak/tcmb_kurlar.csv
(The first line above feeds the argparse description through `__doc__.splitlines()[0]`, so it stays as is.)

Usage (from the project root, EVDS_API_KEY must be filled in .env):
    .venv\\Scripts\\python scripts\\phase01_download_rates.py --baslangic 2024-10-01 --bitis 2026-11-12

The key is sent only in the request header; it is never written to the URL, files or the screen.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from dotenv import dotenv_values  # noqa: E402

from sayman.data.exchange_rates import EVDS_SERIES, write_rates  # noqa: E402

EVDS_URL_TEMPLATE = "https://evds3.tcmb.gov.tr/igmevdsms-dis/series={seriler}&startDate={bas}&endDate={bit}&type=json"


def download(start: dt.date, end: dt.date, api_key: str) -> list[tuple[dt.date, str, Decimal]]:
    series_ids = "-".join(EVDS_SERIES.values())
    url = EVDS_URL_TEMPLATE.format(seriler=series_ids, bas=start.strftime("%d-%m-%Y"), bit=end.strftime("%d-%m-%Y"))
    request = urllib.request.Request(url, headers={"key": api_key, "User-Agent": "sayman-faz1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    rows = []
    for record in data.get("items", []):
        date = dt.datetime.strptime(record["Tarih"], "%d-%m-%Y").date()
        for currency, series_code in EVDS_SERIES.items():
            value = record.get(series_code.replace(".", "_"))
            if value in (None, "", "null"):
                continue  # no rate is published on weekends and holidays
            rows.append((date, currency, Decimal(str(value)).normalize()))
    return rows


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baslangic", type=dt.date.fromisoformat, default=dt.date(2024, 10, 1))
    parser.add_argument("--bitis", type=dt.date.fromisoformat, default=dt.date.today())
    parser.add_argument("--cikti", type=Path, default=ROOT / "data" / "kaynak" / "tcmb_kurlar.csv")
    args = parser.parse_args()

    api_key = (dotenv_values(ROOT / ".env").get("EVDS_API_KEY") or "").strip()
    if not api_key:
        print("EVDS_API_KEY .env dosyasında boş.", file=sys.stderr)
        return 2
    rows = download(args.baslangic, args.bitis, api_key)
    write_rates(rows, args.cikti)
    for currency in EVDS_SERIES:
        dates = [t for t, p, _ in rows if p == currency]
        print(f"{currency}: {len(dates)} gün, {min(dates)} - {max(dates)}")
    print(f"Yazıldı: {args.cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
