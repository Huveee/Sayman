"""Reading and cleaning the UCI Online Retail II data.

Source: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository.
https://doi.org/10.24432/C5CG6D. License: CC BY 4.0.

Every row is either kept (with a category) or dropped by a named rule. That way the reconciliation
"input rows = kept + dropped" can be tested.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator

from sayman.data.money import to_decimal

RAW_COLUMNS = [
    "sayfa",
    "Invoice",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "Price",
    "Customer ID",
    "Country",
]

# Decision made one by one for stock codes that are not product codes (starting with 5 digits).
# Written from the profile of the real file (reports/faz1_uci_profil.json, 13 November 2026).
# Codes not named here (web-shop products such as DCGS..., GIFT_0001_..., SP1002) stay as sales.
STOCK_CODE_DECISIONS = {
    "POST": ("tut", "Müşteriye faturalanan posta ücreti: satışın parçası"),
    "DOT": ("tut", "Web mağazası posta ücreti: perakende satışın parçası"),
    "C2": ("tut", "Müşteriye faturalanan taşıma ücreti"),
    "D": ("tut", "İskonto: C faturasında müşteriye verilen indirim, iade gibi işlenir (C'siz 5 satır, toplam ~£200, satış kalır)"),
    "PADS": ("tut", "Yastık dolgusu, birim fiyatı 0,001: önemsiz tutarlı gerçek ürün"),
    "M": ("at", "Elle düzeltme: büyükleri bir tutarın iptal edilip başka kayda yeniden girilmesi (ör. C512770/512771, "
                "£25.111). Tutulsaydı iade faturaları gerçek karşılıklarına bağlanamıyor, sahte geri ödemeler "
                "doğuyordu (2,36 milyon TL'ye kadar). Bilinen yan etki: yalnızca M ile iptal edilmiş 556444 "
                "(£38.970) satış olarak kalır, satışların yaklaşık %0,2'si"),
    "S": ("at", "Numune: müşterisiz C satırları, nakit iadesi değil"),
    "CRUK": ("at", "Hayır kurumuna ödenen komisyon: gider, müşteri iadesi değil"),
    "BANK CHARGES": ("at", "Banka masrafı: gider"),
    "AMAZONFEE": ("at", "Amazon komisyonu: gider"),
    "ADJUST": ("at", "Elle düzeltme"),
    "ADJUST2": ("at", "Elle düzeltme"),
    "B": ("at", "Şüpheli alacak düzeltmesi (A faturalarında)"),
    "TEST001": ("at", "Deneme ürünü"),
    "TEST002": ("at", "Deneme ürünü"),
}
NON_SALE_CODES = frozenset(k for k, (decision, _) in STOCK_CODE_DECISIONS.items() if decision == "at")

DROP_RULES = {
    "sayfa_tekrari": "İki Excel sayfasının çakışan tarihlerinde birebir aynı satır (ikinci sayfadaki kopya atılır)",
    "duzeltme_faturasi": "Fatura numarası A ile başlıyor: şüpheli alacak düzeltmesi, satış değil",
    "satis_disi_kod": "Stok kodu satış dışı bir gider ya da düzeltme (STOK_KODU_KARARLARI'nda 'at' olanlar)",
    "gecersiz_fiyat": "Birim fiyat sıfır ya da eksi (gerçek dosyada C'siz eksi miktarlı 3.457 depo düzeltmesi satırının "
                      "hepsinin fiyatı 0 olduğu için onlar da bu kuralla atılır)",
    "stok_duzeltmesi": "İptal faturası olmadığı hâlde miktar sıfır ya da eksi, fiyatı artı (depo düzeltmesi)",
    "tutarsiz_iade": "İptal faturası (C) ama miktar artı",
    "okunamayan_satir": "Tarih, miktar ya da fiyat okunamadı",
}

KEPT_CATEGORIES = {
    "satis": "Müşterili satış satırı",
    "iade": "Müşterili iptal/iade satırı (C ile başlayan fatura, eksi miktar)",
    "perakende": "Müşteri numarası olmayan satış: perakende/POS satışı olarak işlenir",
    "perakende_iade": "Müşteri numarası olmayan iade: POS iadesi olarak işlenir",
}


@dataclass(frozen=True, slots=True)
class LineItem:
    invoice_no: str
    stock_code: str
    quantity: int
    tarih: dt.datetime
    price: Decimal
    customer_no: str | None
    country: str
    category: str

    @property
    def amount(self) -> Decimal:
        return self.price * self.quantity


@dataclass
class CleaningResult:
    line_items: list[LineItem]
    input_rows: int
    kept: Counter = field(default_factory=Counter)
    dropped: Counter = field(default_factory=Counter)

    def reconciles(self) -> bool:
        return self.input_rows == sum(self.kept.values()) + sum(self.dropped.values())


def _customer_no(value: str) -> str | None:
    value = (value or "").strip()
    if not value or value.lower() == "nan":
        return None
    if value.endswith(".0"):
        value = value[:-2]
    return value


def _row_key(s: dict) -> tuple:
    return tuple(s.get(k, "") for k in RAW_COLUMNS[1:])


def _find_sheet_duplicates(rows: list[dict]) -> set[int]:
    """In the second sheet, returns the positions of the rows that have an exact counterpart in the first sheet (multiset)."""
    sheets = sorted({s["sayfa"] for s in rows})
    if len(sheets) < 2:
        return set()
    first = Counter(_row_key(s) for s in rows if s["sayfa"] == sheets[0])
    duplicates = set()
    for i, s in enumerate(rows):
        if s["sayfa"] == sheets[0]:
            continue
        key = _row_key(s)
        if first[key] > 0:
            first[key] -= 1
            duplicates.add(i)
    return duplicates


def clean(rows: Iterable[dict], non_sale_codes: frozenset = NON_SALE_CODES) -> CleaningResult:
    rows = list(rows)
    duplicates = _find_sheet_duplicates(rows)
    result = CleaningResult(line_items=[], input_rows=len(rows))
    for i, s in enumerate(rows):
        if i in duplicates:
            result.dropped["sayfa_tekrari"] += 1
            continue
        invoice_no = s["Invoice"].strip()
        stock_code = s["StockCode"].strip()
        cancelled = invoice_no.upper().startswith("C")
        if invoice_no.upper().startswith("A"):
            result.dropped["duzeltme_faturasi"] += 1
            continue
        if stock_code.upper() in non_sale_codes:
            result.dropped["satis_disi_kod"] += 1
            continue
        try:
            quantity = int(Decimal(s["Quantity"]))
            price = to_decimal(s["Price"])
            date = dt.datetime.fromisoformat(s["InvoiceDate"])
        except (InvalidOperation, ValueError):
            result.dropped["okunamayan_satir"] += 1
            continue
        if price <= 0:
            result.dropped["gecersiz_fiyat"] += 1
            continue
        if not cancelled and quantity <= 0:
            result.dropped["stok_duzeltmesi"] += 1
            continue
        if cancelled and quantity > 0:
            result.dropped["tutarsiz_iade"] += 1
            continue
        customer = _customer_no(s.get("Customer ID", ""))
        if customer:
            category = "iade" if cancelled else "satis"
        else:
            category = "perakende_iade" if cancelled else "perakende"
        result.kept[category] += 1
        result.line_items.append(
            LineItem(invoice_no, stock_code, quantity, date, price, customer, s["Country"].strip(), category)
        )
    return result


def read_raw_csv(path: Path) -> Iterator[dict]:
    """Reads the cached raw rows (.csv or .csv.gz, UTF-8)."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def write_raw_csv(rows: Iterable[dict], path: Path) -> int:
    """Writes compressed if the path ends in .gz; no time or file name goes into the gzip header,
    so the same content always gives the same bytes (the same SHA-256 digest)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "wb") as raw_file:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) if str(path).endswith(".gz") else None
        f = io.TextIOWrapper(gz or raw_file, encoding="utf-8", newline="")
        writer = csv.DictWriter(f, fieldnames=RAW_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for s in rows:
            writer.writerow(s)
            n += 1
        f.flush()
        f.detach()
        if gz:
            gz.close()
    return n


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value)


def read_xlsx(path: Path) -> Iterator[dict]:
    """Reads the original Excel file sheet by sheet. openpyxl is needed only here."""
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.sheetnames:
            rows = workbook[sheet].iter_rows(values_only=True)
            header = [str(h).strip() for h in next(rows)]
            for values in rows:
                if values is None or all(v is None for v in values):
                    continue
                s = {"sayfa": sheet}
                for name, value in zip(header, values):
                    s[name] = _cell_text(value)
                yield {k: s.get(k, "") for k in RAW_COLUMNS}
    finally:
        workbook.close()
