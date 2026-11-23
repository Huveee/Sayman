"""Sayman Phase 8 merge script: adds the LoRA to the base model, converts to GGUF and compresses to Q4_K_M (Kaggle, CPU).

Why: Ollama 0.40.11 no longer supports LoRA adapters (Modelfile ADAPTER). The fine-tuned model enters Ollama as a
single GGUF file in the same form as the base model (qwen35 architecture, Q4_K_M).

Input: output of the training notebook (kernel_sources): the ADAPTER folder (PEFT) and the LoRA GGUF of the same adapter
(only for tensor names). The constants are filled in by scripts/phase08_kaggle.py.

Stages and checks (rapor.json):
1. taban_q4  the untouched base model goes through the same path (convert + Q4_K_M) and is summarized tensor by
             tensor. On this computer it is compared with the base model in Ollama: if the same, the base <->
             fine-tuned difference comes only from training.
2. birlestir for every LoRA module W' = W + (alpha/r)·B·A, shard by shard; memory is one shard. If ARA_TUR is f32
             the changed weights stay 32-bit (so that a small change is not rounded to 16 bits and lost). How much the
             change saved in one module resembles the expected change is measured.
3. ayarli_q4 the merged model goes through the same path. Check: only the tensors the LoRA touched must have changed;
             every other tensor and the header must be byte-for-byte the same as the base Q4.
4. kalinti   on sample MLP tensors (fine-tuned Q4 − base Q4) is dequantized and compared with the expected change:
             how much of the LoRA's effect still stands after the Q4_K_M compression?
--no-mtp is used in the conversion: Qwen3.5's extra prediction layer (MTP) is also absent in the base model in Ollama.
Output: /kaggle/working/sayman-ayarli-q4_k_m.gguf, rapor.json
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time
import traceback

OUTPUT = "/kaggle/working"
WORK_DIR = "/tmp/is"  # large intermediate files (not part of the output)
MODEL = "Qwen/Qwen3.5-9B"
REVISION = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
LLAMA_CPP = "b12138"
PACKAGES = ["transformers==5.17.0"]
ADAPTER = "adapter"  # filled in by scripts/phase08_kaggle.py
LORA_GGUF = "sayman-lora.gguf"  # filled in by scripts/phase08_kaggle.py
ARA_TUR = "f32"  # filled in by scripts/phase08_kaggle.py: intermediate type of the conversion (f32, bf16 or f16)
OUTPUT_NAME = "sayman-ayarli-q4_k_m.gguf"
BASE_Q4 = f"{WORK_DIR}/taban-q4.gguf"
# Base only: no adapter is needed; the untouched base model goes through the same path and is put in the output. On this computer
# it is measured as "our own base model" (because the fairness check did not come out the same). Filled in by scripts/phase08_kaggle.py.
YALNIZ_TABAN = False
BASE_OUTPUT_NAME = "sayman-taban-q4_k_m.gguf"
# MLP tensors used to see how much of the LoRA change is left in Q4: (layer, HF name, GGUF name)
SAMPLE_MODULES = [(0, "down_proj", "ffn_down"), (15, "gate_proj", "ffn_gate"), (31, "up_proj", "ffn_up")]

# <<gguf_ozet>>

report: dict = {"basla": time.strftime("%Y-%m-%d %H:%M:%S"), "asamalar": {},
               "sabitler": {"model": MODEL, "surum": REVISION, "llama_cpp": LLAMA_CPP, "adapter": ADAPTER,
                            "lora_gguf": LORA_GGUF, "ara_tur": ARA_TUR, "yalniz_taban": YALNIZ_TABAN}}


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
                                         "hata": f"{type(e).__name__}: {e}"[:2000], "iz": traceback.format_exc()[-4000:]}
                raise
            finally:
                save()
                print(f"[{name}] {report['asamalar'].get(name)}", flush=True)
        return wrapper
    return decorate


def run(*args, **k) -> str:
    r = subprocess.run(list(args), capture_output=True, text=True, **k)
    if r.returncode != 0:
        raise RuntimeError(f"{args[0]} {args[1] if len(args) > 1 else ''} başarısız ({r.returncode}):\n"
                           f"{(r.stdout + r.stderr)[-3000:]}")
    return (r.stdout + r.stderr)[-1500:]


def disk() -> dict:
    return {y: round(shutil.disk_usage(y).free / 2**30, 1) for y in ("/tmp", OUTPUT)}


@stage("ortam")
def check_environment() -> None:
    memory = {}
    with open("/proc/meminfo") as f:
        for s in f:
            k, v = s.split(":")
            if k in ("MemTotal", "MemAvailable"):
                memory[k] = round(int(v.split()[0]) / 2**20, 1)
    report["ortam"] = {"disk_bos_gb": disk(), "bellek_gb": memory, "cpu": os.cpu_count()}
    if disk()["/tmp"] < 48:
        raise SystemExit(f"/tmp'de yer az: {disk()} (en çok ~43 GB gerekiyor)")


@stage("kurulum")
def build_llama_cpp() -> None:
    run(sys.executable, "-m", "pip", "install", "-q", *PACKAGES)
    if not os.path.isdir("/tmp/llama.cpp"):
        run("git", "clone", "-q", "--depth", "1", "--branch", LLAMA_CPP, "https://github.com/ggml-org/llama.cpp",
                 "/tmp/llama.cpp")
    if shutil.which("cmake") is None:
        run(sys.executable, "-m", "pip", "install", "-q", "cmake==3.31.6")
    run("cmake", "-S", "/tmp/llama.cpp", "-B", "/tmp/llama.cpp/build", "-DGGML_CUDA=OFF", "-DLLAMA_CURL=OFF",
             "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_SERVER=OFF", "-DCMAKE_BUILD_TYPE=Release")
    run("cmake", "--build", "/tmp/llama.cpp/build", "--target", "llama-quantize", "-j", str(os.cpu_count() or 4))
    import importlib.metadata as md
    report["surumler"] = {p: md.version(p) for p in ("torch", "transformers", "safetensors", "numpy")}


def find_input(pattern: str) -> str:
    found = sorted(glob.glob(f"/kaggle/input/**/{pattern}", recursive=True))
    if len(found) != 1:
        raise SystemExit(f"{pattern}: tam bir tane olmalı, bulunan {found}")
    return found[0]


@stage("indir")
def download() -> str:
    from huggingface_hub import snapshot_download

    path = snapshot_download(MODEL, revision=REVISION, local_dir=f"{WORK_DIR}/taban")
    report["taban"] = {"boyut_gb": round(sum(os.path.getsize(p) for p in glob.glob(f"{path}/*")) / 2**30, 2),
                      "disk_bos_gb": disk()}
    return path


def make_gguf(hf_dir: str, output_path: str) -> dict:
    """HF folder -> ARA_TUR GGUF -> Q4_K_M; the intermediate file is deleted, the summary of the Q4 file is returned."""
    intermediate = f"{WORK_DIR}/ara-{ARA_TUR}.gguf"
    env = {**os.environ, "PYTHONPATH": "/tmp/llama.cpp/gguf-py"}
    start = time.time()
    # --no-mtp: Qwen3.5's extra prediction layer (MTP / nextn, blk.32) is left out; the base model in Ollama also has 32
    # layers and does not carry this layer. The layer is used only in accelerated (speculative) generation.
    run(sys.executable, "/tmp/llama.cpp/convert_hf_to_gguf.py", hf_dir, "--outtype", ARA_TUR, "--no-mtp",
             "--outfile", intermediate, env=env)
    conversion_s = time.time() - start
    start = time.time()
    run("/tmp/llama.cpp/build/bin/llama-quantize", intermediate, output_path, "Q4_K_M", str(os.cpu_count() or 4))
    compression_s = time.time() - start
    intermediate_gb = round(os.path.getsize(intermediate) / 2**30, 2)
    os.remove(intermediate)
    o = summarize(output_path)
    import hashlib
    h = hashlib.sha256()
    with open(output_path, "rb") as f:
        for part in iter(lambda: f.read(1 << 24), b""):
            h.update(part)
    # sha256 and bytes: these show that the measured base file is the same as the base file in the merge
    o["dosya"] = {"boyut_gb": round(os.path.getsize(output_path) / 2**30, 3), "bayt": os.path.getsize(output_path),
                  "sha256": h.hexdigest(), "ara_boyut_gb": intermediate_gb, "donusturme_s": round(conversion_s, 1),
                  "sikistirma_s": round(compression_s, 1)}
    return o


@stage("taban_q4")
def base_q4(base: str) -> dict:
    o = make_gguf(base, BASE_Q4)  # the file stays until the kalinti stage
    with open(f"{OUTPUT}/taban_q4_ozet.json", "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False)
    report["taban_q4"] = {"dosya": o["dosya"], "tensor": len(o["tensorler"])}
    return o


def _lora_scale(folder: str) -> float:
    config = json.load(open(f"{folder}/adapter_config.json", encoding="utf-8"))
    problems = [name for name, bad in (
        ("peft_type", config.get("peft_type") != "LORA"), ("use_rslora", bool(config.get("use_rslora"))),
        ("use_dora", bool(config.get("use_dora"))), ("fan_in_fan_out", bool(config.get("fan_in_fan_out"))),
        ("modules_to_save", bool(config.get("modules_to_save"))), ("bias", config.get("bias", "none") != "none"),
        ("rank_pattern", bool(config.get("rank_pattern"))), ("alpha_pattern", bool(config.get("alpha_pattern"))))
        if bad]
    if problems:
        raise SystemExit(f"beklenmeyen adapter ayarı: {problems}")
    return config["lora_alpha"] / config["r"]


@stage("birlestir")
def merge(base: str, adapter: str) -> None:
    import torch
    from safetensors.torch import load_file, save_file

    scale = _lora_scale(adapter)
    lora = load_file(f"{adapter}/adapter_model.safetensors")
    modules = sorted({k.rsplit(".lora_", 1)[0] for k in lora if ".lora_" in k})
    targets = {}
    for m in modules:
        name = m.removeprefix("base_model.model.") + ".weight"
        targets[name] = (lora[f"{m}.lora_A.weight"], lora[f"{m}.lora_B.weight"])
    weight_map = json.load(open(f"{base}/model.safetensors.index.json", encoding="utf-8"))["weight_map"]
    missing = [k for k in targets if k not in weight_map]
    if missing:
        raise SystemExit(f"temel modelde bulunmayan LoRA hedefleri: {missing[:5]} (toplam {len(missing)})")
    os.makedirs(f"{WORK_DIR}/birlesik", exist_ok=True)
    for p in os.listdir(base):  # configuration, tokenizer, index, etc.
        if not p.endswith(".safetensors") and os.path.isfile(f"{base}/{p}"):
            shutil.copy(f"{base}/{p}", f"{WORK_DIR}/birlesik/{p}")
    samples = {f"model.language_model.layers.{i}.mlp.{hf}.weight" for i, hf, _ in SAMPLE_MODULES}
    os.makedirs(f"{WORK_DIR}/kalinti", exist_ok=True)
    merged, rounding = 0, None
    for part in sorted(set(weight_map.values())):
        t = load_file(f"{base}/{part}")
        for name in [k for k in t if k in targets]:
            a, b = targets[name]
            old_weight = t[name]
            delta = scale * (b.float() @ a.float())
            if delta.shape != old_weight.shape:
                raise SystemExit(f"{name}: boyut uymuyor {tuple(delta.shape)} ↔ {tuple(old_weight.shape)}")
            # If ARA_TUR is f32 the merged weight stays 32-bit: a small LoRA change is not rounded to 16 bits and lost, the only
            # rounding happens in the Q4_K_M compression. With bf16/f16 the weight returns to the base's own type.
            new_weight = old_weight.float() + delta
            if ARA_TUR != "f32":
                new_weight = new_weight.to(old_weight.dtype)
            if name in samples:
                torch.save(delta, f"{WORK_DIR}/kalinti/{name}.pt")
            if rounding is None:  # in the saved type, how much does the change resemble the expected change?
                stored_delta = (new_weight.float() - old_weight.float()).flatten()
                d = delta.flatten()
                rounding = {"tensor": name, "tur": str(new_weight.dtype), "olcek": scale,
                           "benzerlik": round(float(torch.nn.functional.cosine_similarity(stored_delta, d, dim=0)), 4),
                           "norm_orani": round(float(stored_delta.norm() / d.norm()), 4),
                           "delta_ort_mutlak": float(d.abs().mean()), "agirlik_ort_mutlak": float(old_weight.float().abs().mean())}
            t[name] = new_weight
            merged += 1
        save_file(t, f"{WORK_DIR}/birlesik/{part}", metadata={"format": "pt"})
        del t
        os.remove(f"{base}/{part}")  # to free space; the base's Q4 was extracted earlier
    if merged != len(targets):
        raise SystemExit(f"{len(targets)} modülden {merged} tanesi birleşti")
    report["birlestir"] = {"modul": len(targets), "birlesen": merged, "yuvarlama": rounding, "disk_bos_gb": disk()}


@stage("ayarli_q4")
def finetuned_q4() -> dict:
    o = make_gguf(f"{WORK_DIR}/birlesik", f"{OUTPUT}/{OUTPUT_NAME}")
    shutil.rmtree(f"{WORK_DIR}/birlesik")
    with open(f"{OUTPUT}/ayarli_q4_ozet.json", "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False)
    report["ayarli_q4"] = {"dosya": o["dosya"], "tensor": len(o["tensorler"])}
    return o


@stage("denetim")
def audit(base_summary: dict | None, finetuned_summary: dict, lora_gguf: str) -> None:
    expected = sorted({t.rsplit(".lora_", 1)[0] for t in summarize(lora_gguf, with_digest=False)["tensorler"]})
    result = {"lora_hedefi": len(expected)}
    if base_summary is not None:
        k = compare(base_summary, finetuned_summary)
        changed = sorted(k["veri_farki"])
        result.update({"degisen": len(changed), "ayni": k["ayni_tensor"], "tensor": k["tensor"],
                      "beklenmeyen_degisen": sorted(set(changed) - set(expected))[:20],
                      "degismeyen_hedef": sorted(set(expected) - set(changed))[:20],
                      "kv_farki": k["kv_farki"], "tur_farki": k["tur_farki"],
                      "yalniz_temelde": k["yalniz_ilkinde"], "yalniz_ayarlida": k["yalniz_ikincide"]})
        result["gecti"] = (set(changed) == set(expected) and not k["kv_farki"] and not k["tur_farki"]
                          and not k["yalniz_ilkinde"] and not k["yalniz_ikincide"])
    report["denetim"] = result


@stage("kalinti")
def residual() -> None:
    """How much of the LoRA change is left in Q4_K_M? On sample MLP tensors: (fine-tuned Q4 − base Q4), once dequantized,
    how much does it resemble the expected change (scale·B·A) (cosine) and how large is it (norm ratio)?"""
    import numpy as np
    import torch

    sys.path.insert(0, "/tmp/llama.cpp/gguf-py")
    from gguf import GGUFReader
    from gguf.quants import dequantize

    def read_tensors(path):
        return {t.name: t for t in GGUFReader(path).tensors}

    tb, ta = read_tensors(BASE_Q4), read_tensors(f"{OUTPUT}/{OUTPUT_NAME}")
    result = []
    for i, hf, gg in SAMPLE_MODULES:
        name, g = f"model.language_model.layers.{i}.mlp.{hf}.weight", f"blk.{i}.{gg}.weight"
        path = f"{WORK_DIR}/kalinti/{name}.pt"
        if not os.path.exists(path) or g not in tb:
            result.append({"tensor": g, "atlandi": True})
            continue
        d = torch.load(path).numpy().astype(np.float32)
        b = dequantize(tb[g].data, tb[g].tensor_type).astype(np.float32)
        a = dequantize(ta[g].data, ta[g].tensor_type).astype(np.float32)
        if b.shape != d.shape:
            raise ValueError(f"{g}: açılan boyut {b.shape}, beklenen değişim {d.shape}")
        diff = (a - b).ravel()
        dv = d.ravel()
        result.append({"tensor": g, "tur": tb[g].tensor_type.name,
                      "kosinus": round(float(diff @ dv / (np.linalg.norm(diff) * np.linalg.norm(dv) + 1e-30)), 4),
                      "norm_orani": round(float(np.linalg.norm(diff) / (np.linalg.norm(dv) + 1e-30)), 3),
                      "degisen_oran": round(float(np.mean(a != b)), 4),
                      "delta_ort_mutlak": float(np.abs(dv).mean())})
    report["kalinti"] = result


def main() -> None:
    os.makedirs(WORK_DIR, exist_ok=True)
    save()
    check_environment()
    build_llama_cpp()
    if YALNIZ_TABAN:
        base_q4(download())
        shutil.move(BASE_Q4, f"{OUTPUT}/{BASE_OUTPUT_NAME}")
        report["taban_q4"]["cikti"] = BASE_OUTPUT_NAME
        report["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save()
        return
    adapter = os.path.dirname(find_input(f"{ADAPTER}/adapter_config.json"))
    lora_gguf = find_input(LORA_GGUF)
    report["girdi"] = {"adapter": adapter, "lora_gguf": lora_gguf}
    base = download()
    base_summary = base_q4(base)
    merge(base, adapter)
    finetuned_summary = finetuned_q4()
    audit(base_summary, finetuned_summary, lora_gguf)
    try:
        residual()
    except Exception:
        pass  # information only; the error shows in the report, the output file is not affected
    os.remove(BASE_Q4)
    report["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()


if __name__ == "__main__":
    main()
