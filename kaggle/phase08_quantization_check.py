"""Sayman Faz 8 tanı betiği: Ollama'daki qwen3.5:9b dosyası hangi dönüştürme ve sıkıştırma yoluyla yapılmış? (Kaggle, CPU)

Bizim yolumuzla (llama.cpp b12138, bf16 ara tür, llama-quantize) üretilen temel modelde sıkıştırılmamış F32 tensörlerin
hepsi Ollama'nın dosyasıyla aynı çıktı (aynı kaynak ağırlıklar); ama sıkıştırılmış tensörlerin hemen hepsi ve hesaplanan
ssm_a tensörü farklı çıktı. Bu betik adayları dener ve her adayın tensör özetlerini Ollama'nın dosyasınınkilerle
(HEDEF, bu bilgisayarda çıkarıldı) karşılaştırır:

  ssm_a          ssm_a = -exp(A_log) farklı hassasiyetlerle hesaplanır; 0. katmanda değerler Ollama'nınkilerle yan yana.
  (1. sürüm)     f16_llama: f16 ara tür + llama-quantize -> bf16 ile aynı sonuç (154/427).
                 f16_ollama / bf16_ollama: Ollama GGUF'tan sıkıştırmıyor (yalnızca safetensors içe aktarırken).
  st_ollama      (2. sürüm) Ollama'nın kendi yolu: safetensors klasöründen içe aktarma + --quantize q4_K_M.

Sonuç rapor.json'da: her aday için tür türüne aynı / toplam tensör sayısı.
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

CIKTI = "/kaggle/working"
IS = "/tmp/is"
MODEL = "Qwen/Qwen3.5-9B"
SURUM = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
LLAMA_CPP = "b12138"
OLLAMA_SURUM = "v0.40.11"
PAKETLER = ["transformers==5.17.0", "zstandard==0.23.0"]

HEDEF: dict = {}  # <<hedef>> faz8_kaggle.py doldurur: {tensör: {"tur", "ozet"}}
OLLAMA_SSM_A0: list = []  # <<ssm_a0>> faz8_kaggle.py doldurur: Ollama dosyasındaki blk.0.ssm_a değerleri

# <<gguf_ozet>>

rapor: dict = {"basla": time.strftime("%Y-%m-%d %H:%M:%S"), "asamalar": {}, "adaylar": {}}


def kaydet() -> None:
    with open(f"{CIKTI}/rapor.json", "w", encoding="utf-8") as f:
        json.dump(rapor, f, ensure_ascii=False, indent=1)


def asama(ad):
    def sar(fn):
        def calis(*a, **k):
            bas = time.time()
            try:
                sonuc = fn(*a, **k)
                rapor["asamalar"][ad] = {"durum": "tamam", "sure_s": round(time.time() - bas, 1)}
                return sonuc
            except BaseException as e:
                rapor["asamalar"][ad] = {"durum": "hata", "sure_s": round(time.time() - bas, 1),
                                         "hata": f"{type(e).__name__}: {e}"[:2000], "iz": traceback.format_exc()[-3000:]}
                raise
            finally:
                kaydet()
                print(f"[{ad}] {rapor['asamalar'].get(ad)}", flush=True)
        return calis
    return sar


def calistir(*arg, **k) -> str:
    r = subprocess.run(list(arg), capture_output=True, text=True, **k)
    if r.returncode != 0:
        raise RuntimeError(f"{arg[0]} başarısız ({r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")
    return (r.stdout + r.stderr)[-1500:]


@asama("kurulum")
def kurulum() -> None:
    calistir(sys.executable, "-m", "pip", "install", "-q", *PAKETLER)
    calistir("git", "clone", "-q", "--depth", "1", "--branch", LLAMA_CPP, "https://github.com/ggml-org/llama.cpp",
             "/tmp/llama.cpp")
    if shutil.which("cmake") is None:
        calistir(sys.executable, "-m", "pip", "install", "-q", "cmake==3.31.6")
    calistir("cmake", "-S", "/tmp/llama.cpp", "-B", "/tmp/llama.cpp/build", "-DGGML_CUDA=OFF", "-DLLAMA_CURL=OFF",
             "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_SERVER=OFF", "-DCMAKE_BUILD_TYPE=Release")
    calistir("cmake", "--build", "/tmp/llama.cpp/build", "--target", "llama-quantize", "-j", str(os.cpu_count() or 4))


@asama("ollama_kur")
def ollama_kur() -> str:
    """Ollama'nın resmî Linux sürümünü indirir, açar ve sunucuyu başlatır (modeller /tmp/is/ollama altında)."""
    os.makedirs("/tmp/ollama", exist_ok=True)
    taban = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_SURUM}"
    hatalar = []
    for ad in ("ollama-linux-amd64.tar.zst", "ollama-linux-amd64.tgz"):
        try:
            urllib.request.urlretrieve(f"{taban}/{ad}", f"/tmp/{ad}")
        except Exception as e:
            hatalar.append(f"{ad}: {e}")
            continue
        if ad.endswith(".zst"):
            import zstandard
            with open(f"/tmp/{ad}", "rb") as g, open("/tmp/ollama.tar", "wb") as c:
                zstandard.ZstdDecompressor().copy_stream(g, c)
            calistir("tar", "-xf", "/tmp/ollama.tar", "-C", "/tmp/ollama")
        else:
            calistir("tar", "-xzf", f"/tmp/{ad}", "-C", "/tmp/ollama")
        break
    else:
        raise RuntimeError(f"Ollama indirilemedi: {hatalar}")
    ikili = "/tmp/ollama/bin/ollama"
    ortam = {**os.environ, "OLLAMA_MODELS": f"{IS}/ollama", "OLLAMA_HOST": "127.0.0.1:11434"}
    subprocess.Popen([ikili, "serve"], env=ortam, stdout=open("/tmp/ollama.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
            break
        except Exception:
            time.sleep(1)
    rapor["ollama"] = {"surum": calistir(ikili, "--version", env=ortam).strip()[-200:]}
    return ikili


@asama("indir")
def indir() -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(MODEL, revision=SURUM, local_dir=f"{IS}/taban")


def karsilastir_hedef(o: dict) -> dict:
    sayim: dict = {}
    for ad, h in HEDEF.items():
        t = o["tensorler"].get(ad)
        s = sayim.setdefault(h["tur"], [0, 0])
        s[1] += 1
        if t and t["tur"] == h["tur"] and t.get("ozet") == h["ozet"]:
            s[0] += 1
    toplam = [sum(v[0] for v in sayim.values()), sum(v[1] for v in sayim.values())]
    return {"ture_gore_ayni_toplam": sayim, "ayni": toplam[0], "toplam": toplam[1],
            "fazla_tensor": sorted(set(o["tensorler"]) - set(HEDEF))[:20]}


@asama("ssm_a")
def ssm_a(taban: str) -> None:
    import torch
    from safetensors import safe_open

    import numpy as np

    dizin = json.load(open(f"{taban}/model.safetensors.index.json"))["weight_map"]
    adaylar = {"bf16": lambda t: (-torch.exp(t)).float(), "f32": lambda t: -torch.exp(t.float()),
               "f64": lambda t: (-torch.exp(t.double())).float(),
               "bf16_girdi": lambda t: -torch.exp(t.bfloat16().float()),
               "f16_girdi": lambda t: -torch.exp(t.half().float()),
               "bf16_cikti": lambda t: (-torch.exp(t.float())).bfloat16().float(),
               "f16_cikti": lambda t: (-torch.exp(t.float())).half().float(),
               "np_f32": lambda t: torch.from_numpy(-np.exp(t.float().numpy()))}
    sayim = {k: 0 for k in adaylar}
    n = 0
    if OLLAMA_SSM_A0:  # 0. katmanda değerler yan yana: fark biçimden mi, hassasiyetten mi?
        anahtar0 = next(k for k in dizin if k.endswith(".A_log") and ".layers.0." in k and "mtp" not in k)
        with safe_open(f"{taban}/{dizin[anahtar0]}", "pt") as f:
            t0 = f.get_tensor(anahtar0)
        hedef0 = torch.tensor(OLLAMA_SSM_A0)
        rapor["ssm_a_yan_yana"] = {
            "A_log": [float(x) for x in t0[:6]], "ollama": [float(x) for x in hedef0[:6]],
            **{ad: {"ilk": [float(x) for x in fn(t0)[:6]],
                    "en_cok_fark": float((fn(t0).float() - hedef0).abs().max()),
                    "en_cok_goreli_fark": float(((fn(t0).float() - hedef0).abs() / hedef0.abs()).max())}
               for ad, fn in adaylar.items()}}
        kaydet()
    for anahtar, parca in dizin.items():
        if not anahtar.endswith(".A_log") or ".mtp." in anahtar or anahtar.startswith("mtp."):
            continue
        i = anahtar.split(".layers.")[1].split(".")[0]
        hedef = HEDEF.get(f"blk.{i}.ssm_a")
        if not hedef:
            continue
        with safe_open(f"{taban}/{parca}", "pt") as f:
            t = f.get_tensor(anahtar)
        n += 1
        for ad, fn in adaylar.items():
            b = fn(t).contiguous().numpy().astype("<f4").tobytes()
            sayim[ad] += hashlib.sha256(b).hexdigest()[:24] == hedef["ozet"]
        rapor["adaylar"]["ssm_a"] = {"tensor": n, "ayni": dict(sayim), "kaynak_turu": str(t.dtype)}
    kaydet()


def donustur(taban: str, tur: str) -> str:
    ara = f"{IS}/ara-{tur}.gguf"
    calistir(sys.executable, "/tmp/llama.cpp/convert_hf_to_gguf.py", taban, "--outtype", tur, "--no-mtp",
             "--outfile", ara, env={**os.environ, "PYTHONPATH": "/tmp/llama.cpp/gguf-py"})
    return ara


def llama_q4(ara: str, ad: str) -> None:
    cikis = f"{IS}/{ad}.gguf"
    calistir("/tmp/llama.cpp/build/bin/llama-quantize", ara, cikis, "Q4_K_M", str(os.cpu_count() or 4))
    rapor["adaylar"][ad] = karsilastir_hedef(ozet(cikis))
    os.remove(cikis)
    kaydet()


def ollama_q4(ikili: str, ara: str, ad: str) -> None:
    ortam = {**os.environ, "OLLAMA_MODELS": f"{IS}/ollama", "OLLAMA_HOST": "127.0.0.1:11434"}
    mf = f"{IS}/Modelfile-{ad}"
    with open(mf, "w") as f:
        f.write(f"FROM {ara}\n")
    once = set(glob.glob(f"{IS}/ollama/blobs/*"))
    cikti = calistir(ikili, "create", ad, "-f", mf, "--quantize", "q4_K_M", env=ortam)
    yeni = sorted(set(glob.glob(f"{IS}/ollama/blobs/*")) - once, key=os.path.getsize)
    if not yeni:
        raise RuntimeError(f"ollama create yeni dosya üretmedi: {cikti}")
    rapor["adaylar"][ad] = karsilastir_hedef(ozet(yeni[-1]))
    calistir(ikili, "rm", ad, env=ortam)
    kaydet()


def ollama_st(ikili: str, taban: str, ad: str) -> None:
    """Ollama'nın kendi yolu: safetensors klasöründen içe aktarma ve aynı anda Q4_K_M'ye sıkıştırma."""
    ortam = {**os.environ, "OLLAMA_MODELS": f"{IS}/ollama", "OLLAMA_HOST": "127.0.0.1:11434"}
    mf = f"{IS}/Modelfile-{ad}"
    with open(mf, "w") as f:
        f.write(f"FROM {taban}\n")
    once = set(glob.glob(f"{IS}/ollama/blobs/*"))
    cikti = calistir(ikili, "create", ad, "-f", mf, "--quantize", "q4_K_M", env=ortam)
    yeni = sorted(set(glob.glob(f"{IS}/ollama/blobs/*")) - once, key=os.path.getsize)
    if not yeni:
        raise RuntimeError(f"ollama create yeni dosya üretmedi: {cikti}")
    sonuc = {"dosyalar": [[os.path.basename(p)[:20], round(os.path.getsize(p) / 2**30, 3)] for p in yeni]}
    for p in yeni[::-1]:  # büyükten küçüğe; GGUF olanların hepsi
        try:
            o = ozet(p)
        except ValueError:
            continue
        sonuc[os.path.basename(p)[:20]] = {**karsilastir_hedef(o), "mimari": o["kv"].get("general.architecture"),
                                           "ad": o["kv"].get("general.name"), "tensor": len(o["tensorler"])}
    rapor["adaylar"][ad] = sonuc
    kaydet()


def dene(ad: str, fn, *a) -> None:
    bas = time.time()
    try:
        fn(*a)
    except Exception as e:
        rapor["adaylar"][ad] = {"hata": f"{type(e).__name__}: {e}"[:1500]}
    rapor["adaylar"].setdefault(ad, {})["sure_s"] = round(time.time() - bas, 1)
    kaydet()


def main() -> None:
    os.makedirs(IS, exist_ok=True)
    rapor["hedef_tensor"] = len(HEDEF)
    kaydet()
    kurulum()
    try:
        ikili = ollama_kur()
    except Exception:
        ikili = None
    taban = indir()
    dene("ssm_a", ssm_a, taban)
    # 1. sürümde denendi: f16 ara türle llama-quantize bf16 ile aynı sonucu verdi (154/427); Ollama GGUF'tan
    # sıkıştırmıyor ("create-time quantization is only supported for safetensors imports").
    if ikili:
        dene("st_ollama", ollama_st, ikili, taban, "st_ollama")
    rapor["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
    kaydet()


if __name__ == "__main__":
    main()
