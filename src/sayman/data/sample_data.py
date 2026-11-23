"""Small sample data for tests: raw rows in the UCI format and synthetic rates, produced in code.

It carries the oddities of the real file: two sheets with overlapping days, cancellation invoices starting with C,
rows without a customer (retail), an A adjustment invoice, non-sales codes, zero prices, a stock correction
with negative quantity, a cancellation with positive quantity and an unreadable row. No download is needed.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

from sayman.data.exchange_rates import ExchangeRates

UCI_START = dt.datetime(2009, 12, 1, 8, 26)
UCI_END = dt.datetime(2011, 12, 9, 12, 50)
SHEET_1, SHEET_2 = "Year 2009-2010", "Year 2010-2011"
SHEET_1_LAST = dt.datetime(2010, 12, 9, 23, 59)
SHEET_2_FIRST = dt.datetime(2010, 12, 1)
COUNTRIES = (("United Kingdom", 0.80), ("Germany", 0.06), ("France", 0.05), ("EIRE", 0.03), ("USA", 0.03),
           ("Australia", 0.03))

# Expected drop counts (the special rows added by sample_raw_rows).
SPECIAL_ROWS = {"duzeltme_faturasi": 1, "satis_disi_kod": 6, "gecersiz_fiyat": 1, "stok_duzeltmesi": 1,
                 "tutarsiz_iade": 1, "okunamayan_satir": 1}


def sample_rates(start: dt.date = dt.date(2024, 9, 2), end: dt.date = dt.date(2026, 11, 30)) -> ExchangeRates:
    """Synthetic GBP, EUR and USD buying rates that rise slowly on business days."""
    rows, d, i = [], start, 0
    while d <= end:
        if d.weekday() < 5:
            gbp = Decimal("44") + Decimal(i) * Decimal("0.025")
            for currency, k in (("GBP", gbp), ("EUR", gbp * Decimal("0.84")), ("USD", gbp * Decimal("0.77"))):
                rows.append((d, currency, k.quantize(Decimal("0.0001"))))
            i += 1
        d += dt.timedelta(days=1)
    return ExchangeRates(rows)


def _row(sheet, invoice, stock_code, quantity, date, price, customer, country) -> dict:
    return {"sayfa": sheet, "Invoice": invoice, "StockCode": stock_code, "Description": f"URUN {stock_code}",
            "Quantity": str(quantity), "InvoiceDate": date if isinstance(date, str) else date.isoformat(sep=" "),
            "Price": str(price), "Customer ID": customer, "Country": country}


def sample_raw_rows(seed: int = 7, customer_count: int = 80) -> list[dict]:
    rng = random.Random(f"ornek:{seed}")
    span_seconds = int((UCI_END - UCI_START).total_seconds())
    stock_codes = [str(20000 + 37 * i) for i in range(150)]
    price = {s: Decimal(rng.randint(29, 1275)) / 100 for s in stock_codes}

    def random_moment(lower: dt.datetime = UCI_START) -> dt.datetime:
        z = lower + dt.timedelta(seconds=rng.randint(0, int((UCI_END - lower).total_seconds())))
        z = z.replace(hour=rng.randint(8, 18), second=0)
        return min(max(z, UCI_START), UCI_END)

    invoice_headers = []  # (time, customer, country, is cancellation, lines)
    for i in range(customer_count):
        customer = str(12346 + i)
        r, total, country = rng.random(), 0.0, COUNTRIES[-1][0]
        for u, p in COUNTRIES:
            total += p
            if r < total:
                country = u
                break
        first = UCI_START + dt.timedelta(seconds=rng.randint(0, span_seconds // 2))
        for _ in range(min(120, int(rng.expovariate(1 / 18)) + 1)):
            rows = [(s, rng.randint(1, 48)) for s in rng.sample(stock_codes, rng.randint(1, 12))]
            z = random_moment(first)
            invoice_headers.append((z, customer, country, False, rows))
            if rng.random() < 0.08:
                returned_lines = [(s, -rng.randint(1, q)) for s, q in rows[: rng.randint(1, 2)]]
                invoice_headers.append((min(z + dt.timedelta(days=rng.randint(1, 20)), UCI_END), customer, country, True, returned_lines))
    for _ in range(customer_count * 2):  # retail sales without a customer, and a few returns of them
        rows = [(s, rng.randint(1, 6)) for s in rng.sample(stock_codes, rng.randint(1, 5))]
        z = random_moment()
        invoice_headers.append((z, "", "United Kingdom", False, rows))
        if rng.random() < 0.05:
            invoice_headers.append((z + dt.timedelta(hours=2), "", "United Kingdom", True, [(rows[0][0], -1)]))
    # Edge cases: first and last day, and a customer whose first transaction is a return (no invoice to link).
    invoice_headers.append((UCI_START, "12346", "United Kingdom", False, [(stock_codes[0], 6)]))
    invoice_headers.append((UCI_END, "12347", "United Kingdom", False, [(stock_codes[1], 12)]))
    invoice_headers.append((dt.datetime(2010, 3, 1, 10), str(12346 + customer_count), "United Kingdom", True,
                      [(stock_codes[2], -3)]))
    # Side-account shortfalls: a large POS return (pushes B_TRY negative) and a euro customer whose first
    # transaction is a large return (its refund leaves B_EUR at the start of the month). A_TRY must cover both.
    invoice_headers.append((dt.datetime(2010, 7, 6, 11), "", "United Kingdom", True, [(stock_codes[7], -400)]))
    invoice_headers.append((dt.datetime(2010, 6, 1, 10), str(12347 + customer_count), "Germany", True,
                      [(stock_codes[8], -2000)]))

    raw_rows, invoice_number = [], 489434
    for z, customer, country, cancelled, rows in sorted(invoice_headers, key=lambda b: (b[0], b[1], b[3])):
        invoice = ("C" if cancelled else "") + str(invoice_number)
        invoice_number += 1
        for stock_code, quantity in rows:
            record = (invoice, stock_code, quantity, z, price[stock_code], customer, country)
            if z <= SHEET_1_LAST:
                raw_rows.append(_row(SHEET_1, *record))
            if z >= SHEET_2_FIRST:  # overlapping days appear on both sheets
                raw_rows.append(_row(SHEET_2, *record))

    t = dt.datetime(2011, 6, 1, 10)
    raw_rows += [
        _row(SHEET_2, "A563185", "B", 1, t, "11062.06", "", "United Kingdom"),
        _row(SHEET_2, "C563186", "AMAZONFEE", -1, t, "5575.28", "", "United Kingdom"),
        _row(SHEET_2, "563187", "BANK CHARGES", 1, t, "15.00", "12350", "United Kingdom"),
        # Manual correction pair (an amount is cancelled on one record and re-entered without a customer), sample and commission.
        _row(SHEET_2, "C563192", "M", -1, t, "2511.09", "12351", "United Kingdom"),
        _row(SHEET_2, "563193", "M", 1, t, "2511.09", "", "United Kingdom"),
        _row(SHEET_2, "C563194", "S", -1, t, "60.65", "", "United Kingdom"),
        _row(SHEET_2, "C563195", "CRUK", -1, t, "79.33", "12351", "United Kingdom"),
        _row(SHEET_2, "563188", stock_codes[3], 4, t, "0", "12350", "United Kingdom"),
        _row(SHEET_2, "563189", stock_codes[4], -24, t, "0.85", "", "United Kingdom"),
        _row(SHEET_2, "C563190", stock_codes[5], 2, t, "1.25", "12350", "United Kingdom"),
        _row(SHEET_2, "563191", stock_codes[6], 2, "tarih yok", "1.25", "12350", "United Kingdom"),
    ]
    return raw_rows
