"""TCMB EVDS'den günlük döviz alış kurlarını indirir: data/kaynak/tcmb_kurlar.csv

Kullanım (proje kökünden, .env içinde EVDS_API_KEY dolu olmalı):
    .venv\\Scripts\\python scripts\\faz1_kur_indir.py --baslangic 2024-10-01 --bitis 2026-11-12

Anahtar yalnızca istek başlığında gönderilir; adrese, dosyalara ya da ekrana yazılmaz.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from dotenv import dotenv_values  # noqa: E402

from sayman.data.exchange_rates import SERILER, kurlar_yaz  # noqa: E402

ADRES = "https://evds3.tcmb.gov.tr/igmevdsms-dis/series={seriler}&startDate={bas}&endDate={bit}&type=json"


def indir(baslangic: dt.date, bitis: dt.date, anahtar: str) -> list[tuple[dt.date, str, Decimal]]:
    seriler = "-".join(SERILER.values())
    url = ADRES.format(seriler=seriler, bas=baslangic.strftime("%d-%m-%Y"), bit=bitis.strftime("%d-%m-%Y"))
    istek = urllib.request.Request(url, headers={"key": anahtar, "User-Agent": "sayman-faz1"})
    with urllib.request.urlopen(istek, timeout=60) as yanit:
        veri = json.loads(yanit.read().decode("utf-8"))
    satirlar = []
    for kayit in veri.get("items", []):
        tarih = dt.datetime.strptime(kayit["Tarih"], "%d-%m-%Y").date()
        for pb, seri in SERILER.items():
            deger = kayit.get(seri.replace(".", "_"))
            if deger in (None, "", "null"):
                continue  # hafta sonu ve tatillerde kur yayımlanmaz
            satirlar.append((tarih, pb, Decimal(str(deger)).normalize()))
    return satirlar


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ayri = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ayri.add_argument("--baslangic", type=dt.date.fromisoformat, default=dt.date(2024, 10, 1))
    ayri.add_argument("--bitis", type=dt.date.fromisoformat, default=dt.date.today())
    ayri.add_argument("--cikti", type=Path, default=KOK / "data" / "kaynak" / "tcmb_kurlar.csv")
    arg = ayri.parse_args()

    anahtar = (dotenv_values(KOK / ".env").get("EVDS_API_KEY") or "").strip()
    if not anahtar:
        print("EVDS_API_KEY .env dosyasında boş.", file=sys.stderr)
        return 2
    satirlar = indir(arg.baslangic, arg.bitis, anahtar)
    kurlar_yaz(satirlar, arg.cikti)
    for pb in SERILER:
        tarihler = [t for t, p, _ in satirlar if p == pb]
        print(f"{pb}: {len(tarihler)} gün, {min(tarihler)} - {max(tarihler)}")
    print(f"Yazıldı: {arg.cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
