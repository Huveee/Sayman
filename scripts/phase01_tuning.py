"""Kalibrasyon ayar denemesi: üretici ayarlarından yalnızca izin verilenleri değiştirip bantlara bakar.

Kurallar (gerçek veriyle ilk çalıştırmadan önce yazıldı):
- Değişebilir: vade karışımı (vade_yurtici, vade_ihracat), ödeme profilleri (profiller), batık müşteri payı.
- Değişmez: kalibrasyon bantları ve hesap tanımları (data/kaynak/kalibrasyon.json), DSO formülü
  (2025 yıl sonu alacağı, POS paydada), Atradius gecikme dilimleri (gecikme_dilimleri).
- Her deneme reports/faz1_ayar_denemeleri.json dosyasına eklenir; seçilen ayar 42 dışında en az iki
  tohumla da bantların içinde kalmalıdır.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz1_ayar.py --not "ilk ölçüm"
    .venv\\Scripts\\python -I scripts\\faz1_ayar.py --ayar "{\\"batik_musteri_payi\\": 0.03}" --tohumlar 42,43,44 --not "..."
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.data import calibration  # noqa: E402
from sayman.data.table_io import veri_hash  # noqa: E402
from sayman.data.exchange_rates import kurlar_oku  # noqa: E402
from sayman.data.uci import ham_csv_oku, temizle  # noqa: E402
from sayman.data.generator import Ayarlar, uret  # noqa: E402

AYARLANABILIR = {"vade_yurtici", "vade_ihracat", "profiller", "batik_musteri_payi"}
KAYIT = KOK / "reports" / "faz1_ayar_denemeleri.json"


def _demet(deger):
    return tuple(_demet(v) for v in deger) if isinstance(deger, list) else deger


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ayri = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ayri.add_argument("--ayar", default="{}", help="JSON: değiştirilecek ayarlar")
    ayri.add_argument("--tohumlar", default="42")
    ayri.add_argument("--not", dest="aciklama", default="")
    arg = ayri.parse_args()

    degisiklik = json.loads(arg.ayar)
    yasak = set(degisiklik) - AYARLANABILIR
    if yasak:
        print(f"Bu ayarlar kalibrasyonda değiştirilemez: {sorted(yasak)}", file=sys.stderr)
        return 2
    degisiklik = {k: _demet(v) for k, v in degisiklik.items()}
    kaynak = json.loads((KOK / "data" / "kaynak" / "kalibrasyon.json").read_text(encoding="utf-8"))

    bas = time.time()
    kalemler = temizle(ham_csv_oku(KOK / "data" / "ara" / "uci_ham.csv.gz")).kalemler
    kurlar = kurlar_oku(KOK / "data" / "kaynak" / "tcmb_kurlar.csv")
    sonuclar = []
    for tohum in (int(t) for t in arg.tohumlar.split(",")):
        ayarlar = dataclasses.replace(Ayarlar(), tohum=tohum, **degisiklik)
        T = uret(kalemler, kurlar, ayarlar).tablolar
        olcum = calibration.olc(T, ayarlar.veri_bitis, 2025)
        bantlar = calibration.bantlarla_karsilastir(olcum, kaynak)
        sonuclar.append({"tohum": tohum, "veri_hash": veri_hash(T)[0], "olcum": olcum, "bantlar": bantlar,
                         "hepsi_bantta": all(b["tamam"] for b in bantlar)})
        print(f"tohum {tohum}: " + ", ".join(f"{b['ad']}={b['deger']}{'' if b['tamam'] else ' (BANT DIŞI)'}" for b in bantlar))

    kayitlar = json.loads(KAYIT.read_text(encoding="utf-8")) if KAYIT.exists() else []
    kayitlar.append({"sira": len(kayitlar) + 1, "zaman": dt.datetime.now().isoformat(timespec="seconds"),
                     "not": arg.aciklama, "degisiklik": degisiklik, "sonuclar": sonuclar,
                     "sure_s": round(time.time() - bas, 1)})
    KAYIT.write_text(json.dumps(kayitlar, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"Deneme {len(kayitlar)} kaydedildi: {KAYIT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
