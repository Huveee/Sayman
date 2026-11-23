"""Faz 0 hız ölçümü: aday modelleri yerel Ollama üzerinde ölçer, reports/faz0_hiz.json yazar.

Phase 0 speed benchmark: measures the candidate models on a local Ollama and writes reports/faz0_hiz.json.
(The first line above feeds the argparse description through `__doc__.splitlines()[0]`, so it stays as is.)

Usage (from the project root):
    .venv\\Scripts\\python scripts\\phase00_speed_benchmark.py
    .venv\\Scripts\\python scripts\\phase00_speed_benchmark.py --modeller qwen3.5:4b qwen3.5:9b

Sequence for each candidate:
    1. With the model unloaded from memory, a short call without tool definitions: it loads the model
       and gives the prompt token count without tools.
    2. Cold call with tool definitions: prompt processing speed and total prompt token count.
       Approximate token count of the tool block = 2 - 1.
    3. Warm-up call with tool definitions: the cache fills with the system prompt + tools.
    4. 5 sample questions, each as a separate chat: turn duration, validity, generation speed.
The model is unloaded from memory at the end. The script does not download models; it stops if one is missing.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import importlib.metadata
import json
import platform
import re
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ollama  # noqa: E402

from sayman.model_selection.tool_drafts import TOOLS  # noqa: E402
from sayman.model_selection.call_validation import (  # noqa: E402
    REQUIRED_VALID,
    DURATION_THRESHOLD_S,
    is_expected_call,
    is_response_valid,
    meets_rule,
    selection_rule,
)
from sayman.model_selection.sample_questions import WARMUP_QUESTION, SAMPLE_QUESTIONS  # noqa: E402

DEFAULT_MODELS = ["qwen3.5:4b", "qwen3.5:9b"]
NUM_CTX = 8192
SEED = 42
TODAY = "2026-11-12"
SYSTEM_PROMPT = (
    "Sen Sayman'sın: bir şirketin yapay zekâ destekli finans asistanısın. "
    "Sorulara cevap vermek için gereken veriyi her zaman verilen araçlardan al; rakam uydurma. "
    "Tarihleri YYYY-MM-DD biçiminde yaz. Bugünün tarihi " + TODAY + "."
)
# num_batch: on the qwen3.5 hybrid (attention + recurrent) architecture the cache is restored only from
# checkpoints, and the checkpoint is taken num_batch tokens before the end of the prompt. With the default large
# value this point lies far behind the shared prefix (system prompt + tools) and about 1000 tokens are
# reprocessed on every turn. With 64 the point coincides with the end of the shared prefix.
NUM_BATCH = 64
OPTIONS = {"temperature": 0, "seed": SEED, "num_ctx": NUM_CTX, "num_batch": NUM_BATCH}
# Warm-up calls do not measure the answer itself; it is kept short so the model does not write a long chat reply.
WARMUP_GENERATION_LIMIT = 32
# A tool call is under 100 tokens; a turn that hits this limit has not produced a tool call anyway.
TURN_GENERATION_LIMIT = 512
MIN_FREE_MEMORY_GB = 0.5
GIB = 2**30


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _PowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def memory_gb() -> tuple[float, float]:
    """(total, free) physical memory, GiB."""
    d = _MemoryStatus()
    d.dwLength = ctypes.sizeof(d)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(d))
    return round(d.ullTotalPhys / GIB, 2), round(d.ullAvailPhys / GIB, 2)


def is_plugged_in() -> bool | None:
    d = _PowerStatus()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(d)):
        return None
    return {0: False, 1: True}.get(d.ACLineStatus)


def processor_name() -> str:
    try:
        import winreg

        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except OSError:
        return platform.processor()


def _seconds(ns: int | None) -> float:
    return round((ns or 0) / 1e9, 3)


def _tool_calls(message) -> list[dict]:
    return [
        {"ad": tc.function.name, "argumanlar": dict(tc.function.arguments or {})}
        for tc in (message.tool_calls or [])
    ]


def _messages(question: str, system: str = SYSTEM_PROMPT) -> list[dict]:
    return [{"role": "system", "content": system}, {"role": "user", "content": question}]


def _chat(client, model: str, messages: list[dict], tools: list[dict] | None, **extra_options):
    options = {**OPTIONS, **extra_options}
    start = time.perf_counter()
    response = client.chat(
        model=model, messages=messages, tools=tools, think=False, options=options, keep_alive="15m"
    )
    return response, round(time.perf_counter() - start, 3)


def _unload(client, model: str) -> None:
    client.generate(model=model, prompt="", keep_alive=0)


def _parameter_billions(model: str, detail: str | None) -> float:
    match = re.search(r":(\d+(?:\.\d+)?)b\b", model)
    if match:
        return float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s*B", detail or "")
    return float(match.group(1)) if match else 0.0


def measure_candidate(client, model: str, loaded: dict) -> dict:
    info = loaded[model]
    for running in client.ps().models:
        _unload(client, running.model)
    _, free_before = memory_gb()
    print(f"\n== {model}  (boş bellek {free_before} GB)", flush=True)

    # 1. Short call that loads the model, without tool definitions. The system prompt starts with "." so that
    #    it shares no prefix with the next (tool-enabled) call and that call is measured truly cold.
    without_tools, _ = _chat(client, model, _messages(WARMUP_QUESTION, "." + SYSTEM_PROMPT), None, num_predict=1)

    # 2. Cold call, with tool definitions: prompt processing speed and total prompt token count.
    #    Ollama's prompt token count includes the part served from the cache; that is why the speed is
    #    measured only here, while the prefix is not in the cache.
    cold, cold_s = _chat(
        client, model, _messages(WARMUP_QUESTION), TOOLS, num_predict=WARMUP_GENERATION_LIMIT
    )
    _, free_loaded = memory_gb()
    running = next((m for m in client.ps().models if m.model == model), None)
    cold_prompt_s = _seconds(cold.prompt_eval_duration)
    prompt_tps = round(cold.prompt_eval_count / cold_prompt_s, 2) if cold_prompt_s else None
    load_s = round(_seconds(without_tools.load_duration) + _seconds(cold.load_duration), 3)
    print(
        f"   soğuk: {cold_s} s (yükleme {load_s} s, "
        f"{cold.prompt_eval_count} istem tokenı, {prompt_tps} token/s)",
        flush=True,
    )

    # 3. Warm-up: the system prompt + tools enter the cache
    warmup, warmup_s = _chat(
        client, model, _messages(WARMUP_QUESTION), TOOLS, num_predict=WARMUP_GENERATION_LIMIT
    )

    # 4. The 5 measured turns
    turns = []
    lowest_free = free_loaded
    for s in SAMPLE_QUESTIONS:
        response, duration = _chat(
            client, model, _messages(s["soru"]), TOOLS, num_predict=TURN_GENERATION_LIMIT
        )
        _, free = memory_gb()
        lowest_free = min(lowest_free, free)
        calls = _tool_calls(response.message)
        valid, reason = is_response_valid(calls)
        turn = {
            "kimlik": s["kimlik"],
            "soru": s["soru"],
            "sure_s": duration,
            "istem_token": response.prompt_eval_count,
            "istem_s": _seconds(response.prompt_eval_duration),
            "uretim_token": response.eval_count,
            "uretim_s": _seconds(response.eval_duration),
            "cagrilar": calls,
            "gecerli": valid,
            "neden": reason,
            "dogru_cagri": is_expected_call(calls, s["beklenen_arac"], s["beklenen_argumanlar"]),
            "dusunme_bos": not (response.message.thinking or "").strip(),
            "sinira_takildi": response.done_reason == "length",
            "metin": (response.message.content or "")[:300],
        }
        turns.append(turn)
        print(
            f"   {s['kimlik']}: {duration} s, geçerli={valid} ({reason}), doğru={turn['dogru_cagri']}, "
            f"çağrı={[c['ad'] for c in calls]}",
            flush=True,
        )

    _unload(client, model)

    total_generated = sum(t["uretim_token"] or 0 for t in turns)
    total_generation_s = sum(t["uretim_s"] for t in turns)
    result = {
        "model": model,
        "parametre_milyar": _parameter_billions(model, info["parametre_boyutu"]),
        "ozet": info["ozet"],
        "boyut_gb": info["boyut_gb"],
        "nicemleme": info["nicemleme"],
        "bos_bellek_once_gb": free_before,
        "bos_bellek_yuklu_gb": free_loaded,
        "en_dusuk_bos_bellek_gb": lowest_free,
        "bellekteki_boyut_gb": round(running.size / GIB, 2) if running else None,
        "gpu_bellegi_gb": round(running.size_vram / GIB, 2) if running else None,
        "yukleme_s": load_s,
        "soguk_tur_s": cold_s,
        "soguk_istem_token": cold.prompt_eval_count,
        "soguk_istem_s": cold_prompt_s,
        "istem_isleme_tps": prompt_tps,
        "aracsiz_istem_token": without_tools.prompt_eval_count,
        "arac_blogu_token_yaklasik": (cold.prompt_eval_count or 0) - (without_tools.prompt_eval_count or 0),
        "isinma_tur_s": warmup_s,
        "isinma_istem_token": warmup.prompt_eval_count,
        "turlar": turns,
        "uretim_tps": round(total_generated / total_generation_s, 2) if total_generation_s else None,
        "en_uzun_tur_s": max(t["sure_s"] for t in turns),
        "ortanca_tur_s": round(statistics.median(t["sure_s"] for t in turns), 3),
        "gecerli_sayisi": sum(1 for t in turns if t["gecerli"]),
        "dogru_cagri_sayisi": sum(1 for t in turns if t["dogru_cagri"]),
        "baglam_yetti": (cold.prompt_eval_count or 0) < NUM_CTX,
        "dusunme_kapali": all(t["dusunme_bos"] for t in turns),
        # The prompt processing time of the warmed-up turns should be under a quarter of the cold call's.
        "isinma_dogrulandi": statistics.median(t["istem_s"] for t in turns) < cold_prompt_s / 4,
    }
    result["kural_saglandi"] = meets_rule(result)
    print(
        f"   özet: en uzun tur {result['en_uzun_tur_s']} s, üretim {result['uretim_tps']} token/s, "
        f"geçerli {result['gecerli_sayisi']}/5, doğru {result['dogru_cagri_sayisi']}/5, "
        f"kural {'sağlandı' if result['kural_saglandi'] else 'sağlanmadı'}",
        flush=True,
    )
    return result


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--modeller", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--cikti", type=Path, default=ROOT / "reports" / "faz0_hiz.json")
    args = parser.parse_args()

    client = ollama.Client(timeout=900)
    try:
        ollama_version = client._client.get("/api/version").json().get("version")
    except Exception:  # version info is not required for the measurement
        ollama_version = None
    loaded = {}
    for m in client.list().models:
        loaded[m.model] = {
            "ozet": m.digest,
            "boyut_gb": round(m.size / 1e9, 2),
            "nicemleme": m.details.quantization_level if m.details else None,
            "parametre_boyutu": m.details.parameter_size if m.details else None,
        }
    missing = [m for m in args.modeller if m not in loaded]
    if missing:
        print("Eksik model(ler), önce indirilmeli: " + ", ".join(missing), file=sys.stderr)
        return 2

    total_memory, _ = memory_gb()
    plugged_in = is_plugged_in()
    candidates = [measure_candidate(client, m, loaded) for m in args.modeller]
    selected = selection_rule(candidates)

    warnings = []
    if plugged_in is False:
        warnings.append("Ölçüm pil ile yapıldı; işlemci yavaşlatılmış olabilir.")
    for a in candidates:
        if a["en_dusuk_bos_bellek_gb"] < MIN_FREE_MEMORY_GB:
            warnings.append(f"{a['model']}: boş bellek {a['en_dusuk_bos_bellek_gb']} GB'a düştü; ölçüm geçersiz olabilir.")
        if not a["isinma_dogrulandi"]:
            warnings.append(f"{a['model']}: ön bellek ısınması doğrulanamadı.")
        if not a["dusunme_kapali"]:
            warnings.append(f"{a['model']}: düşünme modu kapanmadı.")
        if not a["baglam_yetti"]:
            warnings.append(f"{a['model']}: istem bağlam penceresini aştı.")

    report = {
        "olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "kural": {
            "aciklama": (
                "Ön bellek ısındıktan sonra 5 araç çağırma turunun her birini en fazla "
                f"{DURATION_THRESHOLD_S:g} saniyede bitiren ve 5 sorunun {REQUIRED_VALID}'inde geçerli araç "
                "çağrısı üreten en büyük model seçilir."
            ),
            "sure_esigi_s": DURATION_THRESHOLD_S,
            "gereken_gecerli": REQUIRED_VALID,
        },
        "ayarlar": {
            "num_ctx": NUM_CTX,
            "num_batch": NUM_BATCH,
            "temperature": 0,
            "seed": SEED,
            "think": False,
            "arac_sayisi": len(TOOLS),
            "isinma_uretim_siniri": WARMUP_GENERATION_LIMIT,
            "tur_uretim_siniri": TURN_GENERATION_LIMIT,
            "bugun": TODAY,
            "sistem_istemi": SYSTEM_PROMPT,
        },
        "makine": {
            "islemci": processor_name(),
            "toplam_bellek_gb": total_memory,
            "fis_takili": plugged_in,
            "isletim_sistemi": platform.platform(),
            "ollama_surumu": ollama_version,
            "python": platform.python_version(),
            "ollama_python": importlib.metadata.version("ollama"),
        },
        "adaylar": candidates,
        "secilen_model": selected,
        "uyarilar": warnings,
    }
    args.cikti.parent.mkdir(parents=True, exist_ok=True)
    args.cikti.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nSeçilen model: {selected or 'yok (hiçbir aday kuralı sağlamadı)'}")
    for u in warnings:
        print("UYARI: " + u)
    print(f"Rapor: {args.cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
