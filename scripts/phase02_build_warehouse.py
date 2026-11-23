"""Faz 2: veri ambarını kurar ve metrikleri "şimdi" için hesaplayıp raporlar.

Girdiler:
    data/uretilen/*.csv           (scripts/phase01_generate_data.py)
    reports/faz1_veri.json        verinin özeti (hash) buradakiyle aynı olmalı
    data/saat.json                simülasyon saati
Çıktılar:
    data/ambar/sayman.duckdb      araçların salt okunur açacağı ambar
    reports/faz2_metrikler.json   şimdi için metrik özetleri ve her metriğin süresi

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz2_ambar_kur.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.warehouse import metrics  # noqa: E402
from sayman.warehouse.loader import ambar_kur, baglan  # noqa: E402
from sayman.data.table_io import csv_oku, veri_hash  # noqa: E402
from sayman.data.clock import saat_oku  # noqa: E402

URETILEN = KOK / "data" / "uretilen"
AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"
DSO_YIL_SONU = dt.date(2025, 12, 31)


def _json(deger):
    if isinstance(deger, (dt.date, Decimal)):
        return str(deger)
    raise TypeError(type(deger))


def _tl(kurus: int) -> str:
    return f"{kurus / 100:,.0f} TL".replace(",", ".")


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    rapor1 = json.loads((KOK / "reports" / "faz1_veri.json").read_text(encoding="utf-8"))
    ozet = veri_hash(csv_oku(URETILEN))[0]
    if ozet != rapor1["veri_hash"]:
        print(f"data/uretilen Faz 1 raporundaki veriyle aynı değil ({ozet[:12]} != {rapor1['veri_hash'][:12]}). "
              "Önce scripts/phase01_generate_data.py çalıştırılmalı.")
        return 1

    bas = time.time()
    ambar_kur(URETILEN, AMBAR, veri_hash=ozet).close()
    kurulum_s = round(time.time() - bas, 2)

    simdi = saat_oku(KOK / "data" / "saat.json").simdi
    con = baglan(AMBAR)
    sonuclar, sureler = {}, {}
    for ad, fonk in metrics.METRIKLER.items():
        bas = time.perf_counter()
        sonuclar[ad] = fonk(con, simdi)
        sureler[ad] = round(time.perf_counter() - bas, 3)
    dso_2025 = metrics.dso(con, DSO_YIL_SONU)
    con.close()

    y, n = sonuclar["yaslandirma"], sonuclar["nakit_akis_tahmini"]
    rapor = {
        "veri_hash": ozet,
        "ambar": str(AMBAR.relative_to(KOK)).replace("\\", "/"),
        "ambar_kurulum_s": kurulum_s,
        "tarih": simdi,
        "nakit_pozisyonu": sonuclar["nakit_pozisyonu"],
        "yaslandirma_toplam": y["toplam"],
        "yaslandirma_musteri_sayisi": len(y["musteriler"]),
        "dso": sonuclar["dso"],
        "dso_2025_yil_sonu": dso_2025,
        "geciken_alacaklar": {"fatura_sayisi": len(sonuclar["geciken_alacaklar"]["faturalar"]),
                              "toplam_try_kurus": sonuclar["geciken_alacaklar"]["toplam_try_kurus"],
                              "en_buyuk_5": sonuclar["geciken_alacaklar"]["faturalar"][:5]},
        "yaklasan_odemeler": {"fatura_sayisi": len(sonuclar["yaklasan_odemeler"]["odemeler"]),
                              "toplam_kurus": sonuclar["yaklasan_odemeler"]["toplam_kurus"]},
        "kur_riski": sonuclar["kur_riski"],
        "nakit_akis_tahmini": n,
        "metrik_suresi_s": sureler,
    }
    cikti = KOK / "reports" / "faz2_metrikler.json"
    cikti.write_text(json.dumps(rapor, ensure_ascii=False, indent=2, default=_json) + "\n", encoding="utf-8")

    print(f"Ambar kuruldu: {AMBAR.relative_to(KOK)} ({kurulum_s} s), veri özeti {ozet[:12]}")
    print(f"Tarih: {simdi}")
    print(f"  Nakit: {_tl(sonuclar['nakit_pozisyonu']['toplam_try_kurus'])}")
    print(f"  Açık alacak: {_tl(y['toplam']['toplam_kurus'])} ({len(y['musteriler'])} müşteri)")
    print(f"  DSO: {sonuclar['dso']['dso_gun']} gün (31.12.2025: {dso_2025['dso_gun']} gün)")
    print(f"  Geciken alacak: {_tl(rapor['geciken_alacaklar']['toplam_try_kurus'])} "
          f"({rapor['geciken_alacaklar']['fatura_sayisi']} fatura)")
    print(f"  30 gün içindeki ödemeler: {_tl(rapor['yaklasan_odemeler']['toplam_kurus'])} "
          f"({rapor['yaklasan_odemeler']['fatura_sayisi']} fatura)")
    for d in sonuclar["kur_riski"]["dovizler"]:
        print(f"  Kur riski {d['para_birimi']}: {_tl(d['toplam_try_kurus'])}, %10 kur değişimi {_tl(d['yuzde_10_etki_kurus'])}")
    print(f"  13 hafta sonra, yalnızca bilinen kalemlerle (maaş, vergi, yeni satışlar hariç): "
          f"{_tl(n['haftalar'][-1]['bilinen_kalemlerle_bakiye_kurus'])}; şüpheli alacak {_tl(n['supheli_alacak_kurus'])}")
    print("  Süreler (s): " + ", ".join(f"{ad} {s}" for ad, s in sureler.items()))
    print(f"Yazıldı: {cikti.relative_to(KOK)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
