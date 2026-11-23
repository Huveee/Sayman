"""Faz 4 zamanlayıcısı: saatin gösterdiği güne kadar izleme kurallarını çalıştırır, yeni uyarıları depoya ekler.

Depo (data/izleme/) son bakılan günü ve o güne kadar bildirilen uyarıları tutar. Aynı saatle ikinci çalışma hiçbir
şey eklemez; saat ilerledikçe yalnızca yeni günlerde başlayan uyarılar eklenir. Saat geri alınamaz.

Bu betik data/saat.json'u değiştirmez: --gun verilirse o günü "bugün" sayar (saat.json'daki veri bitişini
geçemez), verilmezse saat.json'daki günü kullanır.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz4_izle.py
    .venv\\Scripts\\python -I scripts\\faz4_izle.py --gun 2026-10-15
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.monitoring.scheduler import Depo, DepoHatasi, calistir  # noqa: E402
from sayman.data.table_io import csv_oku  # noqa: E402
from sayman.data.clock import Saat, saat_oku  # noqa: E402


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--gun", type=dt.date.fromisoformat, help="bugün sayılacak gün (YYYY-AA-GG)")
    ap.add_argument("--depo", type=Path, default=KOK / "data" / "izleme")
    arg = ap.parse_args()
    saat = saat_oku(KOK / "data" / "saat.json")
    if arg.gun:
        saat = Saat(arg.gun, saat.veri_bitis)
    try:
        sonuc = calistir(csv_oku(KOK / "data" / "uretilen"), saat, Depo(arg.depo))
    except DepoHatasi as e:
        print(f"Durduruldu: {e}")
        return 1
    yeni = sonuc["yeni"]
    if sonuc["onceki_son_gun"] == sonuc["son_gun"]:
        print(f"Bakılacak yeni gün yok (son bakılan gün {sonuc['son_gun']}); depoda {sonuc['toplam']} uyarı.")
        return 0
    print(f"Bakılan günler: {sonuc['onceki_son_gun'] or 'başlangıç'} sonrası .. {sonuc['son_gun']}")
    print(f"Yeni uyarı: {len(yeni)} ({dict(sorted(Counter(u['onem'] for u in yeni).items()))}); depoda toplam "
          f"{sonuc['toplam']}")
    for u in yeni[-10:]:
        print(f"  {u['gun']} [{u['onem']}] {u['baslik']}: {u['aciklama']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
