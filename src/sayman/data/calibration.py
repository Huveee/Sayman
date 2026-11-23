"""Calibration measurements: computed from the generated tables and compared with the bands in data/kaynak/kalibrasyon.json.

The bands were written from real sources before the generator was tuned (scripts/phase01_calibration_sources.py).
Tuning never changes the bands; only the generator settings change.
"""

from __future__ import annotations

import bisect
import datetime as dt
from decimal import Decimal

from sayman.data.as_of import open_amounts, closing_dates
from sayman.data.money import to_cents, from_cents

DELAY_BUCKETS = (("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6))


def _rate_lookup(tables):
    series: dict[str, tuple[list, list]] = {}
    for k in sorted(tables["doviz_kurlari"], key=lambda k: (k["para_birimi"], k["tarih"])):
        t, d = series.setdefault(k["para_birimi"], ([], []))
        t.append(k["tarih"])
        d.append(Decimal(k["alis"]))

    def rate_on(currency: str, date: dt.date) -> Decimal:
        if currency == "TRY":
            return Decimal(1)
        t, d = series[currency]
        return d[max(0, bisect.bisect_right(t, date) - 1)]

    return rate_on


def open_receivable_try(tables, date: dt.date) -> int:
    """Open trade receivable on the given day (TRY cents). The open amount is converted at the invoice rate;
    collections that cannot be tied to an invoice are deducted from the receivable (a credit on the customer account)."""
    rate_on = _rate_lookup(tables)
    invoice_rate = {f["fatura_id"]: Decimal(f["kur"]) for f in tables["faturalar"]}
    total = sum(to_cents(from_cents(a) * invoice_rate[invoice_id]) for invoice_id, a in open_amounts(tables, date).items() if a > 0)
    for t in tables["tahsilatlar"]:
        if t["fatura_id"] is None and t["tarih"] <= date:
            total -= to_cents(from_cents(t["tutar_kurus"]) * rate_on(t["para_birimi"], t["tarih"]))
    return total


def net_sales_try(tables, start: dt.date, end: dt.date) -> int:
    """TRY total of sales and return invoices plus POS sales."""
    invoiced = sum(f["tutar_try_kurus"] for f in tables["faturalar"] if start <= f["fatura_tarihi"] <= end)
    pos = sum(p["tutar_kurus"] for p in tables["pos_islemleri"] if start <= p["islem_tarihi"] <= end)
    return invoiced + pos


def dso(tables, year: int) -> float:
    """365 x (open trade receivable at year end) / (net sales for the year)."""
    receivable = open_receivable_try(tables, dt.date(year, 12, 31))
    sales = net_sales_try(tables, dt.date(year, 1, 1), dt.date(year, 12, 31))
    return round(365 * receivable / sales, 2)


def _mature_invoices(tables, end: dt.date, wait_days: int):
    return [f for f in tables["faturalar"]
            if f["tur"] == "satis" and f["vade_tarihi"] + dt.timedelta(days=wait_days) <= end]


def late_payment_ratio(tables, end: dt.date, wait_days: int = 180) -> tuple[float, int]:
    """Share (by count) of sales invoices at least `wait_days` days past due that were closed after the due date or
    never closed. Returns (ratio, invoice count)."""
    closing = closing_dates(tables, end)
    mature = _mature_invoices(tables, end, wait_days)
    late = sum(1 for f in mature if closing[f["fatura_id"]] is None or closing[f["fatura_id"]] > f["vade_tarihi"])
    return (round(late / len(mature), 4) if mature else 0.0), len(mature)


def delay_distribution(tables, end: dt.date, wait_days: int = 180) -> tuple[dict[str, float], int]:
    """Distribution of late-closed invoices by how many days after the due date they were closed."""
    closing = closing_dates(tables, end)
    days = []
    for f in _mature_invoices(tables, end, wait_days):
        k = closing[f["fatura_id"]]
        if k is not None and k > f["vade_tarihi"]:
            days.append((k - f["vade_tarihi"]).days)
    shares = {name: round(sum(1 for g in days if lower <= g <= upper) / len(days), 4) if days else 0.0
              for name, lower, upper in DELAY_BUCKETS}
    return shares, len(days)


def measure(tables, end: dt.date, dso_year: int) -> dict:
    ratio, n = late_payment_ratio(tables, end)
    distribution, late_count = delay_distribution(tables, end)
    return {"dso_gun": dso(tables, dso_year), "dso_yili": dso_year, "gec_odenen_fatura_orani": ratio,
            "olgun_fatura_sayisi": n, "gecikme_dagilimi": distribution, "gec_kapanan_fatura_sayisi": late_count}


def compare_with_bands(measurement: dict, calibration: dict) -> list[dict]:
    """One {ad, deger, alt, ust, tamam} row per measurement."""
    result = []
    lower, upper = calibration["dso_gun"]["bant"]
    result.append({"ad": "dso_gun", "deger": measurement["dso_gun"], "alt": lower, "ust": upper, "tamam": lower <= measurement["dso_gun"] <= upper})
    lower, upper = calibration["gec_odenen_fatura_orani"]["bant"]
    d = measurement["gec_odenen_fatura_orani"]
    result.append({"ad": "gec_odenen_fatura_orani", "deger": d, "alt": lower, "ust": upper, "tamam": lower <= d <= upper})
    tolerance = calibration["gecikme_dagilimi"]["tolerans"]
    for bucket, share in calibration["gecikme_dagilimi"]["paylar"].items():
        d = measurement["gecikme_dagilimi"][bucket]
        lower, upper = round(share - tolerance, 4), round(share + tolerance, 4)
        result.append({"ad": f"gecikme_{bucket}", "deger": d, "alt": lower, "ust": upper, "tamam": lower <= d <= upper})
    return result
