"""CBRT (TCMB) foreign exchange rates (forex buying). Source: TCMB EVDS, series TP.DK.USD.A, TP.DK.EUR.A, TP.DK.GBP.A.

The rates are downloaded once with the download script and saved to data/kaynak/tcmb_kurlar.csv;
the generator and the tests only read that file. On weekends and holidays the last published rate is used.
"""

from __future__ import annotations

import bisect
import csv
import datetime as dt
from decimal import Decimal
from pathlib import Path

EVDS_SERIES = {"USD": "TP.DK.USD.A", "EUR": "TP.DK.EUR.A", "GBP": "TP.DK.GBP.A"}


class ExchangeRates:
    def __init__(self, rows: list[tuple[dt.date, str, Decimal]]):
        self._dates: dict[str, list[dt.date]] = {}
        self._values: dict[str, list[Decimal]] = {}
        for date, currency, rate in sorted(rows):
            self._dates.setdefault(currency, []).append(date)
            self._values.setdefault(currency, []).append(rate)

    @property
    def currencies(self) -> list[str]:
        return sorted(self._dates)

    def first_date(self, currency: str) -> dt.date:
        return self._dates[currency][0]

    def last_date(self, currency: str) -> dt.date:
        return self._dates[currency][-1]

    def rate(self, currency: str, date: dt.date) -> Decimal:
        """Last rate published on or before the given day. 1 for TRY."""
        if currency == "TRY":
            return Decimal(1)
        dates = self._dates[currency]
        i = bisect.bisect_right(dates, date) - 1
        if i < 0:
            raise KeyError(f"{currency} için {date} tarihinde ya da öncesinde kur yok")
        return self._values[currency][i]

    def rows(self, start: dt.date, end: dt.date) -> list[tuple[dt.date, str, Decimal]]:
        result = []
        for currency in self.currencies:
            for t, k in zip(self._dates[currency], self._values[currency]):
                if start <= t <= end:
                    result.append((t, currency, k))
        return sorted(result)


def read_rates(path: Path) -> ExchangeRates:
    with open(path, encoding="utf-8", newline="") as f:
        rows = [
            (dt.date.fromisoformat(s["tarih"]), s["para_birimi"], Decimal(s["alis"]))
            for s in csv.DictReader(f)
        ]
    return ExchangeRates(rows)


def write_rates(rows: list[tuple[dt.date, str, Decimal]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["tarih", "para_birimi", "alis"])
        for date, currency, rate in sorted(rows):
            writer.writerow([date.isoformat(), currency, str(rate)])
