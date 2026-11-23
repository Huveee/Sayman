"""Sentetik şirket verisini üretir, denetler ve kalibrasyon bantlarıyla karşılaştırır.

Generates the synthetic company data, audits it and compares it with the calibration bands.
(The first line above feeds the argparse description through `__doc__.splitlines()[0]`, so it stays as is.)

Inputs:
    data/ara/uci_ham.csv.gz         (scripts/phase01_download_uci.py)
    data/kaynak/tcmb_kurlar.csv     (scripts/phase01_download_rates.py)
    data/kaynak/kalibrasyon.json    (scripts/phase01_calibration_sources.py)
Outputs:
    data/uretilen/*.csv             tables the tools will read
    data/anahtar/olaylar.json       answer key (the tools do not read it)
    data/saat.json                  simulation clock
    reports/faz1_veri.json          summaries, counts, audits, calibration

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase01_generate_data.py
    .venv\\Scripts\\python -I scripts\\phase01_generate_data.py --yalniz-hash   (prints the digest without writing anything)
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.data import invariants, calibration  # noqa: E402
from sayman.data.table_io import write_csv, file_digest, data_digest  # noqa: E402
from sayman.data.exchange_rates import read_rates  # noqa: E402
from sayman.data.clock import Clock, write_clock  # noqa: E402
from sayman.data.uci import read_raw_csv, clean  # noqa: E402
from sayman.data.generator import GeneratorSettings, generate  # noqa: E402

INPUTS = {
    "uci_ham": ROOT / "data" / "ara" / "uci_ham.csv.gz",
    "tcmb_kurlar": ROOT / "data" / "kaynak" / "tcmb_kurlar.csv",
    "kalibrasyon": ROOT / "data" / "kaynak" / "kalibrasyon.json",
}


def _json_default(value):
    if isinstance(value, (dt.date, Decimal)):
        return str(value)
    raise TypeError(type(value))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tohum", type=int, default=GeneratorSettings.tohum)
    parser.add_argument("--yalniz-hash", action="store_true")
    args = parser.parse_args()
    settings = GeneratorSettings(tohum=args.tohum)

    start = time.time()
    cleaned = clean(read_raw_csv(INPUTS["uci_ham"]))
    generation = generate(cleaned.line_items, read_rates(INPUTS["tcmb_kurlar"]), settings)
    T = generation.tables
    overall, table_hashes = data_digest(T)
    if args.yalniz_hash:
        print(overall)
        return 0

    checks = invariants.run_all(T, generation.key, settings.olay_sayilari, settings.simdi, settings.veri_bitis)
    measurement = calibration.measure(T, settings.veri_bitis, dso_year=2025)
    source = json.loads(INPUTS["kalibrasyon"].read_text(encoding="utf-8"))
    bands = calibration.compare_with_bands(measurement, source)

    write_csv(T, ROOT / "data" / "uretilen")
    key_path = ROOT / "data" / "anahtar" / "olaylar.json"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(generation.key, ensure_ascii=False, indent=1, default=_json_default) + "\n", encoding="utf-8")
    write_clock(Clock(settings.simdi, settings.veri_bitis), ROOT / "data" / "saat.json")

    report = {
        # .csv and .json text files are hashed independently of line endings; .gz byte by byte.
        "girdi_sha256": {name: file_digest(path, as_text=path.suffix in (".csv", ".json")) for name, path in INPUTS.items()},
        "ayarlar": dataclasses.asdict(settings),
        "kaydirma_gun": generation.applied_shift_days,
        "veri_baslangic": generation.data_start,
        "bitisten_sonra_kalan_kalem": generation.unused_items_after_end,
        "temizleme": {"girdi": cleaned.input_rows, "tutulan": dict(cleaned.kept), "atilan": dict(cleaned.dropped),
                      "mutabakat": cleaned.reconciles()},
        "tablo_satirlari": {name: len(v) for name, v in T.items()},
        "veri_hash": overall,
        "tablo_hashleri": table_hashes,
        "olaylar": [{k: o[k] for k in ("olay_id", "tur", "tarih", "simdiden_sonra")} for o in generation.key["olaylar"]],
        "denetimler": {name: {"gecti": not h, "hatalar": h} for name, h in checks.items()},
        "kalibrasyon": {"olcum": measurement, "bantlar": bands},
        "sure_s": round(time.time() - start, 1),
    }
    output = ROOT / "reports" / "faz1_veri.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")

    print(f"Temizleme: {cleaned.input_rows} satır, tutulan {dict(cleaned.kept)}, atılan {dict(cleaned.dropped)}")
    print("Tablolar: " + ", ".join(f"{name}={len(v)}" for name, v in T.items()))
    print(f"Veri özeti (hash): {overall}")
    for name, h in checks.items():
        print(f"  {'GEÇTİ' if not h else 'KALDI'}  {name}" + (f": {h}" if h else ""))
    for b in bands:
        print(f"  {'GEÇTİ' if b['tamam'] else 'KALDI'}  {b['ad']}: {b['deger']} (bant {b['alt']}-{b['ust']})")
    print(f"Süre: {report['sure_s']} s. Yazıldı: {output}")
    return 0 if all(not h for h in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
