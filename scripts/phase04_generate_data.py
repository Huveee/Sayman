"""Phase 4: generates additional data sets for measuring the monitoring rules.

- tohum42_temiz: exactly the same as the project's company (seed 42), only with the events switched off. False
  alarms are counted here.
- tohum43, tohum44 (with events) and tohum43_temiz, tohum44_temiz: untouched data sets that are run only once,
  after the rules are frozen on seed 42.

The eventful form of seed 42 is already in data/uretilen; this script regenerates it and checks that its digest
(hash) equals the Phase 1 report; it does not copy it.

Inputs: data/ara/uci_ham.csv.gz, data/kaynak/tcmb_kurlar.csv, reports/faz1_veri.json
Output:   data/izleme_veri/<name>/*.csv, and for the eventful sets also olaylar.json (answer key; the monitoring
          code does not read it)
          data/izleme_veri/setler.json (seed, event status, digest and row counts of each set)

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase04_generate_data.py
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

from sayman.data.table_io import write_csv, data_digest  # noqa: E402
from sayman.data.exchange_rates import read_rates  # noqa: E402
from sayman.data.uci import read_raw_csv, clean  # noqa: E402
from sayman.data.generator import GeneratorSettings, generate  # noqa: E402

OUTPUT = ROOT / "data" / "izleme_veri"
SETS = [("tohum42_temiz", 42, False), ("tohum43", 43, True), ("tohum43_temiz", 43, False),
          ("tohum44", 44, True), ("tohum44_temiz", 44, False)]


def _json_default(value):
    if isinstance(value, (dt.date, Decimal)):
        return str(value)
    raise TypeError(type(value))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    start = time.time()
    line_items = clean(read_raw_csv(ROOT / "data" / "ara" / "uci_ham.csv.gz")).line_items
    rates = read_rates(ROOT / "data" / "kaynak" / "tcmb_kurlar.csv")
    phase1_report = json.loads((ROOT / "reports" / "faz1_veri.json").read_text(encoding="utf-8"))

    base = generate(line_items, rates, GeneratorSettings(tohum=42))
    digest = data_digest(base.tables)[0]
    if digest != phase1_report["veri_hash"]:
        print(f"Tohum 42 Faz 1'deki veriyi vermedi ({digest[:12]} != {phase1_report['veri_hash'][:12]}); durduruldu.")
        return 1
    print(f"tohum42 (olaylı): özet Faz 1 ile aynı ({digest[:12]}), data/uretilen kullanılır")

    sets = {"tohum42": {"tohum": 42, "olaylar": True, "klasor": "data/uretilen", "veri_hash": digest,
                          "olay_sayisi": len(base.key["olaylar"])}}
    for name, seed, with_events in SETS:
        u = generate(line_items, rates, GeneratorSettings(tohum=seed, olaylar_acik=with_events))
        folder = OUTPUT / name
        folder.mkdir(parents=True, exist_ok=True)
        write_csv(u.tables, folder)
        if with_events:
            (folder / "olaylar.json").write_text(
                json.dumps(u.key, ensure_ascii=False, indent=1, default=_json_default) + "\n", encoding="utf-8")
        sets[name] = {"tohum": seed, "olaylar": with_events, "klasor": f"data/izleme_veri/{name}",
                      "veri_hash": data_digest(u.tables)[0], "veri_baslangic": u.data_start,
                      "tablo_satirlari": {t: len(v) for t, v in u.tables.items()},
                      "olay_sayisi": len(u.key["olaylar"]) if with_events else 0}
        print(f"{name}: {sets[name]['veri_hash'][:12]}, {sets[name]['olay_sayisi']} olay")
    (OUTPUT / "setler.json").write_text(json.dumps(sets, ensure_ascii=False, indent=2, default=_json_default) + "\n",
                                       encoding="utf-8")
    print(f"Süre: {time.time() - start:.0f} s. Yazıldı: {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
