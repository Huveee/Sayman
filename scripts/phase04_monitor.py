"""Phase 4 scheduler: runs the monitoring rules up to the day the clock shows and adds the new alerts to the store.

The store (data/izleme/) keeps the last day looked at and the alerts reported up to that day. A second run with the
same clock adds nothing; as the clock advances, only the alerts that start on new days are added. The clock cannot
be turned back.

This script does not change data/saat.json: if --gun is given it treats that day as "today" (it cannot pass the
data end in saat.json); if not, it uses the day in saat.json.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase04_monitor.py
    .venv\\Scripts\\python -I scripts\\phase04_monitor.py --gun 2026-10-15
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.monitoring.scheduler import AlertStore, AlertStoreError, run_scheduler  # noqa: E402
from sayman.data.table_io import read_csv  # noqa: E402
from sayman.data.clock import Clock, read_clock  # noqa: E402


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--gun", type=dt.date.fromisoformat, help="bugün sayılacak gün (YYYY-AA-GG)")
    parser.add_argument("--depo", type=Path, default=ROOT / "data" / "izleme")
    args = parser.parse_args()
    clock = read_clock(ROOT / "data" / "saat.json")
    if args.gun:
        clock = Clock(args.gun, clock.veri_bitis)
    try:
        result = run_scheduler(read_csv(ROOT / "data" / "uretilen"), clock, AlertStore(args.depo))
    except AlertStoreError as e:
        print(f"Durduruldu: {e}")
        return 1
    new_alerts = result["yeni"]
    if result["onceki_son_gun"] == result["son_gun"]:
        print(f"Bakılacak yeni gün yok (son bakılan gün {result['son_gun']}); depoda {result['toplam']} uyarı.")
        return 0
    print(f"Bakılan günler: {result['onceki_son_gun'] or 'başlangıç'} sonrası .. {result['son_gun']}")
    print(f"Yeni uyarı: {len(new_alerts)} ({dict(sorted(Counter(u['onem'] for u in new_alerts).items()))}); depoda toplam "
          f"{result['toplam']}")
    for u in new_alerts[-10:]:
        print(f"  {u['gun']} [{u['onem']}] {u['baslik']}: {u['aciklama']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
