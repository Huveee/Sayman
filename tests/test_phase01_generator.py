"""Phase 1: synthetic company generator, events, as-of view and calibration measurements (with sample data)."""

import datetime as dt
import os
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import pytest

from sayman.data import invariants, calibration
from sayman.data.table_io import SCHEMA, read_csv, write_csv, table_digest, data_digest
from sayman.data.as_of import DATE_COLUMN, open_amounts, bank_balances, cheque_statuses, as_of_view
from sayman.data.sample_data import sample_raw_rows, sample_rates
from sayman.data.uci import clean
from sayman.data.generator import GeneratorSettings, roll_to_business_day, generate

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = GeneratorSettings()


@pytest.fixture(scope="module")
def line_items():
    return clean(sample_raw_rows()).line_items


@pytest.fixture(scope="module")
def fx_rates():
    return sample_rates()


# The generator tests run with three seeds so that event placement and storylines do not depend on a single seed.
@pytest.fixture(scope="module", params=[42, 43, 44], ids=lambda t: f"tohum{t}")
def seed(request):
    return request.param


@pytest.fixture(scope="module")
def generated(line_items, fx_rates, seed):
    return generate(line_items, fx_rates, replace(SETTINGS, tohum=seed))


@pytest.fixture(scope="module")
def baseline(line_items, fx_rates, seed):
    return generate(line_items, fx_rates, replace(SETTINGS, tohum=seed, olaylar_acik=False))


@pytest.fixture(scope="module")
def tables(generated):
    return generated.tables


# ---------------------------------------------------------------- reproducibility

HASH_CODE = (
    "from sayman.data.sample_data import sample_raw_rows, sample_rates\n"
    "from sayman.data.uci import clean\n"
    "from sayman.data.generator import generate\n"
    "from sayman.data.table_io import data_digest\n"
    "print(data_digest(generate(clean(sample_raw_rows()).line_items, sample_rates()).tables)[0])\n"
)


def _hash_in_separate_process(hash_seed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(ROOT / "src")}
    output = subprocess.run([sys.executable, "-c", HASH_CODE], env=env, capture_output=True, text=True, check=True)
    return output.stdout.strip()


def test_same_seed_in_two_separate_processes_gives_same_hash(line_items, fx_rates):
    first_hash, second_hash = _hash_in_separate_process("1"), _hash_in_separate_process("12345")
    assert first_hash == second_hash == data_digest(generate(line_items, fx_rates, SETTINGS).tables)[0]


def test_different_seed_gives_different_data(line_items, fx_rates, generated, seed):
    other = generate(line_items, fx_rates, replace(SETTINGS, tohum=seed + 100))
    assert data_digest(other.tables)[0] != data_digest(generated.tables)[0]


def test_csv_round_trip_preserves_the_hash(tables, tmp_path):
    write_csv(tables, tmp_path)
    assert data_digest(read_csv(tmp_path)) == data_digest(tables)


# ---------------------------------------------------------------- structure and money


def test_tables_match_the_schema_and_identifiers_are_unique(tables):
    assert set(tables) == set(SCHEMA)
    for name, rows in tables.items():
        for s in rows:
            assert set(s) == set(SCHEMA[name]), name
        ids = [s[SCHEMA[name][0]] for s in rows]
        assert len(ids) == len(set(ids)), name


def test_money_is_integer_cents_and_never_float(tables):
    for name, rows in tables.items():
        for s in rows:
            for column, value in s.items():
                assert not isinstance(value, float), (name, column)
                if column.endswith("_kurus") and value is not None:
                    assert type(value) is int, (name, column)


def test_answer_key_is_not_in_the_tables(generated, tables):
    for rows in tables.values():
        for s in rows:
            assert not any(k.startswith("_") for k in s)
    columns = {s for v in SCHEMA.values() for s in v}
    assert not columns & {"profil", "olay", "olay_id", "gercek_fatura", "batik"}
    unmatched = [t for t in tables["tahsilatlar"] if t["eslesme"] == "bilerek_eslesmemis"]
    assert unmatched and all(t["fatura_id"] is None for t in unmatched)


def test_customer_names_are_unique_and_uci_description_is_not_carried(tables):
    names = [m["ad"] for m in tables["musteriler"]]
    assert len(names) == len(set(names))
    assert "Description" not in {s for v in SCHEMA.values() for s in v}


def test_dates_are_shifted_15_years_with_month_and_weekday_preserved(generated, tables):
    # 15 years is 5479 days; the nearest whole week is 5481: dates shift by at most two days, month and weekday
    # stay in place.
    assert generated.applied_shift_days == 5481 and generated.applied_shift_days % 7 == 0
    first_date = dt.date(2009, 12, 1) + dt.timedelta(days=generated.applied_shift_days)
    assert first_date == generated.data_start == dt.date(2024, 12, 3) and first_date.weekday() == dt.date(2009, 12, 1).weekday()
    days = [f["fatura_tarihi"] for f in tables["faturalar"]]
    assert min(days) == generated.data_start and max(days) <= SETTINGS.veri_bitis
    # The last weeks that fall after the data end (after 10 November 2011 in UCI) are not used.
    assert generated.unused_items_after_end > 0
    assert dt.date(2011, 11, 10) + dt.timedelta(days=generated.applied_shift_days) == SETTINGS.veri_bitis


# ---------------------------------------------------------------- links


def test_every_collection_is_linked_to_an_invoice_or_deliberately_unmatched(generated, tables):
    sales = {f["fatura_id"]: f for f in tables["faturalar"] if f["tur"] == "satis"}
    key = generated.key["eslesmeyen_tahsilatlar"]
    for t in tables["tahsilatlar"]:
        if t["eslesme"] == "eslesti":
            f = sales[t["fatura_id"]]
        else:
            assert t["eslesme"] == "bilerek_eslesmemis" and t["fatura_id"] is None
            f = sales[key[t["tahsilat_id"]]]
        assert t["musteri_id"] == f["musteri_id"] and t["para_birimi"] == f["para_birimi"]
        assert f["fatura_tarihi"] <= t["tarih"] <= SETTINGS.veri_bitis
        assert t["tutar_kurus"] > 0
    assert set(key) == {t["tahsilat_id"] for t in tables["tahsilatlar"] if t["eslesme"] != "eslesti"}


def test_no_invoice_is_over_collected(tables):
    paid = defaultdict(int)
    for t in tables["tahsilatlar"]:
        if t["fatura_id"]:
            paid[t["fatura_id"]] += t["tutar_kurus"]
    for f in tables["faturalar"]:
        if f["tur"] == "iade" and f["iliskili_fatura_id"]:
            paid[f["iliskili_fatura_id"]] += f["uygulanan_kurus"]
    amount = {f["fatura_id"]: f["tutar_kurus"] for f in tables["faturalar"]}
    for invoice_id, total in paid.items():
        assert total <= amount[invoice_id], invoice_id


def test_return_invoices_are_handled_correctly(tables):
    sales = {f["fatura_id"]: f for f in tables["faturalar"] if f["tur"] == "satis"}
    refund_payments = defaultdict(int)
    for o in tables["odemeler"]:
        if o["tur"] == "musteri_iadesi":
            refund_payments[o["iade_fatura_id"]] += o["tutar_kurus"]
    returns = [f for f in tables["faturalar"] if f["tur"] == "iade"]
    assert returns and any(f["iade_eslesme"] == "yok" for f in returns)
    for f in returns:
        assert f["tutar_kurus"] < 0 and f["vade_tarihi"] == f["fatura_tarihi"]
        assert 0 <= f["uygulanan_kurus"] <= -f["tutar_kurus"]
        if f["iliskili_fatura_id"]:
            s = sales[f["iliskili_fatura_id"]]
            assert s["musteri_id"] == f["musteri_id"] and s["fatura_tarihi"] <= f["fatura_tarihi"]
            assert f["iade_eslesme"] in ("stok_kodu", "son_fatura")
        else:
            assert f["iade_eslesme"] == "yok" and f["uygulanan_kurus"] == 0
        if roll_to_business_day(f["fatura_tarihi"] + dt.timedelta(days=5)) <= SETTINGS.veri_bitis:
            assert f["uygulanan_kurus"] + refund_payments[f["fatura_id"]] == -f["tutar_kurus"], f["fatura_id"]


def test_cheques_and_their_transactions_are_consistent(generated, tables):
    collections_by_cheque = defaultdict(list)
    for t in tables["tahsilatlar"]:
        if t["cek_id"]:
            collections_by_cheque[t["cek_id"]].append(t)
    transactions = defaultdict(list)
    for h in tables["cek_hareketleri"]:
        transactions[h["cek_id"]].append(h)
    statuses = cheque_statuses(tables, SETTINGS.veri_bitis)
    assert Counter(statuses.values())["karsiliksiz"] >= 3
    for c in tables["cekler"]:
        ordered = sorted(transactions[c["cek_id"]], key=lambda h: (h["tarih"], h["cek_hareket_id"]))
        assert ordered[0]["durum"] == "portfoyde" and ordered[0]["tarih"] == c["alis_tarihi"]
        assert c["alis_tarihi"] <= c["vade_tarihi"] and all(h["tarih"] <= SETTINGS.veri_bitis for h in ordered)
        if statuses[c["cek_id"]] == "tahsil_edildi":
            assert len(collections_by_cheque[c["cek_id"]]) == 1
            t = collections_by_cheque[c["cek_id"]][0]
            assert t["tutar_kurus"] == c["tutar_kurus"] and t["tarih"] == c["vade_tarihi"]
        else:
            assert not collections_by_cheque[c["cek_id"]]


def test_bank_balance_is_opening_plus_transactions(tables):
    opening = {h["hesap_id"]: h["acilis_bakiye_kurus"] for h in tables["banka_hesaplari"]}
    balance = dict(opening)
    for s in sorted(tables["banka_hareketleri"], key=lambda s: s["hareket_id"]):
        balance[s["hesap_id"]] += s["tutar_kurus"]
        assert s["bakiye_kurus"] == balance[s["hesap_id"]], s["hareket_id"]
        assert s["bakiye_kurus"] >= 0, s["hareket_id"]
    assert bank_balances(tables, SETTINGS.veri_bitis) == balance


def test_every_collection_and_payment_appears_once_in_the_bank(tables):
    source = Counter((s["kaynak_tur"], s["kaynak_id"]) for s in tables["banka_hareketleri"])
    for t in tables["tahsilatlar"]:
        assert source[("tahsilat", t["tahsilat_id"])] == 1
    for o in tables["odemeler"]:
        assert source[("odeme", o["odeme_id"])] == 1
    assert sum(1 for k in source if k[0] == "tahsilat") == len(tables["tahsilatlar"])
    assert sum(1 for k in source if k[0] == "odeme") == len(tables["odemeler"])


# ---------------------------------------------------------------- events


def test_event_count_matches_the_settings(generated):
    events = generated.key["olaylar"]
    assert Counter(o["tur"] for o in events) == Counter(dict(SETTINGS.olay_sayilari))
    assert len(events) == sum(n for _, n in SETTINGS.olay_sayilari) == 12
    assert sum(1 for o in events if dt.date.fromisoformat(o["tarih"]) > SETTINGS.simdi) >= 3
    for o in events:
        assert generated.data_start <= dt.date.fromisoformat(o["tarih"]) <= SETTINGS.veri_bitis
        assert o["simdiden_sonra"] == (dt.date.fromisoformat(o["tarih"]) > SETTINGS.simdi)


def test_baseline_data_is_the_same_when_events_are_off(generated, baseline):
    assert baseline.key["olaylar"] == []
    for name in ("musteriler", "faturalar", "pos_islemleri", "tedarikciler", "banka_hesaplari", "doviz_kurlari"):
        assert table_digest(name, generated.tables[name]) == table_digest(name, baseline.tables[name]), name
    affected = defaultdict(set)
    for o in generated.key["olaylar"]:
        for name, ids in o["etkilenen"].items():
            affected[name].update(ids)
    for name in ("tahsilatlar", "cekler", "cek_hareketleri", "odemeler", "alis_faturalari"):
        key = SCHEMA[name][0]
        with_events = {s[key]: s for s in generated.tables[name] if s[key] not in affected[name]}
        without_events = {s[key]: s for s in baseline.tables[name] if s[key] not in affected[name]}
        assert with_events == without_events, name


def _events_of_kind(generated, kind):
    selected = [o for o in generated.key["olaylar"] if o["tur"] == kind]
    assert len(selected) == dict(SETTINGS.olay_sayilari)[kind]
    return selected


def _lowest_a_try_balance(tables, start, end):
    return min(s["bakiye_kurus"] for s in tables["banka_hareketleri"] if s["hesap_id"] == "A_TRY" and start <= s["tarih"] <= end)


def test_event_large_customer_delay_absent_in_baseline_present_with_events(generated, baseline):
    before = {t["tahsilat_id"]: t for t in baseline.tables["tahsilatlar"]}
    after = {t["tahsilat_id"]: t for t in generated.tables["tahsilatlar"]}
    for o in _events_of_kind(generated, "buyuk_musteri_gecikmesi"):
        assert len(o["tahsilatlar"]) >= 3 and 45 <= o["gecikme_gun"] <= 90
        for e in o["tahsilatlar"]:
            assert before[e["tahsilat_id"]]["tarih"].isoformat() == e["eski_tarih"]
            if e["yeni_tarih"] is None:
                assert e["tahsilat_id"] not in after
            else:
                assert after[e["tahsilat_id"]]["tarih"].isoformat() == e["yeni_tarih"]
                assert (after[e["tahsilat_id"]]["tarih"] - before[e["tahsilat_id"]]["tarih"]).days >= 45


def test_event_bounced_cheque_collected_in_baseline_bounced_with_events(generated, baseline):
    for o in _events_of_kind(generated, "karsiliksiz_cek"):
        assert cheque_statuses(baseline.tables, SETTINGS.veri_bitis)[o["cek_id"]] == "tahsil_edildi"
        assert cheque_statuses(generated.tables, SETTINGS.veri_bitis)[o["cek_id"]] == "karsiliksiz"
        assert cheque_statuses(generated.tables, dt.date.fromisoformat(o["tarih"]) - dt.timedelta(days=1))[o["cek_id"]] != "karsiliksiz"


def test_event_duplicate_payment_single_in_baseline_double_with_events(generated, baseline):
    for o in _events_of_kind(generated, "mukerrer_odeme"):
        def payments(tables):
            return [x for x in tables["odemeler"] if x["alis_fatura_id"] == o["alis_fatura_id"]]
        assert len(payments(baseline.tables)) == 1
        pair = payments(generated.tables)
        assert len(pair) == 2 and pair[0]["tutar_kurus"] == pair[1]["tutar_kurus"] == o["tutar_kurus"]


def test_event_unusual_outflow_is_large_and_goes_to_a_new_counterparty(generated, baseline):
    baseline_payment_ids = {x["odeme_id"] for x in baseline.tables["odemeler"]}
    baseline_descriptions = {x["aciklama"] for x in baseline.tables["odemeler"]}
    supplier_amounts = sorted(x["tutar_kurus"] for x in baseline.tables["odemeler"] if x["tur"] == "tedarikci")
    p95 = supplier_amounts[len(supplier_amounts) * 95 // 100]
    payments = {x["odeme_id"]: x for x in generated.tables["odemeler"]}
    for o in _events_of_kind(generated, "olagandisi_buyuk_cikis"):
        x = payments[o["odeme_id"]]
        assert o["odeme_id"] not in baseline_payment_ids and x["aciklama"] not in baseline_descriptions
        assert x["tutar_kurus"] == o["tutar_kurus"] >= 2 * p95
        day = dt.date.fromisoformat(o["tarih"])
        nearby = [y["tutar_kurus"] for y in generated.tables["odemeler"] if abs((y["tarih"] - day).days) <= 30]
        assert x["tutar_kurus"] == max(nearby)


def test_event_unmatched_accumulation_absent_in_baseline_present_with_events(generated, baseline):
    for o in _events_of_kind(generated, "eslesmeyen_tahsilat_birikimi"):
        start, end = dt.date.fromisoformat(o["tarih"]), dt.date.fromisoformat(o["bitis"])

        def count(tables):
            return sum(1 for t in tables["tahsilatlar"] if t["eslesme"] != "eslesti" and start <= t["tarih"] <= end)
        assert count(baseline.tables) <= 1
        assert count(generated.tables) >= count(baseline.tables) + 4


def test_event_cash_squeeze_absent_in_baseline_present_with_events_then_passes(generated, baseline):
    for o in _events_of_kind(generated, "nakit_sikismasi"):
        day = dt.date.fromisoformat(o["tarih"])
        window = day + dt.timedelta(days=14)
        assert _lowest_a_try_balance(baseline.tables, day, window) >= o["esik_kurus"]
        assert _lowest_a_try_balance(generated.tables, day, window) < o["esik_kurus"]
        assert o["esik_alti_ilk_gun"] is not None and o["esik_alti_ilk_gun"] <= window.isoformat()
        # The squeeze ends once the old dates of the pulled-forward payments pass.
        squeeze_end = dt.date.fromisoformat(o["bitis"])
        assert squeeze_end == max(dt.date.fromisoformat(x["eski_tarih"]) for x in o["odemeler"])
        assert squeeze_end < SETTINGS.veri_bitis and dt.date.fromisoformat(o["esik_alti_son_gun"]) <= squeeze_end


def test_identifiers_are_unordered_so_event_records_are_not_revealed_by_identifier(tables):
    import re

    for name, prefix in (("tahsilatlar", "T"), ("odemeler", "O")):
        rows = tables[name]
        key = SCHEMA[name][0]
        assert all(re.fullmatch(prefix + "[0-9A-F]{8}", s[key]) for s in rows)
        date_order = {s[key]: i for i, s in enumerate(sorted(rows, key=lambda s: (s["tarih"], s[key])))}
        id_order = sorted(date_order)
        n = len(id_order)
        mean = (n - 1) / 2
        cov = sum((i - mean) * (date_order[k] - mean) for i, k in enumerate(id_order))
        variance = sum((i - mean) ** 2 for i in range(n))
        assert abs(cov / variance) < 0.1, name  # identifier order is unrelated to date order
    assert any(o["tur"] == "diger" for o in tables["odemeler"] if o["aciklama"].split()[-1] in ("PRİMİ", "MÜŞAVİRLİK"))


def test_side_account_deficits_are_covered_from_a_try(baseline):
    tables = baseline.tables
    opening = {h["hesap_id"]: h["acilis_bakiye_kurus"] for h in tables["banka_hesaplari"]}
    assert opening["A_TRY"] > 0 and opening["B_TRY"] == opening["B_EUR"] == opening["C_USD"] == 0
    cover_rows = [s for s in tables["banka_hareketleri"] if s["kaynak_id"].endswith("-karsilama")]
    assert {s["hesap_id"] for s in cover_rows} >= {"A_TRY", "B_TRY", "B_EUR"}
    assert all(s["bakiye_kurus"] >= 0 for s in tables["banka_hareketleri"])
    # The lowest balance of A_TRY exactly equals the buffer (exact computation in a single pass).
    sales = defaultdict(int)
    for f in tables["faturalar"]:
        sales[f["fatura_tarihi"].replace(day=1)] += f["tutar_try_kurus"]
    for p in tables["pos_islemleri"]:
        sales[p["islem_tarihi"].replace(day=1)] += p["tutar_kurus"]
    full_months = [a for a in sales if a >= baseline.data_start and (a + dt.timedelta(days=32)).replace(day=1) <= SETTINGS.veri_bitis + dt.timedelta(days=1)]
    average = sum(sales[a] for a in full_months) // len(full_months)
    buffer = round(average * SETTINGS.acilis_tamponu)
    lowest = min(s["bakiye_kurus"] for s in tables["banka_hareketleri"] if s["hesap_id"] == "A_TRY")
    assert abs(lowest - buffer) <= 1


# ---------------------------------------------------------------- as-of view


def test_view_has_nothing_after_now(tables):
    g = as_of_view(tables, SETTINGS.simdi)
    for name, column in DATE_COLUMN.items():
        if column:
            assert all(s[column] <= SETTINGS.simdi for s in g[name]), name
            assert len(g[name]) < len(tables[name]) or name in ("banka_hesaplari", "musteriler"), name


def test_events_after_now_are_hidden_in_the_view(tables, generated):
    g = as_of_view(tables, SETTINGS.simdi)
    payment_ids = {o["odeme_id"] for o in g["odemeler"]}
    collection_ids = {t["tahsilat_id"] for t in g["tahsilatlar"]}
    statuses = cheque_statuses(g, SETTINGS.simdi)
    later = [o for o in generated.key["olaylar"] if o["simdiden_sonra"]]
    assert later
    for o in later:
        if o["tur"] == "karsiliksiz_cek":
            assert statuses.get(o["cek_id"]) != "karsiliksiz"
        elif o["tur"] == "olagandisi_buyuk_cikis":
            assert o["odeme_id"] not in payment_ids
        elif o["tur"] == "buyuk_musteri_gecikmesi":
            for e in o["tahsilatlar"]:
                if e["yeni_tarih"]:
                    assert e["tahsilat_id"] not in collection_ids


@pytest.mark.parametrize("day", [dt.date(2025, 3, 14), dt.date(2025, 12, 31), SETTINGS.simdi])
def test_open_amounts_are_computed_from_the_past_only(tables, day):
    assert open_amounts(tables, day) == open_amounts(as_of_view(tables, day), day)
    assert bank_balances(tables, day) == bank_balances(as_of_view(tables, day), day)


# ---------------------------------------------------------------- calibration measurements


def test_calibration_measurements_are_computed(tables):
    measurement = calibration.measure(tables, SETTINGS.veri_bitis, 2025)
    assert measurement["dso_gun"] > 0 and measurement["olgun_fatura_sayisi"] > 100
    assert 0 < measurement["gec_odenen_fatura_orani"] < 1
    assert abs(sum(measurement["gecikme_dagilimi"].values()) - 1) < 0.01


def test_band_comparison_reads_the_source_file(tables):
    import json

    source = json.loads((ROOT / "data" / "kaynak" / "kalibrasyon.json").read_text(encoding="utf-8"))
    rows = calibration.compare_with_bands(calibration.measure(tables, SETTINGS.veri_bitis, 2025), source)
    assert [s["ad"] for s in rows] == ["dso_gun", "gec_odenen_fatura_orani", "gecikme_1-30", "gecikme_31-60",
                                           "gecikme_61-90", "gecikme_90+"]
    assert all(s["alt"] <= s["ust"] for s in rows)


# ---------------------------------------------------------------- checks module


def test_checks_pass_on_sample_data(generated):
    result = invariants.run_all(generated.tables, generated.key, SETTINGS.olay_sayilari, SETTINGS.simdi, SETTINGS.veri_bitis)
    assert {name: h for name, h in result.items() if h} == {}


def test_checks_catch_corrupted_data(generated):
    import copy

    tables = copy.deepcopy(generated.tables)
    tables["banka_hareketleri"][10]["tutar_kurus"] += 1
    tables["tahsilatlar"][0]["fatura_id"] = "F999999"
    tables["tahsilatlar"][1]["tutar_kurus"] = 1.5
    tables["odemeler"].append(dict(tables["odemeler"][0], odeme_id="O9999999"))
    result = invariants.run_all(tables, generated.key, SETTINGS.olay_sayilari, SETTINGS.simdi, SETTINGS.veri_bitis)
    for name in ("banka_bakiye_acilis_arti_hareket", "tahsilat_faturaya_bagli_ya_da_bilerek_eslesmemis",
               "para_tamsayi_kurus", "her_tahsilat_ve_odeme_bankada_bir_kez"):
        assert result[name], name
