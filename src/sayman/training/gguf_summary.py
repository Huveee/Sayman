"""GGUF dosyasının özeti: üst bilgiler (anahtar-değer) ve her tensörün adı, türü, boyutu ve verisinin özeti.

Faz 8'de iki denetim için kullanılır:
- Adillik: Kaggle'da kendi yolumuzla (dönüştürme + Q4_K_M) ürettiğimiz temel model, Ollama'daki temel modelle tensör
  tensör aynı mı? Aynıysa temel ↔ ince ayarlı fark yalnızca eğitimden gelir.
- Birleştirme: ince ayarlı modelde yalnızca LoRA'nın dokunduğu tensörler mi değişti?

Aynı kod hem bu bilgisayarda hem Kaggle'da çalışır (scripts/faz8_kaggle.py Kaggle betiğine bu dosyayı ekler); bu yüzden
yalnızca standart kütüphane kullanır. Dosya baştan sona okunmaz: üst bilgi okunur, sonra her tensörün kendi baytları.
"""

from __future__ import annotations

import hashlib
import struct

# ggml türü -> (ad, blok boyu, blok bayt) (ggml/include/ggml.h, ggml/src/ggml.c)
TURLER = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20), 6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36), 10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144), 13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74), 18: ("IQ3_XXS", 256, 98), 19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110), 22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1), 25: ("I16", 1, 2), 26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56), 30: ("BF16", 1, 2), 34: ("TQ1_0", 256, 54), 35: ("TQ2_0", 256, 66),
    39: ("MXFP4", 32, 17),
}
_SAYI = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_DIZE, _DIZI = 8, 9


class _Okuyucu:
    def __init__(self, f):
        self.f = f

    def al(self, bicim: str):
        n = struct.calcsize(bicim)
        b = self.f.read(n)
        if len(b) != n:
            raise ValueError("dosya beklenenden kısa")
        return struct.unpack(bicim, b)[0]

    def dize(self) -> str:
        return self.f.read(self.al("<Q")).decode("utf-8", errors="replace")

    def deger(self, tur: int):
        if tur in _SAYI:
            return self.al(_SAYI[tur])
        if tur == _DIZE:
            return self.dize()
        if tur == _DIZI:
            ic, n = self.al("<I"), self.al("<Q")
            return [self.deger(ic) for _ in range(n)]
        raise ValueError(f"bilinmeyen GGUF değer türü {tur}")


def _kisa(deger):
    """Uzun diziler (sözlük, birleştirmeler) yerine uzunluk ve özet: karşılaştırma için yeter, rapor şişmez."""
    if isinstance(deger, list) and len(deger) > 16:
        return {"uzunluk": len(deger), "ozet": hashlib.sha256(repr(deger).encode()).hexdigest()[:16]}
    return deger


def ozet(yol: str, veri_ozeti: bool = True) -> dict:
    """{"surum", "kv": {anahtar: değer}, "tensorler": {ad: {"tur", "boyut", "bayt", "ozet"}}}"""
    with open(yol, "rb") as f:
        o = _Okuyucu(f)
        if f.read(4) != b"GGUF":
            raise ValueError("GGUF dosyası değil")
        surum, n_tensor, n_kv = o.al("<I"), o.al("<Q"), o.al("<Q")
        kv = {}
        for _ in range(n_kv):
            anahtar = o.dize()
            kv[anahtar] = _kisa(o.deger(o.al("<I")))
        bilgiler = []
        for _ in range(n_tensor):
            ad = o.dize()
            boyut = [o.al("<Q") for _ in range(o.al("<I"))]
            tur, konum = o.al("<I"), o.al("<Q")
            bilgiler.append((ad, boyut, tur, konum))
        hiza = int(kv.get("general.alignment", 32))
        bas = f.tell()
        bas += (-bas) % hiza
        tensorler = {}
        for ad, boyut, tur, konum in bilgiler:
            if tur not in TURLER:
                raise ValueError(f"{ad}: bilinmeyen ggml türü {tur}")
            tad, blok, bayt = TURLER[tur]
            eleman = 1
            for d in boyut:
                eleman *= d
            if eleman % blok:
                raise ValueError(f"{ad}: {eleman} öğe {blok}'luk bloklara bölünmüyor")
            uzunluk = eleman // blok * bayt
            kayit = {"tur": tad, "boyut": boyut, "bayt": uzunluk}
            if veri_ozeti:
                f.seek(bas + konum)
                h = hashlib.sha256()
                kalan = uzunluk
                while kalan:
                    parca = f.read(min(kalan, 1 << 24))
                    if not parca:
                        raise ValueError(f"{ad}: veri dosyanın sonunu aşıyor")
                    h.update(parca)
                    kalan -= len(parca)
                kayit["ozet"] = h.hexdigest()[:24]
            tensorler[ad] = kayit
    return {"surum": surum, "kv": kv, "tensorler": tensorler}


ONEMLI_KV = ("general.architecture", "general.file_type", "tokenizer.ggml.model", "tokenizer.ggml.pre",
             "tokenizer.ggml.tokens", "tokenizer.ggml.token_type", "tokenizer.ggml.merges",
             "tokenizer.ggml.eos_token_id", "tokenizer.ggml.bos_token_id", "tokenizer.ggml.padding_token_id")


def karsilastir(a: dict, b: dict) -> dict:
    """İki özetin farkları. Mimari anahtarları (<mimari>.*) ve ONEMLI_KV karşılaştırılır; diğer üst bilgiler (ad,
    lisans, dönüştürücü sürümü) dosyadan dosyaya değişebilir."""
    mimari = a["kv"].get("general.architecture")
    anahtarlar = sorted({k for k in set(a["kv"]) | set(b["kv"])
                         if k in ONEMLI_KV or (mimari and k.startswith(f"{mimari}."))})
    kv_farki = {k: [a["kv"].get(k), b["kv"].get(k)] for k in anahtarlar if a["kv"].get(k) != b["kv"].get(k)}
    ta, tb = a["tensorler"], b["tensorler"]
    ortak = sorted(set(ta) & set(tb))
    tur_farki = [t for t in ortak if (ta[t]["tur"], ta[t]["boyut"]) != (tb[t]["tur"], tb[t]["boyut"])]
    veri_farki = [t for t in ortak if t not in tur_farki and ta[t].get("ozet") != tb[t].get("ozet")]
    return {"kv_farki": kv_farki, "yalniz_ilkinde": sorted(set(ta) - set(tb)),
            "yalniz_ikincide": sorted(set(tb) - set(ta)), "tur_farki": tur_farki, "veri_farki": veri_farki,
            "ayni_tensor": len(ortak) - len(tur_farki) - len(veri_farki), "tensor": len(ortak)}
