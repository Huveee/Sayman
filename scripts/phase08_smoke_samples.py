"""Produces a small number of real-format samples for the Kaggle trial training (smoke test).

The samples come from the development set, run through the real orchestrator with the oracle model: every model call is one
sample (prompt = the text Ollama rendered for that call, completion = the oracle's answer). These samples only test the setup
(memory, speed, loss, export); they are not used in the real training and the model obtained from this training is not measured.
The development set stays reserved for evaluation.

Output: data/egitim/duman/ornekler.jsonl
Usage (from the project root, with Ollama running):
    .venv\\Scripts\\python -I scripts\\phase08_smoke_samples.py
"""

from __future__ import annotations

import json
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.tools.audit_log import AuditLog  # noqa: E402
from sayman.tools.identity import generate_card  # noqa: E402
from sayman.tools.server import SaymanServer  # noqa: E402
from sayman.tools.permissions import read_permissions  # noqa: E402
from sayman.assistant.orchestrator import Assistant, Settings  # noqa: E402
from sayman.documents import library  # noqa: E402
from sayman.training.prompt import RecordingModel, render_prompt, completion  # noqa: E402
from sayman.evaluation.oracle import Oracle, OracleSearcher  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

OUTPUT = ROOT / "data" / "egitim" / "duman" / "ornekler.jsonl"
QUESTION_IDS = ["G-nakit_toplam-01", "G-yas_toplam-01", "G-gec_ikinci-01", "G-dso_donem-01", "G-kur_bugun-01",
           "G-oz_acik_fatura-01", "G-fat_bilgi-01", "G-akis_ilk_hafta-01", "G-belge-01", "G-belge-02", "G-belge-03",
           "G-yetkisiz-baska-01", "G-yetkisiz-yasak-01"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads((ROOT / "reports" / "faz7_soru_seti.json").read_text(encoding="utf-8"))
    development = [s for s in data["sorular"] if s["set"] == "gelistirme"]
    selected = [s for s in development if s["soru_id"] in QUESTION_IDS]
    if len(selected) != len(QUESTION_IDS):
        raise SystemExit("geliştirme setinde olmayan soru kimliği")
    chunks = {p.parca_id: p for p in library.to_chunks(library.read_documents(ROOT / "data" / "belge" / "belgeler.jsonl"))}
    model = RecordingModel(Oracle(None, chunks))
    searcher = OracleSearcher(chunks)
    clock = read_clock(ROOT / "data" / "saat.json")
    signing_key = secrets.token_bytes(32)
    samples = []
    with tempfile.TemporaryDirectory() as temp_dir:
        server = SaymanServer(warehouse=ROOT / "data" / "ambar" / "sayman.duckdb", clock=lambda: clock, key=signing_key,
                                permissions=read_permissions(), audit_log=AuditLog(Path(temp_dir) / "denetim.jsonl"),
                                scope_dir=Path(temp_dir) / "kapsam", belge=searcher, search_method="anlam")
        try:
            assistant = Assistant(server, model, Settings())
            for s in selected:
                model.soru = searcher.soru = s
                turn = assistant.ask(s["soru"], generate_card(s["kullanici"], signing_key, 900))
                for step, c in enumerate(model.drain(), 1):
                    samples.append({"soru_id": s["soru_id"], "adim": step, "durum": turn.status,
                                     "istem": render_prompt(c["mesajlar"], c["araclar"]),
                                     "tamamlama": completion(c["cevap"])})
                print(s["soru_id"], turn.status, turn.model_calls, flush=True)
        finally:
            server.close()
    # Check: give the first prompt to Ollama raw and record what the base model writes on its own and how many
    # tokens the prompt takes in Ollama. The HF token count on Kaggle is compared with this (a cache hit can lower the count).
    import httpx

    from sayman.assistant.model import MODEL, OPTIONS

    r = httpx.post("http://127.0.0.1:11434/api/generate", timeout=600, json={
        "model": MODEL, "prompt": samples[0]["istem"], "raw": True, "stream": False,
        "options": {**OPTIONS, "num_predict": 200}})
    r.raise_for_status()
    raw = r.json()
    samples[0]["ollama_istem_token"] = raw.get("prompt_eval_count")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    (OUTPUT.parent / "ham_uretim.json").write_text(json.dumps(
        {"soru_id": samples[0]["soru_id"], "prompt_eval_count": raw.get("prompt_eval_count"),
         "cevap": raw.get("response"), "beklenen": samples[0]["tamamlama"]}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    with open(OUTPUT, "w", encoding="utf-8", newline="\n") as f:
        for o in samples:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(len(samples), "örnek;", "en uzun istem", max(len(o["istem"]) for o in samples), "karakter")
    print("ham üretim:", repr(raw.get("response", "")[:300]), "| istem tokeni:", raw.get("prompt_eval_count"))


if __name__ == "__main__":
    main()
