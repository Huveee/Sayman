"""Phase 9 pre-measurement: how much room do the two tool lists and the pages of the generated tools take in the model's context (8192 tokens)?

The measurement uses Ollama's prompt_eval_count (the same method as scripts/phase06_smoke.py): the same prompt is asked with
and without tools, the difference is the token count of the tool list. So that the cache does not get mixed up, a random tag
is put at the start of every prompt. For the pages, the 10- and 25-record pages of the CFO's invoice, collection and cheque
lists are given as a tool message. The characters/token ratio is also written for the estimate (the orchestrator's estimate
uses 1.7).

Model: the base qwen3.5:9b (in Phase 9 both arms are measured with it). Run it when there is no other job in Ollama; first
run scripts/phase08_ollama_model.py bosalt.

Output: reports/faz9_boyut.json

    .venv\\Scripts\\python -I -u scripts\\phase09_context_size.py
"""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.tools.audit_log import AuditLog  # noqa: E402
from sayman.tools.identity import generate_card  # noqa: E402
from sayman.tools.permissions import read_permissions  # noqa: E402
from sayman.assistant.model import MODEL, OPTIONS  # noqa: E402
from sayman.assistant.orchestrator import SYSTEM_PROMPT, Settings, tool_definitions  # noqa: E402
from sayman.experiment.generated_tools import GeneratedServer  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"
LIST_TOOLS = ("faturalar_api_v1_faturalar_get", "tahsilatlar_api_v1_tahsilatlar_get", "cekler_api_v1_cekler_get")


def main() -> None:
    import ollama
    sys.stdout.reconfigure(encoding="utf-8")
    client = ollama.Client(timeout=900)

    def token(messages, tools=None) -> int:
        r = client.chat(model=MODEL, messages=messages, tools=tools, think=False, keep_alive="15m",
                         options={**OPTIONS, "num_predict": 1})
        return r.prompt_eval_count

    def short_message():
        return {"role": "system", "content": f"[{secrets.token_hex(4)}] Sen bir finans asistanısın."}

    def full_message():
        return {"role": "system", "content": f"[{secrets.token_hex(4)}] {SYSTEM_PROMPT}"}

    clock = read_clock(ROOT / "data" / "saat.json")
    signing_key = secrets.token_bytes(32)
    with tempfile.TemporaryDirectory() as g:
        s = GeneratedServer(WAREHOUSE, lambda: clock, signing_key, read_permissions(), AuditLog(Path(g) / "d.jsonl"), Path(g) / "k")
        try:
            card = generate_card("selin.aydin", signing_key, 900)
            pages = {}
            for name in LIST_TOOLS:
                for size in (10, 25):
                    response, error = s.gate(name, {"sayfa_boyutu": size}, card)
                    assert not error, response
                    pages[f"{name}:{size}"] = json.dumps(response, ensure_ascii=False)
            manual_tools, auto_tools = tool_definitions(), s.tool_definitions()
        finally:
            s.close()

    start_time = time.perf_counter()
    token([short_message(), {"role": "user", "content": "Merhaba"}])  # so that the model gets loaded
    load_seconds = round(time.perf_counter() - start_time, 1)
    empty_tokens = token([short_message(), {"role": "user", "content": "Merhaba"}])
    manual_tokens = token([short_message(), {"role": "user", "content": "Merhaba"}], manual_tools) - empty_tokens
    auto_tokens = token([short_message(), {"role": "user", "content": "Merhaba"}], auto_tools) - empty_tokens
    system_tokens = token([full_message(), {"role": "user", "content": "Merhaba"}]) - empty_tokens
    baseline = token([short_message(), {"role": "user", "content": "x"}])
    page_stats = {}
    for page_key, text in pages.items():
        t = token([short_message(), {"role": "user", "content": text}]) - baseline
        page_stats[page_key] = {"karakter": len(text), "token": t, "karakter_token": round(len(text) / t, 2)}
        print(page_key, page_stats[page_key], flush=True)

    settings = Settings()
    budget = settings.baglam_token - settings.cevap_payi
    result = {
        "model": MODEL, "num_ctx": settings.baglam_token, "cevap_payi": settings.cevap_payi, "butce": budget,
        "model_yukleme_s": load_seconds,
        "sistem_istemi_token": system_tokens,
        "el": {"arac_sayisi": len(manual_tools), "karakter": len(json.dumps(manual_tools, ensure_ascii=False)), "token": manual_tokens,
               "kalan": budget - system_tokens - manual_tokens},
        "otomatik": {"arac_sayisi": len(auto_tools), "karakter": len(json.dumps(auto_tools, ensure_ascii=False)), "token": auto_tokens,
                     "kalan": budget - system_tokens - auto_tokens},
        "sayfalar": page_stats,
        "not": "kalan: soru, model mesajları ve araç sonuçları için kalan token (bütçe - sistem istemi - araç listesi)",
    }
    (ROOT / "reports" / "faz9_boyut.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                                    encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "sayfalar"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
