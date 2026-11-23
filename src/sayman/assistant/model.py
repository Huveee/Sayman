"""The real language model: qwen3.5:9b on Ollama, with the settings chosen in Phase 0.

Settings: thinking off (think=False), temperature 0, fixed seed (42), context 8,192 tokens, num_batch 64 (Phase 0
note: on qwen3.5 the cache boundary falls at the end of the system prompt + tool list with this value). The answer
length is limited by num_predict; an answer that hits the limit is marked `truncated` and the orchestrator counts
it as unverified.
"""

from __future__ import annotations

from sayman.assistant.orchestrator import ModelResponse, ModelTimeout

MODEL = "qwen3.5:9b"
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_batch": 64, "num_predict": 512}


class OllamaModel:
    def __init__(self, model: str = MODEL, options: dict | None = None, host: str | None = None,
                 keep_alive: str = "15m"):
        self.model, self.host, self.keep_alive = model, host, keep_alive
        self.options = {**OPTIONS, **(options or {})}

    def chat_arguments(self, messages: list[dict], tools: list[dict]) -> dict:
        """The arguments of ollama.Client.chat for one call (the demo's warm-up sends the same request)."""
        return {"model": self.model, "messages": messages, "tools": tools, "think": False, "options": self.options,
                "keep_alive": self.keep_alive}

    def __call__(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelResponse:
        import httpx
        import ollama

        client = ollama.Client(host=self.host, timeout=max(timeout, 0.1))
        try:
            r = client.chat(**self.chat_arguments(messages, tools))
        except httpx.TimeoutException as e:
            raise ModelTimeout(str(e)) from e
        return self.response(r)

    @staticmethod
    def response(r) -> ModelResponse:
        """ModelResponse from an ollama.ChatResponse."""
        m = r.message
        calls = [(tc.function.name, dict(tc.function.arguments or {})) for tc in (m.tool_calls or [])]
        return ModelResponse(content=m.content or "", tool_calls=calls, truncated=r.done_reason == "length",
                           prompt_tokens=r.prompt_eval_count, generated_tokens=r.eval_count,
                           sure_s=round((r.total_duration or 0) / 1e9, 3))
