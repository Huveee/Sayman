"""Phase 8 forgetting test: measures an Ollama model on the Turkish subset of Belebele.

Each question is written as one JSONL line; the script resumes where it stopped. The line holds the model fingerprint
(name, options, Ollama digest) and the digest of the subset; if either one changes the measurement stops.

Usage (from the project root; first scripts\\phase08_download_belebele.py):
    .venv\\Scripts\\python -I -u scripts\\phase08_forgetting.py --etiket temel --model qwen3.5:9b
    .venv\\Scripts\\python -I -u scripts\\phase08_forgetting.py --etiket ayarli --model sayman-ayarli
    .venv\\Scripts\\python -I -u scripts\\phase08_forgetting.py --karsilastir temel ayarli
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.evaluation import fingerprint, forgetting  # noqa: E402

SOURCE = ROOT / "data" / "ham" / "belebele" / "tur_Latn.jsonl"


def results_path(label: str) -> Path:
    return ROOT / "reports" / f"faz8_unutma_{label}.jsonl"


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines() if s.strip()] if p.exists() else []


def question_subset_digest(questions: list[dict]) -> str:
    return hashlib.sha256(json.dumps(questions, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def keep_awake() -> None:
    """Keeps the computer from going to sleep while idle for as long as the process runs; it does not change a system setting."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)


def run_measurement(label: str, model_name: str) -> None:
    import ollama

    questions = forgetting.subset(forgetting.read_questions(SOURCE))
    subset_digest = question_subset_digest(questions)
    model_fingerprint = fingerprint.model(model_name=model_name)
    model_fingerprint = {**model_fingerprint, "secenekler": forgetting.OPTIONS, "ozet": None}
    model_fingerprint["ozet"] = hashlib.sha256(json.dumps({k: v for k, v in model_fingerprint.items() if k != "ozet"}, sort_keys=True,
                                            default=str).encode()).hexdigest()
    target = results_path(label)
    existing = read_jsonl(target)
    for k in existing:
        if k["kume_ozeti"] != subset_digest or k["model_izi"]["ozet"] != model_fingerprint["ozet"]:
            raise SystemExit(f"{target.name}: alt küme ya da model izi değişmiş; yeni bir etiket kullan.")
    done_ids = {k["soru_id"] for k in existing}
    client = ollama.Client(timeout=300)
    print(f"{label}: {len(done_ids)}/{len(questions)} hazır, model {model_name}", flush=True)
    with open(target, "a", encoding="utf-8") as f:
        for i, s in enumerate(questions, 1):
            if s["soru_id"] in done_ids:
                continue
            for attempt in range(3):
                try:
                    start_time = time.perf_counter()
                    r = client.chat(model=model_name, messages=[{"role": "user", "content": forgetting.build_prompt(s)}],
                                     think=False, options=forgetting.OPTIONS, keep_alive="15m")
                    break
                except Exception as e:  # if Ollama has shut down, wait and retry
                    if attempt == 2:
                        raise
                    print(f"  model çağrısı hata verdi ({type(e).__name__}); 60 sn sonra yeniden", flush=True)
                    time.sleep(60)
            text = r.message.content or ""
            record = {"soru_id": s["soru_id"], "dogru": s["dogru"], "secilen": forgetting.answer_number(text),
                     "cevap": text, "sure_s": round(time.perf_counter() - start_time, 2), "kume_ozeti": subset_digest,
                     "model_izi": model_fingerprint}
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{i:4}/{len(questions)} {'doğru' if record['secilen'] == s['dogru'] else 'yanlış':6} "
                  f"{record['sure_s']:6.1f} sn", flush=True)
    print(json.dumps(forgetting.summarize(read_jsonl(target)), ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="Faz 8 unutma testi (Belebele Türkçe)")
    ap.add_argument("--etiket")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--karsilastir", nargs=2, metavar=("TEMEL", "AYARLI"))
    a = ap.parse_args()
    if a.karsilastir:
        t, s = (read_jsonl(results_path(e)) for e in a.karsilastir)
        result = {"temel": {"etiket": a.karsilastir[0], **forgetting.summarize(t)},
                 "ayarli": {"etiket": a.karsilastir[1], **forgetting.summarize(s)},
                 "karsilastirma": forgetting.compare(t, s)}
        (ROOT / "reports" / "faz8_unutma.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return
    if not a.etiket:
        ap.error("--etiket gerekli")
    keep_awake()
    run_measurement(a.etiket, a.model)


if __name__ == "__main__":
    main()
