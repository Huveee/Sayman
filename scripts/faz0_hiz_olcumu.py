"""Faz 0 hız ölçümü: aday modelleri yerel Ollama üzerinde ölçer, reports/faz0_hiz.json yazar.

Kullanım (proje kökünden):
    .venv\\Scripts\\python scripts\\faz0_hiz_olcumu.py
    .venv\\Scripts\\python scripts\\faz0_hiz_olcumu.py --modeller qwen3.5:4b qwen3.5:9b

Her aday için sıra:
    1. Model bellekten boşaltılmışken araç tanımları olmadan kısa bir çağrı: modeli yükler,
       araçsız istem token sayısını verir.
    2. Araç tanımlarıyla soğuk çağrı: istem işleme hızı ve toplam istem token sayısı.
       Araç bloğunun yaklaşık token sayısı = 2 - 1.
    3. Araç tanımlarıyla ısınma çağrısı: ön bellek sistem istemi + araçlarla dolar.
    4. 5 örnek soru, her biri ayrı bir sohbet olarak: tur süresi, geçerlilik, üretim hızı.
Model sonunda bellekten boşaltılır. Betik model indirmez; eksik model varsa durur.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import importlib.metadata
import json
import platform
import re
import statistics
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

import ollama  # noqa: E402

from sayman.faz0.arac_taslaklari import ARACLAR  # noqa: E402
from sayman.faz0.dogrulama import (  # noqa: E402
    GEREKEN_GECERLI,
    SURE_ESIGI_S,
    beklenen_cagri_mi,
    cevap_gecerli_mi,
    kurali_sagliyor_mu,
    secim_kurali,
)
from sayman.faz0.ornek_sorular import ISINMA_SORUSU, ORNEK_SORULAR  # noqa: E402

VARSAYILAN_MODELLER = ["qwen3.5:4b", "qwen3.5:9b"]
NUM_CTX = 8192
TOHUM = 42
BUGUN = "2026-11-12"
SISTEM_ISTEMI = (
    "Sen Sayman'sın: bir şirketin yapay zekâ destekli finans asistanısın. "
    "Sorulara cevap vermek için gereken veriyi her zaman verilen araçlardan al; rakam uydurma. "
    "Tarihleri YYYY-MM-DD biçiminde yaz. Bugünün tarihi " + BUGUN + "."
)
# num_batch: qwen3.5 karma (dikkat + tekrarlı) mimaride ön bellek yalnızca kontrol noktalarından
# geri yüklenir ve kontrol noktası istemin son num_batch tokenı kala alınır. Varsayılan büyük
# değerde bu nokta ortak önekin (sistem istemi + araçlar) çok gerisinde kalır ve her turda ~1000
# token yeniden işlenir. 64 ile nokta ortak önekin sonuna denk gelir.
NUM_BATCH = 64
SECENEKLER = {"temperature": 0, "seed": TOHUM, "num_ctx": NUM_CTX, "num_batch": NUM_BATCH}
# Isınma çağrılarında cevabın kendisi ölçülmez; model uzun bir sohbet cevabı yazmasın diye kısa tutulur.
ISINMA_URETIM_SINIRI = 32
# Bir araç çağrısı 100 tokenın altındadır; bu sınıra takılan tur zaten araç çağrısı üretmemiştir.
TUR_URETIM_SINIRI = 512
BELLEK_ALT_SINIRI_GB = 0.5
GIB = 2**30


class _BellekDurumu(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _GucDurumu(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


def bellek_gb() -> tuple[float, float]:
    """(toplam, boş) fiziksel bellek, GiB."""
    d = _BellekDurumu()
    d.dwLength = ctypes.sizeof(d)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(d))
    return round(d.ullTotalPhys / GIB, 2), round(d.ullAvailPhys / GIB, 2)


def fis_takili_mi() -> bool | None:
    d = _GucDurumu()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(d)):
        return None
    return {0: False, 1: True}.get(d.ACLineStatus)


def islemci_adi() -> str:
    try:
        import winreg

        anahtar = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        return winreg.QueryValueEx(anahtar, "ProcessorNameString")[0].strip()
    except OSError:
        return platform.processor()


def _sn(ns: int | None) -> float:
    return round((ns or 0) / 1e9, 3)


def _cagrilar(mesaj) -> list[dict]:
    return [
        {"ad": tc.function.name, "argumanlar": dict(tc.function.arguments or {})}
        for tc in (mesaj.tool_calls or [])
    ]


def _mesajlar(soru: str, sistem: str = SISTEM_ISTEMI) -> list[dict]:
    return [{"role": "system", "content": sistem}, {"role": "user", "content": soru}]


def _cagir(istemci, model: str, mesajlar: list[dict], araclar: list[dict] | None, **ek_secenek):
    secenek = {**SECENEKLER, **ek_secenek}
    bas = time.perf_counter()
    cevap = istemci.chat(
        model=model, messages=mesajlar, tools=araclar, think=False, options=secenek, keep_alive="15m"
    )
    return cevap, round(time.perf_counter() - bas, 3)


def _bosalt(istemci, model: str) -> None:
    istemci.generate(model=model, prompt="", keep_alive=0)


def _parametre_milyar(model: str, ayrinti: str | None) -> float:
    eslesme = re.search(r":(\d+(?:\.\d+)?)b\b", model)
    if eslesme:
        return float(eslesme.group(1))
    eslesme = re.search(r"(\d+(?:\.\d+)?)\s*B", ayrinti or "")
    return float(eslesme.group(1)) if eslesme else 0.0


def adayi_olc(istemci, model: str, yuklu: dict) -> dict:
    bilgi = yuklu[model]
    for calisan in istemci.ps().models:
        _bosalt(istemci, calisan.model)
    _, bos_once = bellek_gb()
    print(f"\n== {model}  (boş bellek {bos_once} GB)", flush=True)

    # 1. Modeli yükleyen kısa çağrı, araç tanımları olmadan. Sistem istemi "." ile başlar ki
    #    bir sonraki (araçlı) çağrıyla ortak öneki olmasın ve o çağrı gerçekten soğuk ölçülsün.
    aracsiz, _ = _cagir(istemci, model, _mesajlar(ISINMA_SORUSU, "." + SISTEM_ISTEMI), None, num_predict=1)

    # 2. Soğuk çağrı, araç tanımlarıyla: istem işleme hızı ve toplam istem token sayısı.
    #    Ollama'nın istem token sayısı ön bellekten gelen kısmı da içerir; hız bu yüzden yalnız
    #    burada, önek ön bellekte değilken ölçülür.
    soguk, soguk_s = _cagir(
        istemci, model, _mesajlar(ISINMA_SORUSU), ARACLAR, num_predict=ISINMA_URETIM_SINIRI
    )
    _, bos_yuklu = bellek_gb()
    calisan = next((m for m in istemci.ps().models if m.model == model), None)
    soguk_istem_s = _sn(soguk.prompt_eval_duration)
    istem_tps = round(soguk.prompt_eval_count / soguk_istem_s, 2) if soguk_istem_s else None
    yukleme_s = round(_sn(aracsiz.load_duration) + _sn(soguk.load_duration), 3)
    print(
        f"   soğuk: {soguk_s} s (yükleme {yukleme_s} s, "
        f"{soguk.prompt_eval_count} istem tokenı, {istem_tps} token/s)",
        flush=True,
    )

    # 3. Isınma: sistem istemi + araçlar ön belleğe girer
    isinma, isinma_s = _cagir(
        istemci, model, _mesajlar(ISINMA_SORUSU), ARACLAR, num_predict=ISINMA_URETIM_SINIRI
    )

    # 4. Ölçülen 5 tur
    turlar = []
    en_dusuk_bos = bos_yuklu
    for s in ORNEK_SORULAR:
        cevap, sure = _cagir(
            istemci, model, _mesajlar(s["soru"]), ARACLAR, num_predict=TUR_URETIM_SINIRI
        )
        _, bos = bellek_gb()
        en_dusuk_bos = min(en_dusuk_bos, bos)
        cagrilar = _cagrilar(cevap.message)
        gecerli, neden = cevap_gecerli_mi(cagrilar)
        tur = {
            "kimlik": s["kimlik"],
            "soru": s["soru"],
            "sure_s": sure,
            "istem_token": cevap.prompt_eval_count,
            "istem_s": _sn(cevap.prompt_eval_duration),
            "uretim_token": cevap.eval_count,
            "uretim_s": _sn(cevap.eval_duration),
            "cagrilar": cagrilar,
            "gecerli": gecerli,
            "neden": neden,
            "dogru_cagri": beklenen_cagri_mi(cagrilar, s["beklenen_arac"], s["beklenen_argumanlar"]),
            "dusunme_bos": not (cevap.message.thinking or "").strip(),
            "sinira_takildi": cevap.done_reason == "length",
            "metin": (cevap.message.content or "")[:300],
        }
        turlar.append(tur)
        print(
            f"   {s['kimlik']}: {sure} s, geçerli={gecerli} ({neden}), doğru={tur['dogru_cagri']}, "
            f"çağrı={[c['ad'] for c in cagrilar]}",
            flush=True,
        )

    _bosalt(istemci, model)

    toplam_uretim = sum(t["uretim_token"] or 0 for t in turlar)
    toplam_uretim_s = sum(t["uretim_s"] for t in turlar)
    sonuc = {
        "model": model,
        "parametre_milyar": _parametre_milyar(model, bilgi["parametre_boyutu"]),
        "ozet": bilgi["ozet"],
        "boyut_gb": bilgi["boyut_gb"],
        "nicemleme": bilgi["nicemleme"],
        "bos_bellek_once_gb": bos_once,
        "bos_bellek_yuklu_gb": bos_yuklu,
        "en_dusuk_bos_bellek_gb": en_dusuk_bos,
        "bellekteki_boyut_gb": round(calisan.size / GIB, 2) if calisan else None,
        "gpu_bellegi_gb": round(calisan.size_vram / GIB, 2) if calisan else None,
        "yukleme_s": yukleme_s,
        "soguk_tur_s": soguk_s,
        "soguk_istem_token": soguk.prompt_eval_count,
        "soguk_istem_s": soguk_istem_s,
        "istem_isleme_tps": istem_tps,
        "aracsiz_istem_token": aracsiz.prompt_eval_count,
        "arac_blogu_token_yaklasik": (soguk.prompt_eval_count or 0) - (aracsiz.prompt_eval_count or 0),
        "isinma_tur_s": isinma_s,
        "isinma_istem_token": isinma.prompt_eval_count,
        "turlar": turlar,
        "uretim_tps": round(toplam_uretim / toplam_uretim_s, 2) if toplam_uretim_s else None,
        "en_uzun_tur_s": max(t["sure_s"] for t in turlar),
        "ortanca_tur_s": round(statistics.median(t["sure_s"] for t in turlar), 3),
        "gecerli_sayisi": sum(1 for t in turlar if t["gecerli"]),
        "dogru_cagri_sayisi": sum(1 for t in turlar if t["dogru_cagri"]),
        "baglam_yetti": (soguk.prompt_eval_count or 0) < NUM_CTX,
        "dusunme_kapali": all(t["dusunme_bos"] for t in turlar),
        # Isınmış turların istem işleme süresi soğuk çağrınınkinin dörtte birinden kısa olmalı.
        "isinma_dogrulandi": statistics.median(t["istem_s"] for t in turlar) < soguk_istem_s / 4,
    }
    sonuc["kural_saglandi"] = kurali_sagliyor_mu(sonuc)
    print(
        f"   özet: en uzun tur {sonuc['en_uzun_tur_s']} s, üretim {sonuc['uretim_tps']} token/s, "
        f"geçerli {sonuc['gecerli_sayisi']}/5, doğru {sonuc['dogru_cagri_sayisi']}/5, "
        f"kural {'sağlandı' if sonuc['kural_saglandi'] else 'sağlanmadı'}",
        flush=True,
    )
    return sonuc


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ayri = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ayri.add_argument("--modeller", nargs="+", default=VARSAYILAN_MODELLER)
    ayri.add_argument("--cikti", type=Path, default=KOK / "reports" / "faz0_hiz.json")
    arg = ayri.parse_args()

    istemci = ollama.Client(timeout=900)
    try:
        ollama_surumu = istemci._client.get("/api/version").json().get("version")
    except Exception:  # sürüm bilgisi ölçüm için şart değil
        ollama_surumu = None
    yuklu = {}
    for m in istemci.list().models:
        yuklu[m.model] = {
            "ozet": m.digest,
            "boyut_gb": round(m.size / 1e9, 2),
            "nicemleme": m.details.quantization_level if m.details else None,
            "parametre_boyutu": m.details.parameter_size if m.details else None,
        }
    eksik = [m for m in arg.modeller if m not in yuklu]
    if eksik:
        print("Eksik model(ler), önce indirilmeli: " + ", ".join(eksik), file=sys.stderr)
        return 2

    toplam_bellek, _ = bellek_gb()
    fis = fis_takili_mi()
    adaylar = [adayi_olc(istemci, m, yuklu) for m in arg.modeller]
    secilen = secim_kurali(adaylar)

    uyarilar = []
    if fis is False:
        uyarilar.append("Ölçüm pil ile yapıldı; işlemci yavaşlatılmış olabilir.")
    for a in adaylar:
        if a["en_dusuk_bos_bellek_gb"] < BELLEK_ALT_SINIRI_GB:
            uyarilar.append(f"{a['model']}: boş bellek {a['en_dusuk_bos_bellek_gb']} GB'a düştü; ölçüm geçersiz olabilir.")
        if not a["isinma_dogrulandi"]:
            uyarilar.append(f"{a['model']}: ön bellek ısınması doğrulanamadı.")
        if not a["dusunme_kapali"]:
            uyarilar.append(f"{a['model']}: düşünme modu kapanmadı.")
        if not a["baglam_yetti"]:
            uyarilar.append(f"{a['model']}: istem bağlam penceresini aştı.")

    rapor = {
        "olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "kural": {
            "aciklama": (
                "Ön bellek ısındıktan sonra 5 araç çağırma turunun her birini en fazla "
                f"{SURE_ESIGI_S:g} saniyede bitiren ve 5 sorunun {GEREKEN_GECERLI}'inde geçerli araç "
                "çağrısı üreten en büyük model seçilir."
            ),
            "sure_esigi_s": SURE_ESIGI_S,
            "gereken_gecerli": GEREKEN_GECERLI,
        },
        "ayarlar": {
            "num_ctx": NUM_CTX,
            "num_batch": NUM_BATCH,
            "temperature": 0,
            "seed": TOHUM,
            "think": False,
            "arac_sayisi": len(ARACLAR),
            "isinma_uretim_siniri": ISINMA_URETIM_SINIRI,
            "tur_uretim_siniri": TUR_URETIM_SINIRI,
            "bugun": BUGUN,
            "sistem_istemi": SISTEM_ISTEMI,
        },
        "makine": {
            "islemci": islemci_adi(),
            "toplam_bellek_gb": toplam_bellek,
            "fis_takili": fis,
            "isletim_sistemi": platform.platform(),
            "ollama_surumu": ollama_surumu,
            "python": platform.python_version(),
            "ollama_python": importlib.metadata.version("ollama"),
        },
        "adaylar": adaylar,
        "secilen_model": secilen,
        "uyarilar": uyarilar,
    }
    arg.cikti.parent.mkdir(parents=True, exist_ok=True)
    arg.cikti.write_text(json.dumps(rapor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nSeçilen model: {secilen or 'yok (hiçbir aday kuralı sağlamadı)'}")
    for u in uyarilar:
        print("UYARI: " + u)
    print(f"Rapor: {arg.cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
