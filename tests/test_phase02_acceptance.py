"""Phase 2 acceptance test: on tables generated from the real UCI data.

The Phase 1 scripts and scripts/phase02_build_warehouse.py must have been run first. In this test the warehouse
is rebuilt from the CSVs; the file built by the script is checked separately.

Acceptance criterion: every metric is identical to an independent pandas computation on the same data;
the aging buckets sum to the total receivable. The total receivable is compared with Phase 1's separately written
computation (`calibration.acik_alacak_try`); a sum of the same numbers is never compared with itself.
"""

import datetime as dt
import json
import time
from pathlib import Path

from support import pandas_reference
import pytest

from sayman.warehouse import metrics
from sayman.warehouse.loader import build_warehouse, open_warehouse, query_rows
from sayman.data import calibration
from sayman.data.table_io import SCHEMA, read_csv, write_csv
from sayman.data.as_of import open_amounts, bank_balances, as_of_view
from sayman.data.clock import read_clock

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "uretilen"
PHASE1_REPORT = ROOT / "reports" / "faz1_veri.json"
ANSWER_KEY = ROOT / "data" / "anahtar" / "olaylar.json"
WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"

pytestmark = pytest.mark.skipif(not (GENERATED.exists() and PHASE1_REPORT.exists() and ANSWER_KEY.exists()),
                                reason="Önce phase01_download_uci.py ve phase01_generate_data.py çalıştırılmalı")
iso_date = dt.date.fromisoformat


@pytest.fixture(scope="module")
def phase1_report():
    return json.loads(PHASE1_REPORT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tables():
    return read_csv(GENERATED)


@pytest.fixture(scope="module")
def con():
    return build_warehouse(GENERATED)


@pytest.fixture(scope="module")
def P():
    return pandas_reference.read_tables(GENERATED)


@pytest.fixture(scope="module")
def days(phase1_report):
    """Every event kind is live on at least one day: event days, squeeze ends and a week after them; also the first
    day, a Saturday, the end of 2025, now and the last day."""
    answer_key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    end = iso_date(phase1_report["ayarlar"]["veri_bitis"])
    days = {iso_date(phase1_report["veri_baslangic"]), iso_date("2025-06-14"), iso_date("2025-12-31"), iso_date(phase1_report["ayarlar"]["simdi"]), end}
    for o in answer_key["olaylar"]:
        for field in ("tarih", "bitis"):
            if o.get(field):
                days |= {iso_date(o[field]), min(iso_date(o[field]) + dt.timedelta(days=7), end)}
    assert {o["tur"] for o in answer_key["olaylar"]} == set(dict(phase1_report["ayarlar"]["olay_sayilari"]))
    return sorted(days)


def test_warehouse_file_was_built_from_phase1_data(phase1_report):
    assert WAREHOUSE.exists(), "Önce scripts/phase02_build_warehouse.py çalıştırılmalı"
    file_con = open_warehouse(WAREHOUSE)
    try:
        assert file_con.execute("SELECT deger FROM ambar_bilgisi WHERE anahtar = 'veri_hash'").fetchone()[0] == \
            phase1_report["veri_hash"]
        for name, columns in SCHEMA.items():
            assert file_con.execute(f"SELECT count(*) FROM {name}").fetchone()[0] == phase1_report["tablo_satirlari"][name], name
    finally:
        file_con.close()


def test_warehouse_matches_csv_exactly(con, tables):
    from decimal import Decimal

    for name, columns in SCHEMA.items():
        csv = sorted((dict(s) for s in tables[name]), key=lambda s: s[columns[0]])  # copy: does not modify the tables
        for s in csv:
            for k in ("kur", "alis"):
                if s.get(k) is not None:
                    s[k] = Decimal(s[k])
        assert query_rows(con, f"SELECT * FROM {name} ORDER BY {columns[0]}") == csv, name


PARAMETERIZED = (("dso", {"gun": 90}), ("geciken_alacaklar", {"en_az_gun": 31}), ("yaklasan_odemeler", {"gun": 7}),
               ("nakit_akis_tahmini", {"hafta": 4}))


def test_sql_and_pandas_are_identical_for_every_metric(con, P, days):
    for day in days:
        for name, params in [(name, {}) for name in metrics.METRICS] + list(PARAMETERIZED):
            sql = metrics.METRICS[name](con, day, **params)
            sql.pop("dahil_olmayanlar", None)
            assert sql == pandas_reference.METRICS[name](P, day, **params), f"{name} {params} @ {day}"


def test_matches_the_phase1_computations(con, tables, days):
    last_balance = {}
    for h in sorted(tables["banka_hareketleri"], key=lambda h: h["hareket_id"]):
        last_balance.setdefault(h["tarih"], {})[h["hesap_id"]] = h["bakiye_kurus"]
    for day in days:
        accounts = {h["hesap_id"]: h["bakiye_kurus"] for h in metrics.cash_position(con, day)["hesaplar"]}
        assert accounts == bank_balances(tables, day), day
        y = metrics.aging(con, day)
        assert y["toplam"]["toplam_kurus"] == calibration.open_receivable_try(tables, day), day
        buckets = sum(v for k, v in y["toplam"].items() if k != "toplam_kurus")
        assert buckets == y["toplam"]["toplam_kurus"], day
        all_overdue = metrics.overdue_receivables(con, day, en_az_gun=-10**6)["faturalar"]
        assert {f["fatura_id"]: f["acik_kurus"] for f in all_overdue} == {
            k: v for k, v in open_amounts(tables, day).items() if v > 0}, day
    # The "balance" column in the bank rows (Phase 1 ledger) and the end-of-last-day balances are the same too.
    latest = {}
    for day in sorted(last_balance):
        latest.update(last_balance[day])
    end = max(last_balance)
    assert {h["hesap_id"]: h["bakiye_kurus"] for h in metrics.cash_position(con, end)["hesaplar"]} == latest


def test_dso_2025_matches_the_phase1_calibration(con, tables, phase1_report):
    d = metrics.dso(con, iso_date("2025-12-31"))
    assert d["alacak_kurus"] == calibration.open_receivable_try(tables, iso_date("2025-12-31"))
    assert d["satis_kurus"] == calibration.net_sales_try(tables, iso_date("2025-01-01"), iso_date("2025-12-31"))
    assert abs(float(d["dso_gun"]) - phase1_report["kalibrasyon"]["olcum"]["dso_gun"]) < 0.006


def test_future_does_not_leak(con, tables, phase1_report, tmp_path):
    """The metric computed on a given day with all the data equals the one from a warehouse built only from the
    records up to that day."""
    answer_key = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    squeeze = next(iso_date(o["tarih"]) + dt.timedelta(days=3) for o in answer_key["olaylar"] if o["tur"] == "nakit_sikismasi")
    for day in (iso_date(phase1_report["ayarlar"]["simdi"]), squeeze):
        truncated = build_warehouse(_write(as_of_view(tables, day), tmp_path / day.isoformat()))
        for name, func in metrics.METRICS.items():
            assert func(con, day) == func(truncated, day), f"{name} @ {day}"


def _write(tables, folder):
    write_csv(tables, folder)
    return folder


def test_customers_with_cheques_all_use_try(tables):
    currency = {m["musteri_id"]: m["para_birimi"] for m in tables["musteriler"]}
    assert {currency[c["musteri_id"]] for c in tables["cekler"]} == {"TRY"}


def test_metrics_are_fast(phase1_report):
    """The tools will call these metrics once per question. The limit is generous: it must also pass while the
    model fills memory."""
    con = open_warehouse(WAREHOUSE)
    try:
        now = read_clock(ROOT / "data" / "saat.json").simdi
        for name, func in metrics.METRICS.items():
            start = time.perf_counter()
            func(con, now)
            assert time.perf_counter() - start < 5.0, name
    finally:
        con.close()
