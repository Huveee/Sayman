"""Profiles the raw UCI data: to check the cleaning rules against the real file.

Input: data/ara/uci_ham.csv.gz (produced by scripts/phase01_download_uci.py)
Output: reports/faz1_uci_profil.json
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.data.uci import NON_SALE_CODES, read_raw_csv, clean  # noqa: E402

PRODUCT_CODE = re.compile(r"^\d{5}")


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    rows = list(read_raw_csv(ROOT / "data" / "ara" / "uci_ham.csv.gz"))

    sheet_stats = defaultdict(lambda: {"satir": 0, "ilk": "9999", "son": "0000"})
    prefixes, special, no_customer, countries = Counter(), defaultdict(lambda: Counter()), 0, Counter()
    special_amount, special_description = defaultdict(Decimal), defaultdict(Counter)
    negative_non_c = zero_price = positive_c = 0
    customer_countries = defaultdict(set)
    for s in rows:
        p = sheet_stats[s["sayfa"]]
        p["satir"] += 1
        p["ilk"], p["son"] = min(p["ilk"], s["InvoiceDate"]), max(p["son"], s["InvoiceDate"])
        invoice_no = s["Invoice"]
        prefixes[invoice_no[0] if not invoice_no[0].isdigit() else "rakam"] += 1
        code = s["StockCode"].strip()
        try:
            quantity, price = Decimal(s["Quantity"]), Decimal(s["Price"])
        except InvalidOperation:
            continue
        c = invoice_no.startswith("C")
        negative_non_c += (not c) and quantity <= 0
        positive_c += c and quantity > 0
        zero_price += price <= 0
        no_customer += not s["Customer ID"]
        countries[s["Country"]] += 1
        if s["Customer ID"]:
            customer_countries[s["Customer ID"]].add(s["Country"])
        if not PRODUCT_CODE.match(code):
            key = code.upper()
            special[key]["satir"] += 1
            special[key]["c_faturasi"] += c
            special[key]["musterili"] += bool(s["Customer ID"])
            special_amount[key] += quantity * price
            special_description[key][s["Description"]] += 1

    cleaned = clean(rows)
    profile = {
        "toplam_satir": len(rows),
        "sayfalar": dict(sheet_stats),
        "fatura_on_ekleri": dict(prefixes),
        "c_olmayan_eksi_ya_da_sifir_miktar": negative_non_c,
        "arti_miktarli_c": positive_c,
        "sifir_ya_da_eksi_fiyat": zero_price,
        "musterisiz_satir": no_customer,
        "birden_cok_ulkeli_musteri": sum(1 for v in customer_countries.values() if len(v) > 1),
        "ulkeler_ilk_20": dict(countries.most_common(20)),
        "urun_kodu_olmayan_stok_kodlari": {
            k: {**special[k], "tutar_gbp": str(special_amount[k]), "aciklamalar": dict(special_description[k].most_common(3)),
                "satis_disi_listesinde": k in NON_SALE_CODES}
            for k in sorted(special, key=lambda k: -special[k]["satir"])
        },
        "temizleme": {"girdi": cleaned.input_rows, "tutulan": dict(cleaned.kept), "atilan": dict(cleaned.dropped),
                      "mutabakat": cleaned.reconciles()},
    }
    output = ROOT / "reports" / "faz1_uci_profil.json"
    output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in profile.items() if k != "urun_kodu_olmayan_stok_kodlari"}, ensure_ascii=False, indent=2))
    print("Ürün kodu olmayan stok kodları:")
    for k, v in list(profile["urun_kodu_olmayan_stok_kodlari"].items())[:40]:
        print(f"  {k:<14} satır={v['satir']:<6} C={v['c_faturasi']:<5} müşterili={v['musterili']:<6} "
              f"tutar={Decimal(v['tutar_gbp']):>12.2f} liste={v['satis_disi_listesinde']} {list(v['aciklamalar'])[:2]}")
    print(f"Yazıldı: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
