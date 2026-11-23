"""Ollama requests that another thread can stop.

The ollama client gives no way to stop a request that is running. The demo needs one twice: a warm-up for a date
that is no longer today (warmup.py), and a question whose session has ended (the user logged out, or another user
logged in on the same browser). The answer to such a question is never shown, and the next user would wait for it.

The request is the exact body ollama.Client.chat would send (captured with a mock transport, nothing is sent), so
Ollama renders the same prompt as in a real call. It goes out on a plain HTTP connection; closing that connection
makes Ollama cancel the work within a second or two (seen live in its log as "cancel task").
"""

from __future__ import annotations

import http.client
import json
import socket
import threading

from sayman.assistant.orchestrator import ModelResponse, ModelTimeout


class TurnCancelled(Exception):
    """The question was stopped because the session that asked has ended."""


def chat_request(model, messages: list[dict], tools: list[dict]) -> tuple[str, int, str, bytes]:
    """(host, port, path, body) of the chat request OllamaModel would send for these messages; nothing is sent."""
    import httpx
    import ollama

    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"model": "", "created_at": "", "done": True,
                                         "message": {"role": "assistant", "content": ""}})
    client = ollama.Client(host=model.host, transport=httpx.MockTransport(capture))
    try:
        client.chat(**model.chat_arguments(messages, tools))
    except Exception:  # only the request is needed; the made-up reply does not matter
        pass
    if not captured:
        raise RuntimeError("Ollama isteği oluşturulamadı.")
    r = captured[0]
    return r.url.host, r.url.port or 80, r.url.raw_path.decode("ascii"), r.content


def shut(connection: http.client.HTTPConnection) -> None:
    """Closes a connection that another thread may be reading from (shutdown wakes up the blocked read)."""
    try:
        connection.sock.shutdown(socket.SHUT_RDWR)
    except (AttributeError, OSError):  # not connected yet, or already closed
        pass


class CancellableModel:
    """The demo's model: OllamaModel's request on a connection that `cancel` can close. `begin_turn` starts a new
    question; after `cancel` every call of that question raises TurnCancelled at once, so the orchestrator ends the
    turn (status model_hatasi, reason TurnCancelled in the audit log) and the next question does not wait."""

    def __init__(self, model):
        self.model = model
        self._lock = threading.Lock()
        self._cancelled = False
        self._connection: http.client.HTTPConnection | None = None

    def begin_turn(self) -> None:
        with self._lock:
            self._cancelled = False

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            if self._connection is not None:
                shut(self._connection)

    def __call__(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelResponse:
        import ollama

        host, port, path, body = chat_request(self.model, messages, tools)
        connection = http.client.HTTPConnection(host, port, timeout=max(timeout, 0.1))
        try:
            connection.connect()
            with self._lock:
                if self._cancelled:
                    raise TurnCancelled("Oturum kapandı; soru yarıda bırakıldı.")
                self._connection = connection
            connection.request("POST", path, body=body, headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            data = response.read()
        except TimeoutError as e:
            raise ModelTimeout(str(e)) from e
        except (OSError, http.client.HTTPException) as e:
            if self._cancelled:
                raise TurnCancelled("Oturum kapandı; soru yarıda bırakıldı.") from e
            raise
        finally:
            with self._lock:
                self._connection = None
            connection.close()
        if self._cancelled:  # the answer arrived just as the session ended: it is not used
            raise TurnCancelled("Oturum kapandı; soru yarıda bırakıldı.")
        if response.status != 200:
            raise RuntimeError(f"Ollama {response.status}: {data[:200].decode('utf-8', 'replace')}")
        return self.model.response(ollama.ChatResponse(**json.loads(data)))
