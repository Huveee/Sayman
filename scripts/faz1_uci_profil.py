"""UCI ham verisinin profilini çıkarır: temizleme kurallarını gerçek dosyaya göre doğrulamak için.

Girdi: data/ara/uci_ham.csv.gz (scripts/faz1_uci_indir.py üretir)
Çıktı: reports/faz1_uci_profil.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.veri.uci import SATIS_DISI_KODLAR, ham_csv_oku, temizle  # noqa: E402

URUN_KODU = re.compile(r"^\d{5}")


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    satirlar = list(ham_csv_oku(KOK / "data" / "ara" / "uci_ham.csv.gz"))

    sayfa = defaultdict(lambda: {"satir": 0, "ilk": "9999", "son": "0000"})
    onek, ozel, musterisiz, ulke = Counter(), defaultdict(lambda: Counter()), 0, Counter()
    ozel_tutar, ozel_aciklama = defaultdict(Decimal), defaultdict(Counter)
    eksi_c_disi = fiyat_sifir = arti_c = 0
    musteri_ulke = defaultdict(set)
    for s in satirlar:
        p = sayfa[s["sayfa"]]
        p["satir"] += 1
        p["ilk"], p["son"] = min(p["ilk"], s["InvoiceDate"]), max(p["son"], s["InvoiceDate"])
        no = s["Invoice"]
        onek[no[0] if not no[0].isdigit() else "rakam"] += 1
        kod = s["StockCode"].strip()
        try:
            miktar, fiyat = Decimal(s["Quantity"]), Decimal(s["Price"])
        except InvalidOperation:
            continue
        c = no.startswith("C")
        eksi_c_disi += (not c) and miktar <= 0
        arti_c += c and miktar > 0
        fiyat_sifir += fiyat <= 0
        musterisiz += not s["Customer ID"]
        ulke[s["Country"]] += 1
        if s["Customer ID"]:
            musteri_ulke[s["Customer ID"]].add(s["Country"])
        if not URUN_KODU.match(kod):
            anahtar = kod.upper()
            ozel[anahtar]["satir"] += 1
            ozel[anahtar]["c_faturasi"] += c
            ozel[anahtar]["musterili"] += bool(s["Customer ID"])
            ozel_tutar[anahtar] += miktar * fiyat
            ozel_aciklama[anahtar][s["Description"]] += 1

    sonuc = temizle(satirlar)
    profil = {
        "toplam_satir": len(satirlar),
        "sayfalar": dict(sayfa),
        "fatura_on_ekleri": dict(onek),
        "c_olmayan_eksi_ya_da_sifir_miktar": eksi_c_disi,
        "arti_miktarli_c": arti_c,
        "sifir_ya_da_eksi_fiyat": fiyat_sifir,
        "musterisiz_satir": musterisiz,
        "birden_cok_ulkeli_musteri": sum(1 for v in musteri_ulke.values() if len(v) > 1),
        "ulkeler_ilk_20": dict(ulke.most_common(20)),
        "urun_kodu_olmayan_stok_kodlari": {
            k: {**ozel[k], "tutar_gbp": str(ozel_tutar[k]), "aciklamalar": dict(ozel_aciklama[k].most_common(3)),
                "satis_disi_listesinde": k in SATIS_DISI_KODLAR}
            for k in sorted(ozel, key=lambda k: -ozel[k]["satir"])
        },
        "temizleme": {"girdi": sonuc.girdi, "tutulan": dict(sonuc.tutulan), "atilan": dict(sonuc.atilan),
                      "mutabakat": sonuc.mutabakat_tutuyor_mu()},
    }
    cikti = KOK / "reports" / "faz1_uci_profil.json"
    cikti.write_text(json.dumps(profil, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in profil.items() if k != "urun_kodu_olmayan_stok_kodlari"}, ensure_ascii=False, indent=2))
    print("Ürün kodu olmayan stok kodları:")
    for k, v in list(profil["urun_kodu_olmayan_stok_kodlari"].items())[:40]:
        print(f"  {k:<14} satır={v['satir']:<6} C={v['c_faturasi']:<5} müşterili={v['musterili']:<6} "
              f"tutar={Decimal(v['tutar_gbp']):>12.2f} liste={v['satis_disi_listesinde']} {list(v['aciklamalar'])[:2]}")
    print(f"Yazıldı: {cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
