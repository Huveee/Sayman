"""GGUF özeti: küçük bir yapay GGUF dosyası yazılır; okuyucu üst bilgiyi, tensör türlerini ve veri özetlerini doğru
çıkarmalı, karşılaştırma da veri, tür ve üst bilgi farkını yakalamalı."""

import struct

import pytest

from sayman.egitim import gguf_ozet


def _dize(s: str) -> bytes:
    b = s.encode()
    return struct.pack("<Q", len(b)) + b


def _yaz(yol, kv: dict, tensorler: list[tuple[str, list[int], int, bytes]], hiza: int = 32):
    """kv: {anahtar: (tür, değer)}; tensorler: (ad, boyut, ggml türü, veri)."""
    govde = b"GGUF" + struct.pack("<IQQ", 3, len(tensorler), len(kv))
    for k, (tur, deger) in kv.items():
        govde += _dize(k) + struct.pack("<I", tur)
        if tur == 8:
            govde += _dize(deger)
        elif tur == 9:
            govde += struct.pack("<IQ", 4, len(deger)) + b"".join(struct.pack("<I", x) for x in deger)
        else:
            govde += struct.pack({4: "<I", 6: "<f"}[tur], deger)
    konum, veri = 0, b""
    for ad, boyut, tur, b in tensorler:
        govde += _dize(ad) + struct.pack("<I", len(boyut)) + b"".join(struct.pack("<Q", d) for d in boyut)
        govde += struct.pack("<IQ", tur, konum)
        dolgu = (-len(b)) % hiza
        veri += b + b"\0" * dolgu
        konum += len(b) + dolgu
    govde += b"\0" * ((-len(govde)) % hiza)
    yol.write_bytes(govde + veri)


def _temel(tmp_path, ad="a.gguf", q8=b"\x01" * 34, tur_b=8, eos=7):
    p = tmp_path / ad
    _yaz(p, {"general.architecture": (8, "qwen35"), "qwen35.block_count": (4, 2),
             "tokenizer.ggml.eos_token_id": (4, eos), "general.name": (8, ad),
             "tokenizer.ggml.token_type": (9, list(range(40)))},
         [("tok.weight", [4, 2], 0, struct.pack("<8f", *range(8))), ("blk.0.w", [32], tur_b, q8)])
    return p


def test_ozet_ust_bilgi_ve_tensorleri_okur(tmp_path):
    o = gguf_ozet.ozet(str(_temel(tmp_path)))
    assert o["surum"] == 3 and o["kv"]["qwen35.block_count"] == 2
    assert o["kv"]["tokenizer.ggml.token_type"]["uzunluk"] == 40  # uzun dizi özetlenir
    t = o["tensorler"]
    assert t["tok.weight"]["tur"] == "F32" and t["tok.weight"]["bayt"] == 32 and t["tok.weight"]["boyut"] == [4, 2]
    assert t["blk.0.w"]["tur"] == "Q8_0" and t["blk.0.w"]["bayt"] == 34


def test_ayni_veri_ayni_ozet_farkli_ad_onemsiz(tmp_path):
    a = gguf_ozet.ozet(str(_temel(tmp_path, "a.gguf")))
    b = gguf_ozet.ozet(str(_temel(tmp_path, "b.gguf")))  # yalnızca general.name farklı
    k = gguf_ozet.karsilastir(a, b)
    assert k["kv_farki"] == {} and k["veri_farki"] == [] and k["tur_farki"] == [] and k["ayni_tensor"] == 2


def test_veri_tur_ve_ust_bilgi_farki_yakalanir(tmp_path):
    a = gguf_ozet.ozet(str(_temel(tmp_path, "a.gguf")))
    veri = gguf_ozet.karsilastir(a, gguf_ozet.ozet(str(_temel(tmp_path, "b.gguf", q8=b"\x02" * 34))))
    assert veri["veri_farki"] == ["blk.0.w"] and veri["ayni_tensor"] == 1
    tur = gguf_ozet.karsilastir(a, gguf_ozet.ozet(str(_temel(tmp_path, "c.gguf", q8=b"\x01" * 18, tur_b=2))))
    assert tur["tur_farki"] == ["blk.0.w"]
    kv = gguf_ozet.karsilastir(a, gguf_ozet.ozet(str(_temel(tmp_path, "d.gguf", eos=9))))
    assert kv["kv_farki"] == {"tokenizer.ggml.eos_token_id": [7, 9]}


def test_kisa_dosya_ve_yanlis_baslik_reddedilir(tmp_path):
    p = _temel(tmp_path)
    kisa = tmp_path / "kisa.gguf"
    kisa.write_bytes(p.read_bytes()[:-40])  # son 30 bayt dolgu; 40 bayt kesilince son tensörün verisi eksik kalır
    with pytest.raises(ValueError, match="sonunu"):
        gguf_ozet.ozet(str(kisa))
    yanlis = tmp_path / "yanlis.gguf"
    yanlis.write_bytes(b"GGML" + p.read_bytes()[4:])
    with pytest.raises(ValueError, match="GGUF"):
        gguf_ozet.ozet(str(yanlis))


def test_birlestirme_betigi_sabitleri_doldurur_ve_okuyucuyu_icerir():
    import ast
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import faz8_kaggle

    s = faz8_kaggle.birlestir_betigi("adapter_epok2", "sayman-lora-epok2.gguf", "f16")
    ast.parse(s)
    ns = {"__name__": "deneme"}
    exec(compile(s, "birlestir", "exec"), ns)
    assert (ns["ADAPTER"], ns["LORA_GGUF"], ns["ARA_TUR"]) == ("adapter_epok2", "sayman-lora-epok2.gguf", "f16")
    assert ns["ozet"].__module__ == "deneme" and callable(ns["karsilastir"])  # okuyucu dosyanın içinde
    assert "# <<gguf_ozet>>" not in s
