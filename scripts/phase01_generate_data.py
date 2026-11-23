"""Sentetik şirket verisini üretir, denetler ve kalibrasyon bantlarıyla karşılaştırır.

Girdiler:
    data/ara/uci_ham.csv.gz         (scripts/faz1_uci_indir.py)
    data/kaynak/tcmb_kurlar.csv     (scripts/faz1_kur_indir.py)
    data/kaynak/kalibrasyon.json    (scripts/faz1_kalibrasyon_kaynaklari.py)
Çıktılar:
    data/uretilen/*.csv             araçların okuyacağı tablolar
    data/anahtar/olaylar.json       cevap anahtarı (araçlar okumaz)
    data/saat.json                  simülasyon saati
    reports/faz1_veri.json          özetler, sayılar, denetimler, kalibrasyon

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz1_veri_uret.py
    .venv\\Scripts\\python -I scripts\\faz1_veri_uret.py --yalniz-hash   (hiçbir şey yazmadan özeti basar)
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

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.data import invariants, calibration  # noqa: E402
from sayman.data.table_io import csv_yaz, dosya_hash, veri_hash  # noqa: E402
from sayman.data.exchange_rates import kurlar_oku  # noqa: E402
from sayman.data.clock import Saat, saat_yaz  # noqa: E402
from sayman.data.uci import ham_csv_oku, temizle  # noqa: E402
from sayman.data.generator import Ayarlar, uret  # noqa: E402

GIRDILER = {
    "uci_ham": KOK / "data" / "ara" / "uci_ham.csv.gz",
    "tcmb_kurlar": KOK / "data" / "kaynak" / "tcmb_kurlar.csv",
    "kalibrasyon": KOK / "data" / "kaynak" / "kalibrasyon.json",
}


def _json(deger):
    if isinstance(deger, (dt.date, Decimal)):
        return str(deger)
    raise TypeError(type(deger))


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ayri = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ayri.add_argument("--tohum", type=int, default=Ayarlar.tohum)
    ayri.add_argument("--yalniz-hash", action="store_true")
    arg = ayri.parse_args()
    ayarlar = Ayarlar(tohum=arg.tohum)

    bas = time.time()
    temiz = temizle(ham_csv_oku(GIRDILER["uci_ham"]))
    uretim = uret(temiz.kalemler, kurlar_oku(GIRDILER["tcmb_kurlar"]), ayarlar)
    T = uretim.tablolar
    genel, tablo_hashleri = veri_hash(T)
    if arg.yalniz_hash:
        print(genel)
        return 0

    denetimler = invariants.hepsi(T, uretim.anahtar, ayarlar.olay_sayilari, ayarlar.simdi, ayarlar.veri_bitis)
    olcum = calibration.olc(T, ayarlar.veri_bitis, dso_yili=2025)
    kaynak = json.loads(GIRDILER["kalibrasyon"].read_text(encoding="utf-8"))
    bantlar = calibration.bantlarla_karsilastir(olcum, kaynak)

    csv_yaz(T, KOK / "data" / "uretilen")
    anahtar_yol = KOK / "data" / "anahtar" / "olaylar.json"
    anahtar_yol.parent.mkdir(parents=True, exist_ok=True)
    anahtar_yol.write_text(json.dumps(uretim.anahtar, ensure_ascii=False, indent=1, default=_json) + "\n", encoding="utf-8")
    saat_yaz(Saat(ayarlar.simdi, ayarlar.veri_bitis), KOK / "data" / "saat.json")

    rapor = {
        # .csv ve .json metin dosyaları satır sonundan bağımsız özetlenir; .gz bayt bayt.
        "girdi_sha256": {ad: dosya_hash(yol, metin=yol.suffix in (".csv", ".json")) for ad, yol in GIRDILER.items()},
        "ayarlar": dataclasses.asdict(ayarlar),
        "kaydirma_gun": uretim.kaydirma_gun,
        "veri_baslangic": uretim.veri_baslangic,
        "bitisten_sonra_kalan_kalem": uretim.bitisten_sonra_kalan_kalem,
        "temizleme": {"girdi": temiz.girdi, "tutulan": dict(temiz.tutulan), "atilan": dict(temiz.atilan),
                      "mutabakat": temiz.mutabakat_tutuyor_mu()},
        "tablo_satirlari": {ad: len(v) for ad, v in T.items()},
        "veri_hash": genel,
        "tablo_hashleri": tablo_hashleri,
        "olaylar": [{k: o[k] for k in ("olay_id", "tur", "tarih", "simdiden_sonra")} for o in uretim.anahtar["olaylar"]],
        "denetimler": {ad: {"gecti": not h, "hatalar": h} for ad, h in denetimler.items()},
        "kalibrasyon": {"olcum": olcum, "bantlar": bantlar},
        "sure_s": round(time.time() - bas, 1),
    }
    cikti = KOK / "reports" / "faz1_veri.json"
    cikti.write_text(json.dumps(rapor, ensure_ascii=False, indent=2, default=_json) + "\n", encoding="utf-8")

    print(f"Temizleme: {temiz.girdi} satır, tutulan {dict(temiz.tutulan)}, atılan {dict(temiz.atilan)}")
    print("Tablolar: " + ", ".join(f"{ad}={len(v)}" for ad, v in T.items()))
    print(f"Veri özeti (hash): {genel}")
    for ad, h in denetimler.items():
        print(f"  {'GEÇTİ' if not h else 'KALDI'}  {ad}" + (f": {h}" if h else ""))
    for b in bantlar:
        print(f"  {'GEÇTİ' if b['tamam'] else 'KALDI'}  {b['ad']}: {b['deger']} (bant {b['alt']}-{b['ust']})")
    print(f"Süre: {rapor['sure_s']} s. Yazıldı: {cikti}")
    return 0 if all(not h for h in denetimler.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
