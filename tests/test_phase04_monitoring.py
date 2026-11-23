"""Phase 4 unit tests: scoring rules, probability computations, state starts, alert identifier, store."""

import datetime as dt
import itertools
import json
import math

import support.phase04_scoring as puan
import pytest

from sayman.monitoring.rules import (RuleSettings, onset_days, poisson_binomial_tail, poisson_tail, format_tl, make_alert,
                                    percentile)
from sayman.monitoring.scheduler import AlertStore, AlertStoreError, run_scheduler
from sayman.data.clock import Clock

iso_date = dt.date.fromisoformat
DATA_END = iso_date("2026-11-12")


def _alert(rule, day, entity, severity="uyari", source=None):
    return make_alert(rule, iso_date(day), severity, "x", entity, "b", "a", {}, {}, source or {})


def _cheque_event(event_id, date, cheque_id):
    return {"olay_id": event_id, "tur": "karsiliksiz_cek", "tarih": date, "cek_id": cheque_id}


# ---------------------------------------------------------------- scoring


def test_right_rule_identifier_and_window_catch():
    p = puan.score([_alert("karsiliksiz_cek", "2025-09-02", "C1")], [_cheque_event("E1", "2025-09-01", "C1")], DATA_END)
    assert p["yakalanan"] == 1 and p["olaylar"][0]["gun_sonra"] == 1 and p["olaya_denk_gelmeyen_alarm"] == 0


def test_wrong_rule_does_not_catch():
    p = puan.score([_alert("mukerrer_odeme", "2025-09-01", "C1")], [_cheque_event("E1", "2025-09-01", "C1")], DATA_END)
    assert p["yakalanan"] == 0 and p["olaya_denk_gelmeyen_alarm"] == 1


def test_wrong_identifier_does_not_catch():
    p = puan.score([_alert("karsiliksiz_cek", "2025-09-01", "C2")], [_cheque_event("E1", "2025-09-01", "C1")], DATA_END)
    assert p["yakalanan"] == 0


def test_identifier_in_the_source_also_counts():
    u = _alert("mukerrer_odeme", "2025-06-13", "A1", source={"odemeler": ["O2"]})
    o = {"olay_id": "E1", "tur": "mukerrer_odeme", "tarih": "2025-06-13", "mukerrer_odeme_id": "O2",
         "asil_odeme_id": "O1", "alis_fatura_id": "A9"}
    assert puan.score([u], [o], DATA_END)["yakalanan"] == 1


@pytest.mark.parametrize("day", ["2025-08-31", "2025-09-05"])
def test_outside_the_window_does_not_catch(day):
    p = puan.score([_alert("karsiliksiz_cek", day, "C1")], [_cheque_event("E1", "2025-09-01", "C1")], DATA_END)
    assert p["yakalanan"] == 0


def test_one_alert_catches_a_single_event():
    events = [_cheque_event("E1", "2025-09-01", "C1"), _cheque_event("E2", "2025-09-02", "C1")]
    p = puan.score([_alert("karsiliksiz_cek", "2025-09-02", "C1")], events, DATA_END)
    assert p["yakalanan"] == 1 and [r["yakalandi"] for r in p["olaylar"]] == [True, False]


def test_info_neither_catches_nor_counts_as_a_false_alarm():
    u = [_alert("karsiliksiz_cek", "2025-09-01", "C1", severity="bilgi")]
    assert puan.score(u, [_cheque_event("E1", "2025-09-01", "C1")], DATA_END)["yakalanan"] == 0
    y = puan.false_alarms(u, iso_date("2025-01-01"), iso_date("2025-12-31"))
    assert y["alarm"] == 0 and y["bilgi"] == 1


def test_window_is_cut_at_the_data_end():
    o = {"olay_id": "E1", "tur": "buyuk_musteri_gecikmesi", "tarih": "2026-10-20", "musteri_id": "M1",
         "gecikme_gun": 80}
    assert puan.window(o, DATA_END) == (iso_date("2026-10-20"), DATA_END)


def test_monthly_false_alarm():
    u = [_alert("nakit_esigi", f"2025-{a:02d}-01", "A_TRY") for a in range(1, 7)]
    y = puan.false_alarms(u, iso_date("2025-01-01"), iso_date("2025-12-31"))
    assert y["alarm"] == 6 and y["ay"] == pytest.approx(12.0, abs=0.01) and y["aylik"] == pytest.approx(0.5, abs=1e-3)


# ---------------------------------------------------------------- probabilities and helpers


@pytest.mark.parametrize("n,lam", [(0, 2.0), (1, 0.5), (5, 1.3), (12, 3.0)])
def test_poisson_tail(n, lam):
    expected = 1 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(n))
    assert poisson_tail(n, lam) == pytest.approx(expected, abs=1e-12)


def test_poisson_binomial_tail_matches_all_states():
    ps = [0.1, 0.5, 0.03, 0.7, 0.25]
    for k in range(len(ps) + 1):
        expected = sum(math.prod(p if x else 1 - p for p, x in zip(ps, state))
                       for state in itertools.product((0, 1), repeat=len(ps)) if sum(state) >= k)
        assert poisson_binomial_tail(k, ps) == pytest.approx(expected, abs=1e-12)


def test_poisson_binomial_with_equal_probabilities_is_binomial():
    n, p, k = 10, 0.2, 4
    expected = sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    assert poisson_binomial_tail(k, [p] * n) == pytest.approx(expected, abs=1e-12)


def test_state_starts():
    days = [iso_date("2025-01-01"), iso_date("2025-01-02"), iso_date("2025-01-09"), iso_date("2025-01-20"), iso_date("2025-01-21")]
    assert onset_days(days, 7) == [iso_date("2025-01-01"), iso_date("2025-01-20")]
    assert onset_days(days, 10) == [iso_date("2025-01-01"), iso_date("2025-01-20")]  # 9 → 20 January: an 11-day gap
    assert onset_days(days, 11) == [iso_date("2025-01-01")]
    assert onset_days([], 7) == []


def test_percentile():
    assert percentile(list(range(100)), 0.95) == 95
    assert percentile([5], 0.75) == 5


def test_tl_text():
    assert format_tl(123456789) == "1.234.567,89 TL"
    assert format_tl(-5) == "-0,05 TL"


def test_alert_identifier_depends_on_rule_entity_and_day():
    a = _alert("nakit_esigi", "2025-06-01", "A_TRY")
    assert a["uyari_id"] == _alert("nakit_esigi", "2025-06-01", "A_TRY")["uyari_id"]
    assert a["uyari_id"] != _alert("nakit_esigi", "2025-06-02", "A_TRY")["uyari_id"]
    assert a["uyari_id"] != _alert("buyuk_cikis", "2025-06-01", "A_TRY")["uyari_id"]


def test_source_identifiers_are_sorted():
    u = _alert("mukerrer_odeme", "2025-06-01", "A1", source={"odemeler": ["O9", "O1"]})
    assert u["kaynak"]["odemeler"] == ["O1", "O9"]


# ---------------------------------------------------------------- store


def _tiny_tables():
    """The smallest data on which none of the rules raises an alert: one invoice, one account."""
    empty = {name: [] for name in ("musteriler", "pos_islemleri", "tahsilatlar", "cekler", "cek_hareketleri",
                             "tedarikciler", "alis_faturalari", "odemeler", "banka_hareketleri", "doviz_kurlari")}
    return {**empty,
            "faturalar": [{"fatura_id": "F1", "tur": "satis", "musteri_id": "M1", "fatura_tarihi": iso_date("2025-01-01"),
                           "vade_tarihi": iso_date("2025-01-31"), "tutar_kurus": 100, "tutar_try_kurus": 100,
                           "iliskili_fatura_id": None, "uygulanan_kurus": None}],
            "banka_hesaplari": [{"hesap_id": "A_TRY", "acilis_tarihi": iso_date("2025-01-01"),
                                 "acilis_bakiye_kurus": 10**9}]}


def test_store_adds_nothing_at_the_same_clock(tmp_path):
    t, store = _tiny_tables(), AlertStore(tmp_path)
    run_scheduler(t, Clock(iso_date("2025-03-01"), DATA_END), store)
    before = (store.alerts_path.read_bytes() if store.alerts_path.exists() else b"", store.state_path.read_bytes())
    result = run_scheduler(t, Clock(iso_date("2025-03-01"), DATA_END), store)
    assert result["yeni"] == []
    assert (store.alerts_path.read_bytes() if store.alerts_path.exists() else b"", store.state_path.read_bytes()) == before


def test_store_clock_cannot_go_back(tmp_path):
    t, store = _tiny_tables(), AlertStore(tmp_path)
    run_scheduler(t, Clock(iso_date("2025-03-01"), DATA_END), store)
    with pytest.raises(AlertStoreError):
        run_scheduler(t, Clock(iso_date("2025-02-01"), DATA_END), store)


def test_store_does_not_continue_with_other_settings(tmp_path):
    t, store = _tiny_tables(), AlertStore(tmp_path)
    run_scheduler(t, Clock(iso_date("2025-03-01"), DATA_END), store)
    with pytest.raises(AlertStoreError):
        run_scheduler(t, Clock(iso_date("2025-04-01"), DATA_END), store, RuleSettings(gecikme_p=0.05))


def test_store_has_no_wall_clock(tmp_path):
    t, store = _tiny_tables(), AlertStore(tmp_path)
    run_scheduler(t, Clock(iso_date("2025-03-01"), DATA_END), store)
    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert set(state) == {"son_gun", "ayarlar"} and state["son_gun"] == "2025-03-01"
