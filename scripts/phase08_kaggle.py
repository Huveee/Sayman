"""A small helper that talks to Kaggle: uploads a private dataset, pushes the training notebook, fetches its status and output.

The credentials are read from KAGGLE_USERNAME and KAGGLE_API_TOKEN in the .env file and are given only to the environment of
the kaggle command; they are written nowhere, and if the token appears in the printed output it is masked. Datasets and
notebooks are private.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08_kaggle.py veri --klasor data\\egitim\\duman --slug sayman-egitim-duman
    .venv\\Scripts\\python -I scripts\\phase08_kaggle.py itele --slug sayman-faz8-duman --veri sayman-egitim-duman
    .venv\\Scripts\\python -I scripts\\phase08_kaggle.py durum --slug sayman-faz8-duman
    .venv\\Scripts\\python -I scripts\\phase08_kaggle.py cikti --slug sayman-faz8-duman --desen "rapor\\.json"
    .venv\\Scripts\\python -I scripts\\phase08_kaggle.py birlestir --slug sayman-faz8-birlestir --kaynak sayman-faz8-tam ^
        --adapter adapter_epok2 --lora-gguf sayman-lora-epok2.gguf
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KAGGLE = ROOT / ".venv" / "Scripts" / "kaggle.exe"
TRAIN_SCRIPT = ROOT / "kaggle" / "phase08_train.py"
MERGE_SCRIPT = ROOT / "kaggle" / "phase08_merge.py"
GGUF_SUMMARY = ROOT / "src" / "sayman" / "training" / "gguf_summary.py"
QUANTIZATION_CHECK = ROOT / "kaggle" / "phase08_quantization_check.py"
COMPATIBILITY_CHECK = ROOT / "kaggle" / "phase08_compatibility_check.py"
OLLAMA_SUMMARY = ROOT / "data" / "egitim" / "calisma" / "ollama_taban_ozet.json"  # written by phase08_compare_gguf.py


def credentials() -> tuple[str, dict]:
    from dotenv import dotenv_values

    v = dotenv_values(ROOT / ".env")
    name, token = v.get("KAGGLE_USERNAME"), v.get("KAGGLE_API_TOKEN")
    if not name or not token:
        raise SystemExit(".env dosyasında KAGGLE_USERNAME ve KAGGLE_API_TOKEN olmalı")
    return name, {**os.environ, "KAGGLE_USERNAME": name, "KAGGLE_API_TOKEN": token}


def kaggle(*arg: str) -> subprocess.CompletedProcess:
    _, env = credentials()
    r = subprocess.run([str(KAGGLE), *arg], env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    secret = env["KAGGLE_API_TOKEN"]
    r.stdout, r.stderr = r.stdout.replace(secret, "<gizli>"), r.stderr.replace(secret, "<gizli>")
    print((r.stdout + r.stderr).strip())
    return r


def upload_dataset(directory: Path, slug: str) -> None:
    name, _ = credentials()
    (directory / "dataset-metadata.json").write_text(json.dumps(
        {"title": slug, "id": f"{name}/{slug}", "licenses": [{"name": "CC0-1.0"}]}, indent=1), encoding="utf-8")
    r = kaggle("datasets", "status", f"{name}/{slug}")
    if r.returncode == 0 and "ready" in (r.stdout + r.stderr).lower():
        kaggle("datasets", "version", "-p", str(directory), "-m", "güncelleme", "-d")
    else:
        kaggle("datasets", "create", "-p", str(directory))  # default: private


def push_training(slug: str, dataset_slug: str) -> None:
    name, _ = credentials()
    directory = ROOT / "data" / "egitim" / "kernel" / slug
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    shutil.copy(TRAIN_SCRIPT, directory / TRAIN_SCRIPT.name)
    (directory / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{name}/{slug}", "title": slug, "code_file": TRAIN_SCRIPT.name, "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": True, "enable_internet": True, "machine_shape": "NvidiaTeslaT4",
        "dataset_sources": [f"{name}/{dataset_slug}"], "competition_sources": [], "kernel_sources": [],
        "model_sources": []}, indent=1), encoding="utf-8")
    kaggle("kernels", "push", "-p", str(directory))


def merge_script(adapter: str, lora_gguf: str, intermediate_dtype: str, base_only: bool = False) -> str:
    """Merge script: the constants are filled in and the GGUF summary module is inlined (one file goes to Kaggle)."""
    import re

    s = MERGE_SCRIPT.read_text(encoding="utf-8")
    for name, value in (("ADAPTER", adapter), ("LORA_GGUF", lora_gguf), ("ARA_TUR", intermediate_dtype)):
        s, n = re.subn(rf'^{name} = "[^"]*"', f"{name} = {json.dumps(value)}", s, count=1, flags=re.M)
        if n != 1:
            raise SystemExit(f"{name} sabiti bulunamadı")
    s, n = re.subn(r"^YALNIZ_TABAN = (True|False)$", f"YALNIZ_TABAN = {base_only}", s, count=1, flags=re.M)
    if n != 1:
        raise SystemExit("YALNIZ_TABAN sabiti bulunamadı")
    return inline_gguf_summary(s)


def inline_gguf_summary(s: str) -> str:
    module_source = GGUF_SUMMARY.read_text(encoding="utf-8").replace("from __future__ import annotations\n", "")
    if s.count("# <<gguf_ozet>>") != 1:
        raise SystemExit("gguf_ozet işareti bulunamadı")
    return s.replace("# <<gguf_ozet>>", module_source)


def diagnostic_script(script: Path) -> str:
    """Diagnostic scripts: the tensor summaries of the base model in Ollama (extracted on this computer) and the GGUF
    reader are inlined; also the ssm_a values of layer 0, if the script has them."""
    s = script.read_text(encoding="utf-8")
    target = json.loads(OLLAMA_SUMMARY.read_text(encoding="utf-8"))
    marker = "HEDEF: dict = {}  # <<hedef>>"
    if s.count(marker) != 1:
        raise SystemExit("hedef işareti bulunamadı")
    s = s.replace(marker, f"HEDEF: dict = {json.dumps(target)}  # <<hedef>>")
    marker_2 = "OLLAMA_SSM_A0: list = []  # <<ssm_a0>>"
    if marker_2 in s:
        ssm_a0 = json.loads((OLLAMA_SUMMARY.parent / "ollama_ssm_a0.json").read_text(encoding="utf-8"))
        s = s.replace(marker_2, f"OLLAMA_SSM_A0: list = {json.dumps(ssm_a0)}  # <<ssm_a0>>")
    return inline_gguf_summary(s)


def quantization_script() -> str:
    return diagnostic_script(QUANTIZATION_CHECK)


def push_diagnostic(slug: str, script: Path, datasets: list[str]) -> None:
    name, _ = credentials()
    directory = ROOT / "data" / "egitim" / "kernel" / slug
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    (directory / script.name).write_text(diagnostic_script(script), encoding="utf-8")
    (directory / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{name}/{slug}", "title": slug, "code_file": script.name, "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": False, "enable_internet": True,
        "dataset_sources": [f"{name}/{v}" for v in datasets], "competition_sources": [], "kernel_sources": [],
        "model_sources": []}, indent=1), encoding="utf-8")
    kaggle("kernels", "push", "-p", str(directory))


def push_merge(slug: str, source: str | None, adapter: str, lora_gguf: str, intermediate_dtype: str) -> None:
    """If `source` is None only the base model is quantized (no adapter needed)."""
    name, _ = credentials()
    directory = ROOT / "data" / "egitim" / "kernel" / slug
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    (directory / MERGE_SCRIPT.name).write_text(merge_script(adapter, lora_gguf, intermediate_dtype, base_only=source is None),
                                         encoding="utf-8")
    (directory / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{name}/{slug}", "title": slug, "code_file": MERGE_SCRIPT.name, "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": False, "enable_internet": True, "dataset_sources": [],
        "competition_sources": [], "kernel_sources": [f"{name}/{source}"] if source else [], "model_sources": []},
        indent=1), encoding="utf-8")
    kaggle("kernels", "push", "-p", str(directory))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Kaggle yardımcısı (Faz 8)")
    subparsers = ap.add_subparsers(dest="komut", required=True)
    v = subparsers.add_parser("veri")
    v.add_argument("--klasor", type=Path, required=True)
    v.add_argument("--slug", required=True)
    i = subparsers.add_parser("itele")
    i.add_argument("--slug", required=True)
    i.add_argument("--veri", required=True)
    d = subparsers.add_parser("durum")
    d.add_argument("--slug", required=True)
    c = subparsers.add_parser("cikti")
    c.add_argument("--slug", required=True)
    c.add_argument("--desen", default=None, help="yalnızca bu düzenli ifadeye uyan dosyalar")
    b = subparsers.add_parser("birlestir")
    b.add_argument("--slug", required=True)
    b.add_argument("--kaynak", required=True, help="adapterı üreten eğitim not defteri")
    b.add_argument("--adapter", default="adapter")
    b.add_argument("--lora-gguf", default="sayman-lora.gguf")
    b.add_argument("--ara-tur", default="f32", choices=("f32", "bf16", "f16"))
    t = subparsers.add_parser("taban", help="yalnızca dokunulmamış temel model: aynı yoldan geçer ve çıktıya konur")
    t.add_argument("--slug", required=True)
    t.add_argument("--ara-tur", default="f32", choices=("f32", "bf16", "f16"))
    n = subparsers.add_parser("niceleme")
    n.add_argument("--slug", required=True)
    u = subparsers.add_parser("uyum")
    u.add_argument("--slug", required=True)
    a = ap.parse_args()
    name, _ = credentials()
    if a.komut == "veri":
        upload_dataset(a.klasor, a.slug)
    elif a.komut == "itele":
        push_training(a.slug, a.veri)
    elif a.komut == "birlestir":
        push_merge(a.slug, a.kaynak, a.adapter, a.lora_gguf, a.ara_tur)
    elif a.komut == "taban":
        push_merge(a.slug, None, "adapter", "sayman-lora.gguf", a.ara_tur)
    elif a.komut == "niceleme":
        push_diagnostic(a.slug, QUANTIZATION_CHECK, [])
    elif a.komut == "uyum":
        push_diagnostic(a.slug, COMPATIBILITY_CHECK, ["sayman-egitim-tam"])
    elif a.komut == "durum":
        kaggle("kernels", "status", f"{name}/{a.slug}")
    else:
        target = ROOT / "data" / "egitim" / "kaggle_cikti" / a.slug
        target.mkdir(parents=True, exist_ok=True)
        arg = ["kernels", "output", f"{name}/{a.slug}", "-p", str(target), "-o"]
        if a.desen:
            arg += ["--file-pattern", a.desen]
        kaggle(*arg)


if __name__ == "__main__":
    main()
