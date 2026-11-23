"""Gerçek dil modeli: Ollama'daki qwen3.5:9b, Faz 0'da seçilen ayarlarla.

Ayarlar: düşünme kapalı (think=False), sıcaklık 0, sabit tohum (42), bağlam 8.192 token, num_batch 64 (Faz 0 notu:
qwen3.5'te ön bellek bu değerle sistem istemi + araç listesinin sonuna denk gelir). Cevap uzunluğu num_predict ile
sınırlıdır; sınıra takılan cevap "kesildi" olarak işaretlenir ve orkestratör onu doğrulanamamış sayar.
"""

from __future__ import annotations

from sayman.assistant.orchestrator import ModelCevabi, ModelZamanAsimi

MODEL = "qwen3.5:9b"
SECENEKLER = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_batch": 64, "num_predict": 512}


class OllamaModeli:
    def __init__(self, model: str = MODEL, secenekler: dict | None = None, host: str | None = None,
                 keep_alive: str = "15m"):
        self.model, self.host, self.keep_alive = model, host, keep_alive
        self.secenekler = {**SECENEKLER, **(secenekler or {})}

    def __call__(self, mesajlar: list[dict], araclar: list[dict], zaman_asimi: float) -> ModelCevabi:
        import httpx
        import ollama

        istemci = ollama.Client(host=self.host, timeout=max(zaman_asimi, 0.1))
        try:
            r = istemci.chat(model=self.model, messages=mesajlar, tools=araclar, think=False,
                             options=self.secenekler, keep_alive=self.keep_alive)
        except httpx.TimeoutException as e:
            raise ModelZamanAsimi(str(e)) from e
        m = r.message
        cagrilar = [(tc.function.name, dict(tc.function.arguments or {})) for tc in (m.tool_calls or [])]
        return ModelCevabi(icerik=m.content or "", arac_cagrilari=cagrilar, kesildi=r.done_reason == "length",
                           istem_token=r.prompt_eval_count, uretim_token=r.eval_count,
                           sure_s=round((r.total_duration or 0) / 1e9, 3))
