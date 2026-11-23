"""Faz 5: sohbet modeli, anlam modeli ve sıralayıcı aynı anda bellekte dururken bellek ve süre ölçümü.

Faz 6'da bir soru şu sırayla işlenecek: soru vektöre çevrilir (bge-m3, Ollama), kelime + anlam araması yapılır,
ilk 20 aday sıralayıcıdan geçer (bge-reranker-v2-m3, bu süreçte torch ile), sonra sohbet modeli (qwen3.5:9b, Ollama)
cevabı yazar. Bu betik üçünü birlikte yükler ve şunlara bakar:

- Ollama iki modeli birlikte tutabiliyor mu, yoksa biri yüklenince öteki bellekten atılıyor mu?
- Sıralayıcı bu sürecin belleğini ne kadar büyütüyor, bilgisayarda ne kadar boş bellek kalıyor?
- Bir sorunun arama kısmı (vektör + arama + 20 adayı sıralama) kaç saniye sürüyor?

Sorular ayar setinden alınır; sıralama skorları önbelleğe yazılmaz (her ölçüm modeli gerçekten çalıştırır).

Kullanım (proje kökünden, Ollama açıkken):
    .venv\\Scripts\\python -I scripts\\faz5_kaynak_olc.py
"""

from __future__ import annotations

import ctypes
import json
import statistics
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

import ollama  # noqa: E402

from sayman.documents import library  # noqa: E402
from sayman.documents.semantic import AnlamDizini, GommeOnbellegi, anahtar, gom  # noqa: E402
from sayman.documents.semantic import MODEL as GOMME_MODELI  # noqa: E402
from sayman.documents.search import Arayici, ilk  # noqa: E402
from sayman.documents.reranker import Siralayici  # noqa: E402

BELGE = KOK / "data" / "belge"
MODEL = KOK / "data" / "modeller" / "bge-reranker-v2-m3"
RAPOR = KOK / "reports" / "faz5_kaynak.json"
SOHBET = "qwen3.5:9b"
GB = 1024 ** 3


class _Bellek(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64), ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]


class _Surec(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def bos_bellek_gb() -> float:
    b = _Bellek()
    b.dwLength = ctypes.sizeof(b)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(b))
    return round(b.ullAvailPhys / GB, 2)


def surec_bellek_gb() -> float:
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Surec), wintypes.DWORD]
    k32.K32GetProcessMemoryInfo.restype = wintypes.BOOL
    s = _Surec()
    s.cb = ctypes.sizeof(s)
    if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(s), s.cb):
        raise OSError(ctypes.get_last_error())
    return round(s.WorkingSetSize / GB, 2)


def toplam_bellek_gb() -> float:
    b = _Bellek()
    b.dwLength = ctypes.sizeof(b)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(b))
    return round(b.ullTotalPhys / GB, 1)


def bosalt() -> None:
    """Ollama'daki yüklü modelleri bellekten çıkarır (modeller diskte kalır)."""
    for m in ollama.ps()["models"]:
        try:
            ollama.generate(model=m["model"], prompt="", keep_alive=0)
        except ollama.ResponseError:
            ollama.embed(model=m["model"], input="x", keep_alive=0)
    time.sleep(3)


def yuklu() -> dict:
    return {m["model"]: round(m["size"] / GB, 2) for m in ollama.ps()["models"]}


def sohbet() -> float:
    bas = time.perf_counter()
    ollama.generate(model=SOHBET, prompt="Tek kelimeyle cevap ver: Türkiye'nin başkenti?", think=False,
                    keep_alive="15m", options={"num_predict": 8})
    return round(time.perf_counter() - bas, 2)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    bosalt()
    r = {"sohbet_modeli": SOHBET, "gomme_modeli": GOMME_MODELI, "siralayici": MODEL.name,
         "toplam_bellek_gb": toplam_bellek_gb(), "bos_bellek_gb": {"bas": bos_bellek_gb()},
         "not": "Boş bellek, ölçüm sırasında açık olan öteki programlara da bağlıdır."}
    r["sohbet_ilk_cagri_s"] = sohbet()
    r["sohbet_ikinci_cagri_s"] = sohbet()
    bas = time.perf_counter()
    gom(["deneme"])
    r["gomme_ilk_cagri_s"] = round(time.perf_counter() - bas, 2)
    r["ollama_yuklu_gb"] = yuklu()
    r["bos_bellek_gb"]["ollama_iki_model"] = bos_bellek_gb()

    parcalar = library.parcalar(library.oku(BELGE / "belgeler.jsonl"))
    sorular = json.loads((BELGE / "sorular.json").read_text(encoding="utf-8"))["ayar"][::20][:6]
    r["surec_bellek_gb"] = {"siralayicidan_once": surec_bellek_gb()}
    with tempfile.TemporaryDirectory() as gecici:
        siralayici = Siralayici(MODEL, Path(gecici) / "s.json")
        bas = time.perf_counter()
        siralayici._yukle()
        r["siralayici_yukleme_s"] = round(time.perf_counter() - bas, 2)
        r["surec_bellek_gb"]["siralayici_yuklu"] = surec_bellek_gb()
        onbellek = GommeOnbellegi(BELGE / "onbellek" / "gomme_bge-m3.npz")
        dizin = AnlamDizini(parcalar, onbellek)
        arayici = Arayici(parcalar, dizin, siralayici)
        sureler = []
        for s in sorular:
            bas = time.perf_counter()
            onbellek.v[anahtar(s["soru"])] = gom([s["soru"]])[0]  # soru vektörü önbellekten değil, modelden
            sonuc = ilk(arayici.skorlar(s["soru"], "cfo"), 5)
            sureler.append(time.perf_counter() - bas)
            assert sonuc
        dizin.kapat()
        r["surec_bellek_gb"]["arama_sonrasi"] = surec_bellek_gb()
    r["arama_soru_basina_s"] = {"medyan": round(statistics.median(sureler), 2), "en_cok": round(max(sureler), 2),
                                "soru": len(sureler), "aday": arayici.aday}
    r["ollama_yuklu_gb_arama_sonrasi"] = yuklu()
    r["sohbet_arama_sonrasi_s"] = sohbet()
    r["ollama_yuklu_gb_son"] = yuklu()
    r["bos_bellek_gb"]["hepsi_yuklu"] = bos_bellek_gb()
    r["sohbet_modeli_atildi_mi"] = SOHBET not in r["ollama_yuklu_gb_arama_sonrasi"]
    RAPOR.write_text(json.dumps(r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
