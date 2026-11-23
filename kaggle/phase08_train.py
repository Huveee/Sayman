"""Sayman Phase 8 training script: LoRA fine-tuning of Qwen3.5-9B on Kaggle (GPU T4).

This file is submitted to Kaggle as a "script"-type notebook (scripts/phase08_kaggle.py). The input is in the private
dataset attached to the same notebook:
    ayar.json        training settings (mode: duman (smoke) or tam (full))
    ornekler.jsonl   {"istem": ..., "tamamlama": ...}; istem is the text Ollama renders, tamamlama is what the model
                     will write
    dogrulama.jsonl  (optional) samples not used in training: before and after training the loss and the first-token
                     accuracy (does it start with a tool call or with text) are measured
The outputs are written under /kaggle/working:
    rapor.json       versions, durations, memory, loss, length trial, end-of-epoch validation, conversion result
    adapter/         final LoRA weights (PEFT); adapter_epok{n}/ the weights at the end of each epoch
    sayman-lora.gguf the LoRA in llama.cpp format (attached on top of the base model with ADAPTER in Ollama);
                     sayman-lora-epok{n}.gguf the adapter of each epoch

In full training (mode: tam) the length trial goes up to the longest sample in the data; samples that do not fit in
memory are dropped before training, and if there are more of them than the en_cok_atilan count in ayar.json, training
does not start at all.

The loss is computed only on the completion tokens. The prompt and the completion are tokenized separately; this is the
same as Ollama tokenizing the prompt and the model generating the continuation.
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
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")  # before torch
OUTPUT = "/kaggle/working"
PACKAGES = ["unsloth==2026.11.3", "unsloth_zoo==2026.11.3", "transformers==5.17.0", "trl==1.15.0", "peft==0.21.2",
            "bitsandbytes==0.50.2", "accelerate==1.15.0", "datasets==4.8.5"]  # unsloth: datasets<5
LLAMA_CPP = "b12138"
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

report: dict = {"basla": time.strftime("%Y-%m-%d %H:%M:%S"), "asamalar": {}}


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
            except Exception as e:
                report["asamalar"][name] = {"durum": "hata", "sure_s": round(time.time() - start, 1),
                                         "hata": f"{type(e).__name__}: {e}"[:2000],
                                         "iz": traceback.format_exc()[-4000:]}
                raise
            finally:
                save()
                print(f"[{name}] {report['asamalar'].get(name)}", flush=True)
        return wrapper
    return decorate


def input_dir() -> str:
    paths = glob.glob("/kaggle/input/**/ayar.json", recursive=True)
    if len(paths) != 1:
        raise SystemExit(f"ayar.json bulunamadı ya da birden fazla: {paths}")
    return os.path.dirname(paths[0])


@stage("kurulum")
def install_packages() -> None:
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *PACKAGES], check=True)
    import importlib.metadata as md
    report["surumler"] = {p: md.version(p) for p in ("torch", "transformers", "unsloth", "unsloth_zoo", "trl", "peft",
                                                    "bitsandbytes", "accelerate", "triton", "datasets", "pyarrow")
                         if _is_installed(p, md)}


def _is_installed(p, md) -> bool:
    try:
        md.version(p)
        return True
    except md.PackageNotFoundError:
        return False


def encode(tok, o: dict, end_id: int) -> dict:
    p = tok(o["istem"], add_special_tokens=False)["input_ids"]
    c = tok(o["tamamlama"], add_special_tokens=False)["input_ids"]
    if not c or c[-1] != end_id:
        raise ValueError("tamamlama <|im_end|> tokenıyla bitmiyor")
    return {"input_ids": p + c, "attention_mask": [1] * (len(p) + len(c)), "labels": [-100] * len(p) + c,
            "istem_token": len(p), "tamamlama_token": len(c), "soru_id": o.get("soru_id"), "adim": o.get("adim")}


def truncate_inputs(k: dict) -> dict:
    """The input given to the model: logits are computed only at the positions that predict the completion (the last token
    of the prompt and the completion: keep = completion + 1); the labels are cut to the same positions. Qwen3.5's
    vocabulary is 248 thousand tokens; the logits of all positions take ~1 MB of memory per token and do not fit
    examples longer than 4,096 tokens on the T4. The loss is the same; the loss_check stage (report key
    `kayip_denetimi`) verifies this before training."""
    import torch

    keep_count = k["tamamlama_token"] + 1
    return {"input_ids": torch.tensor([k["input_ids"]], device="cuda"),
            "labels": torch.tensor([k["labels"][-keep_count:]], device="cuda"), "logits_to_keep": keep_count}


@stage("kayip_denetimi")
def loss_check(model, encoded: list) -> None:
    """Is the loss computed with the truncated input the same as the loss computed with the logits of all positions? In
    training mode, on the shortest sample and on the sample with the longest completion, on the last 1,024 tokens (so
    that all the logits fit in memory). If not the same, training does not start."""
    import torch

    result = []
    model.train()
    with torch.no_grad():
        selected = [min(encoded, key=lambda k: len(k["input_ids"])), max(encoded, key=lambda k: k["tamamlama_token"])]
        for k in selected:
            k = {**k, "input_ids": k["input_ids"][-1024:], "labels": k["labels"][-1024:]}
            ids = torch.tensor([k["input_ids"]], device="cuda")
            full_loss = float(model(input_ids=ids, labels=torch.tensor([k["labels"]], device="cuda")).loss)
            truncated_loss = float(model(**truncate_inputs(k)).loss)
            result.append({"tamamlama_token": k["tamamlama_token"], "tam": round(full_loss, 5), "kesik": round(truncated_loss, 5)})
            torch.cuda.empty_cache()
    report["kayip_denetimi"] = result
    save()
    if any(abs(x["tam"] - x["kesik"]) > 0.01 + 0.01 * abs(x["tam"]) for x in result):
        raise SystemExit(f"kesik girdiyle kayıp farklı çıktı, eğitim başlamıyor: {result}")


@stage("model")
def load_model(settings: dict):
    from unsloth import FastLanguageModel
    import torch

    model, tok = FastLanguageModel.from_pretrained(settings["model"], revision=settings.get("surum"),
                                                   max_seq_length=settings["max_uzunluk"],
                                                   load_in_4bit=settings.get("dort_bit", True), dtype=None)
    tok = getattr(tok, "tokenizer", tok)  # if the vision model's processor is returned, the text tokenizer
    model = FastLanguageModel.get_peft_model(model, r=settings["r"], lora_alpha=settings["alpha"], lora_dropout=0,
                                             bias="none", target_modules=TARGET_MODULES,
                                             use_gradient_checkpointing="unsloth", random_state=3407,
                                             max_seq_length=settings["max_uzunluk"])
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    report["model"] = {"ad": settings["model"], "egitilen_parametre": trainable, "gpu": torch.cuda.get_device_name(0),
                      "gpu_bellek_gb": round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1),
                      "bf16": torch.cuda.is_bf16_supported(), "yuklu_bellek_gb": round(torch.cuda.memory_allocated() / 2**30, 2)}
    return model, tok


def read_jsonl(path: str) -> list[dict]:
    return [json.loads(s) for s in open(path, encoding="utf-8") if s.strip()] if os.path.exists(path) else []


@stage("degerlendirme")
def evaluate(model, encoded: list, name: str) -> None:
    """Mean loss on the validation samples (per completion token) and the accuracy of the first completion token."""
    import torch

    model.eval()
    total, n, first_correct = 0.0, 0, 0
    with torch.no_grad():
        for k in encoded:
            t, p = k["tamamlama_token"], k["istem_token"]
            ids = torch.tensor([k["input_ids"]], device="cuda")
            # Logits from p-1 to the end: the i-th logit predicts the token at position p+i (the last logit is not used).
            logit = model(input_ids=ids, logits_to_keep=t + 1).logits[0].float()
            if logit.shape[0] != t + 1:
                raise ValueError(f"logits_to_keep uygulanmadı: {tuple(logit.shape)}")
            total += float(torch.nn.functional.cross_entropy(logit[:t], ids[0, p:], reduction="sum"))
            n += t
            first_correct += int(int(logit[0].argmax()) == k["input_ids"][p])
    model.train()
    report.setdefault("degerlendirme", {})[name] = {
        "ornek": len(encoded), "kayip": round(total / max(n, 1), 4),
        "ilk_token_dogrulugu": round(first_correct / len(encoded), 4) if encoded else None}


@stage("veri")
def prepare_data(tok, settings: dict, folder: str):
    samples = read_jsonl(f"{folder}/ornekler.jsonl")
    end_id = tok.convert_tokens_to_ids("<|im_end|>")
    encoded = [encode(tok, o, end_id) for o in samples]
    too_long = [k for k in encoded if len(k["input_ids"]) > settings["max_uzunluk"]]
    encoded = [k for k in encoded if len(k["input_ids"]) <= settings["max_uzunluk"]]
    lengths = sorted(len(k["input_ids"]) for k in encoded)
    report["veri"] = {"ornek": len(samples), "atilan_uzun": len(too_long), "bitis_id": end_id,
                     "eos": tok.eos_token, "en_kisa": lengths[0], "medyan": lengths[len(lengths) // 2],
                     "en_uzun": lengths[-1], "toplam_token": sum(lengths),
                     "tamamlama_token": sum(k["tamamlama_token"] for k in encoded),
                     "ilk_istem_token": [{"soru_id": o.get("soru_id"), "adim": o.get("adim"), "istem_token": k["istem_token"],
                                          "ollama_istem_token": o.get("ollama_istem_token")}
                                         for o, k in list(zip(samples, encoded))[:8]],
                     "ornek_kuyruk": tok.decode(encoded[0]["input_ids"][-40:])}
    validation = [encode(tok, o, end_id) for o in read_jsonl(f"{folder}/dogrulama.jsonl")]
    report["veri"]["dogrulama_ornek"] = len(validation)
    return encoded, [k for k in validation if len(k["input_ids"]) <= settings["max_uzunluk"]]


@stage("uzunluk_denemesi")
def length_trial(model, tok, settings: dict, encoded: list) -> int | None:
    """One forward-backward step each at increasing lengths (no attention mask, as in training): memory and duration. It
    stops at the first length memory cannot handle. The last step of the ladder is the length of the longest sample in
    the data; the longest length that fits is returned (None if none fits)."""
    import torch

    base = max(encoded, key=lambda k: len(k["input_ids"]))["input_ids"]
    longest = len(base)
    ladder = sorted({n for n in settings.get("uzunluk_merdiveni", [3072, 4096, 5120, 6144]) if n < longest} | {longest})
    result = []
    model.train()
    for n in ladder:
        ids = (base * (n // len(base) + 1))[:n]
        inputs = torch.tensor([ids], device="cuda")
        labels = inputs[:, -201:].clone()  # the last 200 tokens like a completion; logits truncated as in training
        labels[:, 0] = -100
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        start = time.time()
        try:
            loss = model(input_ids=inputs, labels=labels, logits_to_keep=201).loss
            loss.backward()
            torch.cuda.synchronize()
            result.append({"token": n, "sure_s": round(time.time() - start, 1),
                          "kayip": round(float(loss.detach()), 4),
                          "en_cok_bellek_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2)})
        except torch.OutOfMemoryError:
            result.append({"token": n, "bellek_yetmedi": True})
            break
        finally:
            model.zero_grad(set_to_none=True)
            del inputs, labels
            torch.cuda.empty_cache()
        report["uzunluk_denemesi"] = result
        save()
    report["uzunluk_denemesi"] = result
    fits = [s["token"] for s in result if not s.get("bellek_yetmedi")]
    return max(fits) if fits else None


@stage("egitim")
def train(model, tok, settings: dict, encoded: list, validation: list | None = None):
    """Training. At the end of every epoch the adapter is saved under /kaggle/working/adapter_epok{n} and the validation
    loss is measured; even if the time limit is hit, the finished epochs are not lost."""
    import torch
    from datasets import Dataset
    from transformers import Trainer, TrainerCallback, TrainingArguments

    # No attention mask is given: the batch is 1 sample, no padding; if a mask is given the attention layer can allocate
    # memory proportional to the square of the length (in the smoke test memory ran out at 6144 tokens for this reason).
    dataset = Dataset.from_list([{"input_ids": x["input_ids"], "labels": x["labels"], "tut": x["tamamlama_token"] + 1}
                              for x in encoded])

    def collate(samples):  # same as truncate_inputs(): logits and labels only at the positions that predict the completion
        assert len(samples) == 1
        o = samples[0]
        return {"input_ids": torch.tensor([o["input_ids"]]), "labels": torch.tensor([o["labels"][-o["tut"]:]]),
                "logits_to_keep": o["tut"]}

    import math
    total_steps = settings.get("adim") or math.ceil(len(encoded) / settings["grad_birikim"]) * (settings.get("epok") or 1)
    warmup = int(round(settings.get("isinma", 0.05) * total_steps))
    records = []
    eval_time = [0.0]  # end-of-epoch measurements; subtracted from the speed computation

    class Recorder(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kw):
            if logs and "loss" in logs:
                records.append({"adim": state.global_step, "kayip": logs["loss"], "zaman": round(time.time() - start, 1),
                                 "lr": logs.get("learning_rate"), "grad_norm": logs.get("grad_norm")})
                report["kayip"] = records
                save()

        def on_epoch_end(self, args, state, control, model=None, **kw):
            e = int(round(state.epoch or 0))
            path = f"{OUTPUT}/adapter_epok{e}"
            model.save_pretrained(path)
            report.setdefault("epoklar", []).append({"epok": e, "adim": state.global_step, "adapter": path,
                                                    "zaman": round(time.time() - start, 1)})
            save()
            if validation:
                t0 = time.time()
                try:
                    evaluate(model, validation, f"epok{e}")
                except Exception as error:  # training continues even if the measurement fails
                    report["epoklar"][-1]["degerlendirme_hatasi"] = f"{type(error).__name__}: {error}"[:500]
                    model.train()
                    save()
                eval_time[0] += time.time() - t0

    training_args = TrainingArguments(
        output_dir="/tmp/cikis", per_device_train_batch_size=1, gradient_accumulation_steps=settings["grad_birikim"],
        max_steps=settings.get("adim") or -1, num_train_epochs=settings.get("epok") or 1, learning_rate=settings["lr"],
        warmup_steps=warmup, lr_scheduler_type="cosine", logging_steps=1, optim="adamw_8bit",
        weight_decay=0.0, fp16=not torch.cuda.is_bf16_supported(), bf16=torch.cuda.is_bf16_supported(),
        seed=3407, report_to="none", remove_unused_columns=False, save_strategy="steps" if settings.get("ara_kayit") else "no",
        save_steps=settings.get("ara_kayit") or 500, save_total_limit=2, dataloader_num_workers=0)
    torch.cuda.reset_peak_memory_stats()
    start = time.time()
    Trainer(model=model, args=training_args, train_dataset=dataset, callbacks=[Recorder()],
            data_collator=collate).train()
    total_s = time.time() - start
    duration = total_s - eval_time[0]
    report["egitim"] = {"sure_s": round(duration, 1), "toplam_sure_s": round(total_s, 1),
                       "olcum_suresi_s": round(eval_time[0], 1),
                       "en_cok_bellek_gb": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                       "adim": records[-1]["adim"] if records else 0,
                       "adim_basina_s": round(duration / max(records[-1]["adim"], 1), 1) if records else None,
                       "token_saniye": round((sum(len(x["input_ids"]) for x in encoded) / len(encoded) * settings["adim"]
                                              * settings["grad_birikim"] if settings.get("adim") else
                                              sum(len(x["input_ids"]) for x in encoded) * (settings.get("epok") or 1)) / duration, 1)}


@stage("uretim_denemesi")
def generation_trial(model, tok, samples: list):
    from unsloth import FastLanguageModel
    import torch

    FastLanguageModel.for_inference(model)
    samples = samples[:12]
    stop_ids = [tok.convert_tokens_to_ids("<|im_end|>"), tok.convert_tokens_to_ids("<|endoftext|>")]
    result = []
    for o in samples:
        ids = tok(o["istem"], add_special_tokens=False, return_tensors="pt")["input_ids"].to("cuda")
        with torch.no_grad():
            generated = model.generate(input_ids=ids, max_new_tokens=160, do_sample=False, eos_token_id=stop_ids)
        result.append({"soru_id": o.get("soru_id"), "adim": o.get("adim"), "beklenen": o["tamamlama"][:200],
                      "uretilen": tok.decode(generated[0][ids.shape[1]:], skip_special_tokens=False)[:300]})
    report["uretim_denemesi"] = result


@stage("kaydet_adapter")
def save_adapter(model, tok):
    model.save_pretrained(f"{OUTPUT}/adapter")
    size = sum(os.path.getsize(p) for p in glob.glob(f"{OUTPUT}/adapter/*"))
    report["adapter"] = {"dosyalar": sorted(os.listdir(f"{OUTPUT}/adapter")), "boyut_mb": round(size / 2**20, 1),
                        "config": json.load(open(f"{OUTPUT}/adapter/adapter_config.json"))}


@stage("lora_gguf")
def convert_lora_to_gguf(settings: dict):
    from huggingface_hub import snapshot_download

    if not os.path.isdir("/tmp/llama.cpp"):
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--branch", LLAMA_CPP,
                        "https://github.com/ggml-org/llama.cpp", "/tmp/llama.cpp"], check=True)
    base = snapshot_download(settings["model"], revision=settings.get("surum"),
                              allow_patterns=["*.json", "*.txt", "*.model", "tokenizer*"])
    env = {**os.environ, "PYTHONPATH": "/tmp/llama.cpp/gguf-py"}
    # The final adapter is "sayman-lora.gguf"; the adapter of each epoch is "sayman-lora-epok{n}.gguf".
    jobs = [("adapter", "sayman-lora.gguf")] + [
        (os.path.basename(k), f"sayman-lora-{os.path.basename(k).removeprefix('adapter_')}.gguf")
        for k in sorted(glob.glob(f"{OUTPUT}/adapter_epok*"))]
    report["lora_gguf"] = {}
    failed = []
    for folder, file in jobs:
        r = subprocess.run([sys.executable, "/tmp/llama.cpp/convert_lora_to_gguf.py", "--base", base, "--outtype",
                            "f16", "--outfile", f"{OUTPUT}/{file}", f"{OUTPUT}/{folder}"],
                           env=env, capture_output=True, text=True)
        report["lora_gguf"][file] = {"adapter": folder, "cikis_kodu": r.returncode,
                                     "cikti": (r.stdout + r.stderr)[-1500:],
                                     "boyut_mb": round(os.path.getsize(f"{OUTPUT}/{file}") / 2**20, 1)
                                     if os.path.exists(f"{OUTPUT}/{file}") else None}
        if r.returncode != 0:
            failed.append(file)
        save()
    if failed:
        raise RuntimeError(f"convert_lora_to_gguf başarısız: {failed}")


def main() -> None:
    folder = input_dir()
    settings = json.load(open(f"{folder}/ayar.json", encoding="utf-8"))
    report["ayar"] = settings
    save()
    install_packages()
    model, tok = load_model(settings)
    encoded, validation = prepare_data(tok, settings, folder)
    loss_check(model, encoded)
    if settings.get("uzunluk_denemesi", True):
        try:
            fits = length_trial(model, tok, settings, encoded)
        except Exception:
            fits = None  # if the trial itself raised an error, it shows in the report
        if settings.get("mod") == "tam":
            # In full training an example that does not fit in memory must not interrupt training: those above the longest length
            # that fits are dropped up front. If more examples than allowed would be dropped, training does not start at all.
            if fits is None:
                raise SystemExit("uzunluk denemesinde hiçbir uzunluk belleğe sığmadı")
            dropped = [{"soru_id": k["soru_id"], "adim": k["adim"], "token": len(k["input_ids"])}
                      for k in encoded if len(k["input_ids"]) > fits]
            report["veri"]["bellek_siniri"] = fits
            report["veri"]["atilan_bellek"] = sorted(dropped, key=lambda x: x["token"])
            save()
            if len(dropped) + report["veri"]["atilan_uzun"] > settings.get("en_cok_atilan", 0):
                raise SystemExit(f"{len(dropped)} örnek belleğe sığmıyor, {report['veri']['atilan_uzun']} örnek "
                                 f"max_uzunluk'tan uzun; izin verilen en çok {settings.get('en_cok_atilan', 0)}")
            encoded = [k for k in encoded if len(k["input_ids"]) <= fits]
            validation = [k for k in validation if len(k["input_ids"]) <= fits]
    if validation:
        try:
            evaluate(model, validation, "egitimden_once")
        except Exception:
            pass
    train(model, tok, settings, encoded, validation)
    save_adapter(model, tok)
    if validation and not report.get("epoklar"):  # if it was measured at the epoch ends, the last measurement already exists
        try:
            evaluate(model, validation, "egitimden_sonra")
        except Exception:
            pass
    try:
        generation_trial(model, tok, read_jsonl(f"{folder}/dogrulama.jsonl") or read_jsonl(f"{folder}/ornekler.jsonl"))
    except Exception:
        pass
    convert_lora_to_gguf(settings)
    report["bitis"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save()


if __name__ == "__main__":
    main()
