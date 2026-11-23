"""Phase 2: data warehouse and metrics (with small hand-computed data and sample data; no download needed).

Three separate checks:
1. A small hand-computed company: the expected value of every metric is written by hand in this file.
2. DuckDB (SQL) and pandas: each reads the same data separately and computes, the results must be identical.
3. Phase 1 computations: bank balances, open invoice amounts and the open receivable total.
"""

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import duckdb
from support import pandas_reference
import pytest

from sayman.warehouse import metrics
from sayman.warehouse.loader import MACROS, build_warehouse, open_warehouse, query_rows
from sayman.data import calibration
from sayman.data.table_io import SCHEMA, read_csv, write_csv
from sayman.data.as_of import open_amounts, bank_balances, as_of_view
from sayman.data.sample_data import sample_raw_rows, sample_rates
from sayman.data.money import to_cents, from_cents
from sayman.data.uci import clean
from sayman.data.generator import GeneratorSettings, generate

iso_date = dt.date.fromisoformat


def warehouse_from_tables(folder, tables, data_hash=None):
    write_csv(tables, folder)
    return build_warehouse(folder, data_digest=data_hash)


# Non-default parameters are compared too: the Phase 3 tools will change them.
PARAMETERIZED = (("dso", {"gun": 90}), ("geciken_alacaklar", {"en_az_gun": 31}), ("yaklasan_odemeler", {"gun": 7}),
               ("nakit_akis_tahmini", {"hafta": 4}))


def compare_sql_and_pandas(con, P, date):
    """Are the SQL and pandas results of the seven metrics (and their parameterized variants) identical?"""
    for name, params in [(name, {}) for name in metrics.METRICS] + list(PARAMETERIZED):
        sql = metrics.METRICS[name](con, date, **params)
        sql.pop("dahil_olmayanlar", None)
        assert sql == pandas_reference.METRICS[name](P, date, **params), f"{name} {params} @ {date}"


# ---------------------------------------------------------------- rounding


@pytest.mark.parametrize("value", ["2.5", "3.5", "-2.5", "-3.5", "0.5", "-0.5", "1.4999995", "-1.5000001", "7",
                                   "116171363.8027", "-0.000001"])
def test_rounding_ties_to_even_and_matches_python(value):
    con = duckdb.connect()
    for macro in MACROS:
        con.execute(macro)
    sql = con.execute(f"SELECT yuvarla_cift(CAST('{value}' AS DECIMAL(38,7)))").fetchone()[0]
    assert sql == to_cents(Decimal(value) / 100)


def test_rate_multiplication_matches_python_cents():
    con = duckdb.connect()
    for macro in MACROS:
        con.execute(macro)
    for cents, fx_rate in [(3188779, "36.4313"), (5, "0.5"), (15, "0.5"), (-15, "0.5"), (21000, "37.1234"), (1, "1")]:
        assert con.execute(f"SELECT kur_carp({cents}, CAST('{fx_rate}' AS DECIMAL(18,6)))").fetchone()[0] == \
            to_cents(from_cents(cents) * Decimal(fx_rate)), (cents, fx_rate)


# ---------------------------------------------------------------- hand-computed small company
#
# AS_OF = 31 March 2025 (Monday). The computation of every expected value is written next to it.


def _hand_built_tables() -> dict[str, list[dict]]:
    def invoice(invoice_id, kind, m, invoice_date, due, currency, amount, fx_rate, related_id=None, applied=None):
        return {"fatura_id": invoice_id, "kaynak_fatura_no": invoice_id, "tur": kind, "musteri_id": m, "fatura_tarihi": iso_date(invoice_date),
                "vade_tarihi": iso_date(due), "para_birimi": currency, "tutar_kurus": amount, "kur": fx_rate,
                "tutar_try_kurus": to_cents(from_cents(amount) * Decimal(fx_rate)), "iliskili_fatura_id": related_id,
                "iade_eslesme": "stok_kodu" if related_id else None, "uygulanan_kurus": applied, "kalem_sayisi": 1}

    def collection(collection_id, m, invoice_id, date, currency, amount, method="havale", cheque_id=None):
        return {"tahsilat_id": collection_id, "musteri_id": m, "fatura_id": invoice_id, "tarih": iso_date(date), "para_birimi": currency,
                "tutar_kurus": amount, "yontem": method, "cek_id": cheque_id, "banka_hesap_id": "A_TRY",
                "eslesme": "eslesti" if invoice_id else "bilerek_eslesmemis"}

    def purchase_invoice(purchase_id, s, date, due, amount, category="mal"):
        return {"alis_fatura_id": purchase_id, "tedarikci_id": s, "tarih": iso_date(date), "vade_tarihi": iso_date(due),
                "tutar_kurus": amount, "kategori": category}

    def payment(payment_id, kind, purchase_id, date, amount, s="S1"):
        return {"odeme_id": payment_id, "tur": kind, "tedarikci_id": s if purchase_id else None, "alis_fatura_id": purchase_id,
                "tarih": iso_date(date), "tutar_kurus": amount, "yontem": "havale", "banka_hesap_id": "A_TRY", "aciklama": payment_id}

    def transaction(transaction_id, compute, date, amount):
        return {"hareket_id": transaction_id, "hesap_id": compute, "tarih": iso_date(date), "tutar_kurus": amount, "bakiye_kurus": 0,
                "aciklama": transaction_id, "kaynak_tur": "test", "kaynak_id": transaction_id}

    return {
        "musteriler": [
            {"musteri_id": "M1", "ad": "Alfa Ltd.", "ulke": "Türkiye", "yurtici": True, "para_birimi": "TRY",
             "vade_gun": 30, "ilk_islem_tarihi": iso_date("2024-09-01")},
            {"musteri_id": "M2", "ad": "Beta GmbH", "ulke": "Almanya", "yurtici": False, "para_birimi": "EUR",
             "vade_gun": 60, "ilk_islem_tarihi": iso_date("2025-01-10")},
            {"musteri_id": "M3", "ad": "Gama AŞ", "ulke": "Türkiye", "yurtici": True, "para_birimi": "TRY",
             "vade_gun": 30, "ilk_islem_tarihi": iso_date("2025-04-15")},
            {"musteri_id": "M4", "ad": "Delta Ltd.", "ulke": "Türkiye", "yurtici": True, "para_birimi": "TRY",
             "vade_gun": 30, "ilk_islem_tarihi": iso_date("2024-11-01")},
        ],
        "faturalar": [
            invoice("F01", "satis", "M1", "2024-09-01", "2024-10-01", "TRY", 7000, "1"),
            invoice("F02", "satis", "M1", "2025-01-02", "2025-02-01", "TRY", 100000, "1"),
            invoice("F03", "satis", "M1", "2025-01-15", "2025-02-14", "TRY", 20000, "1"),
            invoice("F04", "satis", "M1", "2025-02-10", "2025-03-12", "TRY", 50000, "1"),
            invoice("F05", "satis", "M1", "2025-03-20", "2025-04-19", "TRY", 30000, "1"),
            invoice("F06", "iade", "M1", "2025-03-25", "2025-03-25", "TRY", -10000, "1", "F04", 10000),
            invoice("F07", "satis", "M2", "2025-01-10", "2025-03-11", "EUR", 20000, "36.5"),
            invoice("F08", "satis", "M2", "2025-01-20", "2025-03-21", "EUR", 1000, "36.6"),
            invoice("F09", "satis", "M4", "2024-11-01", "2024-12-01", "TRY", 3000, "1"),
            invoice("F10", "satis", "M4", "2025-03-05", "2025-04-04", "TRY", 12000, "1"),
            invoice("F11", "satis", "M3", "2025-04-15", "2025-05-15", "TRY", 40000, "1"),  # after AS_OF
        ],
        "tahsilatlar": [
            collection("T1", "M1", "F02", "2025-02-05", "TRY", 60000),
            collection("T2", "M1", "F03", "2025-02-24", "TRY", 20000),  # F03 closed 10 days after the due date
            collection("T3", "M2", "F07", "2025-03-15", "EUR", 5000, "swift"),
            collection("T4", "M2", "F08", "2025-03-21", "EUR", 1000, "swift"),  # F08 closed on its due date
            collection("T5", "M1", None, "2025-03-28", "TRY", 5000),  # unmatched
            collection("T6", "M1", "F02", "2025-04-10", "TRY", 40000),  # after AS_OF
            collection("T7", "M1", "F04", "2025-04-20", "TRY", 20000, "cek", "C1"),  # after AS_OF
        ],
        "cekler": [
            {"cek_id": "C1", "musteri_id": "M1", "cek_no": "1111111", "kesideci_banka": "Banka X",
             "alis_tarihi": iso_date("2025-03-01"), "vade_tarihi": iso_date("2025-04-20"), "tutar_kurus": 20000},
            {"cek_id": "C2", "musteri_id": "M1", "cek_no": "2222222", "kesideci_banka": "Banka Y",
             "alis_tarihi": iso_date("2025-02-01"), "vade_tarihi": iso_date("2025-03-03"), "tutar_kurus": 9999},
        ],
        "cek_hareketleri": [
            {"cek_hareket_id": "K1", "cek_id": "C2", "tarih": iso_date("2025-02-01"), "durum": "portfoyde"},
            {"cek_hareket_id": "K2", "cek_id": "C2", "tarih": iso_date("2025-02-27"), "durum": "tahsilde"},
            {"cek_hareket_id": "K3", "cek_id": "C1", "tarih": iso_date("2025-03-01"), "durum": "portfoyde"},
            {"cek_hareket_id": "K4", "cek_id": "C2", "tarih": iso_date("2025-03-03"), "durum": "karsiliksiz"},
            {"cek_hareket_id": "K5", "cek_id": "C1", "tarih": iso_date("2025-04-15"), "durum": "tahsilde"},
            {"cek_hareket_id": "K6", "cek_id": "C1", "tarih": iso_date("2025-04-20"), "durum": "tahsil_edildi"},
        ],
        "pos_islemleri": [
            {"pos_id": "P1", "kaynak_fatura_no": "x", "islem_tarihi": iso_date("2025-03-31"), "tutar_kurus": 10000,
             "komisyon_kurus": 200, "valor_tarihi": iso_date("2025-04-01")},
            {"pos_id": "P2", "kaynak_fatura_no": "y", "islem_tarihi": iso_date("2025-03-28"), "tutar_kurus": 5000,
             "komisyon_kurus": 100, "valor_tarihi": iso_date("2025-03-31")},
        ],
        "tedarikciler": [
            {"tedarikci_id": "S1", "ad": "Delta Kağıt", "kategori": "mal", "vade_gun": 30},
            {"tedarikci_id": "S2", "ad": "Kira AŞ", "kategori": "kira", "vade_gun": 30},
        ],
        "alis_faturalari": [
            purchase_invoice("A1", "S1", "2025-03-01", "2025-03-31", 25000),
            purchase_invoice("A2", "S1", "2025-03-15", "2025-04-14", 12000),  # paid early
            purchase_invoice("A3", "S2", "2025-03-25", "2025-04-24", 8000, "kira"),
            purchase_invoice("A4", "S1", "2025-02-01", "2025-03-03", 6000),  # half paid, overdue
            purchase_invoice("A5", "S1", "2025-03-30", "2025-06-28", 9000),
            purchase_invoice("A6", "S1", "2025-01-05", "2025-02-04", 4000),  # paid twice
            purchase_invoice("A7", "S1", "2025-04-05", "2025-05-05", 7777),  # after AS_OF
        ],
        "odemeler": [
            payment("O1", "tedarikci", "A2", "2025-03-20", 12000),
            payment("O2", "tedarikci", "A4", "2025-03-03", 3000),
            payment("O3", "tedarikci", "A4", "2025-04-02", 3000),  # after AS_OF
            payment("O4", "tedarikci", "A6", "2025-02-04", 4000),
            payment("O5", "tedarikci", "A6", "2025-02-05", 4000),  # duplicate
            payment("O6", "maas", None, "2025-03-28", 50000),
        ],
        "banka_hesaplari": [
            {"hesap_id": "A_TRY", "banka": "Banka A", "para_birimi": "TRY", "acilis_tarihi": iso_date("2024-09-01"),
             "acilis_bakiye_kurus": 1000000},
            {"hesap_id": "B_EUR", "banka": "Banka B", "para_birimi": "EUR", "acilis_tarihi": iso_date("2024-09-01"),
             "acilis_bakiye_kurus": 0},
            {"hesap_id": "C_USD", "banka": "Banka C", "para_birimi": "USD", "acilis_tarihi": iso_date("2025-04-01"),
             "acilis_bakiye_kurus": 0},
        ],
        "banka_hareketleri": [
            transaction("H01", "A_TRY", "2025-02-04", -4000), transaction("H02", "A_TRY", "2025-02-05", 60000),
            transaction("H03", "A_TRY", "2025-02-05", -4000), transaction("H04", "A_TRY", "2025-02-24", 20000),
            transaction("H05", "A_TRY", "2025-03-03", -3000), transaction("H06", "A_TRY", "2025-03-20", -12000),
            transaction("H07", "A_TRY", "2025-03-28", 5000), transaction("H08", "A_TRY", "2025-03-28", -50000),
            transaction("H09", "A_TRY", "2025-03-31", 4900), transaction("H10", "A_TRY", "2025-04-02", -3000),
            transaction("H11", "A_TRY", "2025-04-10", 40000),
            transaction("H12", "B_EUR", "2025-03-15", 5000), transaction("H13", "B_EUR", "2025-03-21", 1000),
        ],
        "doviz_kurlari": [
            {"kur_id": f"{t}-{currency}", "tarih": iso_date(t), "para_birimi": currency, "alis": a}
            for t, currency, a in [("2025-01-10", "EUR", "36.5"), ("2025-01-20", "EUR", "36.6"), ("2025-03-28", "EUR", "37"),
                             ("2025-03-31", "EUR", "37.1234"), ("2025-03-31", "USD", "34"),
                             ("2025-04-01", "EUR", "37.5"), ("2025-04-01", "USD", "34.1")]
        ],
    }


AS_OF = iso_date("2025-03-31")


@pytest.fixture(scope="module")
def hand_built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("elle")
    con = warehouse_from_tables(folder, _hand_built_tables())
    return con, pandas_reference.read_tables(folder)


@pytest.fixture(scope="module", params=["sql", "pandas"])
def compute(request, hand_built):
    """The hand-computed expected values are asked of SQL and of pandas separately."""
    con, P = hand_built
    if request.param == "sql":
        return lambda name, *a, **k: metrics.METRICS[name](con, *a, **k)
    return lambda name, *a, **k: pandas_reference.METRICS[name](P, *a, **k)


def test_by_hand_cash_position(compute):
    n = compute("nakit_pozisyonu", AS_OF)
    # A_TRY: 1,000,000 - 4,000 + 60,000 - 4,000 + 20,000 - 3,000 - 12,000 + 5,000 - 50,000 + 4,900 = 1,016,900
    # B_EUR: 6,000 euro-cents x 37.1234 = 222,740.4 -> 222,740. C_USD opens on 1 April: not visible.
    assert [(h["hesap_id"], h["bakiye_kurus"], h["bakiye_try_kurus"]) for h in n["hesaplar"]] == [
        ("A_TRY", 1016900, 1016900), ("B_EUR", 6000, 222740)]
    assert n["toplam_try_kurus"] == 1239640


def test_by_hand_cash_position_on_a_weekend_uses_the_friday_rate(compute):
    n = compute("nakit_pozisyonu", iso_date("2025-03-30"))  # Sunday: no 31 March transaction or rate
    assert [(h["hesap_id"], h["bakiye_kurus"], h["kur"], h["bakiye_try_kurus"]) for h in n["hesaplar"]] == [
        ("A_TRY", 1012000, 1, 1012000), ("B_EUR", 6000, Decimal("37"), 222000)]


def test_by_hand_aging(compute):
    y = compute("yaslandirma", AS_OF)
    row = {m["musteri_id"]: m for m in y["musteriler"]}
    assert [m["musteri_id"] for m in y["musteriler"]] == ["M2", "M1", "M4"]  # by total, largest first
    # M1: F05 30,000 not yet due; F04 50,000-10,000 return = 40,000, 19 days; F02 100,000-60,000 = 40,000, 58 days;
    #     F01 7,000, 181 days; unmatched transfer -5,000; F03 closed.
    assert row["M1"] == {"musteri_id": "M1", "ad": "Alfa Ltd.", "vadesi_gelmemis_kurus": 30000,
                           "gecikme_1_30_kurus": 40000, "gecikme_31_60_kurus": 40000, "gecikme_61_90_kurus": 0,
                           "gecikme_91_180_kurus": 0, "gecikme_180_ustu_kurus": 7000,
                           "eslesmemis_tahsilat_kurus": -5000, "toplam_kurus": 112000}
    # M2: F07 20,000-5,000 = 15,000 euro-cents x 36.5 (invoice rate) = 547,500; 20 days. F08 closed.
    assert row["M2"]["gecikme_1_30_kurus"] == 547500 and row["M2"]["toplam_kurus"] == 547500
    # M4: F10 12,000 not yet due; F09 3,000, 120 days.
    assert (row["M4"]["vadesi_gelmemis_kurus"], row["M4"]["gecikme_91_180_kurus"], row["M4"]["toplam_kurus"]) == (
        12000, 3000, 15000)
    assert y["toplam"]["toplam_kurus"] == 674500


def test_by_hand_dso(compute):
    d = compute("dso", AS_OF)
    # Sales (1 April 2024 - 31 March 2025): invoices 7,000+100,000+20,000+50,000+30,000-10,000+730,000+36,600
    # +3,000+12,000 = 978,600; POS 10,000+5,000 = 15,000. DSO = 365 x 674,500 / 993,600 = 247.778... -> 247.78
    assert (d["alacak_kurus"], d["satis_kurus"], d["dso_gun"]) == (674500, 993600, Decimal("247.78"))


def test_by_hand_overdue_receivables(compute):
    g = compute("geciken_alacaklar", AS_OF)
    assert [(f["fatura_id"], f["gecikme_gun"], f["acik_kurus"], f["acik_try_kurus"]) for f in g["faturalar"]] == [
        ("F07", 20, 15000, 547500), ("F02", 58, 40000, 40000), ("F04", 19, 40000, 40000),
        ("F01", 181, 7000, 7000), ("F09", 120, 3000, 3000)]


def test_by_hand_upcoming_payments(compute):
    y = compute("yaklasan_odemeler", AS_OF)
    # A2 paid, A6 overpaid (negative open amount), A5 is due after 30 days, A7 not issued yet.
    assert [(o["alis_fatura_id"], o["kalan_gun"], o["acik_kurus"]) for o in y["odemeler"]] == [
        ("A4", -28, 3000), ("A1", 0, 25000), ("A3", 24, 8000)]


def test_by_hand_fx_rate_risk(compute):
    k = compute("kur_riski", AS_OF)
    # EUR: receivable 15,000 + bank 6,000 = 21,000 euro-cents; x 37.1234 = 779,591.4 -> 779,591; 10% of it 77,959.14 -> 77,959.
    # Rate difference: 15,000 x 37.1234 = 556,851 - 547,500 (at the invoice rate) = 9,351. The USD account is not open yet.
    assert k["dovizler"] == [{"para_birimi": "EUR", "alacak_doviz_kurus": 15000, "banka_doviz_kurus": 6000,
                              "toplam_doviz_kurus": 21000, "kur": Decimal("37.1234"), "toplam_try_kurus": 779591,
                              "yuzde_10_etki_kurus": 77959, "alacak_kur_farki_kurus": 9351}]


def test_by_hand_cash_flow_forecast(compute):
    n = compute("nakit_akis_tahmini", AS_OF)
    # Pool (M1): C1 in the portfolio 20,000 + unmatched T5 5,000 = 25,000; C2 bounced, not counted. From the oldest due date:
    #   F01 7,000 fully, 18,000 of F02 counts as closed -> 22,000 remains of F02.
    # Average delay: M1 = 10 days (F03), M2 = 0 (F08), company = (10+0)//2 = 5 -> 5 for M4.
    # Week 1 (1-7 Apr): F02 22,000 + F04 40,000 (due+10 passed -> AS_OF+1) + F07 15,000x37.1234 = 556,851
    #   = 618,851; POS P1 9,800; supplier A1 25,000 + A4 3,000 = 28,000.
    # Week 2: F10 (4 Apr + 5 = 9 Apr) 12,000. Week 3: C1 cheque (20 Apr) 20,000. Week 4: A3 (24 Apr) 8,000.
    # Week 5: F05 (19 Apr + 10 = 29 Apr) 30,000. Week 13: A5 (28 Jun) 9,000. F09 is 120 days late: doubtful.
    assert n["acilis_kurus"] == 1239640
    summary = {h["hafta"]: (h["alacak_girisi_kurus"], h["cek_girisi_kurus"], h["pos_girisi_kurus"],
                         h["tedarikci_cikisi_kurus"]) for h in n["haftalar"] if h["net_kurus"]}
    assert summary == {1: (618851, 0, 9800, 28000), 2: (12000, 0, 0, 0), 3: (0, 20000, 0, 0), 4: (0, 0, 0, 8000),
                    5: (30000, 0, 0, 0), 13: (0, 0, 0, 9000)}
    assert [h["bilinen_kalemlerle_bakiye_kurus"] for h in n["haftalar"]] == [
        1840291, 1852291, 1872291, 1864291, 1894291, 1894291, 1894291, 1894291, 1894291, 1894291, 1894291, 1894291,
        1885291]
    assert (n["haftalar"][0]["bas"], n["haftalar"][0]["bit"], n["haftalar"][-1]["bit"]) == (
        iso_date("2025-04-01"), iso_date("2025-04-07"), iso_date("2025-06-30"))
    assert (n["supheli_alacak_kurus"], n["ufuk_disi_giris_kurus"], n["ufuk_disi_cikis_kurus"]) == (3000, 0, 0)


def test_by_hand_shorter_forecast_horizon_sums_out_of_horizon_items_separately(compute):
    n = compute("nakit_akis_tahmini", AS_OF, hafta=2)
    # Items after week 3: C1 20,000 + F05 30,000 inflow; A3 8,000 + A5 9,000 outflow.
    assert (n["ufuk_disi_giris_kurus"], n["ufuk_disi_cikis_kurus"]) == (50000, 17000)


def test_by_hand_fx_unmatched_collection(tmp_path):
    """The Phase 1 generator leaves only TL transfers unmatched; the foreign-currency branch is tried separately here."""
    tables = _hand_built_tables()
    tables["tahsilatlar"].append({"tahsilat_id": "T8", "musteri_id": "M2", "fatura_id": None, "tarih": iso_date("2025-03-25"),
                             "para_birimi": "EUR", "tutar_kurus": 2000, "yontem": "swift", "cek_id": None,
                             "banka_hesap_id": "B_EUR", "eslesme": "bilerek_eslesmemis"})
    tables["banka_hareketleri"].append({"hareket_id": "H14", "hesap_id": "B_EUR", "tarih": iso_date("2025-03-25"),
                                   "tutar_kurus": 2000, "bakiye_kurus": 0, "aciklama": "H14", "kaynak_tur": "test",
                                   "kaynak_id": "H14"})
    con = warehouse_from_tables(tmp_path, tables)
    P = pandas_reference.read_tables(tmp_path)
    for compute in (lambda name, *a: metrics.METRICS[name](con, *a), lambda name, *a: pandas_reference.METRICS[name](P, *a)):
        # 2,000 euro-cents at the latest rate on the collection day (25 March), the 36.6 of 20 January: 73,200.
        m2 = next(m for m in compute("yaslandirma", AS_OF)["musteriler"] if m["musteri_id"] == "M2")
        assert (m2["eslesmemis_tahsilat_kurus"], m2["toplam_kurus"]) == (-73200, 474300)
        eur = compute("kur_riski", AS_OF)["dovizler"][0]
        assert (eur["alacak_doviz_kurus"], eur["banka_doviz_kurus"], eur["toplam_try_kurus"]) == (13000, 8000, 779591)
        n = compute("nakit_akis_tahmini", AS_OF)
        # 2,000 of F07 counts as closed by the pool: 13,000 x 37.1234 = 482,604; B_EUR 8,000 x 37.1234 = 296,987.
        assert (n["acilis_kurus"], n["haftalar"][0]["alacak_girisi_kurus"]) == (1016900 + 296987, 22000 + 40000 + 482604)
    for day in (iso_date("2025-03-24"), iso_date("2025-03-25"), AS_OF, iso_date("2025-04-30")):
        compare_sql_and_pandas(con, P, day)


def test_missing_rate_raises_an_error_instead_of_silently_skipping(hand_built):
    con, P = hand_built
    with pytest.raises(duckdb.Error, match="kur bulunamadı"):
        metrics.cash_position(con, iso_date("2025-01-05"))  # B_EUR is open but the first EUR rate is on 10 January
    with pytest.raises(LookupError):
        pandas_reference.cash_position(P, iso_date("2025-01-05"))


def test_by_hand_data_sql_and_pandas_are_identical_on_every_day(hand_built):
    con, P = hand_built
    day = iso_date("2025-01-10")
    while day <= iso_date("2025-07-15"):
        compare_sql_and_pandas(con, P, day)
        day += dt.timedelta(days=3)


def test_date_cannot_be_a_datetime(hand_built):
    with pytest.raises(TypeError):
        metrics.aging(hand_built[0], dt.datetime(2025, 3, 31))


# ---------------------------------------------------------------- the warehouse itself


def test_warehouse_matches_csv_exactly_and_has_only_schema_tables(tmp_path):
    tables = _hand_built_tables()
    con = warehouse_from_tables(tmp_path, tables, data_hash="abc")
    expected = read_csv(tmp_path)
    for name, columns in SCHEMA.items():
        sql = query_rows(con, f"SELECT * FROM {name} ORDER BY {columns[0]}")
        csv = sorted(expected[name], key=lambda s: s[columns[0]])
        for s in csv:
            for k in ("kur", "alis"):
                if s.get(k) is not None:
                    s[k] = Decimal(s[k])
        assert sql == csv, name
    table_names = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert table_names == set(SCHEMA) | {"ambar_bilgisi"}
    assert con.execute("SELECT deger FROM ambar_bilgisi WHERE anahtar = 'veri_hash'").fetchone()[0] == "abc"


def test_warehouse_file_opens_read_only(tmp_path):
    write_csv(_hand_built_tables(), tmp_path / "csv")
    build_warehouse(tmp_path / "csv", tmp_path / "ambar.duckdb").close()
    con = open_warehouse(tmp_path / "ambar.duckdb")
    assert metrics.cash_position(con, AS_OF)["toplam_try_kurus"] == 1239640  # the macros are stored in the file
    with pytest.raises(duckdb.Error):
        con.execute("DELETE FROM faturalar")
    con.close()


# ---------------------------------------------------------------- sample data: three seeds


@pytest.fixture(scope="module", params=[42, 43, 44], ids=lambda t: f"tohum{t}")
def sample(request, tmp_path_factory):
    settings = replace(GeneratorSettings(), tohum=request.param)
    generated = generate(clean(sample_raw_rows()).line_items, sample_rates(), settings)
    folder = tmp_path_factory.mktemp(f"ornek{request.param}")
    con = warehouse_from_tables(folder, generated.tables)
    return generated, con, pandas_reference.read_tables(folder), settings


def _sample_days(generated, settings) -> list[dt.date]:
    """Every event kind should be live on at least one day: event days and a week after them, plus the first day,
    a weekend day, now and the last day."""
    days = {generated.data_start, settings.simdi, settings.veri_bitis}
    for o in generated.key["olaylar"]:
        for field in ("tarih", "bitis"):
            if o.get(field):
                g = iso_date(o[field])
                days |= {g, min(g + dt.timedelta(days=7), settings.veri_bitis)}
    mid = generated.data_start + (settings.veri_bitis - generated.data_start) / 2
    days.add(mid + dt.timedelta(days=(5 - mid.weekday()) % 7))  # Saturday
    return sorted(days)


def test_sample_data_sql_and_pandas_are_identical(sample):
    generated, con, P, settings = sample
    for day in _sample_days(generated, settings):
        compare_sql_and_pandas(con, P, day)


def test_sample_data_matches_the_phase1_computations(sample):
    generated, con, _, settings = sample
    tables = generated.tables
    for day in _sample_days(generated, settings):
        n = metrics.cash_position(con, day)
        assert {h["hesap_id"]: h["bakiye_kurus"] for h in n["hesaplar"]} == bank_balances(tables, day)
        assert metrics.aging(con, day)["toplam"]["toplam_kurus"] == calibration.open_receivable_try(tables, day)
        all_overdue = metrics.overdue_receivables(con, day, en_az_gun=-10**6)["faturalar"]
        assert {f["fatura_id"]: f["acik_kurus"] for f in all_overdue} == {
            k: v for k, v in open_amounts(tables, day).items() if v > 0}


def test_sample_data_future_does_not_leak(sample, tmp_path):
    """The metric computed on a given day with all the data equals the one from a warehouse built only from the
    data up to that day."""
    generated, con, _, settings = sample
    days = _sample_days(generated, settings)
    for day in (settings.simdi, days[len(days) // 2]):
        truncated = warehouse_from_tables(tmp_path / day.isoformat(), as_of_view(generated.tables, day))
        for name, func in metrics.METRICS.items():
            assert func(con, day) == func(truncated, day), f"{name} @ {day}"


def test_customers_with_cheques_all_use_try(sample):
    """The cash forecast deducts cheques from the customer's open invoices; for that the cheque and the invoice
    are in the same currency."""
    generated, _, _, _ = sample
    currency = {m["musteri_id"]: m["para_birimi"] for m in generated.tables["musteriler"]}
    assert {currency[c["musteri_id"]] for c in generated.tables["cekler"]} <= {"TRY"}
