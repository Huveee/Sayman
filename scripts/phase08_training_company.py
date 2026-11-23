"""Faz 8: eğitim verisi için ikinci bir sentetik şirket.

Değerlendirme şirketi (Faz 1-7) tohum 42 ile üretildi. Eğitim şirketi aynı üreticiyle, tohum 43 ile üretilir: müşteriler,
tedarikçiler, ödeme davranışları, faturalar, çekler, sözleşmeler ve politika rakamları farklıdır. Faz 1'de tohum 43'ün
de bütün kalibrasyon bantlarının içinde kaldığı görülmüştü (reports/faz1_ayar_denemeleri.json, deneme 5).

İki şirkette tasarım gereği ortak olanlar: UCI satış kalemleri (ürünler ve tarihler), TCMB kur tablosu, banka hesap
kimlikleri (A_TRY, B_EUR...), kullanıcılar ve rolleri, kanun metinleri. Sızıntı denetimi bunları ayrıca listeler.

Değerlendirme verisine (data/uretilen, data/ambar, data/belge, data/saat.json) hiçbir şey yazılmaz.

Çıktı: data/egitim/sirket/
    uretilen/*.csv, ambar/sayman.duckdb, saat.json (üretim günü), belge/belgeler.jsonl,
    belge/onbellek/gomme_bge-m3.npz (gomme adımında), sirket.json (özetler ve denetimler)

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8_egitim_sirketi.py uret
    .venv\\Scripts\\python -I scripts\\faz8_egitim_sirketi.py gomme      (Ollama'da bge-m3 gerekir)
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "tests"))

TOHUM = 43
SIRKET = KOK / "data" / "egitim" / "sirket"
URETILEN = SIRKET / "uretilen"
AMBAR = SIRKET / "ambar" / "sayman.duckdb"
BELGE = SIRKET / "belge"
ONBELLEK = BELGE / "onbellek" / "gomme_bge-m3.npz"
DEGERLENDIRME_ONBELLEGI = KOK / "data" / "belge" / "onbellek" / "gomme_bge-m3.npz"
MEVZUAT = KOK / "data" / "ham" / "mevzuat"


def _json(x):
    import datetime as dt
    from decimal import Decimal
    if isinstance(x, (dt.date, Decimal)):
        return str(x)
    raise TypeError(type(x))


def uret_adimi() -> dict:
    from support.phase05_questions import belge_ozeti

    from sayman.warehouse.loader import ambar_kur
    from sayman.documents import library, synthetic
    from sayman.documents.legislation import KANUNLAR, kanunlar
    from sayman.data import invariants, calibration
    from sayman.data.table_io import csv_oku, csv_yaz, veri_hash
    from sayman.data.exchange_rates import kurlar_oku
    from sayman.data.clock import Saat, saat_yaz
    from sayman.data.uci import ham_csv_oku, temizle
    from sayman.data.generator import Ayarlar, uret

    bas = time.time()
    ayarlar = Ayarlar(tohum=TOHUM)
    temiz = temizle(ham_csv_oku(KOK / "data" / "ara" / "uci_ham.csv.gz"))
    uretim = uret(temiz.kalemler, kurlar_oku(KOK / "data" / "kaynak" / "tcmb_kurlar.csv"), ayarlar)
    T = uretim.tablolar
    ozet, _ = veri_hash(T)
    denetimler = invariants.hepsi(T, uretim.anahtar, ayarlar.olay_sayilari, ayarlar.simdi, ayarlar.veri_bitis)
    bantlar = calibration.bantlarla_karsilastir(
        calibration.olc(T, ayarlar.veri_bitis, dso_yili=2025),
        json.loads((KOK / "data" / "kaynak" / "kalibrasyon.json").read_text(encoding="utf-8")))
    if SIRKET.exists():
        for alt in ("uretilen", "ambar", "belge/belgeler.jsonl", "saat.json"):
            p = SIRKET / alt
            shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    csv_yaz(T, URETILEN)
    saat_yaz(Saat(ayarlar.simdi, ayarlar.veri_bitis), SIRKET / "saat.json")
    if ozet != veri_hash(csv_oku(URETILEN))[0]:
        raise SystemExit("yazılan CSV'lerin özeti üretimle aynı değil")
    AMBAR.parent.mkdir(parents=True, exist_ok=True)
    ambar_kur(URETILEN, AMBAR, veri_hash=ozet).close()

    belgeler = synthetic.uret(csv_oku(URETILEN), tohum=TOHUM)
    kanun = kanunlar(MEVZUAT)
    if len(kanun) != len(KANUNLAR):
        raise SystemExit(f"{MEVZUAT} içinde {len(KANUNLAR)} kanun PDF'i bekleniyordu")
    BELGE.mkdir(parents=True, exist_ok=True)
    library.yaz(belgeler + kanun, BELGE / "belgeler.jsonl")
    bilgi = {
        "tohum": TOHUM, "uretim_gunu": ayarlar.simdi, "veri_bitis": ayarlar.veri_bitis, "veri_hash": ozet,
        "tablo_satirlari": {ad: len(v) for ad, v in T.items()},
        "denetimler": {ad: {"gecti": not h, "hatalar": h} for ad, h in denetimler.items()},
        "kalibrasyon_bantlari": [{k: b[k] for k in ("ad", "deger", "alt", "ust", "tamam")} for b in bantlar],
        "belge": len(belgeler), "madde": sum(len(b.maddeler) for b in belgeler),
        "belge_ozeti": belge_ozeti(belgeler), "kutuphane_ozeti": belge_ozeti(belgeler + kanun),
        "sure_s": round(time.time() - bas, 1),
    }
    (SIRKET / "sirket.json").write_text(json.dumps(bilgi, ensure_ascii=False, indent=1, default=_json) + "\n",
                                        encoding="utf-8")
    return bilgi


def gomme_adimi() -> None:
    """Değerlendirme şirketinin vektör önbelleği kopyalanır (kanun maddeleri aynı metin, vektörleri yeniden hesaplanmaz;
    önbellek metnin özetiyle anahtarlanır), eksik kalan eğitim şirketi maddeleri bge-m3 ile vektöre çevrilir."""
    from sayman.documents import library
    from sayman.documents.semantic import GommeOnbellegi

    if not ONBELLEK.exists():
        ONBELLEK.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DEGERLENDIRME_ONBELLEGI, ONBELLEK)
    parcalar = library.parcalar(library.oku(BELGE / "belgeler.jsonl"))
    onbellek = GommeOnbellegi(ONBELLEK)
    bas = time.perf_counter()
    n = onbellek.doldur([p.arama_metni for p in parcalar],
                        ilerleme=lambda i, t: print(f"  {i}/{t} ({time.perf_counter() - bas:.0f} sn)", flush=True))
    print(f"{n} yeni vektör, {len(parcalar)} parça, {time.perf_counter() - bas:.0f} sn")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8 eğitim şirketi")
    ap.add_argument("adim", choices=("uret", "gomme"))
    a = ap.parse_args()
    if a.adim == "uret":
        b = uret_adimi()
        print(json.dumps({k: b[k] for k in ("tohum", "veri_hash", "tablo_satirlari", "belge", "madde", "sure_s")},
                         ensure_ascii=False, default=_json))
        for ad, d in b["denetimler"].items():
            print(f"  {'GEÇTİ' if d['gecti'] else 'KALDI'}  {ad}" + ("" if d["gecti"] else f": {d['hatalar']}"))
        for x in b["kalibrasyon_bantlari"]:
            print(f"  {'GEÇTİ' if x['tamam'] else 'KALDI'}  {x['ad']}: {x['deger']} (bant {x['alt']}-{x['ust']})")
    else:
        gomme_adimi()


if __name__ == "__main__":
    main()
