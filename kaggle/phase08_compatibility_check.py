"""Sayman Faz 8 uyum denemesi: kendi yolumuzla yaptığımız GGUF, Ollama 0.40.11'de kütüphane modeli gibi çalışıyor mu?
(Kaggle, CPU)

Bulgu (faz8_niceleme_denemesi.py): llama.cpp b12138 ile dönüştürülen temel modelde ssm_a değerleri Ollama'nın
dosyasındakilerin aynısı ama başka sırada (doğrusal dikkat katmanlarında kafa sırası farklı). Ollama'nın kendi
llama-server'ı llama.cpp'nin 9ff0d7e90 sürümüyle derlenmiş. Bu betik:

1. Ollama 0.40.11'i kurar ve kütüphaneden qwen3.5:9b'yi çeker (özeti bu bilgisayardakiyle aynı olmalı).
2. Temel modeli 9ff0d7e90 sürümüyle dönüştürüp Q4_K_M'ye sıkıştırır ve tensör tensör kütüphane dosyasıyla
   karşılaştırır (HEDEF).
3. Aynı işi b12138 ile yapar.
4. Üç modeli (kütüphane, kendi_9ff, kendi_b12138) Ollama'ya aynı Modelfile ile kurar (yalnızca dil modeli dosyası farklı)
   ve aynı ham istemlerle (eğitim doğrulama örnekleri) sıcaklık 0'da cevap ürettirir. Cevaplar karşılaştırılır.
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
SURUMLER = {"9ff": "9ff0d7e90", "b12138": "b12138"}
OLLAMA_SURUM = "v0.40.11"
KUTUPHANE = "qwen3.5:9b"
KUTUPHANE_OZETI = "02d45dc1cf451ba2475ac33b301c2dd8f985abe4c182ce04a1f2f5bf0260278d"
PAKETLER = ["transformers==5.17.0", "zstandard==0.23.0"]
ISTEM_SAYISI = 4
SECENEKLER = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_batch": 64, "num_predict": 48}
ORTAM = {**os.environ, "OLLAMA_MODELS": f"{IS}/ollama", "OLLAMA_HOST": "127.0.0.1:11434"}

HEDEF: dict = {}  # <<hedef>> faz8_kaggle.py doldurur: {tensör: {"tur", "ozet"}}

# <<gguf_ozet>>

rapor: dict = {"basla": time.strftime("%Y-%m-%d %H:%M:%S"), "asamalar": {}, "llama_cpp": {}, "tensor": {},
               "uretim": {}}


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
        raise RuntimeError(f"{' '.join(map(str, arg[:3]))} başarısız ({r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}")
    return (r.stdout + r.stderr)[-1500:]


@asama("kurulum")
def kurulum() -> None:
    calistir(sys.executable, "-m", "pip", "install", "-q", *PAKETLER)
    if shutil.which("cmake") is None:
        calistir(sys.executable, "-m", "pip", "install", "-q", "cmake==3.31.6")
    calistir("git", "clone", "-q", "https://github.com/ggml-org/llama.cpp", "/tmp/llama.cpp")
    for ad, surum in SURUMLER.items():
        klasor = f"/tmp/llama-{ad}"
        try:  # Ollama'nın sürümü yamalı olabilir; bulunamazsa diğer sürümle devam
            calistir("git", "-C", "/tmp/llama.cpp", "worktree", "add", "-f", "--detach", klasor, surum)
            rapor["llama_cpp"][ad] = {
                "surum": surum, "tarih": calistir("git", "-C", klasor, "log", "-1", "--format=%cI").strip(),
                "etiket": calistir("git", "-C", klasor, "describe", "--tags", "--always").strip(),
                "no_mtp_var": "--no-mtp" in open(f"{klasor}/convert_hf_to_gguf.py").read()}
            calistir("cmake", "-S", klasor, "-B", f"{klasor}/build", "-DGGML_CUDA=OFF", "-DLLAMA_CURL=OFF",
                     "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_SERVER=OFF", "-DCMAKE_BUILD_TYPE=Release")
            calistir("cmake", "--build", f"{klasor}/build", "--target", "llama-quantize", "-j",
                     str(os.cpu_count() or 4))
        except Exception as e:
            rapor["llama_cpp"][ad] = {"surum": surum, "hata": f"{type(e).__name__}: {e}"[:1500]}
        kaydet()


@asama("ollama")
def ollama_kur() -> str:
    os.makedirs("/tmp/ollama", exist_ok=True)
    ad = "ollama-linux-amd64.tar.zst"
    urllib.request.urlretrieve(f"https://github.com/ollama/ollama/releases/download/{OLLAMA_SURUM}/{ad}", f"/tmp/{ad}")
    import zstandard
    with open(f"/tmp/{ad}", "rb") as g, open("/tmp/ollama.tar", "wb") as c:
        zstandard.ZstdDecompressor().copy_stream(g, c)
    calistir("tar", "-xf", "/tmp/ollama.tar", "-C", "/tmp/ollama")
    ikili = "/tmp/ollama/bin/ollama"
    subprocess.Popen([ikili, "serve"], env=ORTAM, stdout=open(f"{CIKTI}/ollama.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:11434/api/version", timeout=2)
            break
        except Exception:
            time.sleep(1)
    calistir(ikili, "pull", KUTUPHANE, env=ORTAM)
    mf = subprocess.run([ikili, "show", KUTUPHANE, "--modelfile"], env=ORTAM, capture_output=True, text=True,
                        check=True).stdout
    yonergeler = [s for s in mf.splitlines() if s.split(" ", 1)[0] in ("FROM", "TEMPLATE", "RENDERER", "PARSER",
                                                                         "PARAMETER", "SYSTEM")]
    froms = [s for s in yonergeler if s.startswith("FROM ")]
    rapor["kutuphane"] = {"modelfile": yonergeler, "ozet_ayni": KUTUPHANE_OZETI in froms[0]}
    if KUTUPHANE_OZETI not in froms[0]:
        raise SystemExit(f"kütüphane modeli bu bilgisayardakinden farklı: {froms}")
    return ikili


@asama("indir")
def indir() -> str:
    from huggingface_hub import snapshot_download

    return snapshot_download(MODEL, revision=SURUM, local_dir=f"{IS}/taban")


def hedefle(o: dict) -> dict:
    sayim: dict = {}
    for ad, h in HEDEF.items():
        t = o["tensorler"].get(ad)
        s = sayim.setdefault(h["tur"], [0, 0])
        s[1] += 1
        if t and t["tur"] == h["tur"] and t.get("ozet") == h["ozet"]:
            s[0] += 1
    return {"ture_gore_ayni_toplam": sayim, "ayni": sum(v[0] for v in sayim.values()), "toplam": len(HEDEF),
            "tensor": len(o["tensorler"]), "fazla": sorted(set(o["tensorler"]) - set(HEDEF))[:10],
            "eksik": sorted(set(HEDEF) - set(o["tensorler"]))[:10]}


def gguf_yap(ad: str, taban: str) -> str:
    klasor = f"/tmp/llama-{ad}"
    ara, cikis = f"{IS}/{ad}-bf16.gguf", f"{IS}/kendi_{ad}.gguf"
    arg = [sys.executable, f"{klasor}/convert_hf_to_gguf.py", taban, "--outtype", "bf16", "--outfile", ara]
    if rapor["llama_cpp"][ad]["no_mtp_var"]:
        arg.append("--no-mtp")
    calistir(*arg, env={**os.environ, "PYTHONPATH": f"{klasor}/gguf-py"})
    calistir(f"{klasor}/build/bin/llama-quantize", ara, cikis, "Q4_K_M", str(os.cpu_count() or 4))
    os.remove(ara)
    rapor["tensor"][ad] = hedefle(ozet(cikis))
    kaydet()
    return cikis


def kur(ikili: str, ad: str, gguf: str) -> None:
    satirlar = list(rapor["kutuphane"]["modelfile"])
    i = next(i for i, s in enumerate(satirlar) if s.startswith("FROM "))
    satirlar[i] = f"FROM {gguf}"
    with open(f"{IS}/Modelfile-{ad}", "w") as f:
        f.write("\n".join(satirlar) + "\n")
    calistir(ikili, "create", ad, "-f", f"{IS}/Modelfile-{ad}", env=ORTAM)


def istek(yol: str, govde: dict, sure: int = 3600) -> dict:
    r = urllib.request.Request(f"http://127.0.0.1:11434{yol}", data=json.dumps(govde).encode(),
                               headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=sure).read())


def bosalt() -> None:
    for m in json.loads(urllib.request.urlopen("http://127.0.0.1:11434/api/ps").read()).get("models", []):
        istek("/api/generate", {"model": m["name"], "keep_alive": 0})


@asama("uretim")
def uretim(modeller: list[str], istemler: list[dict]) -> None:
    for model in modeller:
        bosalt()
        sonuc = []
        for o in istemler:
            bas = time.time()
            y = istek("/api/generate", {"model": model, "prompt": o["istem"], "raw": True, "stream": False,
                                        "options": SECENEKLER})
            sonuc.append({"soru_id": o.get("soru_id"), "adim": o.get("adim"), "cevap": y["response"],
                          "sure_s": round(time.time() - bas, 1)})
            rapor["uretim"][model] = sonuc
            kaydet()
    ana = rapor["uretim"].get(KUTUPHANE, [])
    rapor["uretim_ozet"] = {m: {"kutuphaneyle_ayni": sum(a["cevap"] == b["cevap"] for a, b in zip(ana, v)),
                                "istem": len(v)} for m, v in rapor["uretim"].items()}


def main() -> None:
    os.makedirs(IS, exist_ok=True)
    rapor["hedef_tensor"] = len(HEDEF)
    kaydet()
    kurulum()
    ikili = ollama_kur()
    taban = indir()
    dosyalar = {}
    for ad in SURUMLER:
        if "hata" in rapor["llama_cpp"].get(ad, {"hata": "yok"}):
            continue
        try:
            dosyalar[ad] = gguf_yap(ad, taban)
        except Exception as e:
            rapor["tensor"][ad] = {"hata": f"{type(e).__name__}: {e}"[:1500]}
            kaydet()
    for ad, yol in dosyalar.items():
        kur(ikili, f"kendi_{ad}", yol)
    veri = sorted(glob.glob("/kaggle/input/**/dogrulama.jsonl", recursive=True))
    istemler = [json.loads(s) for s in open(veri[0], encoding="utf-8") if s.strip()][:ISTEM_SAYISI]
    uretim([KUTUPHANE] + [f"kendi_{ad}" for ad in dosyalar], istemler)
    rapor["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
    kaydet()


if __name__ == "__main__":
    main()
