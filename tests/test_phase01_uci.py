"""Phase 1: UCI cleaning rules, money helpers and the simulation clock (with sample data, no download needed)."""

import datetime as dt
from collections import Counter
from decimal import Decimal

import pytest

from sayman.data.sample_data import SPECIAL_ROWS, SHEET_1_LAST, SHEET_2, sample_raw_rows
from sayman.data.money import to_cents, from_cents, to_decimal, apply_rate
from sayman.data.clock import Clock, read_clock, write_clock
from sayman.data.uci import DROP_RULES, KEPT_CATEGORIES, read_raw_csv, write_raw_csv, clean


@pytest.fixture(scope="module")
def raw_rows():
    return sample_raw_rows()


@pytest.fixture(scope="module")
def cleaned(raw_rows):
    return clean(raw_rows)


def test_every_row_is_kept_or_dropped_by_a_named_rule(cleaned):
    assert cleaned.reconciles()
    assert set(cleaned.dropped) <= set(DROP_RULES)
    assert set(cleaned.kept) <= set(KEPT_CATEGORIES)
    assert len(cleaned.line_items) == sum(cleaned.kept.values())


def test_special_rows_are_dropped_by_the_right_rule(cleaned):
    for rule, count in SPECIAL_ROWS.items():
        assert cleaned.dropped[rule] == count, rule


def test_sheet_repeats_are_dropped(raw_rows, cleaned):
    expected = sum(1 for s in raw_rows if s["sayfa"] == SHEET_2 and s["InvoiceDate"] <= SHEET_1_LAST.isoformat(sep=" ")
                   and s["InvoiceDate"][:4].isdigit())
    assert expected > 0
    assert cleaned.dropped["sayfa_tekrari"] == expected


def test_sheet_repeats_match_like_a_multiset():
    row = {"Invoice": "500000", "StockCode": "85123A", "Description": "X", "Quantity": "1",
             "InvoiceDate": "2010-12-02 10:00:00", "Price": "2.55", "Customer ID": "12346", "Country": "United Kingdom"}
    raw_rows = [dict(row, sayfa="Year 2009-2010"), dict(row, sayfa="Year 2009-2010"),
           dict(row, sayfa="Year 2010-2011"), dict(row, sayfa="Year 2010-2011"), dict(row, sayfa="Year 2010-2011")]
    s = clean(raw_rows)
    assert s.dropped["sayfa_tekrari"] == 2  # two of the three copies on the second sheet have a counterpart on the first sheet
    assert s.kept["satis"] == 3


def test_c_invoices_are_returns_with_negative_quantity(cleaned):
    for k in cleaned.line_items:
        cancelled = k.invoice_no.startswith("C")
        assert (k.category in ("iade", "perakende_iade")) == cancelled
        assert (k.quantity < 0) == cancelled
        assert k.price > 0
        assert isinstance(k.amount, Decimal)
        assert (k.customer_no is None) == k.category.startswith("perakende")


def test_negative_non_c_and_positive_c_rows_are_dropped():
    base = {"sayfa": "S", "StockCode": "1", "Description": "", "InvoiceDate": "2011-01-03 10:00:00",
             "Price": "1.00", "Customer ID": "12346", "Country": "United Kingdom"}
    s = clean([dict(base, Invoice="1", Quantity="-5"), dict(base, Invoice="C2", Quantity="5"),
                 dict(base, Invoice="C3", Quantity="-5"), dict(base, Invoice="4", Quantity="5")])
    assert s.dropped == Counter({"stok_duzeltmesi": 1, "tutarsiz_iade": 1})
    assert s.kept == Counter({"iade": 1, "satis": 1})


def test_customer_number_is_cleaned_of_decimal_format():
    base = {"sayfa": "S", "Invoice": "1", "StockCode": "1", "Description": "", "Quantity": "1",
             "InvoiceDate": "2011-01-03 10:00:00", "Price": "1.00", "Country": "United Kingdom"}
    s = clean([dict(base, **{"Customer ID": "12346.0"}), dict(base, **{"Customer ID": "nan"})])
    assert [k.customer_no for k in s.line_items] == ["12346", None]


def test_raw_csv_gz_round_trip(raw_rows, tmp_path):
    path = tmp_path / "ham.csv.gz"
    assert write_raw_csv(raw_rows, path) == len(raw_rows)
    read_back = list(read_raw_csv(path))
    assert read_back == raw_rows
    assert clean(read_back).line_items == clean(raw_rows).line_items


def test_money_rejects_float_and_rounds_to_cents():
    with pytest.raises(TypeError):
        to_decimal(1.5)
    assert to_cents(Decimal("1.005")) == 100  # ties round to even
    assert to_cents(Decimal("1.015")) == 102
    assert from_cents(12345) == Decimal("123.45")
    assert apply_rate(10_000, Decimal("0.02")) == 200


def test_clock_advances_but_not_past_the_data_end(tmp_path):
    clock = Clock(dt.date(2026, 9, 1), dt.date(2026, 11, 6))
    assert clock.advance(30).simdi == dt.date(2026, 10, 1)
    assert clock.advance(365).simdi == dt.date(2026, 11, 6)
    with pytest.raises(ValueError):
        clock.advance(-1)
    with pytest.raises(ValueError):
        Clock(dt.date(2026, 12, 1), dt.date(2026, 11, 6))
    write_clock(clock, tmp_path / "saat.json")
    assert read_clock(tmp_path / "saat.json") == clock


def test_text_file_digest_is_independent_of_line_endings(tmp_path):
    from sayman.data.table_io import file_digest

    (tmp_path / "lf.csv").write_bytes(b"a,b\n1,2\n")
    (tmp_path / "crlf.csv").write_bytes(b"a,b\r\n1,2\r\n")
    assert file_digest(tmp_path / "lf.csv", as_text=True) == file_digest(tmp_path / "crlf.csv", as_text=True)
    assert file_digest(tmp_path / "lf.csv") != file_digest(tmp_path / "crlf.csv")
