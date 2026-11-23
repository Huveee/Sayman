"""Sayman Phase 8 compatibility check: does a GGUF we make ourselves work in Ollama 0.40.11 like a library model?
(Kaggle, CPU)

Finding (phase08_quantization_check.py): in the base model converted with llama.cpp b12138 the ssm_a values are the
same as those in Ollama's file but in a different order (the head order differs in the linear-attention layers).
Ollama's own llama-server is built with llama.cpp version 9ff0d7e90. This script:

1. Installs Ollama 0.40.11 and pulls qwen3.5:9b from the library (its digest must equal the one on this computer).
2. Converts the base model with version 9ff0d7e90, compresses it to Q4_K_M and compares it with the library file
   tensor by tensor (HEDEF).
3. Does the same with b12138.
4. Sets up the three models (library, kendi_9ff, kendi_b12138) in Ollama with the same Modelfile (only the
   language-model file differs) and has them generate answers at temperature 0 to the same raw prompts (training
   validation samples). The answers are compared.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request

OUTPUT = "/kaggle/working"
WORK_DIR = "/tmp/is"
MODEL = "Qwen/Qwen3.5-9B"
REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
LLAMA_CPP_VERSIONS = {"9ff": "9ff0d7e90", "b12138": "b12138"}
OLLAMA_VERSION = "v0.40.11"
LIBRARY_MODEL = "qwen3.5:9b"
LIBRARY_DIGEST = "02d45dc1cf451ba2475ac33b301c2dd8f985abe4c182ce04a1f2f5bf0260278d"
PACKAGES = ["transformers==5.17.0", "zstandard==0.23.0"]
PROMPT_COUNT = 4
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_batch": 64, "num_predict": 48}
ENV = {**os.environ, "OLLAMA_MODELS": f"{WORK_DIR}/ollama", "OLLAMA_HOST": "127.0.0.1:11434"}

HEDEF: dict = {}  # <<hedef>> faz8_kaggle.py doldurur: {tensör: {"tur", "ozet"}}

# <<gguf_ozet>>

report: dict = {"basla": time.strftime("%Y-%m-%d %H:%M:%S"), "asamalar": {}, "llama_cpp": {}, "tensor": {},
               "uretim": {}}


def save() -> None:
    with open(f"{OUTPUT}/rapor.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)


def stage(name):
    def decorate(fn):
        def wrapper(*a, **k):
            start = time.time()
            try:
                result = fn(*a, **k)
                report["asamalar"][name] = {"durum": "tamam", "sure_s": round(time.time() - start, 1)}
                return result
            except BaseException as e:
                report["asamalar"][name] = {"durum": "hata", "sure_s": round(time.time() - start, 1),
                                         "hata": f"{type(e).__name__}: {e}"[:2000], "iz": traceback.format_exc()[-3000:]}
                raise
            finally:
                save()
                print(f"[{name}] {report['asamalar'].get(name)}", flush=True)
        return wrapper
    return decorate


def run(*args, **k) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, **k)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, args[:3]))} başarısız ({r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")
    return (r.stdout + r.stderr)[-1500:]


@stage("kurulum")
def build_llama_cpp() -> None:
    run(sys.executable, "-m", "pip", "install", "-q", *PACKAGES)
    if shutil.which("cmake") is None:
        run(sys.executable, "-m", "pip", "install", "-q", "cmake==3.31.6")
    run("git", "clone", "-q", "https://github.com/ggml-org/llama.cpp", "/tmp/llama.cpp")
    for name, version in LLAMA_CPP_VERSIONS.items():
        folder = f"/tmp/llama-{name}"
        try:  # Ollama's version may be patched; if it cannot be found, continue with the other version
            run("git", "-C", "/tmp/llama.cpp", "worktree", "add", "-f", "--detach", folder, version)
            report["llama_cpp"][name] = {
                "surum": version, "tarih": run("git", "-C", folder, "log", "-1", "--format=%cI").strip(),
                "etiket": run("git", "-C", folder, "describe", "--tags", "--always").strip(),
                "no_mtp_var": "--no-mtp" in open(f"{folder}/convert_hf_to_gguf.py").read()}
            run("cmake", "-S", folder, "-B", f"{folder}/build", "-DGGML_CUDA=OFF", "-DLLAMA_CURL=OFF",
                     "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_SERVER=OFF", "-DCMAKE_BUILD_TYPE=Release")
            run("cmake", "--build", f"{folder}/build", "--target", "llama-quantize", "-j",
                     str(os.cpu_count() or 4))
        except Exception as e:
            report["llama_cpp"][name] = {"surum": version, "hata": f"{type(e).__name__}: {e}"[:1500]}
        save()


@stage("ollama")
def setup_ollama() -> str:
    os.makedirs("/tmp/ollama", exist_ok=True)
    name = "ollama-linux-amd64.tar.zst"
    urllib.request.urlretrieve(f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/{name}", f"/tmp/{name}")
    import zstandard
    with open(f"/tmp/{name}", "rb") as g, open("/tmp/ollama.tar", "wb") as c:
        zstandard.ZstdDecompressor().copy_stream(g, c)
    run("tar", "-xf", "/tmp/ollama.tar", "-C", "/tmp/ollama")
    binary = "/tmp/ollama/bin/ollama"
    subprocess.Popen([binary, "serve"], env=ENV, stdout=open(f"{OUTPUT}/ollama.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
            break
        except Exception:
            time.sleep(1)
    run(binary, "pull", LIBRARY_MODEL, env=ENV)
    mf = subprocess.run([binary, "show", LIBRARY_MODEL, "--modelfile"], env=ENV, capture_output=True, text=True,
                        check=True).stdout
    directives = [s for s in mf.splitlines() if s.split(" ", 1)[0] in ("FROM", "TEMPLATE", "RENDERER", "PARSER",
                                                                         "PARAMETER", "SYSTEM")]
    froms = [s for s in directives if s.startswith("FROM ")]
    report["kutuphane"] = {"modelfile": directives, "ozet_ayni": LIBRARY_DIGEST in froms[0]}
    if LIBRARY_DIGEST not in froms[0]:
        raise SystemExit(f"kütüphane modeli bu bilgisayardakinden farklı: {froms}")
    return binary


@stage("indir")
def download() -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(MODEL, revision=REVISION, local_dir=f"{WORK_DIR}/taban")


def compare_with_target(o: dict) -> dict:
    counts: dict = {}
    for name, h in HEDEF.items():
        t = o["tensorler"].get(name)
        s = counts.setdefault(h["tur"], [0, 0])
        s[1] += 1
        if t and t["tur"] == h["tur"] and t.get("ozet") == h["ozet"]:
            s[0] += 1
    return {"ture_gore_ayni_toplam": counts, "ayni": sum(v[0] for v in counts.values()), "toplam": len(HEDEF),
            "tensor": len(o["tensorler"]), "fazla": sorted(set(o["tensorler"]) - set(HEDEF))[:10],
            "eksik": sorted(set(HEDEF) - set(o["tensorler"]))[:10]}


def make_gguf(name: str, base: str) -> str:
    folder = f"/tmp/llama-{name}"
    intermediate, output_path = f"{WORK_DIR}/{name}-bf16.gguf", f"{WORK_DIR}/kendi_{name}.gguf"
    args = [sys.executable, f"{folder}/convert_hf_to_gguf.py", base, "--outtype", "bf16", "--outfile", intermediate]
    if report["llama_cpp"][name]["no_mtp_var"]:
        args.append("--no-mtp")
    run(*args, env={**os.environ, "PYTHONPATH": f"{folder}/gguf-py"})
    run(f"{folder}/build/bin/llama-quantize", intermediate, output_path, "Q4_K_M", str(os.cpu_count() or 4))
    os.remove(intermediate)
    report["tensor"][name] = compare_with_target(summarize(output_path))
    save()
    return output_path


def create_model(binary: str, name: str, gguf: str) -> None:
    lines = list(report["kutuphane"]["modelfile"])
    i = next(i for i, s in enumerate(lines) if s.startswith("FROM "))
    lines[i] = f"FROM {gguf}"
    with open(f"{WORK_DIR}/Modelfile-{name}", "w") as f:
        f.write("\n".join(lines) + "\n")
    run(binary, "create", name, "-f", f"{WORK_DIR}/Modelfile-{name}", env=ENV)


def http_request(path: str, body: dict, timeout: int = 3600) -> dict:
    r = urllib.request.Request(f"http://127.0.0.1:11434{path}", data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def unload() -> None:
    for m in json.loads(urllib.request.urlopen("http://127.0.0.1:11434/api/ps").read()).get("models", []):
        http_request("/api/generate", {"model": m["name"], "keep_alive": 0})


@stage("uretim")
def generate(models: list[str], prompts: list[dict]) -> None:
    for model in models:
        unload()
        result = []
        for o in prompts:
            start = time.time()
            y = http_request("/api/generate", {"model": model, "prompt": o["istem"], "raw": True, "stream": False,
                                        "options": OPTIONS})
            result.append({"soru_id": o.get("soru_id"), "adim": o.get("adim"), "cevap": y["response"],
                          "sure_s": round(time.time() - start, 1)})
            report["uretim"][model] = result
            save()
    reference = report["uretim"].get(LIBRARY_MODEL, [])
    report["uretim_ozet"] = {m: {"kutuphaneyle_ayni": sum(a["cevap"] == b["cevap"] for a, b in zip(reference, v)),
                                "istem": len(v)} for m, v in report["uretim"].items()}


def main() -> None:
    os.makedirs(WORK_DIR, exist_ok=True)
    report["hedef_tensor"] = len(HEDEF)
    save()
    build_llama_cpp()
    binary = setup_ollama()
    base = download()
    files = {}
    for name in LLAMA_CPP_VERSIONS:
        if "hata" in report["llama_cpp"].get(name, {"hata": "yok"}):
            continue
        try:
            files[name] = make_gguf(name, base)
        except Exception as e:
            report["tensor"][name] = {"hata": f"{type(e).__name__}: {e}"[:1500]}
            save()
    for name, path in files.items():
        create_model(binary, f"kendi_{name}", path)
    validation_files = sorted(glob.glob("/kaggle/input/**/dogrulama.jsonl", recursive=True))
    prompts = [json.loads(s) for s in open(validation_files[0], encoding="utf-8") if s.strip()][:PROMPT_COUNT]
    generate([LIBRARY_MODEL] + [f"kendi_{name}" for name in files], prompts)
    report["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()


if __name__ == "__main__":
    main()
