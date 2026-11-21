"""Kaggle ile konuşan küçük yardımcı: özel veri seti yükler, eğitim not defterini gönderir, durumunu ve çıktısını alır.

Kimlik .env dosyasındaki KAGGLE_USERNAME ve KAGGLE_API_TOKEN'dan okunur ve yalnızca kaggle komutunun ortamına verilir;
hiçbir yere yazılmaz, ekrana basılan çıktıda anahtar geçerse gizlenir. Veri setleri ve not defterleri özeldir.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8_kaggle.py veri --klasor data\\egitim\\duman --slug sayman-egitim-duman
    .venv\\Scripts\\python -I scripts\\faz8_kaggle.py itele --slug sayman-faz8-duman --veri sayman-egitim-duman
    .venv\\Scripts\\python -I scripts\\faz8_kaggle.py durum --slug sayman-faz8-duman
    .venv\\Scripts\\python -I scripts\\faz8_kaggle.py cikti --slug sayman-faz8-duman --desen "rapor\\.json"
    .venv\\Scripts\\python -I scripts\\faz8_kaggle.py birlestir --slug sayman-faz8-birlestir --kaynak sayman-faz8-tam ^
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

KOK = Path(__file__).resolve().parents[1]
KAGGLE = KOK / ".venv" / "Scripts" / "kaggle.exe"
BETIK = KOK / "kaggle" / "faz8_egit.py"
BIRLESTIR = KOK / "kaggle" / "faz8_birlestir.py"
GGUF_OZET = KOK / "src" / "sayman" / "egitim" / "gguf_ozet.py"
NICELEME = KOK / "kaggle" / "faz8_niceleme_denemesi.py"
UYUM = KOK / "kaggle" / "faz8_uyum_denemesi.py"
OLLAMA_OZETI = KOK / "data" / "egitim" / "calisma" / "ollama_taban_ozet.json"  # faz8_gguf_karsilastir.py çıkarır


def kimlik() -> tuple[str, dict]:
    from dotenv import dotenv_values

    v = dotenv_values(KOK / ".env")
    ad, tok = v.get("KAGGLE_USERNAME"), v.get("KAGGLE_API_TOKEN")
    if not ad or not tok:
        raise SystemExit(".env dosyasında KAGGLE_USERNAME ve KAGGLE_API_TOKEN olmalı")
    return ad, {**os.environ, "KAGGLE_USERNAME": ad, "KAGGLE_API_TOKEN": tok}


def kaggle(*arg: str) -> subprocess.CompletedProcess:
    _, ortam = kimlik()
    r = subprocess.run([str(KAGGLE), *arg], env=ortam, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    gizli = ortam["KAGGLE_API_TOKEN"]
    r.stdout, r.stderr = r.stdout.replace(gizli, "<gizli>"), r.stderr.replace(gizli, "<gizli>")
    print((r.stdout + r.stderr).strip())
    return r


def veri(klasor: Path, slug: str) -> None:
    ad, _ = kimlik()
    (klasor / "dataset-metadata.json").write_text(json.dumps(
        {"title": slug, "id": f"{ad}/{slug}", "licenses": [{"name": "CC0-1.0"}]}, indent=1), encoding="utf-8")
    r = kaggle("datasets", "status", f"{ad}/{slug}")
    if r.returncode == 0 and "ready" in (r.stdout + r.stderr).lower():
        kaggle("datasets", "version", "-p", str(klasor), "-m", "güncelleme", "-d")
    else:
        kaggle("datasets", "create", "-p", str(klasor))  # varsayılan: özel


def itele(slug: str, veri_slug: str) -> None:
    ad, _ = kimlik()
    klasor = KOK / "data" / "egitim" / "kernel" / slug
    if klasor.exists():
        shutil.rmtree(klasor)
    klasor.mkdir(parents=True)
    shutil.copy(BETIK, klasor / BETIK.name)
    (klasor / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{ad}/{slug}", "title": slug, "code_file": BETIK.name, "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": True, "enable_internet": True, "machine_shape": "NvidiaTeslaT4",
        "dataset_sources": [f"{ad}/{veri_slug}"], "competition_sources": [], "kernel_sources": [],
        "model_sources": []}, indent=1), encoding="utf-8")
    kaggle("kernels", "push", "-p", str(klasor))


def birlestir_betigi(adapter: str, lora_gguf: str, ara_tur: str, yalniz_taban: bool = False) -> str:
    """Birleştirme betiği: sabitler doldurulur, GGUF özet modülü içine eklenir (Kaggle'a tek dosya gider)."""
    import re

    s = BIRLESTIR.read_text(encoding="utf-8")
    for ad, deger in (("ADAPTER", adapter), ("LORA_GGUF", lora_gguf), ("ARA_TUR", ara_tur)):
        s, n = re.subn(rf'^{ad} = "[^"]*"', f"{ad} = {json.dumps(deger)}", s, count=1, flags=re.M)
        if n != 1:
            raise SystemExit(f"{ad} sabiti bulunamadı")
    s, n = re.subn(r"^YALNIZ_TABAN = (True|False)$", f"YALNIZ_TABAN = {yalniz_taban}", s, count=1, flags=re.M)
    if n != 1:
        raise SystemExit("YALNIZ_TABAN sabiti bulunamadı")
    return gguf_ekle(s)


def gguf_ekle(s: str) -> str:
    modul = GGUF_OZET.read_text(encoding="utf-8").replace("from __future__ import annotations\n", "")
    if s.count("# <<gguf_ozet>>") != 1:
        raise SystemExit("gguf_ozet işareti bulunamadı")
    return s.replace("# <<gguf_ozet>>", modul)


def tani_betigi(betik: Path) -> str:
    """Tanı betikleri: Ollama'daki temel modelin tensör özetleri (bu bilgisayarda çıkarıldı) ve GGUF okuyucu eklenir;
    betikte varsa 0. katmanın ssm_a değerleri de."""
    s = betik.read_text(encoding="utf-8")
    hedef = json.loads(OLLAMA_OZETI.read_text(encoding="utf-8"))
    isaret = "HEDEF: dict = {}  # <<hedef>>"
    if s.count(isaret) != 1:
        raise SystemExit("hedef işareti bulunamadı")
    s = s.replace(isaret, f"HEDEF: dict = {json.dumps(hedef)}  # <<hedef>>")
    isaret2 = "OLLAMA_SSM_A0: list = []  # <<ssm_a0>>"
    if isaret2 in s:
        ssm_a0 = json.loads((OLLAMA_OZETI.parent / "ollama_ssm_a0.json").read_text(encoding="utf-8"))
        s = s.replace(isaret2, f"OLLAMA_SSM_A0: list = {json.dumps(ssm_a0)}  # <<ssm_a0>>")
    return gguf_ekle(s)


def niceleme_betigi() -> str:
    return tani_betigi(NICELEME)


def tani(slug: str, betik: Path, veri: list[str]) -> None:
    ad, _ = kimlik()
    klasor = KOK / "data" / "egitim" / "kernel" / slug
    if klasor.exists():
        shutil.rmtree(klasor)
    klasor.mkdir(parents=True)
    (klasor / betik.name).write_text(tani_betigi(betik), encoding="utf-8")
    (klasor / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{ad}/{slug}", "title": slug, "code_file": betik.name, "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": False, "enable_internet": True,
        "dataset_sources": [f"{ad}/{v}" for v in veri], "competition_sources": [], "kernel_sources": [],
        "model_sources": []}, indent=1), encoding="utf-8")
    kaggle("kernels", "push", "-p", str(klasor))


def birlestir(slug: str, kaynak: str | None, adapter: str, lora_gguf: str, ara_tur: str) -> None:
    """kaynak None ise yalnızca temel model sıkıştırılır (adapter gerekmez)."""
    ad, _ = kimlik()
    klasor = KOK / "data" / "egitim" / "kernel" / slug
    if klasor.exists():
        shutil.rmtree(klasor)
    klasor.mkdir(parents=True)
    (klasor / BIRLESTIR.name).write_text(birlestir_betigi(adapter, lora_gguf, ara_tur, yalniz_taban=kaynak is None),
                                         encoding="utf-8")
    (klasor / "kernel-metadata.json").write_text(json.dumps({
        "id": f"{ad}/{slug}", "title": slug, "code_file": BIRLESTIR.name, "language": "python", "kernel_type": "script",
        "is_private": True, "enable_gpu": False, "enable_internet": True, "dataset_sources": [],
        "competition_sources": [], "kernel_sources": [f"{ad}/{kaynak}"] if kaynak else [], "model_sources": []},
        indent=1), encoding="utf-8")
    kaggle("kernels", "push", "-p", str(klasor))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Kaggle yardımcısı (Faz 8)")
    alt = ap.add_subparsers(dest="komut", required=True)
    v = alt.add_parser("veri")
    v.add_argument("--klasor", type=Path, required=True)
    v.add_argument("--slug", required=True)
    i = alt.add_parser("itele")
    i.add_argument("--slug", required=True)
    i.add_argument("--veri", required=True)
    d = alt.add_parser("durum")
    d.add_argument("--slug", required=True)
    c = alt.add_parser("cikti")
    c.add_argument("--slug", required=True)
    c.add_argument("--desen", default=None, help="yalnızca bu düzenli ifadeye uyan dosyalar")
    b = alt.add_parser("birlestir")
    b.add_argument("--slug", required=True)
    b.add_argument("--kaynak", required=True, help="adapterı üreten eğitim not defteri")
    b.add_argument("--adapter", default="adapter")
    b.add_argument("--lora-gguf", default="sayman-lora.gguf")
    b.add_argument("--ara-tur", default="f32", choices=("f32", "bf16", "f16"))
    t = alt.add_parser("taban", help="yalnızca dokunulmamış temel model: aynı yoldan geçer ve çıktıya konur")
    t.add_argument("--slug", required=True)
    t.add_argument("--ara-tur", default="f32", choices=("f32", "bf16", "f16"))
    n = alt.add_parser("niceleme")
    n.add_argument("--slug", required=True)
    u = alt.add_parser("uyum")
    u.add_argument("--slug", required=True)
    a = ap.parse_args()
    ad, _ = kimlik()
    if a.komut == "veri":
        veri(a.klasor, a.slug)
    elif a.komut == "itele":
        itele(a.slug, a.veri)
    elif a.komut == "birlestir":
        birlestir(a.slug, a.kaynak, a.adapter, a.lora_gguf, a.ara_tur)
    elif a.komut == "taban":
        birlestir(a.slug, None, "adapter", "sayman-lora.gguf", a.ara_tur)
    elif a.komut == "niceleme":
        tani(a.slug, NICELEME, [])
    elif a.komut == "uyum":
        tani(a.slug, UYUM, ["sayman-egitim-tam"])
    elif a.komut == "durum":
        kaggle("kernels", "status", f"{ad}/{a.slug}")
    else:
        hedef = KOK / "data" / "egitim" / "kaggle_cikti" / a.slug
        hedef.mkdir(parents=True, exist_ok=True)
        arg = ["kernels", "output", f"{ad}/{a.slug}", "-p", str(hedef), "-o"]
        if a.desen:
            arg += ["--file-pattern", a.desen]
        kaggle(*arg)


if __name__ == "__main__":
    main()
