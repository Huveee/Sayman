"""Phase 10 demo: warming the model up after the clock advances. Only the newest date is warmed up, a warm-up of an
older date is stopped, and the warm-up sends the same request as a real turn. No Ollama: a fake warmer and a fake
HTTP server stand in for it."""

import datetime as dt
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_phase02_warehouse import AS_OF, _hand_built_tables
from test_phase10_demo import _make_demo

from sayman.assistant.model import OllamaModel
from sayman.assistant.orchestrator import tool_definitions
from sayman.data.clock import Clock
from sayman.data.table_io import write_csv
from sayman.demo.warmup import OllamaWarmer, warm_up_messages
from sayman.warehouse.loader import build_warehouse

WAIT_S = 10


def _until(condition, timeout_s=WAIT_S):
    end = time.monotonic() + timeout_s
    while not condition():
        if time.monotonic() > end:
            raise AssertionError("beklenen durum oluşmadı")
        time.sleep(0.01)


# ------------------------------------------------------------------ the demo's warm-up queue

class BlockingWarmer:
    """Each warm-up waits until the test releases it; records the dates and the cancel calls."""

    def __init__(self):
        self.started, self.cancels, self.release = [], [], threading.Semaphore(0)

    def __call__(self, today):
        self.started.append(today)
        self.release.acquire(timeout=WAIT_S)

    def cancel_older(self, newest):
        self.cancels.append(newest)


@pytest.fixture(scope="module")
def warehouse(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faz10_isinma")
    tables = _hand_built_tables()
    write_csv(tables, folder / "csv")
    build_warehouse(folder / "csv", folder / "sayman.duckdb").close()
    return folder, tables


def _demo(warehouse, tmp_path, warmer):
    folder, tables = warehouse
    return _make_demo(tmp_path, folder / "sayman.duckdb", Clock(AS_OF, dt.date(2025, 6, 30)), tables, warmer=warmer)


def _advance(demo, days=1):
    with demo.lock:
        return demo.advance(days)


def test_only_the_newest_date_is_warmed_up(warehouse, tmp_path):
    warmer = BlockingWarmer()
    demo = _demo(warehouse, tmp_path, warmer)
    try:
        _until(lambda: warmer.started == [AS_OF])
        assert demo.model_ready is False
        _advance(demo)
        _advance(demo)  # two advances while the first warm-up still runs
        newest = AS_OF + dt.timedelta(days=2)
        assert warmer.cancels == [AS_OF, AS_OF + dt.timedelta(days=1), newest]  # the warm-ups of older dates are stopped
        warmer.release.release()  # the first warm-up ends; the day in between is skipped
        _until(lambda: len(warmer.started) == 2)
        assert warmer.started == [AS_OF, newest]
        assert demo.model_ready is False  # ready only when the newest date is warmed up
        warmer.release.release()
        _until(lambda: demo.model_ready)
        time.sleep(0.05)
        assert warmer.started == [AS_OF, newest]
    finally:
        demo.close()


def test_a_failed_warm_up_still_marks_the_model_ready(warehouse, tmp_path):
    def failing(today):
        raise OSError("Ollama kapalı")
    demo = _demo(warehouse, tmp_path, failing)
    try:
        _until(lambda: demo.model_ready)  # only the first question is slow
    finally:
        demo.close()


def test_without_a_warmer_the_model_counts_as_ready(warehouse, tmp_path):
    demo = _demo(warehouse, tmp_path, None)
    try:
        assert demo.model_ready is True
        _advance(demo)
        assert demo.model_ready is True
    finally:
        demo.close()


# ------------------------------------------------------------------ the Ollama warmer against a fake server

class FakeOllama:
    """Answers /api/chat like Ollama, or holds the request until the client goes away (a long prompt being read)."""

    def __init__(self, hold: bool):
        self.bodies, self.received, self.gone = [], threading.Event(), threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                fake.bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
                fake.received.set()
                if hold:
                    self.connection.settimeout(WAIT_S)
                    try:
                        if self.connection.recv(1) == b"":  # the client closed the connection
                            fake.gone.set()
                    except OSError:
                        fake.gone.set()
                    return
                reply = json.dumps({"model": "sahte", "created_at": "2026-01-01T00:00:00Z", "done": True,
                                    "done_reason": "stop", "message": {"role": "assistant", "content": "Merhaba"}})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply.encode())

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def answering():
    fake = FakeOllama(hold=False)
    yield fake
    fake.close()


@pytest.fixture
def holding():
    fake = FakeOllama(hold=True)
    yield fake
    fake.close()


def test_warm_up_sends_the_same_request_as_a_real_turn(answering):
    model = OllamaModel("sayman-ayarli", host=answering.host)
    day = dt.date(2026, 9, 22)
    OllamaWarmer(model)(day)
    model(warm_up_messages(day), tool_definitions(), 30)
    warm_up, turn = answering.bodies
    assert warm_up == turn  # byte for byte: Ollama renders the same prompt, so the cache it fills is the turn's
    body = json.loads(warm_up)
    assert body["think"] is False and body["options"] == model.options and body["stream"] is False
    assert "2026-09-22" in body["messages"][0]["content"]


def test_a_running_warm_up_of_an_older_date_is_stopped(holding):
    warmer = OllamaWarmer(OllamaModel("sayman-ayarli", host=holding.host))
    outcome = []
    t = threading.Thread(target=lambda: outcome.append(warmer(dt.date(2026, 9, 15)) or "döndü"))
    t.start()
    assert holding.received.wait(WAIT_S)
    warmer.cancel_older(dt.date(2026, 9, 15))  # the same date: it keeps running
    time.sleep(0.2)
    assert t.is_alive() and not holding.gone.is_set()
    started = time.monotonic()
    warmer.cancel_older(dt.date(2026, 9, 22))
    t.join(WAIT_S)
    assert outcome == ["döndü"] and time.monotonic() - started < 2  # no error: a stopped warm-up is not a failure
    assert holding.gone.wait(WAIT_S)  # the server saw the connection close


def test_a_warm_up_of_an_older_date_is_not_sent(answering):
    warmer = OllamaWarmer(OllamaModel("sayman-ayarli", host=answering.host))
    warmer.cancel_older(dt.date(2026, 9, 22))
    warmer(dt.date(2026, 9, 15))
    assert answering.bodies == []
    warmer(dt.date(2026, 9, 22))
    assert len(answering.bodies) == 1


def test_a_warm_up_that_fails_on_its_own_raises():
    fake = FakeOllama(hold=False)
    host = fake.host
    fake.close()  # nothing listens there any more
    with pytest.raises(OSError):
        OllamaWarmer(OllamaModel("sayman-ayarli", host=host))(dt.date(2026, 9, 22))
