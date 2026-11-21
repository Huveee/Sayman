"""Sayman Faz 8 birleştirme betiği: LoRA'yı temel modele katar, GGUF'a çevirir ve Q4_K_M'ye sıkıştırır (Kaggle, CPU).

Neden: Ollama 0.40.11 LoRA adapterını (Modelfile ADAPTER) artık desteklemiyor. İnce ayarlı model Ollama'ya, temel
modelle aynı biçimde (qwen35 mimarili, Q4_K_M) tek bir GGUF dosyası olarak girer.

Girdi: eğitim not defterinin çıktısı (kernel_sources): ADAPTER klasörü (PEFT) ve aynı adapterın LoRA GGUF'u (yalnızca
tensör adları için). Sabitleri scripts/faz8_kaggle.py doldurur.

Aşamalar ve denetimler (rapor.json):
1. taban_q4  dokunulmamış temel model de aynı yoldan geçer (dönüştür + Q4_K_M) ve tensör tensör özetlenir. Bu bilgisayarda
             Ollama'daki temel modelle karşılaştırılır: aynıysa temel ↔ ince ayarlı fark yalnızca eğitimden gelir.
2. birlestir her LoRA modülü için W' = W + (alpha/r)·B·A, parça (shard) parça; bellek bir parça kadar. ARA_TUR f32
             ise değişen ağırlıklar 32 bit kalır (küçük değişim 16 bite yuvarlanıp kaybolmasın). Bir modülde
             kaydedilen değişimin beklenen değişime benzerliği ölçülür.
3. ayarli_q4 birleşmiş model aynı yoldan geçer. Denetim: yalnızca LoRA'nın dokunduğu tensörler değişmiş olmalı, geri
             kalan her tensör ve üst bilgi temel Q4 ile bayt bayt aynı olmalı.
4. kalinti   örnek MLP tensörlerinde (ince ayarlı Q4 − temel Q4) açılır ve beklenen değişimle karşılaştırılır:
             LoRA'nın etkisinin ne kadarı Q4_K_M sıkıştırmasından sonra da duruyor?
Dönüştürmede --no-mtp kullanılır: Qwen3.5'in ek tahmin katmanı (MTP) Ollama'daki temel modelde de yoktur.
Çıktı: /kaggle/working/sayman-ayarli-q4_k_m.gguf, rapor.json
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

CIKTI = "/kaggle/working"
IS = "/tmp/is"  # büyük ara dosyalar (çıktıya girmez)
MODEL = "Qwen/Qwen3.5-9B"
SURUM = "c202236235762e1c871ad0ccb60c8ee5ba337b9a"
LLAMA_CPP = "b12138"
PAKETLER = ["transformers==5.17.0"]
ADAPTER = "adapter"  # faz8_kaggle.py doldurur
LORA_GGUF = "sayman-lora.gguf"  # faz8_kaggle.py doldurur
ARA_TUR = "f32"  # faz8_kaggle.py doldurur: dönüştürmenin ara türü (f32, bf16 ya da f16)
CIKIS_ADI = "sayman-ayarli-q4_k_m.gguf"
TABAN_Q4 = f"{IS}/taban-q4.gguf"
# Yalnızca temel: adapter gerekmez; dokunulmamış temel model aynı yoldan geçer ve çıktıya konur. Bu bilgisayarda
# "kendi temel modelimiz" olarak ölçülür (adillik denetimi aynı çıkmadığı için). faz8_kaggle.py doldurur.
YALNIZ_TABAN = False
TABAN_CIKIS_ADI = "sayman-taban-q4_k_m.gguf"
# Q4'te LoRA değişiminin ne kadarının kaldığına bakılan MLP tensörleri: (katman, HF adı, GGUF adı)
ORNEK_MODULLER = [(0, "down_proj", "ffn_down"), (15, "gate_proj", "ffn_gate"), (31, "up_proj", "ffn_up")]

# <<gguf_ozet>>

rapor: dict = {"basla": time.strftime("%Y-%m-%d %H:%M:%S"), "asamalar": {},
               "sabitler": {"model": MODEL, "surum": SURUM, "llama_cpp": LLAMA_CPP, "adapter": ADAPTER,
                            "lora_gguf": LORA_GGUF, "ara_tur": ARA_TUR, "yalniz_taban": YALNIZ_TABAN}}


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
                                         "hata": f"{type(e).__name__}: {e}"[:2000], "iz": traceback.format_exc()[-4000:]}
                raise
            finally:
                kaydet()
                print(f"[{ad}] {rapor['asamalar'].get(ad)}", flush=True)
        return calis
    return sar


def calistir(*arg, **k) -> str:
    r = subprocess.run(list(arg), capture_output=True, text=True, **k)
    if r.returncode != 0:
        raise RuntimeError(f"{arg[0]} {arg[1] if len(arg) > 1 else ''} başarısız ({r.returncode}):\n"
                           f"{(r.stdout + r.stderr)[-3000:]}")
    return (r.stdout + r.stderr)[-1500:]


def disk() -> dict:
    return {y: round(shutil.disk_usage(y).free / 2**30, 1) for y in ("/tmp", CIKTI)}


@asama("ortam")
def ortam() -> None:
    bellek = {}
    with open("/proc/meminfo") as f:
        for s in f:
            k, v = s.split(":")
            if k in ("MemTotal", "MemAvailable"):
                bellek[k] = round(int(v.split()[0]) / 2**20, 1)
    rapor["ortam"] = {"disk_bos_gb": disk(), "bellek_gb": bellek, "cpu": os.cpu_count()}
    if disk()["/tmp"] < 48:
        raise SystemExit(f"/tmp'de yer az: {disk()} (en çok ~43 GB gerekiyor)")


@asama("kurulum")
def kurulum() -> None:
    calistir(sys.executable, "-m", "pip", "install", "-q", *PAKETLER)
    if not os.path.isdir("/tmp/llama.cpp"):
        calistir("git", "clone", "-q", "--depth", "1", "--branch", LLAMA_CPP, "https://github.com/ggml-org/llama.cpp",
                 "/tmp/llama.cpp")
    if shutil.which("cmake") is None:
        calistir(sys.executable, "-m", "pip", "install", "-q", "cmake==3.31.6")
    calistir("cmake", "-S", "/tmp/llama.cpp", "-B", "/tmp/llama.cpp/build", "-DGGML_CUDA=OFF", "-DLLAMA_CURL=OFF",
             "-DLLAMA_BUILD_TESTS=OFF", "-DLLAMA_BUILD_SERVER=OFF", "-DCMAKE_BUILD_TYPE=Release")
    calistir("cmake", "--build", "/tmp/llama.cpp/build", "--target", "llama-quantize", "-j", str(os.cpu_count() or 4))
    import importlib.metadata as md
    rapor["surumler"] = {p: md.version(p) for p in ("torch", "transformers", "safetensors", "numpy")}


def girdi(desen: str) -> str:
    bulunan = sorted(glob.glob(f"/kaggle/input/**/{desen}", recursive=True))
    if len(bulunan) != 1:
        raise SystemExit(f"{desen}: tam bir tane olmalı, bulunan {bulunan}")
    return bulunan[0]


@asama("indir")
def indir() -> str:
    from huggingface_hub import snapshot_download

    yol = snapshot_download(MODEL, revision=SURUM, local_dir=f"{IS}/taban")
    rapor["taban"] = {"boyut_gb": round(sum(os.path.getsize(p) for p in glob.glob(f"{yol}/*")) / 2**30, 2),
                      "disk_bos_gb": disk()}
    return yol


def gguf_yap(hf_klasor: str, cikis: str) -> dict:
    """HF klasörü -> ARA_TUR GGUF -> Q4_K_M; ara dosya silinir, Q4 dosyasının özeti döner."""
    ara = f"{IS}/ara-{ARA_TUR}.gguf"
    ortam_ = {**os.environ, "PYTHONPATH": "/tmp/llama.cpp/gguf-py"}
    bas = time.time()
    # --no-mtp: Qwen3.5'in ek tahmin katmanı (MTP / nextn, blk.32) dışarıda kalır; Ollama'daki temel model de 32
    # katmanlıdır ve bu katmanı taşımaz. Katman yalnızca hızlandırılmış (spekülatif) üretimde kullanılır.
    calistir(sys.executable, "/tmp/llama.cpp/convert_hf_to_gguf.py", hf_klasor, "--outtype", ARA_TUR, "--no-mtp",
             "--outfile", ara, env=ortam_)
    donusturme = time.time() - bas
    bas = time.time()
    calistir("/tmp/llama.cpp/build/bin/llama-quantize", ara, cikis, "Q4_K_M", str(os.cpu_count() or 4))
    sikistirma = time.time() - bas
    ara_gb = round(os.path.getsize(ara) / 2**30, 2)
    os.remove(ara)
    o = ozet(cikis)
    import hashlib
    h = hashlib.sha256()
    with open(cikis, "rb") as f:
        for parca in iter(lambda: f.read(1 << 24), b""):
            h.update(parca)
    # sha256 ve bayt: ölçülen temel dosyanın birleştirmedeki temel dosyayla aynı olduğu bunlarla gösterilir
    o["dosya"] = {"boyut_gb": round(os.path.getsize(cikis) / 2**30, 3), "bayt": os.path.getsize(cikis),
                  "sha256": h.hexdigest(), "ara_boyut_gb": ara_gb, "donusturme_s": round(donusturme, 1),
                  "sikistirma_s": round(sikistirma, 1)}
    return o


@asama("taban_q4")
def taban_q4(taban: str) -> dict:
    o = gguf_yap(taban, TABAN_Q4)  # dosya kalinti aşamasına kadar durur
    with open(f"{CIKTI}/taban_q4_ozet.json", "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False)
    rapor["taban_q4"] = {"dosya": o["dosya"], "tensor": len(o["tensorler"])}
    return o


def _lora_ayari(klasor: str) -> float:
    ayar = json.load(open(f"{klasor}/adapter_config.json", encoding="utf-8"))
    sorunlar = [ad for ad, kotu in (
        ("peft_type", ayar.get("peft_type") != "LORA"), ("use_rslora", bool(ayar.get("use_rslora"))),
        ("use_dora", bool(ayar.get("use_dora"))), ("fan_in_fan_out", bool(ayar.get("fan_in_fan_out"))),
        ("modules_to_save", bool(ayar.get("modules_to_save"))), ("bias", ayar.get("bias", "none") != "none"),
        ("rank_pattern", bool(ayar.get("rank_pattern"))), ("alpha_pattern", bool(ayar.get("alpha_pattern"))))
        if kotu]
    if sorunlar:
        raise SystemExit(f"beklenmeyen adapter ayarı: {sorunlar}")
    return ayar["lora_alpha"] / ayar["r"]


@asama("birlestir")
def birlestir(taban: str, adapter: str) -> None:
    import torch
    from safetensors.torch import load_file, save_file

    olcek = _lora_ayari(adapter)
    lora = load_file(f"{adapter}/adapter_model.safetensors")
    moduller = sorted({k.rsplit(".lora_", 1)[0] for k in lora if ".lora_" in k})
    hedef = {}
    for m in moduller:
        ad = m.removeprefix("base_model.model.") + ".weight"
        hedef[ad] = (lora[f"{m}.lora_A.weight"], lora[f"{m}.lora_B.weight"])
    dizin = json.load(open(f"{taban}/model.safetensors.index.json", encoding="utf-8"))["weight_map"]
    eksik = [k for k in hedef if k not in dizin]
    if eksik:
        raise SystemExit(f"temel modelde bulunmayan LoRA hedefleri: {eksik[:5]} (toplam {len(eksik)})")
    os.makedirs(f"{IS}/birlesik", exist_ok=True)
    for p in os.listdir(taban):  # yapılandırma, tokenlayıcı, dizin vb.
        if not p.endswith(".safetensors") and os.path.isfile(f"{taban}/{p}"):
            shutil.copy(f"{taban}/{p}", f"{IS}/birlesik/{p}")
    ornekler = {f"model.language_model.layers.{i}.mlp.{hf}.weight" for i, hf, _ in ORNEK_MODULLER}
    os.makedirs(f"{IS}/kalinti", exist_ok=True)
    birlesen, yuvarlama = 0, None
    for parca in sorted(set(dizin.values())):
        t = load_file(f"{taban}/{parca}")
        for ad in [k for k in t if k in hedef]:
            a, b = hedef[ad]
            eski = t[ad]
            delta = olcek * (b.float() @ a.float())
            if delta.shape != eski.shape:
                raise SystemExit(f"{ad}: boyut uymuyor {tuple(delta.shape)} ↔ {tuple(eski.shape)}")
            # ARA_TUR f32 ise birleşmiş ağırlık 32 bit kalır: küçük LoRA değişimi 16 bite yuvarlanıp kaybolmaz,
            # tek yuvarlama Q4_K_M sıkıştırmasında olur. bf16/f16'da ağırlık temelin kendi türüne döner.
            yeni = eski.float() + delta
            if ARA_TUR != "f32":
                yeni = yeni.to(eski.dtype)
            if ad in ornekler:
                torch.save(delta, f"{IS}/kalinti/{ad}.pt")
            if yuvarlama is None:  # kaydedilen türde değişim, beklenen değişime ne kadar benziyor?
                kalan = (yeni.float() - eski.float()).flatten()
                d = delta.flatten()
                yuvarlama = {"tensor": ad, "tur": str(yeni.dtype), "olcek": olcek,
                           "benzerlik": round(float(torch.nn.functional.cosine_similarity(kalan, d, dim=0)), 4),
                           "norm_orani": round(float(kalan.norm() / d.norm()), 4),
                           "delta_ort_mutlak": float(d.abs().mean()), "agirlik_ort_mutlak": float(eski.float().abs().mean())}
            t[ad] = yeni
            birlesen += 1
        save_file(t, f"{IS}/birlesik/{parca}", metadata={"format": "pt"})
        del t
        os.remove(f"{taban}/{parca}")  # yer açmak için; temelin Q4'ü daha önce çıkarıldı
    if birlesen != len(hedef):
        raise SystemExit(f"{len(hedef)} modülden {birlesen} tanesi birleşti")
    rapor["birlestir"] = {"modul": len(hedef), "birlesen": birlesen, "yuvarlama": yuvarlama, "disk_bos_gb": disk()}


@asama("ayarli_q4")
def ayarli_q4() -> dict:
    o = gguf_yap(f"{IS}/birlesik", f"{CIKTI}/{CIKIS_ADI}")
    shutil.rmtree(f"{IS}/birlesik")
    with open(f"{CIKTI}/ayarli_q4_ozet.json", "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False)
    rapor["ayarli_q4"] = {"dosya": o["dosya"], "tensor": len(o["tensorler"])}
    return o


@asama("denetim")
def denetim(taban_o: dict | None, ayarli_o: dict, lora_gguf: str) -> None:
    beklenen = sorted({t.rsplit(".lora_", 1)[0] for t in ozet(lora_gguf, veri_ozeti=False)["tensorler"]})
    sonuc = {"lora_hedefi": len(beklenen)}
    if taban_o is not None:
        k = karsilastir(taban_o, ayarli_o)
        degisen = sorted(k["veri_farki"])
        sonuc.update({"degisen": len(degisen), "ayni": k["ayni_tensor"], "tensor": k["tensor"],
                      "beklenmeyen_degisen": sorted(set(degisen) - set(beklenen))[:20],
                      "degismeyen_hedef": sorted(set(beklenen) - set(degisen))[:20],
                      "kv_farki": k["kv_farki"], "tur_farki": k["tur_farki"],
                      "yalniz_temelde": k["yalniz_ilkinde"], "yalniz_ayarlida": k["yalniz_ikincide"]})
        sonuc["gecti"] = (set(degisen) == set(beklenen) and not k["kv_farki"] and not k["tur_farki"]
                          and not k["yalniz_ilkinde"] and not k["yalniz_ikincide"])
    rapor["denetim"] = sonuc


@asama("kalinti")
def kalinti() -> None:
    """Q4_K_M'de LoRA değişiminin ne kadarı kaldı? Örnek MLP tensörlerinde: (ince ayarlı Q4 − temel Q4), açılınca
    beklenen değişime (ölçek·B·A) ne kadar benziyor (kosinüs) ve büyüklüğü ne kadar (norm oranı)?"""
    import numpy as np
    import torch

    sys.path.insert(0, "/tmp/llama.cpp/gguf-py")
    from gguf import GGUFReader
    from gguf.quants import dequantize

    def tensorler(yol):
        return {t.name: t for t in GGUFReader(yol).tensors}

    tb, ta = tensorler(TABAN_Q4), tensorler(f"{CIKTI}/{CIKIS_ADI}")
    sonuc = []
    for i, hf, gg in ORNEK_MODULLER:
        ad, g = f"model.language_model.layers.{i}.mlp.{hf}.weight", f"blk.{i}.{gg}.weight"
        yol = f"{IS}/kalinti/{ad}.pt"
        if not os.path.exists(yol) or g not in tb:
            sonuc.append({"tensor": g, "atlandi": True})
            continue
        d = torch.load(yol).numpy().astype(np.float32)
        b = dequantize(tb[g].data, tb[g].tensor_type).astype(np.float32)
        a = dequantize(ta[g].data, ta[g].tensor_type).astype(np.float32)
        if b.shape != d.shape:
            raise ValueError(f"{g}: açılan boyut {b.shape}, beklenen değişim {d.shape}")
        fark = (a - b).ravel()
        dv = d.ravel()
        sonuc.append({"tensor": g, "tur": tb[g].tensor_type.name,
                      "kosinus": round(float(fark @ dv / (np.linalg.norm(fark) * np.linalg.norm(dv) + 1e-30)), 4),
                      "norm_orani": round(float(np.linalg.norm(fark) / (np.linalg.norm(dv) + 1e-30)), 3),
                      "degisen_oran": round(float(np.mean(a != b)), 4),
                      "delta_ort_mutlak": float(np.abs(dv).mean())})
    rapor["kalinti"] = sonuc


def main() -> None:
    os.makedirs(IS, exist_ok=True)
    kaydet()
    ortam()
    kurulum()
    if YALNIZ_TABAN:
        taban_q4(indir())
        shutil.move(TABAN_Q4, f"{CIKTI}/{TABAN_CIKIS_ADI}")
        rapor["taban_q4"]["cikti"] = TABAN_CIKIS_ADI
        rapor["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
        kaydet()
        return
    adapter = os.path.dirname(girdi(f"{ADAPTER}/adapter_config.json"))
    lora_gguf = girdi(LORA_GGUF)
    rapor["girdi"] = {"adapter": adapter, "lora_gguf": lora_gguf}
    taban = indir()
    taban_o = taban_q4(taban)
    birlestir(taban, adapter)
    ayarli_o = ayarli_q4()
    denetim(taban_o, ayarli_o, lora_gguf)
    try:
        kalinti()
    except Exception:
        pass  # yalnızca bilgi; hata raporda görünür, çıktı dosyası etkilenmez
    os.remove(TABAN_Q4)
    rapor["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
    kaydet()


if __name__ == "__main__":
    main()
