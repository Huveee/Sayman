"""Faz 8 unutma testi: Belebele Türkçe alt kümesinde bir Ollama modelini ölçer.

Her soru bir JSONL satırına yazılır; betik kaldığı yerden devam eder. Satırda modelin izi (ad, seçenekler, Ollama
özeti) ve alt kümenin özeti vardır; ikisinden biri değişirse ölçüm durur.

Kullanım (proje kökünden; önce scripts\\faz8_belebele_indir.py):
    .venv\\Scripts\\python -I -u scripts\\faz8_unutma.py --etiket temel --model qwen3.5:9b
    .venv\\Scripts\\python -I -u scripts\\faz8_unutma.py --etiket ayarli --model sayman-ayarli
    .venv\\Scripts\\python -I -u scripts\\faz8_unutma.py --karsilastir temel ayarli
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.evaluation import fingerprint, forgetting  # noqa: E402

KAYNAK = KOK / "data" / "ham" / "belebele" / "tur_Latn.jsonl"


def yol(etiket: str) -> Path:
    return KOK / "reports" / f"faz8_unutma_{etiket}.jsonl"


def oku(p: Path) -> list[dict]:
    return [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines() if s.strip()] if p.exists() else []


def kume_ozeti(sorular: list[dict]) -> str:
    return hashlib.sha256(json.dumps(sorular, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def uyanik_tut() -> None:
    """Süreç sürdükçe bilgisayarın boşta uykuya geçmesini engeller; sistem ayarı değiştirmez."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)


def calistir(etiket: str, model_adi: str) -> None:
    import ollama

    sorular = forgetting.alt_kume(forgetting.sorulari_oku(KAYNAK))
    kume = kume_ozeti(sorular)
    izi = fingerprint.model(model_adi=model_adi)
    izi = {**izi, "secenekler": forgetting.SECENEKLER, "ozet": None}
    izi["ozet"] = hashlib.sha256(json.dumps({k: v for k, v in izi.items() if k != "ozet"}, sort_keys=True,
                                            default=str).encode()).hexdigest()
    hedef = yol(etiket)
    eski = oku(hedef)
    for k in eski:
        if k["kume_ozeti"] != kume or k["model_izi"]["ozet"] != izi["ozet"]:
            raise SystemExit(f"{hedef.name}: alt küme ya da model izi değişmiş; yeni bir etiket kullan.")
    yapilan = {k["soru_id"] for k in eski}
    istemci = ollama.Client(timeout=300)
    print(f"{etiket}: {len(yapilan)}/{len(sorular)} hazır, model {model_adi}", flush=True)
    with open(hedef, "a", encoding="utf-8") as f:
        for i, s in enumerate(sorular, 1):
            if s["soru_id"] in yapilan:
                continue
            for deneme in range(3):
                try:
                    bas = time.perf_counter()
                    r = istemci.chat(model=model_adi, messages=[{"role": "user", "content": forgetting.istem(s)}],
                                     think=False, options=forgetting.SECENEKLER, keep_alive="15m")
                    break
                except Exception as e:  # Ollama kapandıysa bekleyip yeniden dene
                    if deneme == 2:
                        raise
                    print(f"  model çağrısı hata verdi ({type(e).__name__}); 60 sn sonra yeniden", flush=True)
                    time.sleep(60)
            metin = r.message.content or ""
            kayit = {"soru_id": s["soru_id"], "dogru": s["dogru"], "secilen": forgetting.cevap_numarasi(metin),
                     "cevap": metin, "sure_s": round(time.perf_counter() - bas, 2), "kume_ozeti": kume,
                     "model_izi": izi}
            f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
            f.flush()
            print(f"{i:4}/{len(sorular)} {'doğru' if kayit['secilen'] == s['dogru'] else 'yanlış':6} "
                  f"{kayit['sure_s']:6.1f} sn", flush=True)
    print(json.dumps(forgetting.ozetle(oku(hedef)), ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser(description="Faz 8 unutma testi (Belebele Türkçe)")
    ap.add_argument("--etiket")
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--karsilastir", nargs=2, metavar=("TEMEL", "AYARLI"))
    a = ap.parse_args()
    if a.karsilastir:
        t, s = (oku(yol(e)) for e in a.karsilastir)
        sonuc = {"temel": {"etiket": a.karsilastir[0], **forgetting.ozetle(t)},
                 "ayarli": {"etiket": a.karsilastir[1], **forgetting.ozetle(s)},
                 "karsilastirma": forgetting.karsilastir(t, s)}
        (KOK / "reports" / "faz8_unutma.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1),
                                                          encoding="utf-8")
        print(json.dumps(sonuc, ensure_ascii=False, indent=1))
        return
    if not a.etiket:
        ap.error("--etiket gerekli")
    uyanik_tut()
    calistir(a.etiket, a.model)


if __name__ == "__main__":
    main()
