"""Sayman Phase 8 diagnostic script: with which conversion and compression path was the qwen3.5:9b file in Ollama made? (Kaggle, CPU)

In the base model produced our way (llama.cpp b12138, bf16 intermediate type, llama-quantize), all the uncompressed
F32 tensors come out the same as Ollama's file (same source weights); but almost all the compressed tensors and the
computed ssm_a tensor come out different. This script tries candidates and compares each candidate's tensor digests
with those of Ollama's file (HEDEF, extracted on this computer):

  ssm_a          ssm_a = -exp(A_log) is computed at different precisions; layer 0 values side by side with Ollama's.
  (1st version)  f16_llama: f16 intermediate type + llama-quantize -> same result as bf16 (154/427).
                 f16_ollama / bf16_ollama: Ollama does not compress from GGUF (only when importing safetensors).
  st_ollama      (2nd version) Ollama's own path: import from a safetensors folder + --quantize q4_K_M.

The result is in rapor.json: for each candidate, same by tensor type / total tensor count.
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
LLAMA_CPP = "b12138"
OLLAMA_VERSION = "v0.40.11"
PACKAGES = ["transformers==5.17.0", "zstandard==0.23.0"]

HEDEF: dict = {}  # <<hedef>> faz8_kaggle.py doldurur: {tensör: {"tur", "ozet"}}
OLLAMA_SSM_A0: list = []  # <<ssm_a0>> faz8_kaggle.py doldurur: Ollama dosyasındaki blk.0.ssm_a değerleri

# <<gguf_ozet>>

report: dict = {"basla": time.strftime("%Y-%m-%d %H:%M:%S"), "asamalar": {}, "adaylar": {}}


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
        raise RuntimeError(f"{args[0]} başarısız ({r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")
    return (r.stdout + r.stderr)[-1500:]


@stage("kurulum")
def build_llama_cpp() -> None:
    run(sys.executable, "-m", "pip", "install", "-q", *PACKAGES)
    run("git", "clone", "-q", "--depth", "1", "--branch", LLAMA_CPP, "https://github.com/ggml-org/llama.cpp",
             "/tmp/llama.cpp")
    if shutil.which("cmake") is None:
        run(sys.executable, "-m", "pip", "install", "-q", "cmake==3.31.6")
    run("cmake", "-S", "/tmp/llama.cpp", "-B", "/tmp/llama.cpp/build", "-DGGML_CUDA=OFF", "-DLLAMA_CURL=OFF",
             "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_SERVER=OFF", "-DCMAKE_BUILD_TYPE=Release")
    run("cmake", "--build", "/tmp/llama.cpp/build", "--target", "llama-quantize", "-j", str(os.cpu_count() or 4))


@stage("ollama_kur")
def setup_ollama() -> str:
    """Downloads the official Linux release of Ollama, unpacks it and starts the server (models under /tmp/is/ollama)."""
    os.makedirs("/tmp/ollama", exist_ok=True)
    base = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}"
    errors = []
    for name in ("ollama-linux-amd64.tar.zst", "ollama-linux-amd64.tgz"):
        try:
            urllib.request.urlretrieve(f"{base}/{name}", f"/tmp/{name}")
        except Exception as e:
            errors.append(f"{name}: {e}")
            continue
        if name.endswith(".zst"):
            import zstandard
            with open(f"/tmp/{name}", "rb") as g, open("/tmp/ollama.tar", "wb") as c:
                zstandard.ZstdDecompressor().copy_stream(g, c)
            run("tar", "-xf", "/tmp/ollama.tar", "-C", "/tmp/ollama")
        else:
            run("tar", "-xzf", f"/tmp/{name}", "-C", "/tmp/ollama")
        break
    else:
        raise RuntimeError(f"Ollama indirilemedi: {errors}")
    binary = "/tmp/ollama/bin/ollama"
    env = {**os.environ, "OLLAMA_MODELS": f"{WORK_DIR}/ollama", "OLLAMA_HOST": "127.0.0.1:11434"}
    subprocess.Popen([binary, "serve"], env=env, stdout=open("/tmp/ollama.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
            break
        except Exception:
            time.sleep(1)
    report["ollama"] = {"surum": run(binary, "--version", env=env).strip()[-200:]}
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
    totals = [sum(v[0] for v in counts.values()), sum(v[1] for v in counts.values())]
    return {"ture_gore_ayni_toplam": counts, "ayni": totals[0], "toplam": totals[1],
            "fazla_tensor": sorted(set(o["tensorler"]) - set(HEDEF))[:20]}


@stage("ssm_a")
def ssm_a(base: str) -> None:
    import torch
    from safetensors import safe_open

    import numpy as np

    weight_map = json.load(open(f"{base}/model.safetensors.index.json"))["weight_map"]
    candidates = {"bf16": lambda t: (-torch.exp(t)).float(), "f32": lambda t: -torch.exp(t.float()),
               "f64": lambda t: (-torch.exp(t.double())).float(),
               "bf16_girdi": lambda t: -torch.exp(t.bfloat16().float()),
               "f16_girdi": lambda t: -torch.exp(t.half().float()),
               "bf16_cikti": lambda t: (-torch.exp(t.float())).bfloat16().float(),
               "f16_cikti": lambda t: (-torch.exp(t.float())).half().float(),
               "np_f32": lambda t: torch.from_numpy(-np.exp(t.float().numpy()))}
    counts = {k: 0 for k in candidates}
    n = 0
    if OLLAMA_SSM_A0:  # values for layer 0 side by side: is the difference from the form or from the precision?
        key0 = next(k for k in weight_map if k.endswith(".A_log") and ".layers.0." in k and "mtp" not in k)
        with safe_open(f"{base}/{weight_map[key0]}", "pt") as f:
            t0 = f.get_tensor(key0)
        target0 = torch.tensor(OLLAMA_SSM_A0)
        report["ssm_a_yan_yana"] = {
            "A_log": [float(x) for x in t0[:6]], "ollama": [float(x) for x in target0[:6]],
            **{name: {"ilk": [float(x) for x in fn(t0)[:6]],
                    "en_cok_fark": float((fn(t0).float() - target0).abs().max()),
                    "en_cok_goreli_fark": float(((fn(t0).float() - target0).abs() / target0.abs()).max())}
               for name, fn in candidates.items()}}
        save()
    for key, shard in weight_map.items():
        if not key.endswith(".A_log") or ".mtp." in key or key.startswith("mtp."):
            continue
        i = key.split(".layers.")[1].split(".")[0]
        target = HEDEF.get(f"blk.{i}.ssm_a")
        if not target:
            continue
        with safe_open(f"{base}/{shard}", "pt") as f:
            t = f.get_tensor(key)
        n += 1
        for name, fn in candidates.items():
            b = fn(t).contiguous().numpy().astype("<f4").tobytes()
            counts[name] += hashlib.sha256(b).hexdigest()[:24] == target["ozet"]
        report["adaylar"]["ssm_a"] = {"tensor": n, "ayni": dict(counts), "kaynak_turu": str(t.dtype)}
    save()


def convert(base: str, out_type: str) -> str:
    intermediate = f"{WORK_DIR}/ara-{out_type}.gguf"
    run(sys.executable, "/tmp/llama.cpp/convert_hf_to_gguf.py", base, "--outtype", out_type, "--no-mtp",
             "--outfile", intermediate, env={**os.environ, "PYTHONPATH": "/tmp/llama.cpp/gguf-py"})
    return intermediate


def quantize_with_llama(intermediate: str, name: str) -> None:
    output_path = f"{WORK_DIR}/{name}.gguf"
    run("/tmp/llama.cpp/build/bin/llama-quantize", intermediate, output_path, "Q4_K_M", str(os.cpu_count() or 4))
    report["adaylar"][name] = compare_with_target(summarize(output_path))
    os.remove(output_path)
    save()


def quantize_with_ollama(binary: str, intermediate: str, name: str) -> None:
    env = {**os.environ, "OLLAMA_MODELS": f"{WORK_DIR}/ollama", "OLLAMA_HOST": "127.0.0.1:11434"}
    mf = f"{WORK_DIR}/Modelfile-{name}"
    with open(mf, "w") as f:
        f.write(f"FROM {intermediate}\n")
    before = set(glob.glob(f"{WORK_DIR}/ollama/blobs/*"))
    command_output = run(binary, "create", name, "-f", mf, "--quantize", "q4_K_M", env=env)
    new_blobs = sorted(set(glob.glob(f"{WORK_DIR}/ollama/blobs/*")) - before, key=os.path.getsize)
    if not new_blobs:
        raise RuntimeError(f"ollama create yeni dosya üretmedi: {command_output}")
    report["adaylar"][name] = compare_with_target(summarize(new_blobs[-1]))
    run(binary, "rm", name, env=env)
    save()


def import_safetensors_with_ollama(binary: str, base: str, name: str) -> None:
    """Ollama's own path: import from a safetensors folder and compress to Q4_K_M at the same time."""
    env = {**os.environ, "OLLAMA_MODELS": f"{WORK_DIR}/ollama", "OLLAMA_HOST": "127.0.0.1:11434"}
    mf = f"{WORK_DIR}/Modelfile-{name}"
    with open(mf, "w") as f:
        f.write(f"FROM {base}\n")
    before = set(glob.glob(f"{WORK_DIR}/ollama/blobs/*"))
    command_output = run(binary, "create", name, "-f", mf, "--quantize", "q4_K_M", env=env)
    new_blobs = sorted(set(glob.glob(f"{WORK_DIR}/ollama/blobs/*")) - before, key=os.path.getsize)
    if not new_blobs:
        raise RuntimeError(f"ollama create yeni dosya üretmedi: {command_output}")
    result = {"dosyalar": [[os.path.basename(p)[:20], round(os.path.getsize(p) / 2**30, 3)] for p in new_blobs]}
    for p in new_blobs[::-1]:  # largest to smallest; all of those that are GGUF
        try:
            o = summarize(p)
        except ValueError:
            continue
        result[os.path.basename(p)[:20]] = {**compare_with_target(o), "mimari": o["kv"].get("general.architecture"),
                                           "ad": o["kv"].get("general.name"), "tensor": len(o["tensorler"])}
    report["adaylar"][name] = result
    save()


def attempt(name: str, fn, *a) -> None:
    start = time.time()
    try:
        fn(*a)
    except Exception as e:
        report["adaylar"][name] = {"hata": f"{type(e).__name__}: {e}"[:1500]}
    report["adaylar"].setdefault(name, {})["sure_s"] = round(time.time() - start, 1)
    save()


def main() -> None:
    os.makedirs(WORK_DIR, exist_ok=True)
    report["hedef_tensor"] = len(HEDEF)
    save()
    build_llama_cpp()
    try:
        binary = setup_ollama()
    except Exception:
        binary = None
    base = download()
    attempt("ssm_a", ssm_a, base)
    # Tried in the 1st version: with an f16 intermediate type llama-quantize gave the same result as bf16 (154/427);
    # Ollama does not compress from GGUF ("create-time quantization is only supported for safetensors imports").
    if binary:
        attempt("st_ollama", import_safetensors_with_ollama, binary, base, "st_ollama")
    report["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()


if __name__ == "__main__":
    main()
