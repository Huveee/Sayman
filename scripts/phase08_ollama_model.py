"""Faz 8: ince ayarlı GGUF'u Ollama'ya temel modelle aynı biçimde kurar ve modelin gerçekten değiştiğini denetler.

Ollama 0.40.11 LoRA adapterını (ADAPTER) desteklemiyor; ince ayarlı model, LoRA'sı katılmış tek bir GGUF dosyasıdır
(kaggle/faz8_birlestir.py). Adımlar (proje kökünden; Ollama'da başka iş çalışmıyorken):

    .venv\\Scripts\\python -I scripts\\faz8_ollama_model.py kur --ad sayman-ayarli --gguf <dosya.gguf>
    .venv\\Scripts\\python -I scripts\\faz8_ollama_model.py dene --ad sayman-ayarli ^
        --ornekler data\\egitim\\tam\\dogrulama.jsonl --kaggle-rapor data\\egitim\\kaggle_cikti\\sayman-faz8-tam\\rapor.json ^
        --temel sayman-taban-kendi --yalniz-kaggle
    .venv\\Scripts\\python -I scripts\\faz8_ollama_model.py bosalt

kur: temel modelin (qwen3.5:9b) Modelfile'ı alınır, yalnızca ilk FROM satırı (dil modeli) verilen dosyayla değişir.
Görsel kodlayıcı, TEMPLATE, RENDERER, PARSER ve PARAMETER satırları aynen kalır; betik sonra bunları denetler.

dene: üç denetim, rapor reports/faz8_model_denemesi_<ad>.json
  1. Çizim: aynı mesajlar ve araçlar iki modelde de aynı istem metnine çizilmeli.
  2. Üretim: örneklerin istemleri ham olarak iki modele verilir. İnce ayarlı modelin çıktısı Kaggle'daki üretim
     denemesine benzemeli, temel modelinkinden farklı olmalı. Her model boş başlangıçla (bosalt) sorulur.
  3. Seçenekler: üretimde SECENEKLER kullanılır (num_predict hariç), böylece model yeniden yüklenmez.

bosalt: Ollama'da yüklü bütün modelleri kapatır; her ölçüm aynı boş durumdan başlar.
"""

from __future__ import annotations

import argparse
import difflib
import json
import subprocess
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

OLLAMA = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
HOST = "http://127.0.0.1:11434"
TEMEL = "qwen3.5:9b"


def ollama(*arg: str) -> str:
    r = subprocess.run([str(OLLAMA), *arg], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit(f"ollama {' '.join(arg)} başarısız:\n{r.stdout}{r.stderr}")
    return r.stdout


YONERGELER = ("FROM", "TEMPLATE", "RENDERER", "PARSER", "PARAMETER", "SYSTEM", "ADAPTER", "MESSAGE")


def modelfile_satirlari(model: str = TEMEL) -> list[str]:
    """Modelfile'ın tek satırlık yönergeleri (LICENSE gibi çok satırlı bloklar dışarıda)."""
    return [s for s in ollama("show", model, "--modelfile").splitlines() if s.split(" ", 1)[0] in YONERGELER]


def kur(ad: str, gguf: Path) -> None:
    gguf = gguf.resolve()
    if not gguf.exists():
        raise SystemExit(f"yok: {gguf}")
    temel = modelfile_satirlari()
    froms = [i for i, s in enumerate(temel) if s.startswith("FROM ")]
    if len(froms) != 2:
        raise SystemExit(f"temel modelde iki FROM (dil modeli + görsel kodlayıcı) bekleniyordu: {froms}")
    if any(s.startswith(("ADAPTER", "MESSAGE")) for s in temel):
        raise SystemExit("temel modelde beklenmeyen ADAPTER/MESSAGE yönergesi")
    satirlar = list(temel)
    satirlar[froms[0]] = f"FROM {gguf}"
    klasor = KOK / "data" / "egitim" / "calisma" / ad
    klasor.mkdir(parents=True, exist_ok=True)
    mf = klasor / "Modelfile"
    mf.write_text("\n".join(satirlar) + "\n", encoding="utf-8")
    print(mf.read_text(encoding="utf-8"))
    print(ollama("create", ad, "-f", str(mf))[-300:])
    beklenen = sorted(s for s in temel if not s.startswith("FROM "))  # Ollama PARAMETER sırasını değiştirebiliyor
    bulunan = sorted(s for s in modelfile_satirlari(ad) if not s.startswith("FROM "))
    if beklenen != bulunan:
        raise SystemExit("kurulan modelin Modelfile'ı temel modelinkinden farklı (FROM dışında)")
    yeni_froms = [s for s in modelfile_satirlari(ad) if s.startswith("FROM ")]
    if yeni_froms[1] != temel[froms[1]]:
        raise SystemExit("görsel kodlayıcı temel modelinkiyle aynı değil")
    print("Modelfile FROM dışında temel modelle aynı; görsel kodlayıcı aynı.")


def bosalt() -> None:
    import httpx

    yuklu = httpx.get(f"{HOST}/api/ps", timeout=30).json().get("models", [])
    for m in yuklu:
        httpx.post(f"{HOST}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=300)
    kalan = httpx.get(f"{HOST}/api/ps", timeout=30).json().get("models", [])
    print("boşaltıldı:", [m["name"] for m in yuklu], "kalan:", [m["name"] for m in kalan])
    if kalan:
        raise SystemExit("bazı modeller hâlâ yüklü")


def ciz_denetimi(ad: str, temel: str = TEMEL) -> dict:
    from sayman.assistant.orchestrator import arac_tanimlari
    from sayman.training.prompt import istem_ciz

    araclar = arac_tanimlari()
    izler = [json.loads(s) for s in (KOK / "data" / "egitim" / "izler.jsonl").read_text(encoding="utf-8").splitlines()
             if s.strip()][:3]
    sonuc = []
    for iz in izler:
        for c in iz["cagrilar"]:
            t, a = istem_ciz(c["mesajlar"], araclar, temel), istem_ciz(c["mesajlar"], araclar, ad)
            sonuc.append({"soru_id": iz["soru_id"], "ayni": t == a, "karakter": len(t)})
    return {"istem": len(sonuc), "hepsi_ayni": all(s["ayni"] for s in sonuc), "ayrinti": sonuc}


def uret(model: str, istem: str, istemci) -> str:
    from sayman.assistant.model import SECENEKLER

    r = istemci.post(f"{HOST}/api/generate", json={
        "model": model, "prompt": istem, "raw": True, "stream": False, "think": False,
        "options": {**SECENEKLER, "num_predict": 160}}, timeout=900)
    r.raise_for_status()
    return r.json()["response"]


def benzerlik(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a, b).ratio(), 3)


def kes(metin: str) -> str:
    """Karşılaştırma için ilk <|im_end|>'e kadar (Kaggle üretimi durmadan devam edebiliyordu)."""
    return metin.split("<|im_end|>")[0].strip()


def dene(ad: str, ornekler_yolu: Path, kaggle_rapor: Path | None, temel: str = TEMEL,
         yalniz_kaggle: bool = False) -> None:
    """yalniz_kaggle: yalnızca Kaggle'daki üretim denemesinde de sorulan örnekler (CPU'da 110 örnek saatler sürer)."""
    import httpx

    ornekler = [json.loads(s) for s in ornekler_yolu.read_text(encoding="utf-8").splitlines() if s.strip()]
    kaggle = {}
    if kaggle_rapor and kaggle_rapor.exists():
        for u in json.loads(kaggle_rapor.read_text(encoding="utf-8")).get("uretim_denemesi", []):
            kaggle[(u["soru_id"], u["adim"])] = u["uretilen"]
    if yalniz_kaggle:
        ornekler = [o for o in ornekler if (o.get("soru_id"), o.get("adim")) in kaggle]
        if len(ornekler) != len(kaggle):
            raise SystemExit(f"Kaggle'daki {len(kaggle)} örneğin {len(ornekler)} tanesi örnek dosyasında bulundu")
    rapor = {"model": ad, "temel": temel, "ciz": ciz_denetimi(ad, temel), "uretim": []}
    print("çizim aynı mı:", rapor["ciz"]["hepsi_ayni"], flush=True)
    with httpx.Client() as istemci:
        for model in (ad, temel):  # model başına sırayla ve boş başlangıçla (önceki işler cevabı değiştirebiliyor)
            bosalt()
            for i, o in enumerate(ornekler):
                cikti = uret(model, o["istem"], istemci)
                if model == ad:
                    rapor["uretim"].append({"soru_id": o.get("soru_id"), "adim": o.get("adim"),
                                            "beklenen": kes(o["tamamlama"]), "ayarli": cikti})
                else:
                    rapor["uretim"][i]["temel"] = cikti
                print(f"{model} {o.get('soru_id')} {o.get('adim')}: {kes(cikti)[:90]!r}", flush=True)
    for u in rapor["uretim"]:
        k = kaggle.get((u["soru_id"], u["adim"]))
        u["kaggle"] = k
        u["ayarli_kaggle_benzerligi"] = benzerlik(kes(u["ayarli"]), kes(k)) if k is not None else None
        u["temel_kaggle_benzerligi"] = benzerlik(kes(u["temel"]), kes(k)) if k is not None else None
        u["ayarli_temel_ayni"] = kes(u["ayarli"]) == kes(u["temel"])
        u["ayarli_beklenen_ayni"] = kes(u["ayarli"]) == u["beklenen"]
        u["temel_beklenen_ayni"] = kes(u["temel"]) == u["beklenen"]
    u = rapor["uretim"]
    karsilastirilan = [x for x in u if x["kaggle"] is not None]
    rapor["ozet"] = {
        "ornek": len(u),
        "ayarli_temelden_farkli": sum(not x["ayarli_temel_ayni"] for x in u),
        "ayarli_beklenenle_ayni": sum(x["ayarli_beklenen_ayni"] for x in u),
        "temel_beklenenle_ayni": sum(x["temel_beklenen_ayni"] for x in u),
        "kaggle_ile_karsilastirilan": len(karsilastirilan),
        "ayarli_kaggle_benzerligi_ort": round(sum(x["ayarli_kaggle_benzerligi"] for x in karsilastirilan)
                                              / len(karsilastirilan), 3) if karsilastirilan else None,
        "temel_kaggle_benzerligi_ort": round(sum(x["temel_kaggle_benzerligi"] for x in karsilastirilan)
                                             / len(karsilastirilan), 3) if karsilastirilan else None,
    }
    cikti = KOK / "reports" / f"faz8_model_denemesi_{ad}.json"
    cikti.write_text(json.dumps(rapor, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(rapor["ozet"], ensure_ascii=False, indent=1))
    print("yazıldı:", cikti.relative_to(KOK))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8: ince ayarlı modeli Ollama'ya kurma ve denetleme")
    alt = ap.add_subparsers(dest="komut", required=True)
    o = alt.add_parser("kur")
    o.add_argument("--ad", required=True)
    o.add_argument("--gguf", type=Path, required=True)
    alt.add_parser("bosalt")
    d = alt.add_parser("dene")
    d.add_argument("--ad", required=True)
    d.add_argument("--ornekler", type=Path, required=True)
    d.add_argument("--kaggle-rapor", type=Path, default=None)
    d.add_argument("--temel", default=TEMEL, help="karşılaştırılan temel model (ör. sayman-taban-kendi)")
    d.add_argument("--yalniz-kaggle", action="store_true", help="yalnızca Kaggle üretim denemesindeki örnekler")
    a = ap.parse_args()
    if a.komut == "kur":
        kur(a.ad, a.gguf)
    elif a.komut == "bosalt":
        bosalt()
    else:
        dene(a.ad, a.ornekler, a.kaggle_rapor, a.temel, a.yalniz_kaggle)


if __name__ == "__main__":
    main()
