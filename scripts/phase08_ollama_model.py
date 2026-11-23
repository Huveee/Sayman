"""Phase 8: installs the fine-tuned GGUF in Ollama in the same form as the base model and checks that the model really changed.

Ollama 0.40.11 does not support a LoRA adapter (ADAPTER); the fine-tuned model is a single GGUF file with its LoRA merged in
(kaggle/phase08_merge.py). Steps (from the project root; while no other job runs in Ollama):

    .venv\\Scripts\\python -I scripts\\phase08_ollama_model.py kur --ad sayman-ayarli --gguf <file.gguf>
    .venv\\Scripts\\python -I scripts\\phase08_ollama_model.py dene --ad sayman-ayarli ^
        --ornekler data\\egitim\\tam\\dogrulama.jsonl --kaggle-rapor data\\egitim\\kaggle_cikti\\sayman-faz8-tam\\rapor.json ^
        --temel sayman-taban-kendi --yalniz-kaggle
    .venv\\Scripts\\python -I scripts\\phase08_ollama_model.py bosalt

kur: the Modelfile of the base model (qwen3.5:9b) is taken and only the first FROM line (the language model) is replaced
with the given file. The vision encoder, TEMPLATE, RENDERER, PARSER and PARAMETER lines stay as they are; the script then checks them.

dene: three checks, report reports/faz8_model_denemesi_<name>.json
  1. Rendering: the same messages and tools must render to the same prompt text in both models.
  2. Generation: the prompts of the samples are given raw to both models. The output of the fine-tuned model should
     resemble the Kaggle generation trial and differ from the base model's. Each model is queried from an empty start (bosalt).
  3. Options: generation uses the shared model options (all except num_predict), so the model is not reloaded.

bosalt: closes all the models loaded in Ollama; every measurement starts from the same empty state.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

OLLAMA = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
HOST = "http://127.0.0.1:11434"
BASE_MODEL = "qwen3.5:9b"


def ollama(*arg: str) -> str:
    r = subprocess.run([str(OLLAMA), *arg], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"ollama {' '.join(arg)} başarısız:\n{r.stdout}{r.stderr}")
    return r.stdout


DIRECTIVES = ("FROM", "TEMPLATE", "RENDERER", "PARSER", "PARAMETER", "SYSTEM", "ADAPTER", "MESSAGE")


def modelfile_lines(model: str = BASE_MODEL) -> list[str]:
    """The single-line directives of the Modelfile (multi-line blocks such as LICENSE are left out)."""
    return [s for s in ollama("show", model, "--modelfile").splitlines() if s.split(" ", 1)[0] in DIRECTIVES]


def build_model(name: str, gguf: Path) -> None:
    gguf = gguf.resolve()
    if not gguf.exists():
        raise SystemExit(f"yok: {gguf}")
    base = modelfile_lines()
    froms = [i for i, s in enumerate(base) if s.startswith("FROM ")]
    if len(froms) != 2:
        raise SystemExit(f"temel modelde iki FROM (dil modeli + görsel kodlayıcı) bekleniyordu: {froms}")
    if any(s.startswith(("ADAPTER", "MESSAGE")) for s in base):
        raise SystemExit("temel modelde beklenmeyen ADAPTER/MESSAGE yönergesi")
    lines = list(base)
    lines[froms[0]] = f"FROM {gguf}"
    directory = ROOT / "data" / "egitim" / "calisma" / name
    directory.mkdir(parents=True, exist_ok=True)
    mf = directory / "Modelfile"
    mf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(mf.read_text(encoding="utf-8"))
    print(ollama("create", name, "-f", str(mf))[-300:])
    expected = sorted(s for s in base if not s.startswith("FROM "))  # Ollama can reorder the PARAMETER lines
    actual = sorted(s for s in modelfile_lines(name) if not s.startswith("FROM "))
    if expected != actual:
        raise SystemExit("kurulan modelin Modelfile'ı temel modelinkinden farklı (FROM dışında)")
    new_froms = [s for s in modelfile_lines(name) if s.startswith("FROM ")]
    if new_froms[1] != base[froms[1]]:
        raise SystemExit("görsel kodlayıcı temel modelinkiyle aynı değil")
    print("Modelfile FROM dışında temel modelle aynı; görsel kodlayıcı aynı.")


def unload_all() -> None:
    import httpx

    loaded = httpx.get(f"{HOST}/api/ps", timeout=30).json().get("models", [])
    for m in loaded:
        httpx.post(f"{HOST}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=300)
    remaining = httpx.get(f"{HOST}/api/ps", timeout=30).json().get("models", [])
    print("boşaltıldı:", [m["name"] for m in loaded], "kalan:", [m["name"] for m in remaining])
    if remaining:
        raise SystemExit("bazı modeller hâlâ yüklü")


def check_rendering(name: str, base: str = BASE_MODEL) -> dict:
    from sayman.assistant.orchestrator import tool_definitions
    from sayman.training.prompt import render_prompt

    tools = tool_definitions()
    traces = [json.loads(s) for s in (ROOT / "data" / "egitim" / "izler.jsonl").read_text(encoding="utf-8").splitlines()
             if s.strip()][:3]
    result = []
    for trace in traces:
        for c in trace["cagrilar"]:
            t, a = render_prompt(c["mesajlar"], tools, base), render_prompt(c["mesajlar"], tools, name)
            result.append({"soru_id": trace["soru_id"], "ayni": t == a, "karakter": len(t)})
    return {"istem": len(result), "hepsi_ayni": all(s["ayni"] for s in result), "ayrinti": result}


def generate(model: str, prompt: str, client) -> str:
    from sayman.assistant.model import OPTIONS

    r = client.post(f"{HOST}/api/generate", json={
        "model": model, "prompt": prompt, "raw": True, "stream": False, "think": False,
        "options": {**OPTIONS, "num_predict": 160}}, timeout=900)
    r.raise_for_status()
    return r.json()["response"]


def similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def truncate_at_end(text: str) -> str:
    """Up to the first <|im_end|> for the comparison (the Kaggle generation could keep going without stopping)."""
    return text.split("<|im_end|>")[0].strip()


def run_trial(name: str, samples_path: Path, kaggle_report: Path | None, base: str = BASE_MODEL,
         kaggle_only: bool = False) -> None:
    """kaggle_only: only the samples that were also asked in the Kaggle generation trial (110 samples take hours on the CPU)."""
    import httpx

    samples = [json.loads(s) for s in samples_path.read_text(encoding="utf-8").splitlines() if s.strip()]
    kaggle = {}
    if kaggle_report and kaggle_report.exists():
        for u in json.loads(kaggle_report.read_text(encoding="utf-8")).get("uretim_denemesi", []):
            kaggle[(u["soru_id"], u["adim"])] = u["uretilen"]
    if kaggle_only:
        samples = [o for o in samples if (o.get("soru_id"), o.get("adim")) in kaggle]
        if len(samples) != len(kaggle):
            raise SystemExit(f"Kaggle'daki {len(kaggle)} örneğin {len(samples)} tanesi örnek dosyasında bulundu")
    report = {"model": name, "temel": base, "ciz": check_rendering(name, base), "uretim": []}
    print("çizim aynı mı:", report["ciz"]["hepsi_ayni"], flush=True)
    with httpx.Client() as client:
        for model in (name, base):  # one model at a time from an empty start (earlier jobs can change the answer)
            unload_all()
            for i, o in enumerate(samples):
                output = generate(model, o["istem"], client)
                if model == name:
                    report["uretim"].append({"soru_id": o.get("soru_id"), "adim": o.get("adim"),
                                            "beklenen": truncate_at_end(o["tamamlama"]), "ayarli": output})
                else:
                    report["uretim"][i]["temel"] = output
                print(f"{model} {o.get('soru_id')} {o.get('adim')}: {truncate_at_end(output)[:90]!r}", flush=True)
    for u in report["uretim"]:
        k = kaggle.get((u["soru_id"], u["adim"]))
        u["kaggle"] = k
        u["ayarli_kaggle_benzerligi"] = similarity(truncate_at_end(u["ayarli"]), truncate_at_end(k)) if k is not None else None
        u["temel_kaggle_benzerligi"] = similarity(truncate_at_end(u["temel"]), truncate_at_end(k)) if k is not None else None
        u["ayarli_temel_ayni"] = truncate_at_end(u["ayarli"]) == truncate_at_end(u["temel"])
        u["ayarli_beklenen_ayni"] = truncate_at_end(u["ayarli"]) == u["beklenen"]
        u["temel_beklenen_ayni"] = truncate_at_end(u["temel"]) == u["beklenen"]
    u = report["uretim"]
    compared = [x for x in u if x["kaggle"] is not None]
    report["ozet"] = {
        "ornek": len(u),
        "ayarli_temelden_farkli": sum(not x["ayarli_temel_ayni"] for x in u),
        "ayarli_beklenenle_ayni": sum(x["ayarli_beklenen_ayni"] for x in u),
        "temel_beklenenle_ayni": sum(x["temel_beklenen_ayni"] for x in u),
        "kaggle_ile_karsilastirilan": len(compared),
        "ayarli_kaggle_benzerligi_ort": round(sum(x["ayarli_kaggle_benzerligi"] for x in compared)
                                              / len(compared), 3) if compared else None,
        "temel_kaggle_benzerligi_ort": round(sum(x["temel_kaggle_benzerligi"] for x in compared)
                                             / len(compared), 3) if compared else None,
    }
    output = ROOT / "reports" / f"faz8_model_denemesi_{name}.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["ozet"], ensure_ascii=False, indent=1))
    print("yazıldı:", output.relative_to(ROOT))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8: ince ayarlı modeli Ollama'ya kurma ve denetleme")
    subparsers = ap.add_subparsers(dest="komut", required=True)
    o = subparsers.add_parser("kur")
    o.add_argument("--ad", required=True)
    o.add_argument("--gguf", type=Path, required=True)
    subparsers.add_parser("bosalt")
    d = subparsers.add_parser("dene")
    d.add_argument("--ad", required=True)
    d.add_argument("--ornekler", type=Path, required=True)
    d.add_argument("--kaggle-rapor", type=Path, default=None)
    d.add_argument("--temel", default=BASE_MODEL, help="karşılaştırılan temel model (ör. sayman-taban-kendi)")
    d.add_argument("--yalniz-kaggle", action="store_true", help="yalnızca Kaggle üretim denemesindeki örnekler")
    a = ap.parse_args()
    if a.komut == "kur":
        build_model(a.ad, a.gguf)
    elif a.komut == "bosalt":
        unload_all()
    else:
        run_trial(a.ad, a.ornekler, a.kaggle_rapor, a.temel, a.yalniz_kaggle)


if __name__ == "__main__":
    main()
