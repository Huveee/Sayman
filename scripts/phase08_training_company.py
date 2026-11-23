"""Phase 8: a second synthetic company for the training data.

The evaluation company (Phases 1-7) was generated with seed 42. The training company is generated with the same generator
and seed 43: the customers, suppliers, payment behaviors, invoices, cheques, contracts and policy figures are different.
In Phase 1, seed 43 was also seen to stay inside all the calibration bands (reports/faz1_ayar_denemeleri.json, trial 5).

What the two companies share by design: the UCI sales lines (products and dates), the TCMB rate table, the bank account
identities (A_TRY, B_EUR...), the users and their roles, the law texts. The leakage check lists these separately.

Nothing is written to the evaluation data (data/uretilen, data/ambar, data/belge, data/saat.json).

Output: data/egitim/sirket/
    uretilen/*.csv, ambar/sayman.duckdb, saat.json (generation day), belge/belgeler.jsonl,
    belge/onbellek/gomme_bge-m3.npz (in the gomme step), sirket.json (summaries and checks)

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08_training_company.py uret
    .venv\\Scripts\\python -I scripts\\phase08_training_company.py gomme      (needs bge-m3 in Ollama)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

SEED = 43
COMPANY = ROOT / "data" / "egitim" / "sirket"
GENERATED = COMPANY / "uretilen"
WAREHOUSE = COMPANY / "ambar" / "sayman.duckdb"
DOCUMENTS = COMPANY / "belge"
CACHE = DOCUMENTS / "onbellek" / "gomme_bge-m3.npz"
EVALUATION_CACHE = ROOT / "data" / "belge" / "onbellek" / "gomme_bge-m3.npz"
LEGISLATION = ROOT / "data" / "ham" / "mevzuat"


def _json(x):
    import datetime as dt
    from decimal import Decimal
    if isinstance(x, (dt.date, Decimal)):
        return str(x)
    raise TypeError(type(x))


def generate_step() -> dict:
    from support.phase05_questions import documents_digest

    from sayman.warehouse.loader import build_warehouse
    from sayman.documents import library, synthetic
    from sayman.documents.legislation import LAWS, laws
    from sayman.data import invariants, calibration
    from sayman.data.table_io import read_csv, write_csv, data_digest
    from sayman.data.exchange_rates import read_rates
    from sayman.data.clock import Clock, write_clock
    from sayman.data.uci import read_raw_csv, clean
    from sayman.data.generator import GeneratorSettings, generate

    start_time = time.time()
    settings = GeneratorSettings(tohum=SEED)
    cleaned = clean(read_raw_csv(ROOT / "data" / "ara" / "uci_ham.csv.gz"))
    generation = generate(cleaned.line_items, read_rates(ROOT / "data" / "kaynak" / "tcmb_kurlar.csv"), settings)
    tables = generation.tables
    tables_digest, _ = data_digest(tables)
    checks = invariants.run_all(tables, generation.key, settings.olay_sayilari, settings.simdi, settings.veri_bitis)
    bands = calibration.compare_with_bands(
        calibration.measure(tables, settings.veri_bitis, dso_year=2025),
        json.loads((ROOT / "data" / "kaynak" / "kalibrasyon.json").read_text(encoding="utf-8")))
    if COMPANY.exists():
        for child in ("uretilen", "ambar", "belge/belgeler.jsonl", "saat.json"):
            p = COMPANY / child
            shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    write_csv(tables, GENERATED)
    write_clock(Clock(settings.simdi, settings.veri_bitis), COMPANY / "saat.json")
    if tables_digest != data_digest(read_csv(GENERATED))[0]:
        raise SystemExit("yazılan CSV'lerin özeti üretimle aynı değil")
    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)
    build_warehouse(GENERATED, WAREHOUSE, data_digest=tables_digest).close()

    documents = synthetic.generate(read_csv(GENERATED), seed=SEED)
    law_documents = laws(LEGISLATION)
    if len(law_documents) != len(LAWS):
        raise SystemExit(f"{LEGISLATION} içinde {len(LAWS)} kanun PDF'i bekleniyordu")
    DOCUMENTS.mkdir(parents=True, exist_ok=True)
    library.write_documents(documents + law_documents, DOCUMENTS / "belgeler.jsonl")
    info = {
        "tohum": SEED, "uretim_gunu": settings.simdi, "veri_bitis": settings.veri_bitis, "veri_hash": tables_digest,
        "tablo_satirlari": {name: len(v) for name, v in tables.items()},
        "denetimler": {name: {"gecti": not h, "hatalar": h} for name, h in checks.items()},
        "kalibrasyon_bantlari": [{k: b[k] for k in ("ad", "deger", "alt", "ust", "tamam")} for b in bands],
        "belge": len(documents), "madde": sum(len(b.maddeler) for b in documents),
        "belge_ozeti": documents_digest(documents), "kutuphane_ozeti": documents_digest(documents + law_documents),
        "sure_s": round(time.time() - start_time, 1),
    }
    (COMPANY / "sirket.json").write_text(json.dumps(info, ensure_ascii=False, indent=1, default=_json) + "\n",
                                        encoding="utf-8")
    return info


def embedding_step() -> None:
    """The vector cache of the evaluation company is copied (the law articles are the same text, their vectors are not
    recomputed; the cache is keyed by the digest of the text), and the training company articles still missing are
    turned into vectors with bge-m3."""
    from sayman.documents import library
    from sayman.documents.semantic import EmbeddingCache

    if not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EVALUATION_CACHE, CACHE)
    chunks = library.to_chunks(library.read_documents(DOCUMENTS / "belgeler.jsonl"))
    cache = EmbeddingCache(CACHE)
    start_time = time.perf_counter()
    n = cache.fill([p.arama_metni for p in chunks],
                        progress=lambda i, t: print(f"  {i}/{t} ({time.perf_counter() - start_time:.0f} sn)", flush=True))
    print(f"{n} yeni vektör, {len(chunks)} parça, {time.perf_counter() - start_time:.0f} sn")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8 eğitim şirketi")
    ap.add_argument("adim", choices=("uret", "gomme"))
    a = ap.parse_args()
    if a.adim == "uret":
        b = generate_step()
        print(json.dumps({k: b[k] for k in ("tohum", "veri_hash", "tablo_satirlari", "belge", "madde", "sure_s")},
                         ensure_ascii=False, default=_json))
        for name, d in b["denetimler"].items():
            print(f"  {'GEÇTİ' if d['gecti'] else 'KALDI'}  {name}" + ("" if d["gecti"] else f": {d['hatalar']}"))
        for x in b["kalibrasyon_bantlari"]:
            print(f"  {'GEÇTİ' if x['tamam'] else 'KALDI'}  {x['ad']}: {x['deger']} (bant {x['alt']}-{x['ust']})")
    else:
        embedding_step()


if __name__ == "__main__":
    main()
