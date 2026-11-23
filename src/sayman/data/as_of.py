"""As-of view: the data as it stood on a given day.

Tables store only dated facts (an invoice was issued, a collection arrived, a cheque was presented for collection).
Statuses (is an invoice open, where is a cheque) are derived from those facts, using only the ones up to the given day.
That way no record after "now" leaks to the tools.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

# table -> date column that decides visibility (None: master record, always visible)
DATE_COLUMN = {
    "musteriler": "ilk_islem_tarihi",
    "faturalar": "fatura_tarihi",
    "pos_islemleri": "islem_tarihi",
    "tahsilatlar": "tarih",
    "cekler": "alis_tarihi",
    "cek_hareketleri": "tarih",
    "tedarikciler": None,
    "alis_faturalari": "tarih",
    "odemeler": "tarih",
    "banka_hesaplari": "acilis_tarihi",
    "banka_hareketleri": "tarih",
    "doviz_kurlari": "tarih",
}


def as_of_view(tables: dict[str, list[dict]], date: dt.date) -> dict[str, list[dict]]:
    """Records that had happened by the end of the given day."""
    result = {}
    for name, rows in tables.items():
        column = DATE_COLUMN[name]
        result[name] = list(rows) if column is None else [s for s in rows if s[column] <= date]
    return result


def open_amounts(tables: dict[str, list[dict]], date: dt.date) -> dict[str, int]:
    """Open amount of each sales invoice on the given day (in the invoice currency, cents).
    Open = amount - matched collections - the applied part of linked return invoices."""
    g = as_of_view(tables, date)
    outstanding = {f["fatura_id"]: f["tutar_kurus"] for f in g["faturalar"] if f["tur"] == "satis"}
    for t in g["tahsilatlar"]:
        if t["fatura_id"] in outstanding:
            outstanding[t["fatura_id"]] -= t["tutar_kurus"]
    for f in g["faturalar"]:
        if f["tur"] == "iade" and f["iliskili_fatura_id"] in outstanding:
            outstanding[f["iliskili_fatura_id"]] -= f["uygulanan_kurus"] or 0
    return outstanding


def closing_dates(tables: dict[str, list[dict]], date: dt.date) -> dict[str, dt.date | None]:
    """Day on which each sales invoice's open amount reached zero (None if it was not closed by the given day)."""
    movements = defaultdict(list)
    for t in tables["tahsilatlar"]:
        if t["fatura_id"] and t["tarih"] <= date:
            movements[t["fatura_id"]].append((t["tarih"], t["tutar_kurus"]))
    for f in tables["faturalar"]:
        if f["tur"] == "iade" and f["iliskili_fatura_id"] and f["fatura_tarihi"] <= date:
            movements[f["iliskili_fatura_id"]].append((f["fatura_tarihi"], f["uygulanan_kurus"] or 0))
    result = {}
    for f in tables["faturalar"]:
        if f["tur"] != "satis" or f["fatura_tarihi"] > date:
            continue
        remaining, closing = f["tutar_kurus"], f["fatura_tarihi"] if f["tutar_kurus"] <= 0 else None
        for day, amount in sorted(movements[f["fatura_id"]]):
            if closing is not None:
                break
            remaining -= amount
            if remaining <= 0:
                closing = day
        result[f["fatura_id"]] = closing
    return result


def cheque_statuses(tables: dict[str, list[dict]], date: dt.date) -> dict[str, str]:
    """Latest status of each cheque on the given day (`portfoyde`, `tahsilde`, `tahsil_edildi`, `karsiliksiz`)."""
    latest: dict[str, tuple] = {}
    for h in tables["cek_hareketleri"]:
        if h["tarih"] <= date and (h["cek_id"] not in latest or (h["tarih"], h["cek_hareket_id"]) > latest[h["cek_id"]][:2]):
            latest[h["cek_id"]] = (h["tarih"], h["cek_hareket_id"], h["durum"])
    return {c: v[2] for c, v in latest.items()}


def bank_balances(tables: dict[str, list[dict]], date: dt.date) -> dict[str, int]:
    """Balance of each account at the end of the given day: opening balance + movements up to that day."""
    balance = {h["hesap_id"]: h["acilis_bakiye_kurus"] for h in tables["banka_hesaplari"] if h["acilis_tarihi"] <= date}
    for s in tables["banka_hareketleri"]:
        if s["tarih"] <= date:
            balance[s["hesap_id"]] += s["tutar_kurus"]
    return balance
