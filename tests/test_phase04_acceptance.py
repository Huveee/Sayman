"""Phase 4 acceptance test: monitoring rules and the scheduler on the real data (seed 42).

The Phase 1 script and scripts/phase04_generate_data.py must have been run first.

Acceptance criteria:
- At least 90% of the embedded events are caught (data/uretilen, answer key data/anahtar/olaylar.json).
- On the event-free data (the event-free twin of the same company) at most 2 false alarms per month.
- Two runs with the same clock give the same result.
Also:
- Whether the clock advances day by day or in one go, the store ends up with the same alerts.
- The rules do not see the future: the alerts of the run at the data end, up to day d, are the same as the
  alerts of the run made on day d.
- The monitoring code does not read the answer key.
- The event-free twin differs from the data with events only in the rows listed in the answer key (and in the
  bank transactions derived from them).
- Every rule reacts to its own condition: an alert appears when the condition is added by hand, and none appears
  when half of the condition is missing.
"""

import builtins
import copy
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import support.phase04_scoring as puan
import pytest

from sayman.monitoring.scheduler import AlertStore, first_day, run_scheduler, scan_alerts
from sayman.data.table_io import SCHEMA, read_csv
from sayman.data.clock import Clock, read_clock

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "uretilen"
ANSWER_KEY = ROOT / "data" / "anahtar" / "olaylar.json"
CLEAN_DIR = ROOT / "data" / "izleme_veri" / "tohum42_temiz"
CLOCK_FILE = ROOT / "data" / "saat.json"
iso_date = dt.date.fromisoformat

pytestmark = pytest.mark.skipif(not (GENERATED.exists() and ANSWER_KEY.exists() and CLEAN_DIR.exists() and CLOCK_FILE.exists()),
                                reason="Önce phase01_generate_data.py ve phase04_generate_data.py çalıştırılmalı")


@pytest.fixture(scope="module")
def data_end():
    return read_clock(CLOCK_FILE).veri_bitis


@pytest.fixture(scope="module")
def tables():
    return read_csv(GENERATED)


@pytest.fixture(scope="module")
def clean_tables():
    return read_csv(CLEAN_DIR)


@pytest.fixture(scope="module")
def events():
    return json.loads(ANSWER_KEY.read_text(encoding="utf-8"))["olaylar"]


@pytest.fixture(scope="module")
def full_run(tables, data_end):
    start = time.perf_counter()
    u = scan_alerts(tables, data_end)
    return u, time.perf_counter() - start


def _digest(alerts):
    return hashlib.sha256(json.dumps(alerts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- acceptance criteria


def test_90_percent_of_embedded_events_are_caught(full_run, events, data_end):
    p = puan.score(full_run[0], events, data_end)
    missed = [r["olay_id"] + " " + r["tur"] for r in p["olaylar"] if not r["yakalandi"]]
    assert p["olay"] == 12
    assert p["geri_cagirma"] >= 0.90, f"kaçan olaylar: {missed}"


def test_eventless_data_has_at_most_2_false_alarms_per_month(clean_tables, data_end):
    y = puan.false_alarms(scan_alerts(clean_tables, data_end), first_day(clean_tables), data_end)
    assert y["aylik"] <= 2.0, y


def test_same_clock_gives_same_result(tables, full_run, data_end):
    assert _digest(scan_alerts(tables, data_end)) == _digest(full_run[0])


def test_same_clock_gives_same_result_in_another_process(full_run, data_end):
    """The result is the same even if the hash seed (which affects set and dict order) changes."""
    script = ("import json,hashlib,sys,datetime as dt; from pathlib import Path; sys.path.insert(0, sys.argv[1]);"
             "from sayman.data.table_io import read_csv; from sayman.monitoring.scheduler import scan_alerts;"
             "u = scan_alerts(read_csv(Path(sys.argv[2])), dt.date.fromisoformat(sys.argv[3]));"
             "print(hashlib.sha256(json.dumps(u, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest())")
    digests = set()
    for seed in ("0", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        output = subprocess.run([sys.executable, "-I", "-c", script, str(ROOT / "src"), str(GENERATED), data_end.isoformat()],
                               capture_output=True, text=True, env=env, timeout=300, check=True)
        digests.add(output.stdout.strip())
    assert digests == {_digest(full_run[0])}


def test_full_run_is_fast(full_run):
    assert full_run[1] < 10, f"{full_run[1]:.1f} s"


# ---------------------------------------------------------------- scheduler


STEPS = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-03-01", "2025-06-01", "2025-06-02", "2025-06-17",
           "2025-06-25", "2025-06-26", "2025-06-27", "2025-09-15", "2025-09-16", "2025-09-17", "2026-01-29",
           "2026-01-30", "2026-04-27", "2026-04-28", "2026-07-09", "2026-07-10", "2026-11-03", "2026-11-04",
           "2026-11-05", "2026-11-12"]


def test_same_alerts_even_when_the_clock_advances_step_by_step(tables, full_run, data_end, tmp_path):
    store = AlertStore(tmp_path / "izleme")
    for day in STEPS:
        run_scheduler(tables, Clock(iso_date(day), data_end), store)
    assert store.alerts() == full_run[0]
    before = store.alerts_path.read_bytes()
    assert run_scheduler(tables, Clock(data_end, data_end), store)["yeni"] == []
    assert store.alerts_path.read_bytes() == before


def test_store_advanced_in_one_go_is_the_same(tables, full_run, data_end, tmp_path):
    store = AlertStore(tmp_path / "izleme")
    run_scheduler(tables, Clock(data_end, data_end), store)
    assert store.alerts() == full_run[0]


@pytest.mark.parametrize("day", ["2025-02-10", "2025-06-26", "2025-09-16", "2026-01-30", "2026-04-28",
                                 "2026-09-15", "2026-11-04"])
def test_rules_do_not_see_the_future(tables, full_run, day):
    d = iso_date(day)
    assert scan_alerts(tables, d) == [u for u in full_run[0] if iso_date(u["gun"]) <= d]


def test_alert_sources_are_records_visible_on_that_day(tables, full_run):
    """The records behind every alert are in the data and happened on the alert's day (or before)."""
    date_columns = {"faturalar": "fatura_tarihi", "tahsilatlar": "tarih", "cekler": "alis_tarihi", "cek_hareketleri": "tarih",
             "alis_faturalari": "tarih", "odemeler": "tarih", "banka_hareketleri": "tarih", "musteriler": None}
    index = {name: {s[SCHEMA[name][0]]: s for s in tables[name]} for name in date_columns}
    for u in full_run[0]:
        assert u["onem"] in ("uyari", "bilgi") and u["aciklama"] and u["baslik"]
        for name, ids in u["kaynak"].items():
            assert ids, u["uyari_id"]
            for k in ids:
                assert k in index[name], (u["uyari_id"], name, k)
                if date_columns[name]:
                    assert index[name][k][date_columns[name]] <= iso_date(u["gun"]), (u["uyari_id"], name, k)
    assert len({u["uyari_id"] for u in full_run[0]}) == len(full_run[0])


# ---------------------------------------------------------------- answer key


def test_monitoring_code_does_not_read_the_answer_key(monkeypatch, data_end, tmp_path):
    read_files = []
    original = io.open

    def traced_open(file, *a, **k):
        read_files.append(str(file))
        return original(file, *a, **k)

    monkeypatch.setattr(builtins, "open", traced_open)
    monkeypatch.setattr(io, "open", traced_open)
    t = read_csv(GENERATED)
    run_scheduler(t, Clock(data_end, data_end), AlertStore(tmp_path / "izleme"))
    monkeypatch.undo()
    assert any(o.endswith("faturalar.csv") for o in read_files)
    assert not [o for o in read_files if "anahtar" in o.replace("\\", "/").split("/") or o.endswith("olaylar.json")]


def test_monitoring_source_has_no_answer_key_path():
    for file in (ROOT / "src" / "sayman" / "monitoring").glob("*.py"):
        text = file.read_text(encoding="utf-8")
        # The event generator writes "bilerek_eslesmemis" on the collections it embeds; the rules never look at
        # the match column.
        for forbidden in ("olaylar.json", "data/anahtar", "phase04_scoring", "izleme_veri", "musteri_profilleri",
                      "bilerek_eslesmemis", '"eslesme"'):
            assert forbidden not in text, (file.name, forbidden)


def test_eventless_twin_differs_only_in_answer_key_rows(tables, clean_tables, events):
    affected: dict[str, set] = {}
    for o in events:
        for name, ids in o["etkilenen"].items():
            affected.setdefault(name, set()).update(ids)
    all_ids = set().union(*affected.values())
    for name, columns in SCHEMA.items():
        pk = columns[0]
        if name == "banka_hareketleri":
            # Transactions are derived: their numbers are sequence numbers, the balance column is cumulative.
            # They are compared by content; every transaction that differs must come from a collection or
            # payment in the answer key.
            def content(s):
                return (s["hesap_id"], s["tarih"], s["tutar_kurus"], s["aciklama"], s["kaynak_tur"], s["kaynak_id"])
            a, b = Counter(map(content, tables[name])), Counter(map(content, clean_tables[name]))
            differing = (a - b) + (b - a)
            assert differing and {x[5] for x in differing} <= all_ids, name
            continue
        a, b = {s[pk]: s for s in tables[name]}, {s[pk]: s for s in clean_tables[name]}
        differing = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
        assert differing <= affected.get(name, set()), (name, sorted(differing - affected.get(name, set()))[:5])


# ---------------------------------------------------------------- every rule reacts to its own condition


def _alerts_in_window(t, start, end, rule):
    return [u for u in scan_alerts(t, end) if u["kural"] == rule and start <= iso_date(u["gun"]) <= end]


def test_duplicate_payment_added_by_hand_is_caught(clean_tables):
    t = copy.deepcopy(clean_tables)
    original = next(o for o in sorted(t["odemeler"], key=lambda o: (o["tarih"], o["odeme_id"]))
                if o["tur"] == "tedarikci" and o["tarih"] >= iso_date("2026-03-02"))
    duplicate = {**original, "odeme_id": "OTEST0001", "tarih": original["tarih"] + dt.timedelta(days=1)}
    t["odemeler"].append(duplicate)
    u = _alerts_in_window(t, duplicate["tarih"], duplicate["tarih"] + dt.timedelta(days=3), "mukerrer_odeme")
    assert [(x["varlik"]["id"], x["gun"]) for x in u] == [(original["alis_fatura_id"], duplicate["tarih"].isoformat())]
    assert "OTEST0001" in u[0]["kaynak"]["odemeler"]


def test_large_outflow_only_for_a_new_counterparty_and_a_large_amount(clean_tables):
    day = iso_date("2026-03-10")
    history = sorted(o["tutar_kurus"] for o in clean_tables["odemeler"]
                    if o["tur"] == "tedarikci" and day - dt.timedelta(days=365) <= o["tarih"] < day)
    p95 = history[int(len(history) * 0.95)]
    existing = next(o["aciklama"] for o in clean_tables["odemeler"] if o["tur"] == "diger" and o["tarih"] < day)
    template = {"tur": "diger", "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": None,
              "iade_fatura_id": None, "tarih": day, "yontem": "havale", "banka_hesap_id": "A_TRY"}
    t = copy.deepcopy(clean_tables)
    t["odemeler"] += [
        {**template, "odeme_id": "OTEST1", "tutar_kurus": 3 * p95, "aciklama": "EFT GİDEN DENEME KARŞI TARAF A"},
        {**template, "odeme_id": "OTEST2", "tutar_kurus": 3 * p95, "aciklama": existing},
        {**template, "odeme_id": "OTEST3", "tutar_kurus": p95 // 2, "aciklama": "EFT GİDEN DENEME KARŞI TARAF B"},
    ]
    u = _alerts_in_window(t, day, day, "buyuk_cikis")
    assert [x["varlik"]["id"] for x in u] == ["OTEST1"]


def test_bounced_cheque_is_alert_or_info_by_amount(clean_tables):
    day0 = iso_date("2026-02-01")
    cheques = {c["cek_id"]: c for c in clean_tables["cekler"]}
    history = sorted(c["tutar_kurus"] for c in clean_tables["cekler"] if day0 - dt.timedelta(days=364) <= c["alis_tarihi"] <= day0)
    p75 = history[int(len(history) * 0.75)]
    candidates = sorted((h for h in clean_tables["cek_hareketleri"]
                      if h["durum"] == "tahsil_edildi" and iso_date("2026-02-01") <= h["tarih"] <= iso_date("2026-02-28")),
                     key=lambda h: (h["tarih"], h["cek_hareket_id"]))
    large = next(h for h in candidates if cheques[h["cek_id"]]["tutar_kurus"] >= 1.5 * p75)
    small = next(h for h in candidates if cheques[h["cek_id"]]["tutar_kurus"] <= 0.5 * p75)
    t = copy.deepcopy(clean_tables)
    for h in t["cek_hareketleri"]:
        if h["cek_hareket_id"] in (large["cek_hareket_id"], small["cek_hareket_id"]):
            h["durum"] = "karsiliksiz"
    u = {x["varlik"]["id"]: x["onem"] for x in _alerts_in_window(t, iso_date("2026-02-01"), iso_date("2026-02-28"), "karsiliksiz_cek")}
    assert u[large["cek_id"]] == "uyari" and u[small["cek_id"]] == "bilgi"
