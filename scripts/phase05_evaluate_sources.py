"""Phase 5: memory and duration measurement while the chat model, the semantic model and the reranker are all in memory.

In Phase 6 a question will be processed in this order: the question is converted to a vector (bge-m3, Ollama), word +
semantic search is done, the top 20 candidates pass through the reranker (bge-reranker-v2-m3, in this process with
torch), then the chat model (qwen3.5:9b, Ollama) writes the answer. This script loads all three together and looks at:

- Can Ollama hold two models together, or is one evicted from memory when the other loads?
- How much does the reranker grow this process's memory, and how much free memory is left on the computer?
- How many seconds does the search part of one question (vector + search + ranking 20 candidates) take?

The questions are taken from the tuning set; ranking scores are not written to the cache (every measurement really
runs the model).

Usage (from the project root, with Ollama running):
    .venv\\Scripts\\python -I scripts\\phase05_evaluate_sources.py
"""

from __future__ import annotations

import ctypes
import json
import statistics
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ollama  # noqa: E402

from sayman.documents import library  # noqa: E402
from sayman.documents.semantic import SemanticIndex, EmbeddingCache, cache_key, embed  # noqa: E402
from sayman.documents.semantic import MODEL as GOMME_MODELI  # noqa: E402
from sayman.documents.search import Searcher, top_k  # noqa: E402
from sayman.documents.reranker import Reranker  # noqa: E402

DOCUMENT_DIR = ROOT / "data" / "belge"
MODEL = ROOT / "data" / "modeller" / "bge-reranker-v2-m3"
REPORT = ROOT / "reports" / "faz5_kaynak.json"
CHAT_MODEL = "qwen3.5:9b"
GB = 1024 ** 3


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64), ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]


class _ProcessMemory(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def free_memory_gb() -> float:
    b = _MemoryStatus()
    b.dwLength = ctypes.sizeof(b)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(b))
    return round(b.ullAvailPhys / GB, 2)


def process_memory_gb() -> float:
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessMemory), wintypes.DWORD]
    k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    s = _ProcessMemory()
    s.cb = ctypes.sizeof(s)
    if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(s), s.cb):
        raise OSError(ctypes.get_last_error())
    return round(s.WorkingSetSize / GB, 2)


def total_memory_gb() -> float:
    b = _MemoryStatus()
    b.dwLength = ctypes.sizeof(b)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(b))
    return round(b.ullTotalPhys / GB, 1)


def unload() -> None:
    """Unloads the models loaded in Ollama from memory (the models stay on disk)."""
    for m in ollama.ps()["models"]:
        try:
            ollama.generate(model=m["model"], prompt="", keep_alive=0)
        except ollama.ResponseError:
            ollama.embed(model=m["model"], input="x", keep_alive=0)
    time.sleep(3)


def loaded() -> dict:
    return {m["model"]: round(m["size"] / GB, 2) for m in ollama.ps()["models"]}


def chat() -> float:
    start = time.perf_counter()
    ollama.generate(model=CHAT_MODEL, prompt="Tek kelimeyle cevap ver: Türkiye'nin başkenti?", think=False,
                    keep_alive="15m", options={"num_predict": 8})
    return round(time.perf_counter() - start, 2)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    unload()
    r = {"sohbet_modeli": CHAT_MODEL, "gomme_modeli": GOMME_MODELI, "siralayici": MODEL.name,
         "toplam_bellek_gb": total_memory_gb(), "bos_bellek_gb": {"bas": free_memory_gb()},
         "not": "Boş bellek, ölçüm sırasında açık olan öteki programlara da bağlıdır."}
    r["sohbet_ilk_cagri_s"] = chat()
    r["sohbet_ikinci_cagri_s"] = chat()
    start = time.perf_counter()
    embed(["deneme"])
    r["gomme_ilk_cagri_s"] = round(time.perf_counter() - start, 2)
    r["ollama_yuklu_gb"] = loaded()
    r["bos_bellek_gb"]["ollama_iki_model"] = free_memory_gb()

    chunks = library.to_chunks(library.read_documents(DOCUMENT_DIR / "belgeler.jsonl"))
    questions = json.loads((DOCUMENT_DIR / "sorular.json").read_text(encoding="utf-8"))["ayar"][::20][:6]
    r["surec_bellek_gb"] = {"siralayicidan_once": process_memory_gb()}
    with tempfile.TemporaryDirectory() as temp_dir:
        reranker = Reranker(MODEL, Path(temp_dir) / "s.json")
        start = time.perf_counter()
        reranker._load()
        r["siralayici_yukleme_s"] = round(time.perf_counter() - start, 2)
        r["surec_bellek_gb"]["siralayici_yuklu"] = process_memory_gb()
        cache = EmbeddingCache(DOCUMENT_DIR / "onbellek" / "gomme_bge-m3.npz")
        index = SemanticIndex(chunks, cache)
        searcher = Searcher(chunks, index, reranker)
        durations = []
        for s in questions:
            start = time.perf_counter()
            cache.v[cache_key(s["soru"])] = embed([s["soru"]])[0]  # the question vector comes from the model, not the cache
            result = top_k(searcher.scores(s["soru"], "cfo"), 5)
            durations.append(time.perf_counter() - start)
            assert result
        index.close()
        r["surec_bellek_gb"]["arama_sonrasi"] = process_memory_gb()
    r["arama_soru_basina_s"] = {"medyan": round(statistics.median(durations), 2), "en_cok": round(max(durations), 2),
                                "soru": len(durations), "aday": searcher.candidate_count}
    r["ollama_yuklu_gb_arama_sonrasi"] = loaded()
    r["sohbet_arama_sonrasi_s"] = chat()
    r["ollama_yuklu_gb_son"] = loaded()
    r["bos_bellek_gb"]["hepsi_yuklu"] = free_memory_gb()
    r["sohbet_modeli_atildi_mi"] = CHAT_MODEL not in r["ollama_yuklu_gb_arama_sonrasi"]
    REPORT.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
