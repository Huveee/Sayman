"""Sayman Faz 8 eğitim betiği: Kaggle'da (GPU T4) Qwen3.5-9B'ye LoRA ince ayarı.

Bu dosya Kaggle'a "script" türünde bir not defteri olarak gönderilir (scripts/faz8_kaggle.py). Girdi, aynı not
defterine bağlanan özel veri setindedir:
    ayar.json        eğitim ayarları (mod: duman ya da tam)
    ornekler.jsonl   {"istem": ..., "tamamlama": ...}; istem Ollama'nın çizdiği metin, tamamlama modelin yazacağı
    dogrulama.jsonl  (isteğe bağlı) eğitimde kullanılmayan örnekler: eğitimden önce ve sonra kayıp ve ilk token
                     doğruluğu (araç çağrısına mı metne mi başlıyor) ölçülür
Çıktılar /kaggle/working altına yazılır:
    rapor.json       sürümler, süreler, bellek, kayıp, uzunluk denemesi, epok sonu doğrulama, dönüştürme sonucu
    adapter/         son LoRA ağırlıkları (PEFT); adapter_epok{n}/ her epokun sonundaki ağırlıklar
    sayman-lora.gguf LoRA'nın llama.cpp biçimi (Ollama'da ADAPTER ile temel modelin üstüne takılır);
                     sayman-lora-epok{n}.gguf her epokun adapterı

Tam eğitimde (mod: tam) uzunluk denemesi verideki en uzun örneğe kadar çıkar; belleğe sığmayan örnekler eğitimden
önce atılır ve ayar.json'daki en_cok_atilan sayısından çoksa eğitim hiç başlamaz.

Kayıp yalnızca tamamlama tokenlarında hesaplanır. İstem ve tamamlama ayrı ayrı tokenlara bölünür; bu, Ollama'nın
istemi tokenlara bölüp modelin devamını üretmesiyle aynıdır.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
import traceback

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")  # torch'tan önce
CIKTI = "/kaggle/working"
PAKETLER = ["unsloth==2026.11.3", "unsloth_zoo==2026.11.3", "transformers==5.17.0", "trl==1.15.0", "peft==0.21.2",
            "bitsandbytes==0.50.2", "accelerate==1.15.0", "datasets==4.8.5"]  # unsloth: datasets<5
LLAMA_CPP = "b12138"
HEDEF_MODULLER = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

rapor: dict = {"basla": time.strftime("%Y-%m-%d %H:%M:%S"), "asamalar": {}}


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
            except Exception as e:
                rapor["asamalar"][ad] = {"durum": "hata", "sure_s": round(time.time() - bas, 1),
                                         "hata": f"{type(e).__name__}: {e}"[:2000],
                                         "iz": traceback.format_exc()[-4000:]}
                raise
            finally:
                kaydet()
                print(f"[{ad}] {rapor['asamalar'].get(ad)}", flush=True)
        return calis
    return sar


def girdi_klasoru() -> str:
    yollar = glob.glob("/kaggle/input/**/ayar.json", recursive=True)
    if len(yollar) != 1:
        raise SystemExit(f"ayar.json bulunamadı ya da birden fazla: {yollar}")
    return os.path.dirname(yollar[0])


@asama("kurulum")
def kurulum() -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *PAKETLER], check=True)
    import importlib.metadata as md
    rapor["surumler"] = {p: md.version(p) for p in ("torch", "transformers", "unsloth", "unsloth_zoo", "trl", "peft",
                                                    "bitsandbytes", "accelerate", "triton", "datasets", "pyarrow")
                         if _var_mi(p, md)}


def _var_mi(p, md) -> bool:
    try:
        md.version(p)
        return True
    except md.PackageNotFoundError:
        return False


def kodla(tok, o: dict, bitis_id: int) -> dict:
    p = tok(o["istem"], add_special_tokens=False)["input_ids"]
    c = tok(o["tamamlama"], add_special_tokens=False)["input_ids"]
    if not c or c[-1] != bitis_id:
        raise ValueError("tamamlama <|im_end|> tokenıyla bitmiyor")
    return {"input_ids": p + c, "attention_mask": [1] * (len(p) + len(c)), "labels": [-100] * len(p) + c,
            "istem_token": len(p), "tamamlama_token": len(c), "soru_id": o.get("soru_id"), "adim": o.get("adim")}


def kesik(k: dict) -> dict:
    """Modele verilecek girdi: logitler yalnızca tamamlamayı tahmin eden konumlarda hesaplanır (istemin son tokenı ve
    tamamlama: tut = tamamlama + 1); etiketler de aynı konumlara kesilir. Qwen3.5'in sözlüğü 248 bin tokendır; bütün
    konumların logitleri token başına ~1 MB bellek tutar ve 4.096 tokendan uzun örnekleri T4'e sığdırmaz. Kayıp
    aynıdır; kayip_denetimi aşaması bunu eğitimden önce denetler."""
    import torch

    tut = k["tamamlama_token"] + 1
    return {"input_ids": torch.tensor([k["input_ids"]], device="cuda"),
            "labels": torch.tensor([k["labels"][-tut:]], device="cuda"), "logits_to_keep": tut}


@asama("kayip_denetimi")
def kayip_denetimi(model, kodlu: list) -> None:
    """Kesik girdiyle hesaplanan kayıp, bütün konumların logitleriyle hesaplanan kayıpla aynı mı? Eğitim kipinde, en
    kısa örnekte ve tamamlaması en uzun örnekte, son 1.024 tokenda (bütün logitler belleğe sığsın diye). Aynı değilse
    eğitim başlamaz."""
    import torch

    sonuc = []
    model.train()
    with torch.no_grad():
        secilen = [min(kodlu, key=lambda k: len(k["input_ids"])), max(kodlu, key=lambda k: k["tamamlama_token"])]
        for k in secilen:
            k = {**k, "input_ids": k["input_ids"][-1024:], "labels": k["labels"][-1024:]}
            ids = torch.tensor([k["input_ids"]], device="cuda")
            tam = float(model(input_ids=ids, labels=torch.tensor([k["labels"]], device="cuda")).loss)
            kes = float(model(**kesik(k)).loss)
            sonuc.append({"tamamlama_token": k["tamamlama_token"], "tam": round(tam, 5), "kesik": round(kes, 5)})
            torch.cuda.empty_cache()
    rapor["kayip_denetimi"] = sonuc
    kaydet()
    if any(abs(x["tam"] - x["kesik"]) > 0.01 + 0.01 * abs(x["tam"]) for x in sonuc):
        raise SystemExit(f"kesik girdiyle kayıp farklı çıktı, eğitim başlamıyor: {sonuc}")


@asama("model")
def model_yukle(ayar: dict):
    from unsloth import FastLanguageModel
    import torch

    model, tok = FastLanguageModel.from_pretrained(ayar["model"], revision=ayar.get("surum"),
                                                   max_seq_length=ayar["max_uzunluk"],
                                                   load_in_4bit=ayar.get("dort_bit", True), dtype=None)
    tok = getattr(tok, "tokenizer", tok)  # görsel model işlemcisi dönerse metin tokenlayıcısı
    model = FastLanguageModel.get_peft_model(model, r=ayar["r"], lora_alpha=ayar["alpha"], lora_dropout=0,
                                             bias="none", target_modules=HEDEF_MODULLER,
                                             use_gradient_checkpointing="unsloth", random_state=3407,
                                             max_seq_length=ayar["max_uzunluk"])
    egitilen = sum(p.numel() for p in model.parameters() if p.requires_grad)
    rapor["model"] = {"ad": ayar["model"], "egitilen_parametre": egitilen, "gpu": torch.cuda.get_device_name(0),
                      "gpu_bellek_gb": round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1),
                      "bf16": torch.cuda.is_bf16_supported(), "yuklu_bellek_gb": round(torch.cuda.memory_allocated() / 2**30, 2)}
    return model, tok


def oku(yol: str) -> list[dict]:
    return [json.loads(s) for s in open(yol, encoding="utf-8") if s.strip()] if os.path.exists(yol) else []


@asama("degerlendirme")
def degerlendir(model, kodlu: list, ad: str) -> None:
    """Doğrulama örneklerinde ortalama kayıp (tamamlama tokenı başına) ve ilk tamamlama tokenının doğruluğu."""
    import torch

    model.eval()
    toplam, n, ilk_dogru = 0.0, 0, 0
    with torch.no_grad():
        for k in kodlu:
            t, p = k["tamamlama_token"], k["istem_token"]
            ids = torch.tensor([k["input_ids"]], device="cuda")
            # Logitler p-1 ... sonuna kadar: i. logit p+i konumundaki tokenı tahmin eder (son logit kullanılmaz).
            logit = model(input_ids=ids, logits_to_keep=t + 1).logits[0].float()
            if logit.shape[0] != t + 1:
                raise ValueError(f"logits_to_keep uygulanmadı: {tuple(logit.shape)}")
            toplam += float(torch.nn.functional.cross_entropy(logit[:t], ids[0, p:], reduction="sum"))
            n += t
            ilk_dogru += int(int(logit[0].argmax()) == k["input_ids"][p])
    model.train()
    rapor.setdefault("degerlendirme", {})[ad] = {
        "ornek": len(kodlu), "kayip": round(toplam / max(n, 1), 4),
        "ilk_token_dogrulugu": round(ilk_dogru / len(kodlu), 4) if kodlu else None}


@asama("veri")
def veri_hazirla(tok, ayar: dict, klasor: str):
    ornekler = oku(f"{klasor}/ornekler.jsonl")
    bitis_id = tok.convert_tokens_to_ids("<|im_end|>")
    kodlu = [kodla(tok, o, bitis_id) for o in ornekler]
    uzun = [k for k in kodlu if len(k["input_ids"]) > ayar["max_uzunluk"]]
    kodlu = [k for k in kodlu if len(k["input_ids"]) <= ayar["max_uzunluk"]]
    uzunluklar = sorted(len(k["input_ids"]) for k in kodlu)
    rapor["veri"] = {"ornek": len(ornekler), "atilan_uzun": len(uzun), "bitis_id": bitis_id,
                     "eos": tok.eos_token, "en_kisa": uzunluklar[0], "medyan": uzunluklar[len(uzunluklar) // 2],
                     "en_uzun": uzunluklar[-1], "toplam_token": sum(uzunluklar),
                     "tamamlama_token": sum(k["tamamlama_token"] for k in kodlu),
                     "ilk_istem_token": [{"soru_id": o.get("soru_id"), "adim": o.get("adim"), "istem_token": k["istem_token"],
                                          "ollama_istem_token": o.get("ollama_istem_token")}
                                         for o, k in list(zip(ornekler, kodlu))[:8]],
                     "ornek_kuyruk": tok.decode(kodlu[0]["input_ids"][-40:])}
    dogrulama = [kodla(tok, o, bitis_id) for o in oku(f"{klasor}/dogrulama.jsonl")]
    rapor["veri"]["dogrulama_ornek"] = len(dogrulama)
    return kodlu, [k for k in dogrulama if len(k["input_ids"]) <= ayar["max_uzunluk"]]


@asama("uzunluk_denemesi")
def uzunluk_denemesi(model, tok, ayar: dict, kodlu: list) -> int | None:
    """Artan uzunluklarda birer ileri-geri adım (dikkat maskesi yok, eğitimdeki gibi): bellek ve süre. Bellek
    yetmeyen ilk uzunlukta durur. Merdivenin son basamağı verideki en uzun örneğin kendi uzunluğudur; sığan en uzun
    uzunluk döner (hiçbiri sığmazsa None)."""
    import torch

    taban = max(kodlu, key=lambda k: len(k["input_ids"]))["input_ids"]
    en_uzun = len(taban)
    merdiven = sorted({n for n in ayar.get("uzunluk_merdiveni", [3072, 4096, 5120, 6144]) if n < en_uzun} | {en_uzun})
    sonuc = []
    model.train()
    for n in merdiven:
        ids = (taban * (n // len(taban) + 1))[:n]
        girdi = torch.tensor([ids], device="cuda")
        etiket = girdi[:, -201:].clone()  # tamamlama gibi son 200 token; logitler eğitimdeki gibi kesik
        etiket[:, 0] = -100
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        bas = time.time()
        try:
            kayip = model(input_ids=girdi, labels=etiket, logits_to_keep=201).loss
            kayip.backward()
            torch.cuda.synchronize()
            sonuc.append({"token": n, "sure_s": round(time.time() - bas, 1),
                          "kayip": round(float(kayip.detach()), 4),
                          "en_cok_bellek_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)})
        except torch.OutOfMemoryError:
            sonuc.append({"token": n, "bellek_yetmedi": True})
            break
        finally:
            model.zero_grad(set_to_none=True)
            del girdi, etiket
            torch.cuda.empty_cache()
        rapor["uzunluk_denemesi"] = sonuc
        kaydet()
    rapor["uzunluk_denemesi"] = sonuc
    sigan = [s["token"] for s in sonuc if not s.get("bellek_yetmedi")]
    return max(sigan) if sigan else None


@asama("egitim")
def egit(model, tok, ayar: dict, kodlu: list, dogrulama: list | None = None):
    """Eğitim. Her epok sonunda adapter /kaggle/working/adapter_epok{n} altına kaydedilir ve doğrulama kaybı ölçülür;
    süre sınırına takılırsa bile biten epoklar kaybolmaz."""
    import torch
    from datasets import Dataset
    from transformers import Trainer, TrainerCallback, TrainingArguments

    # Dikkat maskesi verilmez: toplu iş 1 örnek, dolgu yok; maske verilirse dikkat katmanı uzunluğun karesi kadar
    # bellek ayırabiliyor (duman testinde 6144 tokenda bellek bu yüzden yetmedi).
    veri = Dataset.from_list([{"input_ids": x["input_ids"], "labels": x["labels"], "tut": x["tamamlama_token"] + 1}
                              for x in kodlu])

    def topla(ornekler):  # kesik() ile aynı: logitler ve etiketler yalnızca tamamlamayı tahmin eden konumlarda
        assert len(ornekler) == 1
        o = ornekler[0]
        return {"input_ids": torch.tensor([o["input_ids"]]), "labels": torch.tensor([o["labels"][-o["tut"]:]]),
                "logits_to_keep": o["tut"]}

    import math
    toplam_adim = ayar.get("adim") or math.ceil(len(kodlu) / ayar["grad_birikim"]) * (ayar.get("epok") or 1)
    isinma = int(round(ayar.get("isinma", 0.05) * toplam_adim))
    kayitlar = []
    olcum_suresi = [0.0]  # epok sonu ölçümleri; hız hesabından çıkarılır

    class Kaydedici(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                kayitlar.append({"adim": state.global_step, "kayip": logs["loss"], "zaman": round(time.time() - bas, 1),
                                 "lr": logs.get("learning_rate"), "grad_norm": logs.get("grad_norm")})
                rapor["kayip"] = kayitlar
                kaydet()

        def on_epoch_end(self, args, state, control, model=None, **kw):
            e = int(round(state.epoch or 0))
            yol = f"{CIKTI}/adapter_epok{e}"
            model.save_pretrained(yol)
            rapor.setdefault("epoklar", []).append({"epok": e, "adim": state.global_step, "adapter": yol,
                                                    "zaman": round(time.time() - bas, 1)})
            kaydet()
            if dogrulama:
                t0 = time.time()
                try:
                    degerlendir(model, dogrulama, f"epok{e}")
                except Exception as hata:  # ölçüm başarısız olsa da eğitim sürer
                    rapor["epoklar"][-1]["degerlendirme_hatasi"] = f"{type(hata).__name__}: {hata}"[:500]
                    model.train()
                    kaydet()
                olcum_suresi[0] += time.time() - t0

    arg = TrainingArguments(
        output_dir="/tmp/cikis", per_device_train_batch_size=1, gradient_accumulation_steps=ayar["grad_birikim"],
        max_steps=ayar.get("adim") or -1, num_train_epochs=ayar.get("epok") or 1, learning_rate=ayar["lr"],
        warmup_steps=isinma, lr_scheduler_type="cosine", logging_steps=1, optim="adamw_8bit",
        weight_decay=0.0, fp16=not torch.cuda.is_bf16_supported(), bf16=torch.cuda.is_bf16_supported(),
        seed=3407, report_to="none", remove_unused_columns=False, save_strategy="steps" if ayar.get("ara_kayit") else "no",
        save_steps=ayar.get("ara_kayit") or 500, save_total_limit=2, dataloader_num_workers=0)
    torch.cuda.reset_peak_memory_stats()
    bas = time.time()
    Trainer(model=model, args=arg, train_dataset=veri, callbacks=[Kaydedici()],
            data_collator=topla).train()
    toplam_sure = time.time() - bas
    sure = toplam_sure - olcum_suresi[0]
    rapor["egitim"] = {"sure_s": round(sure, 1), "toplam_sure_s": round(toplam_sure, 1),
                       "olcum_suresi_s": round(olcum_suresi[0], 1),
                       "en_cok_bellek_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                       "adim": kayitlar[-1]["adim"] if kayitlar else 0,
                       "adim_basina_s": round(sure / max(kayitlar[-1]["adim"], 1), 1) if kayitlar else None,
                       "token_saniye": round((sum(len(x["input_ids"]) for x in kodlu) / len(kodlu) * ayar["adim"]
                                              * ayar["grad_birikim"] if ayar.get("adim") else
                                              sum(len(x["input_ids"]) for x in kodlu) * (ayar.get("epok") or 1)) / sure, 1)}


@asama("uretim_denemesi")
def uretim_denemesi(model, tok, ornekler: list):
    from unsloth import FastLanguageModel
    import torch

    FastLanguageModel.for_inference(model)
    ornekler = ornekler[:12]
    durdur = [tok.convert_tokens_to_ids("<|im_end|>"), tok.convert_tokens_to_ids("<|endoftext|>")]
    sonuc = []
    for o in ornekler:
        ids = tok(o["istem"], add_special_tokens=False, return_tensors="pt")["input_ids"].to("cuda")
        with torch.no_grad():
            cikan = model.generate(input_ids=ids, max_new_tokens=160, do_sample=False, eos_token_id=durdur)
        sonuc.append({"soru_id": o.get("soru_id"), "adim": o.get("adim"), "beklenen": o["tamamlama"][:200],
                      "uretilen": tok.decode(cikan[0][ids.shape[1]:], skip_special_tokens=False)[:300]})
    rapor["uretim_denemesi"] = sonuc


@asama("kaydet_adapter")
def adapter_kaydet(model, tok):
    model.save_pretrained(f"{CIKTI}/adapter")
    boyut = sum(os.path.getsize(p) for p in glob.glob(f"{CIKTI}/adapter/*"))
    rapor["adapter"] = {"dosyalar": sorted(os.listdir(f"{CIKTI}/adapter")), "boyut_mb": round(boyut / 2**20, 1),
                        "config": json.load(open(f"{CIKTI}/adapter/adapter_config.json"))}


@asama("lora_gguf")
def lora_gguf(ayar: dict):
    from huggingface_hub import snapshot_download

    if not os.path.isdir("/tmp/llama.cpp"):
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", LLAMA_CPP,
                        "https://github.com/ggml-org/llama.cpp", "/tmp/llama.cpp"], check=True)
    taban = snapshot_download(ayar["model"], revision=ayar.get("surum"),
                              allow_patterns=["*.json", "*.txt", "*.model", "tokenizer*"])
    ortam = {**os.environ, "PYTHONPATH": "/tmp/llama.cpp/gguf-py"}
    # Son adapter "sayman-lora.gguf"; her epokun adapterı "sayman-lora-epok{n}.gguf".
    isler = [("adapter", "sayman-lora.gguf")] + [
        (os.path.basename(k), f"sayman-lora-{os.path.basename(k).removeprefix('adapter_')}.gguf")
        for k in sorted(glob.glob(f"{CIKTI}/adapter_epok*"))]
    rapor["lora_gguf"] = {}
    basarisiz = []
    for klasor, dosya in isler:
        r = subprocess.run([sys.executable, "/tmp/llama.cpp/convert_lora_to_gguf.py", "--base", taban, "--outtype",
                            "f16", "--outfile", f"{CIKTI}/{dosya}", f"{CIKTI}/{klasor}"],
                           env=ortam, capture_output=True, text=True)
        rapor["lora_gguf"][dosya] = {"adapter": klasor, "cikis_kodu": r.returncode,
                                     "cikti": (r.stdout + r.stderr)[-1500:],
                                     "boyut_mb": round(os.path.getsize(f"{CIKTI}/{dosya}") / 2**20, 1)
                                     if os.path.exists(f"{CIKTI}/{dosya}") else None}
        if r.returncode != 0:
            basarisiz.append(dosya)
        kaydet()
    if basarisiz:
        raise RuntimeError(f"convert_lora_to_gguf başarısız: {basarisiz}")


def main() -> None:
    klasor = girdi_klasoru()
    ayar = json.load(open(f"{klasor}/ayar.json", encoding="utf-8"))
    rapor["ayar"] = ayar
    kaydet()
    kurulum()
    model, tok = model_yukle(ayar)
    kodlu, dogrulama = veri_hazirla(tok, ayar, klasor)
    kayip_denetimi(model, kodlu)
    if ayar.get("uzunluk_denemesi", True):
        try:
            sigan = uzunluk_denemesi(model, tok, ayar, kodlu)
        except Exception:
            sigan = None  # deneme kendisi hata verdiyse raporda görünür
        if ayar.get("mod") == "tam":
            # Tam eğitimde belleğe sığmayan örnek eğitimi yarıda kesmesin: sığan en uzun uzunluğun üstündekiler
            # baştan atılır. İzin verilenden çok örnek atılacaksa eğitim hiç başlamaz.
            if sigan is None:
                raise SystemExit("uzunluk denemesinde hiçbir uzunluk belleğe sığmadı")
            atilan = [{"soru_id": k["soru_id"], "adim": k["adim"], "token": len(k["input_ids"])}
                      for k in kodlu if len(k["input_ids"]) > sigan]
            rapor["veri"]["bellek_siniri"] = sigan
            rapor["veri"]["atilan_bellek"] = sorted(atilan, key=lambda x: x["token"])
            kaydet()
            if len(atilan) + rapor["veri"]["atilan_uzun"] > ayar.get("en_cok_atilan", 0):
                raise SystemExit(f"{len(atilan)} örnek belleğe sığmıyor, {rapor['veri']['atilan_uzun']} örnek "
                                 f"max_uzunluk'tan uzun; izin verilen en çok {ayar.get('en_cok_atilan', 0)}")
            kodlu = [k for k in kodlu if len(k["input_ids"]) <= sigan]
            dogrulama = [k for k in dogrulama if len(k["input_ids"]) <= sigan]
    if dogrulama:
        try:
            degerlendir(model, dogrulama, "egitimden_once")
        except Exception:
            pass
    egit(model, tok, ayar, kodlu, dogrulama)
    adapter_kaydet(model, tok)
    if dogrulama and not rapor.get("epoklar"):  # epok sonlarında ölçüldüyse son ölçüm zaten var
        try:
            degerlendir(model, dogrulama, "egitimden_sonra")
        except Exception:
            pass
    try:
        uretim_denemesi(model, tok, oku(f"{klasor}/dogrulama.jsonl") or oku(f"{klasor}/ornekler.jsonl"))
    except Exception:
        pass
    lora_gguf(ayar)
    rapor["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
    kaydet()


if __name__ == "__main__":
    main()
