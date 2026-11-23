"""Phase 10 demo: an answer reaches only the session that asked. In the live demo the CFO asked, logged out before the
answer came, and the export sales manager logged in on the same browser; the CFO's answer then appeared on the
export manager's screen. The server now withholds an answer whose session has ended; the page cancels the request
(src/sayman/demo/static/app.js, oturumuBitir)."""

import datetime as dt
import json
import threading

import pytest
from fastapi.testclient import TestClient
from test_phase02_warehouse import AS_OF, _hand_built_tables
from test_phase10_demo import _login, _make_demo

from sayman.data.clock import Clock
from sayman.data.table_io import write_csv
from sayman.demo.app import COOKIE_NAME, build_app
from sayman.warehouse.loader import build_warehouse

CFO, EXPORT = "selin.aydin", "deniz.arslan"
QUESTION = "Bankalarda toplam ne kadar nakit var?"
WITHHELD = "Oturum kapandı; cevap gösterilmedi."
WAIT_S = 10


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faz10_oturum")
    tables = _hand_built_tables()
    write_csv(tables, folder / "csv")
    build_warehouse(folder / "csv", folder / "sayman.duckdb").close()
    return folder, tables


@pytest.fixture
def demo(warehouse, tmp_path):
    folder, tables = warehouse
    d = _make_demo(tmp_path, folder / "sayman.duckdb", Clock(AS_OF, dt.date(2025, 6, 30)), tables)
    yield d
    d.close()


def _held(monkeypatch, owner, name):
    """Makes owner.name wait until the test releases it; returns (started, release)."""
    started, release = threading.Event(), threading.Event()
    original = getattr(owner, name)

    def held(*args, **kwargs):
        started.set()
        release.wait(WAIT_S)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, name, held)
    return started, release


def _in_background(request) -> dict:
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("yanit", request()))
    t.start()
    out["thread"] = t
    return out


def _same_browser(app, client) -> TestClient:
    """A second client with the first one's session cookie: the same browser tab, used while a request waits."""
    other = TestClient(app)
    other.cookies.set(COOKIE_NAME, client.cookies.get(COOKIE_NAME))
    return other


def _finish(pending, release):
    release.set()
    pending["thread"].join(WAIT_S)
    return pending["yanit"]


def test_answer_is_withheld_when_another_user_logs_in_meanwhile(demo, monkeypatch):
    app = build_app(demo)
    started, release = _held(monkeypatch, demo.assistant, "ask")
    cfo = _login(app, CFO)
    pending = _in_background(lambda: cfo.post("/api/sor", json={"soru": QUESTION}))
    assert started.wait(WAIT_S)
    browser = _same_browser(app, cfo)
    assert browser.post("/api/giris", json={"kullanici": EXPORT}).status_code == 200  # the old session closes
    busy = browser.post("/api/sor", json={"soru": "Vadesi geçmiş alacaklarımız ne kadar?"})
    assert busy.status_code == 409  # the model is still on the CFO's question
    r = _finish(pending, release)
    assert r.status_code == 401 and r.json() == {"detail": WITHHELD}  # nothing of the CFO's answer goes out
    turn = json.loads((demo.directory / "asistan.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert turn["kullanici"] == CFO and turn["durum"] == "tamam"  # the turn ran, under the user who asked
    assert browser.get("/api/ben").json()["kullanici"] == EXPORT


def test_answer_is_withheld_after_logout(demo, monkeypatch):
    app = build_app(demo)
    started, release = _held(monkeypatch, demo.assistant, "ask")
    cfo = _login(app, CFO)
    pending = _in_background(lambda: cfo.post("/api/sor", json={"soru": QUESTION}))
    assert started.wait(WAIT_S)
    assert _same_browser(app, cfo).post("/api/cikis").status_code == 200
    r = _finish(pending, release)
    assert r.status_code == 401 and r.json() == {"detail": WITHHELD}


def test_answer_reaches_the_session_that_is_still_open(demo, monkeypatch):
    app = build_app(demo)
    started, release = _held(monkeypatch, demo.assistant, "ask")
    cfo = _login(app, CFO)
    pending = _in_background(lambda: cfo.post("/api/sor", json={"soru": QUESTION}))
    assert started.wait(WAIT_S)
    _login(app, EXPORT)  # another browser: the CFO's session stays open
    r = _finish(pending, release)
    assert r.status_code == 200 and r.json()["durum"] == "tamam" and "TL" in r.json()["cevap"]


def test_report_after_a_clock_advance_is_withheld_when_the_user_changed(demo, monkeypatch):
    app = build_app(demo)
    started, release = _held(monkeypatch, demo, "advance")
    cfo = _login(app, CFO)
    before = demo.clock.simdi
    pending = _in_background(lambda: cfo.post("/api/ileri_sar", json={"gun": 1}))
    assert started.wait(WAIT_S)
    browser = _same_browser(app, cfo)
    assert browser.post("/api/giris", json={"kullanici": EXPORT}).status_code == 200
    r = _finish(pending, release)
    assert r.status_code == 401 and r.json() == {"detail": WITHHELD}  # no CFO report, no CFO alerts
    assert demo.clock.simdi == before + dt.timedelta(days=1)  # the clock is shared: it did advance
    assert browser.get("/api/rapor").json()["kullanici"] == EXPORT
