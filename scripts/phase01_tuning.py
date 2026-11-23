"""Kalibrasyon ayar denemesi: üretici ayarlarından yalnızca izin verilenleri değiştirip bantlara bakar.

Calibration tuning trial: changes only the permitted generator settings and checks the bands.
(The first line above feeds the argparse description through `__doc__.splitlines()[0]`, so it stays as is.)

Rules (written before the first run on the real data):
- May change: the due-date mix (`vade_yurtici`, `vade_ihracat`), the payment profiles (`profiller`), the bad-debt
  customer share.
- May not change: the calibration bands and metric definitions (data/kaynak/kalibrasyon.json), the DSO formula
  (2025 year-end receivables, POS in the denominator), the Atradius delay buckets (`gecikme_dilimleri`).
- Every trial is appended to reports/faz1_ayar_denemeleri.json; the chosen setting must also stay inside the
  bands with at least two seeds other than 42.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase01_tuning.py --not "ilk ölçüm"
    .venv\\Scripts\\python -I scripts\\phase01_tuning.py --ayar "{\\"batik_musteri_payi\\": 0.03}" --tohumlar 42,43,44 --not "..."
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.data import calibration  # noqa: E402
from sayman.data.table_io import data_digest  # noqa: E402
from sayman.data.exchange_rates import read_rates  # noqa: E402
from sayman.data.uci import read_raw_csv, clean  # noqa: E402
from sayman.data.generator import GeneratorSettings, generate  # noqa: E402

TUNABLE = {"vade_yurtici", "vade_ihracat", "profiller", "batik_musteri_payi"}
TRIAL_LOG = ROOT / "reports" / "faz1_ayar_denemeleri.json"


def _to_tuple(value):
    return tuple(_to_tuple(v) for v in value) if isinstance(value, list) else value


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ayar", default="{}", help="JSON: değiştirilecek ayarlar")
    parser.add_argument("--tohumlar", default="42")
    parser.add_argument("--not", dest="aciklama", default="")
    args = parser.parse_args()

    changes = json.loads(args.ayar)
    forbidden = set(changes) - TUNABLE
    if forbidden:
        print(f"Bu ayarlar kalibrasyonda değiştirilemez: {sorted(forbidden)}", file=sys.stderr)
        return 2
    changes = {k: _to_tuple(v) for k, v in changes.items()}
    source = json.loads((ROOT / "data" / "kaynak" / "kalibrasyon.json").read_text(encoding="utf-8"))

    start = time.time()
    line_items = clean(read_raw_csv(ROOT / "data" / "ara" / "uci_ham.csv.gz")).line_items
    rates = read_rates(ROOT / "data" / "kaynak" / "tcmb_kurlar.csv")
    results = []
    for seed in (int(t) for t in args.tohumlar.split(",")):
        settings = dataclasses.replace(GeneratorSettings(), tohum=seed, **changes)
        T = generate(line_items, rates, settings).tables
        measurement = calibration.measure(T, settings.veri_bitis, 2025)
        bands = calibration.compare_with_bands(measurement, source)
        results.append({"tohum": seed, "veri_hash": data_digest(T)[0], "olcum": measurement, "bantlar": bands,
                         "hepsi_bantta": all(b["tamam"] for b in bands)})
        print(f"tohum {seed}: " + ", ".join(f"{b['ad']}={b['deger']}{'' if b['tamam'] else ' (BANT DIŞI)'}" for b in bands))

    records = json.loads(TRIAL_LOG.read_text(encoding="utf-8")) if TRIAL_LOG.exists() else []
    records.append({"sira": len(records) + 1, "zaman": dt.datetime.now().isoformat(timespec="seconds"),
                     "not": args.aciklama, "degisiklik": changes, "sonuclar": results,
                     "sure_s": round(time.time() - start, 1)})
    TRIAL_LOG.write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Deneme {len(records)} kaydedildi: {TRIAL_LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
