"""Warming the model up for the demo's date.

The system prompt contains today's date. This model (qwen3.5, hybrid memory) cannot reuse its cache from the date
onwards, so after every clock advance it reads the whole ~2,800-token prompt again: 85-100 s on this computer (no
graphics card). The warm-up pays that time in the background, so the next first question does not.

Ollama runs one request at a time. When the clock advances twice in a row, the warm-up for the older date is of no
use any more and would make both the newer warm-up and the next question wait behind it. `OllamaWarmer.cancel_older`
closes its connection; Ollama then stops reading the stale prompt.
"""

from __future__ import annotations

import datetime as dt
import http.client
import socket
import threading

from sayman.assistant.orchestrator import SYSTEM_PROMPT, tool_definitions


def warm_up_messages(today: dt.date) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPT.format(bugun=today.isoformat())},
            {"role": "user", "content": "Merhaba"}]


class OllamaWarmer:
    """Sends the warm-up as the exact request ollama.Client.chat would send (captured with a mock transport, so the
    prompt Ollama renders is the same as in a real turn), but on a plain HTTP connection that another thread can
    close."""

    def __init__(self, model, timeout_s: float = 600.0):
        self.model, self.timeout_s = model, timeout_s
        self._lock = threading.Lock()
        self._newest: dt.date | None = None
        self._running: tuple[dt.date, http.client.HTTPConnection] | None = None

    def request(self, today: dt.date) -> tuple[str, int, str, bytes]:
        """(host, port, path, body) of the chat request for that day; nothing is sent."""
        import httpx
        import ollama

        captured: list[httpx.Request] = []

        def capture(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"model": "", "created_at": "", "done": True,
                                             "message": {"role": "assistant", "content": ""}})
        client = ollama.Client(host=self.model.host, transport=httpx.MockTransport(capture))
        try:
            client.chat(**self.model.chat_arguments(warm_up_messages(today), tool_definitions()))
        except Exception:  # only the request is needed; the made-up reply does not matter
            pass
        if not captured:
            raise RuntimeError("Isınma isteği oluşturulamadı.")
        r = captured[0]
        return r.url.host, r.url.port or 80, r.url.raw_path.decode("ascii"), r.content

    def __call__(self, today: dt.date) -> None:
        host, port, path, body = self.request(today)
        connection = http.client.HTTPConnection(host, port, timeout=self.timeout_s)
        connection.connect()
        with self._lock:
            if self._stale(today):
                connection.close()
                return
            self._running = (today, connection)
        try:
            connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            connection.getresponse().read()
        except OSError:
            if not self._stale(today):
                raise
        finally:
            with self._lock:
                self._running = None
            connection.close()

    def cancel_older(self, newest: dt.date) -> None:
        """Stops the warm-up of any date before `newest`, running or about to start. Dates only move forward."""
        with self._lock:
            self._newest = newest if self._newest is None else max(self._newest, newest)
            if self._running is not None and self._running[0] < self._newest:
                try:
                    self._running[1].sock.shutdown(socket.SHUT_RDWR)
                except (AttributeError, OSError):  # already closed
                    pass

    def _stale(self, today: dt.date) -> bool:
        return self._newest is not None and today < self._newest
