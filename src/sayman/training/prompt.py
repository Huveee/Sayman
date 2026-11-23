"""Training sample = (prompt, completion): the text the model sees in real use and the text it must write.

The prompt is rendered by Ollama itself (`_debug_render_only`): the messages and the tool list that the orchestrator
sent in that call are given to the server in the form the Ollama model class (`sayman.assistant.model`) sends them
(with the same ollama client classes), and the text produced by the qwen3.5 renderer of Ollama is taken as it is. The
HF chat template of the model renders some places differently (e.g. it puts an empty thinking block in front of a tool
call in the same turn); the template is not imitated so that the training text is the same as the rendering used in
the measurement.

The completion is the raw text the model produces: a tool call in the XML format of Qwen3.5 (the same format in which
Ollama renders the history), the final answer as plain text; both end with <|im_end|>.
"""

from __future__ import annotations

import copy
import json

from sayman.assistant.orchestrator import ModelResponse

END_TOKEN = "<|im_end|>"


def request_body(messages: list[dict], tools: list[dict], model: str) -> dict:
    """The body the Ollama model class sends to /api/chat (with the same client classes), with only rendering
    requested."""
    from ollama._client import _copy_messages, _copy_tools
    from ollama._types import ChatRequest

    from sayman.assistant.model import OPTIONS

    # The options are the same as in the Ollama model class: if a different num_ctx is sent, Ollama unloads the loaded
    # model and loads it again (even if only rendering is requested); if a measurement is running at the same time,
    # both fall into reloading on every request.
    body = ChatRequest(model=model, messages=list(_copy_messages(messages)), tools=list(_copy_tools(tools)),
                        stream=False, think=False, options=dict(OPTIONS)).model_dump(exclude_none=True)
    body["_debug_render_only"] = True
    return body


def render_prompt(messages: list[dict], tools: list[dict], model: str = "qwen3.5:9b",
              host: str = "http://127.0.0.1:11434", client=None) -> str:
    import httpx

    body = request_body(messages, tools, model)
    r = (client or httpx).post(f"{host}/api/chat", json=body, timeout=120)
    r.raise_for_status()
    text = (r.json().get("_debug_info") or {}).get("rendered_template")
    if not text:
        raise RuntimeError("Ollama çizilmiş istemi döndürmedi (_debug_render_only)")
    return text


def _value_text(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def tool_call_text(calls: list[tuple[str, dict]], content: str = "") -> str:
    pieces = []
    for i, (name, arg) in enumerate(calls):
        p = "" if i == 0 and not content.strip() else ("\n\n" if i == 0 else "\n")
        p += f"<tool_call>\n<function={name}>\n"
        for k, v in (arg or {}).items():
            p += f"<parameter={k}>\n{_value_text(v)}\n</parameter>\n"
        pieces.append(p + "</function>\n</tool_call>")
    return content.strip() + "".join(pieces)


def completion(reply: ModelResponse) -> str:
    if reply.tool_calls:
        return tool_call_text(reply.tool_calls, reply.content) + END_TOKEN
    return reply.content.strip() + END_TOKEN


class RecordingModel:
    """Wraps a model; for every call it keeps the messages the orchestrator sent (the copy at that moment) and the
    answer of the model."""

    def __init__(self, model):
        self.model = model
        self.calls: list[dict] = []

    def __getattr__(self, name):  # fields like `soru` go to the wrapped model
        return getattr(self.model, name)

    def __setattr__(self, name, value):
        if name in ("model", "calls"):
            object.__setattr__(self, name, value)
        else:
            setattr(self.model, name, value)

    def __call__(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelResponse:
        reply = self.model(messages, tools, timeout)
        self.calls.append({"mesajlar": copy.deepcopy(messages), "araclar": tools, "cevap": reply})
        return reply

    def drain(self) -> list[dict]:
        c, self.calls = self.calls, []
        return c
