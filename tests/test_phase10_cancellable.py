"""Phase 10 demo: the model call that can be stopped (src/sayman/demo/cancellable.py). It must send exactly what
OllamaModel sends and read the answer the same way; `cancel` must stop a running call at once. No Ollama: a fake
HTTP server stands in for it."""

import threading
import time

import pytest
from test_phase10_warmup import WAIT_S, FakeOllama

from sayman.assistant.model import OllamaModel
from sayman.assistant.orchestrator import ModelTimeout, tool_definitions
from sayman.demo.cancellable import CancellableModel, TurnCancelled

MESSAGES = [{"role": "system", "content": "Bugün 2026-10-13."}, {"role": "user", "content": "Merhaba"}]


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


def test_same_request_and_same_answer_as_ollama_model(answering):
    plain = OllamaModel("sayman-ayarli", host=answering.host)
    expected = plain(MESSAGES, tool_definitions(), 30)
    got = CancellableModel(plain)(MESSAGES, tool_definitions(), 30)
    first, second = answering.bodies
    assert first == second  # byte for byte: the model sees the same prompt
    assert got == expected and got.content == "Merhaba"


def test_cancel_stops_a_running_call(holding):
    model = CancellableModel(OllamaModel("sayman-ayarli", host=holding.host))
    model.begin_turn()
    outcome = []

    def call():
        try:
            model(MESSAGES, tool_definitions(), 30)
        except Exception as e:
            outcome.append(e)
    t = threading.Thread(target=call)
    t.start()
    assert holding.received.wait(WAIT_S)
    started = time.monotonic()
    model.cancel()
    t.join(WAIT_S)
    assert len(outcome) == 1 and isinstance(outcome[0], TurnCancelled)
    assert time.monotonic() - started < 2
    assert holding.gone.wait(WAIT_S)  # the connection closed: Ollama cancels the work


def test_after_cancel_the_question_sends_nothing_until_the_next_question(answering):
    model = CancellableModel(OllamaModel("sayman-ayarli", host=answering.host))
    model.begin_turn()
    model.cancel()
    with pytest.raises(TurnCancelled):
        model(MESSAGES, tool_definitions(), 30)
    assert answering.bodies == []
    model.begin_turn()  # the next question
    assert model(MESSAGES, tool_definitions(), 30).content == "Merhaba"


def test_a_slow_answer_is_a_timeout(holding):
    model = CancellableModel(OllamaModel("sayman-ayarli", host=holding.host))
    with pytest.raises(ModelTimeout):
        model(MESSAGES, tool_definitions(), 0.3)
