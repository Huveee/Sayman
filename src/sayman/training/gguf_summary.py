"""Summary of a GGUF file: the header (key-value) and, for every tensor, its name, type, size and a digest of its
data.

It is used in Phase 8 for two checks:
- Fairness: is the base model we produced on Kaggle in our own way (conversion + Q4_K_M) the same, tensor by tensor,
  as the base model in Ollama? If it is, the base ↔ fine-tuned difference comes only from training.
- Merge: did only the tensors that LoRA touched change in the fine-tuned model?

The same code runs both on this computer and on Kaggle (scripts/phase08_kaggle.py inlines this file into the Kaggle
script); so it uses only the standard library. The file is not read from start to end: the header is read, then the
own bytes of every tensor.
"""

from __future__ import annotations

import hashlib
import struct

# ggml type -> (name, block size, block bytes) (ggml/include/ggml.h, ggml/src/ggml.c)
GGML_TYPES = {
    0: ("F32", 1, 4), 1: ("F16", 1, 2), 2: ("Q4_0", 32, 18), 3: ("Q4_1", 32, 20), 6: ("Q5_0", 32, 22),
    7: ("Q5_1", 32, 24), 8: ("Q8_0", 32, 34), 9: ("Q8_1", 32, 36), 10: ("Q2_K", 256, 84), 11: ("Q3_K", 256, 110),
    12: ("Q4_K", 256, 144), 13: ("Q5_K", 256, 176), 14: ("Q6_K", 256, 210), 15: ("Q8_K", 256, 292),
    16: ("IQ2_XXS", 256, 66), 17: ("IQ2_XS", 256, 74), 18: ("IQ3_XXS", 256, 98), 19: ("IQ1_S", 256, 50),
    20: ("IQ4_NL", 32, 18), 21: ("IQ3_S", 256, 110), 22: ("IQ2_S", 256, 82), 23: ("IQ4_XS", 256, 136),
    24: ("I8", 1, 1), 25: ("I16", 1, 2), 26: ("I32", 1, 4), 27: ("I64", 1, 8), 28: ("F64", 1, 8),
    29: ("IQ1_M", 256, 56), 30: ("BF16", 1, 2), 34: ("TQ1_0", 256, 54), 35: ("TQ2_0", 256, 66),
    39: ("MXFP4", 32, 17),
}
_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
_STRING_TYPE, _ARRAY_TYPE = 8, 9


class _Reader:
    def __init__(self, f):
        self.f = f

    def fetch(self, fmt: str):
        n = struct.calcsize(fmt)
        b = self.f.read(n)
        if len(b) != n:
            raise ValueError("dosya beklenenden kısa")
        return struct.unpack(fmt, b)[0]

    def string(self) -> str:
        return self.f.read(self.fetch("<Q")).decode("utf-8", errors="replace")

    def deger(self, type_id: int):
        if type_id in _SCALARS:
            return self.fetch(_SCALARS[type_id])
        if type_id == _STRING_TYPE:
            return self.string()
        if type_id == _ARRAY_TYPE:
            inner_type, n = self.fetch("<I"), self.fetch("<Q")
            return [self.deger(inner_type) for _ in range(n)]
        raise ValueError(f"bilinmeyen GGUF değer türü {type_id}")


def _shorten(value):
    """Length and digest instead of long arrays (vocabulary, merges): enough for comparison, and the report does not
    bloat."""
    if isinstance(value, list) and len(value) > 16:
        return {"uzunluk": len(value), "ozet": hashlib.sha256(repr(value).encode()).hexdigest()[:16]}
    return value


def summarize(path: str, with_digest: bool = True) -> dict:
    """{"surum", "kv": {key: value}, "tensorler": {name: {"tur", "boyut", "bayt", "ozet"}}}"""
    with open(path, "rb") as f:
        o = _Reader(f)
        if f.read(4) != b"GGUF":
            raise ValueError("GGUF dosyası değil")
        version, n_tensor, n_kv = o.fetch("<I"), o.fetch("<Q"), o.fetch("<Q")
        kv = {}
        for _ in range(n_kv):
            key = o.string()
            kv[key] = _shorten(o.deger(o.fetch("<I")))
        infos = []
        for _ in range(n_tensor):
            name = o.string()
            dims = [o.fetch("<Q") for _ in range(o.fetch("<I"))]
            type_id, offset = o.fetch("<I"), o.fetch("<Q")
            infos.append((name, dims, type_id, offset))
        alignment = int(kv.get("general.alignment", 32))
        data_start = f.tell()
        data_start += (-data_start) % alignment
        tensors = {}
        for name, dims, type_id, offset in infos:
            if type_id not in GGML_TYPES:
                raise ValueError(f"{name}: bilinmeyen ggml türü {type_id}")
            type_name, block_size, block_bytes = GGML_TYPES[type_id]
            elements = 1
            for d in dims:
                elements *= d
            if elements % block_size:
                raise ValueError(f"{name}: {elements} öğe {block_size}'luk bloklara bölünmüyor")
            length = elements // block_size * block_bytes
            entry = {"tur": type_name, "boyut": dims, "bayt": length}
            if with_digest:
                f.seek(data_start + offset)
                h = hashlib.sha256()
                remaining = length
                while remaining:
                    piece = f.read(min(remaining, 1 << 24))
                    if not piece:
                        raise ValueError(f"{name}: veri dosyanın sonunu aşıyor")
                    h.update(piece)
                    remaining -= len(piece)
                entry["ozet"] = h.hexdigest()[:24]
            tensors[name] = entry
    return {"surum": version, "kv": kv, "tensorler": tensors}


IMPORTANT_KV = ("general.architecture", "general.file_type", "tokenizer.ggml.model", "tokenizer.ggml.pre",
             "tokenizer.ggml.tokens", "tokenizer.ggml.token_type", "tokenizer.ggml.merges",
             "tokenizer.ggml.eos_token_id", "tokenizer.ggml.bos_token_id", "tokenizer.ggml.padding_token_id")


def compare(a: dict, b: dict) -> dict:
    """Differences of two summaries. The architecture keys (<architecture>.*) and IMPORTANT_KV are compared; the other
    headers (name, license, converter version) may change from file to file."""
    architecture = a["kv"].get("general.architecture")
    keys = sorted({k for k in set(a["kv"]) | set(b["kv"])
                         if k in IMPORTANT_KV or (architecture and k.startswith(f"{architecture}."))})
    kv_diff = {k: [a["kv"].get(k), b["kv"].get(k)] for k in keys if a["kv"].get(k) != b["kv"].get(k)}
    ta, tb = a["tensorler"], b["tensorler"]
    common = sorted(set(ta) & set(tb))
    type_diff = [t for t in common if (ta[t]["tur"], ta[t]["boyut"]) != (tb[t]["tur"], tb[t]["boyut"])]
    data_diff = [t for t in common if t not in type_diff and ta[t].get("ozet") != tb[t].get("ozet")]
    return {"kv_farki": kv_diff, "yalniz_ilkinde": sorted(set(ta) - set(tb)),
            "yalniz_ikincide": sorted(set(tb) - set(ta)), "tur_farki": type_diff, "veri_farki": data_diff,
            "ayni_tensor": len(common) - len(type_diff) - len(data_diff), "tensor": len(common)}
