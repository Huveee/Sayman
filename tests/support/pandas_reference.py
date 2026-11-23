"""A second, independent pandas computation of the Phase 2 metrics (used only by tests).

It shares no code with the DuckDB side: it reads the CSVs itself, builds the types itself and does its own rounding.
The definitions are those in the docstring of `sayman/warehouse/metrics.py`; the two computations must give the same results.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

import numpy as np
import pandas as pd

TABLES = ("musteriler", "faturalar", "pos_islemleri", "tahsilatlar", "cekler", "cek_hareketleri", "tedarikciler",
            "alis_faturalari", "odemeler", "banka_hesaplari", "banka_hareketleri", "doviz_kurlari")
BUCKETS = (("vadesi_gelmemis", -10**9, 0), ("gecikme_1_30", 1, 30), ("gecikme_31_60", 31, 60),
            ("gecikme_61_90", 61, 90), ("gecikme_91_180", 91, 180), ("gecikme_180_ustu", 181, 10**9))


def _kind(column: str) -> str:
    if column.endswith("_kurus") or column in ("vade_gun", "kalem_sayisi"):
        return "int"
    if column == "yurtici":
        return "bool"
    if column in ("kur", "alis"):
        return "ondalik"
    if column == "tarih" or column.endswith("tarihi"):
        return "gun"
    return "metin"


def read_tables(directory: Path) -> dict[str, pd.DataFrame]:
    T = {}
    for name in TABLES:
        df = pd.read_csv(Path(directory) / f"{name}.csv", dtype=str, keep_default_na=False, encoding="utf-8")
        for s in df.columns:
            blank_mask = df[s] == ""
            kind = _kind(s)
            if kind == "int":
                df[s] = pd.array([None if b else int(v) for v, b in zip(df[s], blank_mask)], dtype="Int64")
            elif kind == "bool":
                df[s] = df[s] == "1"
            elif kind == "ondalik":
                df[s] = [None if b else Decimal(v) for v, b in zip(df[s], blank_mask)]
            elif kind == "gun":
                df[s] = pd.to_datetime(df[s].where(~blank_mask, None), format="%Y-%m-%d")
            else:
                df[s] = df[s].where(~blank_mask, None)
        T[name] = df
    return T


def apply_rate(cents, rate: Decimal) -> int:
    return int((Decimal(int(cents)) * rate).to_integral_value(rounding=ROUND_HALF_EVEN))


def _timestamp(tarih: dt.date) -> pd.Timestamp:
    return pd.Timestamp(tarih)


def _to_date(x) -> dt.date:
    return pd.Timestamp(x).date()


def _rates(T, d: pd.Timestamp) -> dict[str, Decimal]:
    k = T["doviz_kurlari"]
    k = k[k["tarih"] <= d].sort_values(["para_birimi", "tarih"])
    rates = {currency: g["alis"].iloc[-1] for currency, g in k.groupby("para_birimi")}
    rates["TRY"] = Decimal(1)
    return rates


def _rate(rates: dict, currency: str) -> Decimal:
    if currency not in rates:
        raise LookupError(f"kur bulunamadı: {currency}")
    return rates[currency]


def _open_receivables(T, d: pd.Timestamp) -> pd.DataFrame:
    f = T["faturalar"]
    sales_rows = f[(f["tur"] == "satis") & (f["fatura_tarihi"] <= d)].copy()
    t = T["tahsilatlar"]
    t = t[(t["tarih"] <= d) & t["fatura_id"].notna()]
    collected = {k: int(v) for k, v in t.groupby("fatura_id")["tutar_kurus"].sum().items()}
    i = f[(f["tur"] == "iade") & (f["fatura_tarihi"] <= d) & f["iliskili_fatura_id"].notna()]
    returned_amounts = {k: int(v) for k, v in i.assign(u=i["uygulanan_kurus"].fillna(0)).groupby("iliskili_fatura_id")["u"].sum().items()}
    sales_rows["acik_kurus"] = [int(a) - collected.get(invoice_id, 0) - returned_amounts.get(invoice_id, 0)
                         for a, invoice_id in zip(sales_rows["tutar_kurus"], sales_rows["fatura_id"])]
    sales_rows["gecikme_gun"] = (d - sales_rows["vade_tarihi"]).dt.days.astype(int)
    open_rows = sales_rows[sales_rows["acik_kurus"] > 0].copy()
    open_rows["acik_try_kurus"] = [apply_rate(a, k) for a, k in zip(open_rows["acik_kurus"], open_rows["kur"])]
    return open_rows


def _unmatched(T, d: pd.Timestamp) -> pd.DataFrame:
    t = T["tahsilatlar"]
    t = t[t["fatura_id"].isna() & (t["tarih"] <= d)].copy()
    if t.empty:
        return t.assign(try_kurus=pd.Series(dtype=object))
    rates = T["doviz_kurlari"].sort_values("tarih")[["tarih", "para_birimi", "alis"]]
    t = pd.merge_asof(t.sort_values("tarih"), rates, on="tarih", by="para_birimi", direction="backward")
    computed = []
    for currency, amount, buy_rate in zip(t["para_birimi"], t["tutar_kurus"], t["alis"]):
        if currency == "TRY":
            computed.append(int(amount))
        elif buy_rate is None or (isinstance(buy_rate, float) and np.isnan(buy_rate)):
            raise LookupError(f"kur bulunamadı: {currency}")
        else:
            computed.append(apply_rate(amount, buy_rate))
    return t.assign(try_kurus=computed)


def _balances(T, d: pd.Timestamp) -> pd.DataFrame:
    h = T["banka_hesaplari"]
    h = h[h["acilis_tarihi"] <= d].copy()
    bank_transactions = T["banka_hareketleri"]
    total = bank_transactions[bank_transactions["tarih"] <= d].groupby("hesap_id")["tutar_kurus"].sum()
    h["bakiye_kurus"] = [int(a) + int(total.get(i, 0)) for a, i in zip(h["acilis_bakiye_kurus"], h["hesap_id"])]
    return h


def _open_payables(T, d: pd.Timestamp) -> pd.DataFrame:
    a = T["alis_faturalari"]
    a = a[a["tarih"] <= d].copy()
    o = T["odemeler"]
    paid = o[(o["tarih"] <= d) & o["alis_fatura_id"].notna()].groupby("alis_fatura_id")["tutar_kurus"].sum()
    a["acik_kurus"] = [int(t) - int(paid.get(i, 0)) for t, i in zip(a["tutar_kurus"], a["alis_fatura_id"])]
    return a


# ---------------------------------------------------------------- metrics


def cash_position(T, tarih: dt.date) -> dict:
    d = _timestamp(tarih)
    rates = _rates(T, d)
    accounts = []
    for _, h in _balances(T, d).sort_values("hesap_id").iterrows():
        rate = _rate(rates, h["para_birimi"])
        accounts.append({"hesap_id": h["hesap_id"], "banka": h["banka"], "para_birimi": h["para_birimi"],
                         "bakiye_kurus": h["bakiye_kurus"], "kur": rate, "bakiye_try_kurus": apply_rate(h["bakiye_kurus"], rate)})
    return {"tarih": tarih, "hesaplar": accounts, "toplam_try_kurus": sum(h["bakiye_try_kurus"] for h in accounts)}


def aging(T, tarih: dt.date) -> dict:
    d = _timestamp(tarih)
    open_rows = _open_receivables(T, d)
    unmatched = _unmatched(T, d)
    columns = [f"{name}_kurus" for name, _, _ in BUCKETS]
    by_customer = {}
    for m, g, amount in zip(open_rows["musteri_id"], open_rows["gecikme_gun"], open_rows["acik_try_kurus"]):
        customer_row = by_customer.setdefault(m, dict.fromkeys(columns + ["eslesmemis_tahsilat_kurus"], 0))
        bucket = next(name for name, low, high in BUCKETS if low <= g <= high)
        customer_row[f"{bucket}_kurus"] += amount
    for m, amount in zip(unmatched["musteri_id"], unmatched["try_kurus"]):
        customer_row = by_customer.setdefault(m, dict.fromkeys(columns + ["eslesmemis_tahsilat_kurus"], 0))
        customer_row["eslesmemis_tahsilat_kurus"] -= amount
    name = dict(zip(T["musteriler"]["musteri_id"], T["musteriler"]["ad"]))
    customers = []
    for m, customer_row in by_customer.items():
        customers.append({"musteri_id": m, "ad": name.get(m), **customer_row, "toplam_kurus": sum(customer_row.values())})
    customers.sort(key=lambda r: (-r["toplam_kurus"], r["musteri_id"]))
    all_values = columns + ["eslesmemis_tahsilat_kurus", "toplam_kurus"]
    return {"tarih": tarih, "musteriler": customers, "toplam": {s: sum(r[s] for r in customers) for s in all_values}}


def dso(T, tarih: dt.date, gun: int = 365) -> dict:
    receivable = aging(T, tarih)["toplam"]["toplam_kurus"]
    start, end = _timestamp(tarih - dt.timedelta(days=gun - 1)), _timestamp(tarih)
    f, p = T["faturalar"], T["pos_islemleri"]
    sales = int(f[(f["fatura_tarihi"] >= start) & (f["fatura_tarihi"] <= end)]["tutar_try_kurus"].sum()) + int(
        p[(p["islem_tarihi"] >= start) & (p["islem_tarihi"] <= end)]["tutar_kurus"].sum())
    computed = None
    if sales > 0:
        computed = (Decimal(gun) * Decimal(receivable) / Decimal(sales)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return {"tarih": tarih, "gun": gun, "alacak_kurus": receivable, "satis_kurus": sales, "dso_gun": computed}


def overdue_receivables(T, tarih: dt.date, en_az_gun: int = 1) -> dict:
    open_rows = _open_receivables(T, _timestamp(tarih))
    open_rows = open_rows[open_rows["gecikme_gun"] >= en_az_gun]
    name = dict(zip(T["musteriler"]["musteri_id"], T["musteriler"]["ad"]))
    invoices = [{"fatura_id": r["fatura_id"], "musteri_id": r["musteri_id"], "ad": name.get(r["musteri_id"]),
                  "fatura_tarihi": _to_date(r["fatura_tarihi"]), "vade_tarihi": _to_date(r["vade_tarihi"]),
                  "gecikme_gun": int(r["gecikme_gun"]), "para_birimi": r["para_birimi"],
                  "acik_kurus": int(r["acik_kurus"]), "acik_try_kurus": int(r["acik_try_kurus"])}
                 for _, r in open_rows.iterrows()]
    invoices.sort(key=lambda r: (-r["acik_try_kurus"], r["fatura_id"]))
    return {"tarih": tarih, "en_az_gun": en_az_gun, "faturalar": invoices,
            "toplam_try_kurus": sum(r["acik_try_kurus"] for r in invoices)}


def upcoming_payments(T, tarih: dt.date, gun: int = 30) -> dict:
    d = _timestamp(tarih)
    a = _open_payables(T, d)
    a = a[(a["acik_kurus"] > 0) & (a["vade_tarihi"] <= d + pd.Timedelta(days=gun))]
    name = dict(zip(T["tedarikciler"]["tedarikci_id"], T["tedarikciler"]["ad"]))
    payments = [{"alis_fatura_id": r["alis_fatura_id"], "tedarikci_id": r["tedarikci_id"], "ad": name.get(r["tedarikci_id"]),
                 "kategori": r["kategori"], "tarih": _to_date(r["tarih"]), "vade_tarihi": _to_date(r["vade_tarihi"]),
                 "kalan_gun": (r["vade_tarihi"] - d).days, "acik_kurus": int(r["acik_kurus"])}
                for _, r in a.iterrows()]
    payments.sort(key=lambda r: (r["vade_tarihi"], r["alis_fatura_id"]))
    return {"tarih": tarih, "gun": gun, "odemeler": payments, "toplam_kurus": sum(r["acik_kurus"] for r in payments)}


def fx_risk(T, tarih: dt.date) -> dict:
    d = _timestamp(tarih)
    rates = _rates(T, d)
    m, h = T["musteriler"], T["banka_hesaplari"]
    currency_codes = sorted((set(m[m["ilk_islem_tarihi"] <= d]["para_birimi"]) | set(h[h["acilis_tarihi"] <= d]["para_birimi"]))
                   - {"TRY"})
    open_rows = _open_receivables(T, d)
    t = T["tahsilatlar"]
    unmatched = t[t["fatura_id"].isna() & (t["tarih"] <= d)]
    balances = _balances(T, d)
    currencies = []
    for currency in currency_codes:
        rate = _rate(rates, currency)
        p = open_rows[open_rows["para_birimi"] == currency]
        receivable = int(p["acik_kurus"].sum()) - int(unmatched[unmatched["para_birimi"] == currency]["tutar_kurus"].sum())
        difference = sum(apply_rate(a, rate) - apply_rate(a, k) for a, k in zip(p["acik_kurus"], p["kur"]))
        bank_balance = int(balances[balances["para_birimi"] == currency]["bakiye_kurus"].sum())
        total = receivable + bank_balance
        currencies.append({
            "para_birimi": currency, "alacak_doviz_kurus": receivable, "banka_doviz_kurus": bank_balance, "toplam_doviz_kurus": total,
            "kur": rate, "toplam_try_kurus": apply_rate(total, rate),
            "yuzde_10_etki_kurus": int((Decimal(total) * rate * Decimal("0.1")).to_integral_value(rounding=ROUND_HALF_EVEN)),
            "alacak_kur_farki_kurus": difference,
        })
    return {"tarih": tarih, "dovizler": currencies, "toplam_try_kurus": sum(x["toplam_try_kurus"] for x in currencies)}


def _closed_delays(T, d: pd.Timestamp, gun: int = 365) -> dict[str, list[int]]:
    """Customer -> delays of the sales invoices closed in the `gun` days ending at D (a delay below 0 counts as 0)."""
    f = T["faturalar"]
    t = T["tahsilatlar"]
    t = t[t["fatura_id"].notna() & (t["tarih"] <= d)][["fatura_id", "tarih", "tutar_kurus"]]
    i = f[(f["tur"] == "iade") & f["iliskili_fatura_id"].notna() & (f["fatura_tarihi"] <= d)]
    i = pd.DataFrame({"fatura_id": i["iliskili_fatura_id"], "tarih": i["fatura_tarihi"],
                      "tutar_kurus": i["uygulanan_kurus"].fillna(0)})
    credit_events = pd.concat([t, i]).groupby(["fatura_id", "tarih"], as_index=False)["tutar_kurus"].sum()
    credit_events = credit_events.sort_values(["fatura_id", "tarih"])
    credit_events["kumulatif"] = credit_events.groupby("fatura_id")["tutar_kurus"].cumsum()
    sales_rows = f[(f["tur"] == "satis") & (f["fatura_tarihi"] <= d)]
    amount = dict(zip(sales_rows["fatura_id"], sales_rows["tutar_kurus"]))
    first_close = {}
    for invoice_id, event_day, cumulative in zip(credit_events["fatura_id"], credit_events["tarih"], credit_events["kumulatif"]):
        if invoice_id in amount and invoice_id not in first_close and cumulative >= amount[invoice_id]:
            first_close[invoice_id] = event_day
    delays: dict[str, list[int]] = {}
    low = d - pd.Timedelta(days=gun - 1)
    for invoice_id, m, due, invoice_date, invoice_total in zip(sales_rows["fatura_id"], sales_rows["musteri_id"], sales_rows["vade_tarihi"], sales_rows["fatura_tarihi"],
                                   sales_rows["tutar_kurus"]):
        closing = invoice_date if invoice_total <= 0 else first_close.get(invoice_id)
        if closing is None or not (low <= closing <= d):
            continue
        delays.setdefault(m, []).append(max(0, (closing - due).days))
    return delays


def _average_delays(T, d: pd.Timestamp) -> tuple[dict[str, int], int]:
    g = _closed_delays(T, d)
    all_values = [x for v in g.values() for x in v]
    company_average = sum(all_values) // len(all_values) if all_values else 0
    return {m: sum(v) // len(v) for m, v in g.items()}, company_average


def payment_history(T, tarih: dt.date, gun: int = 365) -> dict:
    g = _closed_delays(T, _timestamp(tarih), gun)
    customers = {m: {"fatura_sayisi": len(v), "gec_kapanan": sum(1 for x in v if x > 0),
                      "ortalama_gecikme_gun": sum(v) // len(v)} for m, v in sorted(g.items())}
    return {"tarih": tarih, "gun": gun, "musteriler": customers,
            "kapanan_fatura": sum(m["fatura_sayisi"] for m in customers.values())}


def cash_flow_forecast(T, tarih: dt.date, hafta: int = 13) -> dict:
    d = _timestamp(tarih)
    rates = _rates(T, d)
    tomorrow = d + pd.Timedelta(days=1)
    currency = dict(zip(T["musteriler"]["musteri_id"], T["musteriler"]["para_birimi"]))

    cheque_moves = T["cek_hareketleri"]
    cheque_moves = cheque_moves[cheque_moves["tarih"] <= d].sort_values(["cek_id", "tarih", "cek_hareket_id"])
    last_status = cheque_moves.groupby("cek_id")["durum"].last()
    c = T["cekler"]
    c = c[(c["alis_tarihi"] <= d) & c["cek_id"].map(last_status).isin(["portfoyde", "tahsilde"])]
    t = T["tahsilatlar"]
    unmatched = t[t["fatura_id"].isna() & (t["tarih"] <= d)]
    pool = pd.concat([c[["musteri_id", "tutar_kurus"]], unmatched[["musteri_id", "tutar_kurus"]]]
                      ).groupby("musteri_id")["tutar_kurus"].sum()

    open_rows = _open_receivables(T, d).sort_values(["musteri_id", "vade_tarihi", "fatura_id"])
    open_rows["onceki"] = open_rows.groupby("musteri_id")["acik_kurus"].cumsum() - open_rows["acik_kurus"]
    averages, company_average = _average_delays(T, d)

    items = []  # (kind, day or None, amount)
    for m, a, previous, g, due, p in zip(open_rows["musteri_id"], open_rows["acik_kurus"], open_rows["onceki"], open_rows["gecikme_gun"],
                                        open_rows["vade_tarihi"], open_rows["para_birimi"]):
        remaining = int(a) - min(int(a), max(0, int(pool.get(m, 0)) - int(previous)))
        if remaining <= 0:
            continue
        gun = None if g > 90 else max(due + pd.Timedelta(days=averages.get(m, company_average)), tomorrow)
        items.append(("alacak", gun, apply_rate(remaining, _rate(rates, p))))
    for m, due, amount in zip(c["musteri_id"], c["vade_tarihi"], c["tutar_kurus"]):
        items.append(("cek", max(due, tomorrow), apply_rate(amount, _rate(rates, currency[m]))))
    p = T["pos_islemleri"]
    p = p[(p["islem_tarihi"] <= d) & (p["valor_tarihi"] > d)]
    for value_date, amount, commission in zip(p["valor_tarihi"], p["tutar_kurus"], p["komisyon_kurus"]):
        items.append(("pos", value_date, int(amount) - int(commission)))
    b = _open_payables(T, d)
    b = b[b["acik_kurus"] > 0]
    for due, amount in zip(b["vade_tarihi"], b["acik_kurus"]):
        items.append(("tedarikci", max(due, tomorrow), int(amount)))

    buckets: dict[tuple, int] = {}
    for kind, gun, amount in items:
        if gun is None:
            k = -1
        else:
            k = ((gun - d).days - 1) // 7 + 1
            k = 0 if k > hafta else k
        buckets[(kind, k)] = buckets.get((kind, k), 0) + amount

    opening = cash_position(T, tarih)["toplam_try_kurus"]
    weeks, balance = [], opening
    for k in range(1, hafta + 1):
        inflow = {kind: buckets.get((kind, k), 0) for kind in ("alacak", "cek", "pos")}
        outflow = buckets.get(("tedarikci", k), 0)
        net = sum(inflow.values()) - outflow
        balance += net
        weeks.append({"hafta": k, "bas": tarih + dt.timedelta(days=7 * (k - 1) + 1), "bit": tarih + dt.timedelta(days=7 * k),
                         "alacak_girisi_kurus": inflow["alacak"], "cek_girisi_kurus": inflow["cek"],
                         "pos_girisi_kurus": inflow["pos"], "tedarikci_cikisi_kurus": outflow, "net_kurus": net,
                         "bilinen_kalemlerle_bakiye_kurus": balance})
    return {"tarih": tarih, "hafta_sayisi": hafta, "acilis_kurus": opening, "haftalar": weeks,
            "ufuk_disi_giris_kurus": sum(buckets.get((kind, 0), 0) for kind in ("alacak", "cek", "pos")),
            "ufuk_disi_cikis_kurus": buckets.get(("tedarikci", 0), 0),
            "supheli_alacak_kurus": buckets.get(("alacak", -1), 0)}


METRICS = {
    "nakit_pozisyonu": cash_position,
    "yaslandirma": aging,
    "dso": dso,
    "geciken_alacaklar": overdue_receivables,
    "yaklasan_odemeler": upcoming_payments,
    "kur_riski": fx_risk,
    "nakit_akis_tahmini": cash_flow_forecast,
    "odeme_gecmisi": payment_history,
}
