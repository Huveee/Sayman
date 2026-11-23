"""Phase 10 acceptance tests: the demo back end (FastAPI) with a scripted model.

Acceptance criteria (FAZLAR.md): the CFO and a sales manager get different answers to the same question; fast-forwarding
time triggers the report and the alert. Also: identity comes only from the server session (the user in the body is ignored,
a request without a session gets 401), alerts are filtered by the role's scope, the report is built only from the role's
tools, and the warehouse and data/saat.json do not change during the demo.

Two environments: Phase 2's small hand-computed company (fast API tests) and the real data (time and alert tests;
skipped if the data is missing).
"""

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import support.authorization_trials as fd
import pytest
from fastapi.testclient import TestClient
from test_phase02_warehouse import AS_OF, _hand_built_tables

from sayman.warehouse.loader import build_warehouse
from sayman.tools.permissions import read_permissions
from sayman.assistant.orchestrator import ModelResponse
from sayman.demo.app import COOKIE_NAME, Demo, format_lira, build_app
from sayman.data.table_io import read_csv, write_csv
from sayman.data.clock import Clock, read_clock, write_clock

ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = bytes(range(32))
CFO, DOMESTIC, EXPORT = fd.USERS["cfo"], fd.USERS["yurtici"], fd.USERS["ihracat"]
QUESTION = "Vadesi geçmiş alacaklarımızın toplamı ne kadar?"
REAL_WAREHOUSE, REAL_CLOCK, GENERATED = (ROOT / "data" / "ambar" / "sayman.duckdb", ROOT / "data" / "saat.json",
                                       ROOT / "data" / "uretilen")


class ScriptedModel:
    """If the question contains "nakit" it calls nakit_pozisyonu, otherwise alacak_yaslandirma; it writes the figure from
    the tool result."""

    def __init__(self):
        self.call_count = 0

    def __call__(self, messages, tools, timeout):
        self.call_count += 1
        if messages[-1]["role"] != "tool":
            name = "nakit_pozisyonu" if "nakit" in messages[1]["content"].lower() else "alacak_yaslandirma"
            return ModelResponse(tool_calls=[(name, {})], prompt_tokens=100, generated_tokens=10)
        yanit = json.loads(messages[-1]["content"])
        result, scope = yanit["sonuc"], yanit["kapsam"]
        if "hesaplar" in result:
            return ModelResponse(content=f"Bankalardaki toplam nakit {format_lira(result['toplam_tl'])}.")
        return ModelResponse(content=f"Vadesi geçmiş alacaklarınızın toplamı {format_lira(result['vadesi_gecmis_tl'])} "
                                  f"({scope}).")


def _make_demo(folder, warehouse, start, tables, **extra):
    return Demo(warehouse=warehouse, state_dir=folder / "demo", initial_clock=start, key=SIGNING_KEY, permissions=read_permissions(),
                tables=tables, model=ScriptedModel(), time_fn=lambda: 1_750_000_000.0, **extra)


def _login(app, user) -> TestClient:
    c = TestClient(app)
    r = c.post("/api/giris", json={"kullanici": user})
    assert r.status_code == 200 and r.json()["kullanici"] == user
    return c


@pytest.fixture(scope="module")
def manual_demo(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faz10_elle")
    tables = _hand_built_tables()
    write_csv(tables, folder / "csv")
    build_warehouse(folder / "csv", folder / "sayman.duckdb").close()
    demo = _make_demo(folder, folder / "sayman.duckdb", Clock(AS_OF, dt.date(2025, 6, 30)), tables)
    yield demo, build_app(demo)
    demo.close()


# ------------------------------------------------------------------ identity and session


@pytest.mark.parametrize("path,method", [("/api/ben", "get"), ("/api/rapor", "get"), ("/api/uyarilar", "get"),
                                        ("/api/ornek_sorular", "get"), ("/api/sor", "post"),
                                        ("/api/ileri_sar", "post")])
def test_request_without_session_401(manual_demo, path, method):
    c = TestClient(manual_demo[1])
    r = c.post(path, json={"soru": QUESTION}) if method == "post" else c.get(path)
    assert r.status_code == 401
    c.cookies.set(COOKIE_NAME, "uydurma-oturum")
    r = c.post(path, json={"soru": QUESTION}) if method == "post" else c.get(path)
    assert r.status_code == 401


def test_login_unknown_user_and_httponly_cookie(manual_demo):
    c = TestClient(manual_demo[1])
    assert c.post("/api/giris", json={"kullanici": "kimse"}).status_code == 400
    r = c.post("/api/giris", json={"kullanici": CFO})
    cookie = r.headers["set-cookie"]
    assert "httponly" in cookie.lower() and "samesite=strict" in cookie.lower()
    # neither the key nor the card goes to the browser
    assert SIGNING_KEY.hex() not in r.text and "kart" not in r.json()
    assert c.post("/api/cikis").json() == {"tamam": True}
    assert c.get("/api/ben").status_code == 401


def test_user_list_and_page(manual_demo):
    c = TestClient(manual_demo[1])
    people = c.get("/api/kullanicilar").json()["kullanicilar"]
    assert {k["kullanici"] for k in people} == {CFO, DOMESTIC, EXPORT}
    assert "Sayman" in c.get("/").text and c.get("/static/app.js").status_code == 200


# ------------------------------------------------------------------ acceptance: same question, different answer


def test_cfo_and_sales_manager_get_different_answers_to_same_question(manual_demo):
    demo, app = manual_demo
    answers = {}
    for k in (CFO, DOMESTIC, EXPORT):
        r = _login(app, k).post("/api/sor", json={"soru": QUESTION})
        assert r.status_code == 200
        j = r.json()
        assert j["durum"] == "tamam" and j["araclar"] == [{"arac": "alacak_yaslandirma", "hata": None}]
        expected, error = demo.server.gate("alacak_yaslandirma", {}, demo.card(k))
        assert not error and format_lira(expected["sonuc"]["vadesi_gecmis_tl"]) in j["cevap"]
        answers[k] = j["cevap"]
    assert len(set(answers.values())) == 3


def test_user_in_body_is_ignored(manual_demo):
    app = manual_demo[1]
    domestic = _login(app, DOMESTIC).post("/api/sor", json={"soru": QUESTION}).json()["cevap"]
    spoofed = _login(app, DOMESTIC).post("/api/sor", json={"soru": QUESTION, "kullanici": CFO, "rol": "cfo"}).json()
    cfo = _login(app, CFO).post("/api/sor", json={"soru": QUESTION}).json()["cevap"]
    assert spoofed["cevap"] == domestic != cfo


def test_sales_manager_has_no_permission_for_cash_question(manual_demo):
    app = manual_demo[1]
    j = _login(app, DOMESTIC).post("/api/sor", json={"soru": "Bankalarda ne kadar nakit var?"}).json()
    assert j["durum"] == "ret" and j["araclar"] == [{"arac": "nakit_pozisyonu", "hata": "yetki_yok"}]
    assert _login(app, CFO).post("/api/sor", json={"soru": "Bankalarda ne kadar nakit var?"}).json()["durum"] == "tamam"


def test_question_validation_and_409_when_busy(manual_demo):
    demo, app = manual_demo
    c = _login(app, CFO)
    assert c.post("/api/sor", json={"soru": "  "}).status_code == 422
    assert c.post("/api/sor", json={"soru": "x" * 501}).status_code == 422
    assert demo.lock.acquire(blocking=False)
    try:
        r = c.post("/api/sor", json={"soru": QUESTION})
        assert r.status_code == 409 and c.get("/api/durum").json()["mesgul"] is True
    finally:
        demo.lock.release()


# ------------------------------------------------------------------ report and alert scope


def test_report_is_built_only_from_roles_tools(manual_demo):
    app = manual_demo[1]
    cfo = _login(app, CFO).get("/api/rapor").json()
    sales_report = _login(app, DOMESTIC).get("/api/rapor").json()
    assert [b["baslik"] for b in cfo["bolumler"]] == ["Nakit pozisyonu", "Önümüzdeki 7 günün ödemeleri",
                                                      "Açık alacaklar", "7 gün içinde vadesi gelen çekler"]
    assert [b["baslik"] for b in sales_report["bolumler"]] == ["Açık alacaklar", "7 gün içinde vadesi gelen çekler"]
    assert cfo["tarih"] == sales_report["tarih"] == AS_OF.isoformat() and sales_report["kapsam_adi"] == "yalnızca yurtiçi müşteriler"
    assert all(b["ozet"] != "—" for b in cfo["bolumler"] + sales_report["bolumler"])  # no section was rejected at the gate
    assert cfo["bolumler"][2]["ozet"] != sales_report["bolumler"][0]["ozet"]


def test_alerts_are_filtered_by_scope(manual_demo):
    demo = manual_demo[0]
    customers = _hand_built_tables()["musteriler"]  # independent of the filter's source (the warehouse)
    domestic = next(m["musteri_id"] for m in customers if m["yurtici"])
    export = next(m["musteri_id"] for m in customers if not m["yurtici"])
    alerts = [{"uyari_id": "U1", "gun": "2025-03-01", "onem": "uyari", "baslik": "a", "aciklama": "a",
                 "musteri_id": domestic, "kaynak": {"x": [1]}},
                {"uyari_id": "U2", "gun": "2025-03-02", "onem": "uyari", "baslik": "b", "aciklama": "b",
                 "musteri_id": export},
                {"uyari_id": "U3", "gun": "2025-03-03", "onem": "bilgi", "baslik": "c", "aciklama": "c",
                 "musteri_id": None},
                {"uyari_id": "U4", "gun": "2025-03-04", "onem": "bilgi", "baslik": "d", "aciklama": "d",
                 "musteri_id": "M_BILINMEYEN"}]
    assert [u["uyari_id"] for u in demo.filter_alerts(CFO, alerts)] == ["U1", "U2", "U3", "U4"]
    assert [u["uyari_id"] for u in demo.filter_alerts(DOMESTIC, alerts)] == ["U1"]
    assert [u["uyari_id"] for u in demo.filter_alerts(EXPORT, alerts)] == ["U2"]
    assert "kaynak" not in demo.filter_alerts(CFO, alerts)[0] and "kural" not in demo.filter_alerts(CFO, alerts)[0]


def test_sales_description_has_no_company_wide_figure():
    from sayman.demo.app import scoped_description
    cheque_text = ("Ada Ltd. müşterisinin Banka G çeki (41.931,43 TL, vade 2026-09-03) karşılıksız çıktı; son 12 ayın "
           "çeklerinin üst çeyreği 27.203,60 TL'den başlıyor.")
    assert scoped_description("karsiliksiz_cek", cheque_text, "b") == ("Ada Ltd. müşterisinin Banka G çeki (41.931,43 TL, "
                                                             "vade 2026-09-03) karşılıksız çıktı.")
    late_text = "Ada Ltd. son 12 ayın en büyük 50 müşterisinden biri. Vadesi son 30 günde dolan 4 faturasından 3 tanesi açık."
    assert scoped_description("musteri_gecikmesi", late_text, "b") == "Vadesi son 30 günde dolan 4 faturasından 3 tanesi açık."
    assert scoped_description("yeni_kural", "şirket geneli 1.000 TL", "Başlık") == "Başlık."


def test_request_with_other_host_name_is_rejected(manual_demo):
    assert TestClient(manual_demo[1], base_url="http://saldirgan.example").get("/api/kullanicilar").status_code == 400
    assert TestClient(manual_demo[1], base_url="http://127.0.0.1:8010").get("/api/kullanicilar").status_code == 200


# ------------------------------------------------------------------ acceptance: fast-forward time (real data)

real_data_available = pytest.mark.skipif(not (REAL_WAREHOUSE.exists() and REAL_CLOCK.exists() and GENERATED.exists()),
                                reason="Önce Faz 1-2 betikleri çalıştırılmalı")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def real_demo(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faz10_gercek")
    before = (_digest(REAL_WAREHOUSE), _digest(REAL_CLOCK))
    demo = _make_demo(folder, REAL_WAREHOUSE, read_clock(REAL_CLOCK), read_csv(GENERATED))
    yield demo, build_app(demo), before, folder
    demo.close()


@real_data_available
def test_fast_forwarding_time_produces_report_and_alert(real_demo):
    demo, app, before, folder = real_demo
    start_date = demo.clock.simdi
    c = _login(app, CFO)
    report_before = c.get("/api/rapor").json()
    stored_count = len(demo.store.alerts())
    r = c.post("/api/ileri_sar", json={})
    assert r.status_code == 200
    j = r.json()
    assert j["onceki"] == start_date.isoformat() and j["bugun"] == (start_date + dt.timedelta(days=7)).isoformat()
    # New alerts: only from the days advanced (the store is filled up to today on startup)
    assert j["yeni_uyarilar"] and all(start_date.isoformat() < u["gun"] <= j["bugun"] for u in j["yeni_uyarilar"])
    assert len(demo.store.alerts()) == stored_count + len(j["yeni_uyarilar"])
    # The report is the report of the new day
    assert j["rapor"]["tarih"] == j["bugun"] != report_before["tarih"]
    assert j["rapor"]["bolumler"] != report_before["bolumler"]
    assert c.get("/api/durum").json()["bugun"] == j["bugun"]
    new_ids = {u["uyari_id"] for u in j["yeni_uyarilar"]}
    assert new_ids <= {u["uyari_id"] for u in c.get("/api/uyarilar").json()["uyarilar"]}
    # Sales managers see only the alerts of their own customers; they do not see company-level alerts or company-wide
    # thresholds. The correctness criterion is independent of the filter's source (the warehouse): the generated CSV tables.
    csv_domestic = {m["musteri_id"]: m["yurtici"] for m in demo.tables["musteriler"]}
    domestic_alerts = _login(app, DOMESTIC).get("/api/uyarilar").json()["uyarilar"]
    assert domestic_alerts
    for k, is_domestic, rows in ((DOMESTIC, True, domestic_alerts),
                                 (EXPORT, False, _login(app, EXPORT).get("/api/uyarilar").json()["uyarilar"])):
        for u in rows:
            assert u["musteri_id"] is not None and csv_domestic[u["musteri_id"]] is is_domestic
            assert "üst çeyreği" not in u["aciklama"] and "en büyük" not in u["aciklama"]
    # The sales manager can fast-forward time too; the new alerts and the report returned to them are in their own scope
    j2 = _login(app, DOMESTIC).post("/api/ileri_sar", json={}).json()
    assert all(csv_domestic[u["musteri_id"]] is True for u in j2["yeni_uyarilar"])
    assert [b["baslik"] for b in j2["rapor"]["bolumler"]] == ["Açık alacaklar", "7 gün içinde vadesi gelen çekler"]
    # The warehouse and the project clock did not change; the demo clock is in its own folder
    assert (_digest(REAL_WAREHOUSE), _digest(REAL_CLOCK)) == before
    assert read_clock(folder / "demo" / "saat.json").simdi.isoformat() == j2["bugun"]


@real_data_available
def test_clock_stops_at_end_of_data_and_survives_reopening(real_demo):
    demo, app, _, folder = real_demo
    c = _login(app, CFO)
    assert c.post("/api/ileri_sar", json={"gun": 0}).status_code == 422
    while demo.clock.simdi < demo.clock.veri_bitis:
        assert c.post("/api/ileri_sar", json={"gun": 31}).status_code == 200
    r = c.post("/api/ileri_sar", json={})
    assert r.status_code == 409 and "sıfırla" in r.json()["detail"]
    # A reopened demo continues from the day it stopped and does not present old alerts as new. Even if the clock file lags
    # behind the store (the store was written, the clock write was interrupted), the clock catches up with the store.
    demo.close()
    write_clock(read_clock(REAL_CLOCK), folder / "demo" / "saat.json")
    second = _make_demo(folder, REAL_WAREHOUSE, read_clock(REAL_CLOCK), demo.tables)
    try:
        assert second.clock == demo.clock and len(second.store.alerts()) == len(demo.store.alerts())
    finally:
        second.close()
