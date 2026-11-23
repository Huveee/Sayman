"""Phase 2: builds the data warehouse and computes and reports the metrics for "now" (`simdi`).

Inputs:
    data/uretilen/*.csv           (scripts/phase01_generate_data.py)
    reports/faz1_veri.json        digest of the data; must equal the one computed here
    data/saat.json                simulation clock
Outputs:
    data/ambar/sayman.duckdb      the warehouse the tools open read-only
    reports/faz2_metrikler.json   metric summaries for now and the duration of each metric

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase02_build_warehouse.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.warehouse import metrics  # noqa: E402
from sayman.warehouse.loader import build_warehouse, open_warehouse  # noqa: E402
from sayman.data.table_io import read_csv, data_digest  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

GENERATED = ROOT / "data" / "uretilen"
WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"
DSO_YEAR_END = dt.date(2025, 12, 31)


def _json_default(value):
    if isinstance(value, (dt.date, Decimal)):
        return str(value)
    raise TypeError(type(value))


def _format_tl(cents: int) -> str:
    return f"{cents / 100:,.0f} TL".replace(",", ".")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    phase1_report = json.loads((ROOT / "reports" / "faz1_veri.json").read_text(encoding="utf-8"))
    digest = data_digest(read_csv(GENERATED))[0]
    if digest != phase1_report["veri_hash"]:
        print(f"data/uretilen Faz 1 raporundaki veriyle aynı değil ({digest[:12]} != {phase1_report['veri_hash'][:12]}). "
              "Önce scripts/phase01_generate_data.py çalıştırılmalı.")
        return 1

    start = time.time()
    build_warehouse(GENERATED, WAREHOUSE, data_digest=digest).close()
    build_s = round(time.time() - start, 2)

    now = read_clock(ROOT / "data" / "saat.json").simdi
    con = open_warehouse(WAREHOUSE)
    results, durations = {}, {}
    for name, func in metrics.METRICS.items():
        start = time.perf_counter()
        results[name] = func(con, now)
        durations[name] = round(time.perf_counter() - start, 3)
    dso_2025 = metrics.dso(con, DSO_YEAR_END)
    con.close()

    y, n = results["yaslandirma"], results["nakit_akis_tahmini"]
    report = {
        "veri_hash": digest,
        "ambar": str(WAREHOUSE.relative_to(ROOT)).replace("\\", "/"),
        "ambar_kurulum_s": build_s,
        "tarih": now,
        "nakit_pozisyonu": results["nakit_pozisyonu"],
        "yaslandirma_toplam": y["toplam"],
        "yaslandirma_musteri_sayisi": len(y["musteriler"]),
        "dso": results["dso"],
        "dso_2025_yil_sonu": dso_2025,
        "geciken_alacaklar": {"fatura_sayisi": len(results["geciken_alacaklar"]["faturalar"]),
                              "toplam_try_kurus": results["geciken_alacaklar"]["toplam_try_kurus"],
                              "en_buyuk_5": results["geciken_alacaklar"]["faturalar"][:5]},
        "yaklasan_odemeler": {"fatura_sayisi": len(results["yaklasan_odemeler"]["odemeler"]),
                              "toplam_kurus": results["yaklasan_odemeler"]["toplam_kurus"]},
        "kur_riski": results["kur_riski"],
        "nakit_akis_tahmini": n,
        "metrik_suresi_s": durations,
    }
    output = ROOT / "reports" / "faz2_metrikler.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")

    print(f"Ambar kuruldu: {WAREHOUSE.relative_to(ROOT)} ({build_s} s), veri özeti {digest[:12]}")
    print(f"Tarih: {now}")
    print(f"  Nakit: {_format_tl(results['nakit_pozisyonu']['toplam_try_kurus'])}")
    print(f"  Açık alacak: {_format_tl(y['toplam']['toplam_kurus'])} ({len(y['musteriler'])} müşteri)")
    print(f"  DSO: {results['dso']['dso_gun']} gün (31.12.2025: {dso_2025['dso_gun']} gün)")
    print(f"  Geciken alacak: {_format_tl(report['geciken_alacaklar']['toplam_try_kurus'])} "
          f"({report['geciken_alacaklar']['fatura_sayisi']} fatura)")
    print(f"  30 gün içindeki ödemeler: {_format_tl(report['yaklasan_odemeler']['toplam_kurus'])} "
          f"({report['yaklasan_odemeler']['fatura_sayisi']} fatura)")
    for d in results["kur_riski"]["dovizler"]:
        print(f"  Kur riski {d['para_birimi']}: {_format_tl(d['toplam_try_kurus'])}, %10 kur değişimi {_format_tl(d['yuzde_10_etki_kurus'])}")
    print(f"  13 hafta sonra, yalnızca bilinen kalemlerle (maaş, vergi, yeni satışlar hariç): "
          f"{_format_tl(n['haftalar'][-1]['bilinen_kalemlerle_bakiye_kurus'])}; şüpheli alacak {_format_tl(n['supheli_alacak_kurus'])}")
    print("  Süreler (s): " + ", ".join(f"{name} {s}" for name, s in durations.items()))
    print(f"Yazıldı: {output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
