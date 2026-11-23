"""GGUF summary: a small synthetic GGUF file is written; the reader must extract the header, the tensor types and the data
digests correctly, and the comparison must catch differences in data, type and header."""

import struct

import pytest

from sayman.training import gguf_summary


def _pack_string(s: str) -> bytes:
    b = s.encode()
    return struct.pack("<Q", len(b)) + b


def _write_gguf(path, kv: dict, tensors: list[tuple[str, list[int], int, bytes]], alignment: int = 32):
    """kv: {key: (kind, value)}; tensors: (name, dims, ggml kind, data)."""
    body = b"GGUF" + struct.pack("<IQQ", 3, len(tensors), len(kv))
    for k, (kind, value) in kv.items():
        body += _pack_string(k) + struct.pack("<I", kind)
        if kind == 8:
            body += _pack_string(value)
        elif kind == 9:
            body += struct.pack("<IQ", 4, len(value)) + b"".join(struct.pack("<I", x) for x in value)
        else:
            body += struct.pack({4: "<I", 6: "<f"}[kind], value)
    offset, data = 0, b""
    for name, dims, kind, b in tensors:
        body += _pack_string(name) + struct.pack("<I", len(dims)) + b"".join(struct.pack("<Q", d) for d in dims)
        body += struct.pack("<IQ", kind, offset)
        padding = (-len(b)) % alignment
        data += b + b"\0" * padding
        offset += len(b) + padding
    body += b"\0" * ((-len(body)) % alignment)
    path.write_bytes(body + data)


def _base_gguf(tmp_path, name="a.gguf", q8=b"\x01" * 34, block_kind=8, eos=7):
    p = tmp_path / name
    _write_gguf(p, {"general.architecture": (8, "qwen35"), "qwen35.block_count": (4, 2),
             "tokenizer.ggml.eos_token_id": (4, eos), "general.name": (8, name),
             "tokenizer.ggml.token_type": (9, list(range(40)))},
         [("tok.weight", [4, 2], 0, struct.pack("<8f", *range(8))), ("blk.0.w", [32], block_kind, q8)])
    return p


def test_summary_reads_header_and_tensors(tmp_path):
    o = gguf_summary.summarize(str(_base_gguf(tmp_path)))
    assert o["surum"] == 3 and o["kv"]["qwen35.block_count"] == 2
    assert o["kv"]["tokenizer.ggml.token_type"]["uzunluk"] == 40  # the long array is summarised
    t = o["tensorler"]
    assert t["tok.weight"]["tur"] == "F32" and t["tok.weight"]["bayt"] == 32 and t["tok.weight"]["boyut"] == [4, 2]
    assert t["blk.0.w"]["tur"] == "Q8_0" and t["blk.0.w"]["bayt"] == 34


def test_same_data_same_summary_different_name_irrelevant(tmp_path):
    a = gguf_summary.summarize(str(_base_gguf(tmp_path, "a.gguf")))
    b = gguf_summary.summarize(str(_base_gguf(tmp_path, "b.gguf")))  # only general.name differs
    k = gguf_summary.compare(a, b)
    assert k["kv_farki"] == {} and k["veri_farki"] == [] and k["tur_farki"] == [] and k["ayni_tensor"] == 2


def test_data_kind_and_header_differences_are_caught(tmp_path):
    a = gguf_summary.summarize(str(_base_gguf(tmp_path, "a.gguf")))
    data = gguf_summary.compare(a, gguf_summary.summarize(str(_base_gguf(tmp_path, "b.gguf", q8=b"\x02" * 34))))
    assert data["veri_farki"] == ["blk.0.w"] and data["ayni_tensor"] == 1
    kind = gguf_summary.compare(a, gguf_summary.summarize(str(_base_gguf(tmp_path, "c.gguf", q8=b"\x01" * 18, block_kind=2))))
    assert kind["tur_farki"] == ["blk.0.w"]
    kv = gguf_summary.compare(a, gguf_summary.summarize(str(_base_gguf(tmp_path, "d.gguf", eos=9))))
    assert kv["kv_farki"] == {"tokenizer.ggml.eos_token_id": [7, 9]}


def test_short_file_and_wrong_header_are_rejected(tmp_path):
    p = _base_gguf(tmp_path)
    short_file = tmp_path / "kisa.gguf"
    short_file.write_bytes(p.read_bytes()[:-40])  # the last 30 bytes are padding; cutting 40 bytes leaves the data of the last tensor incomplete
    with pytest.raises(ValueError, match="sonunu"):
        gguf_summary.summarize(str(short_file))
    wrong_file = tmp_path / "yanlis.gguf"
    wrong_file.write_bytes(b"GGML" + p.read_bytes()[4:])
    with pytest.raises(ValueError, match="GGUF"):
        gguf_summary.summarize(str(wrong_file))


def test_merge_script_fills_constants_and_includes_reader():
    import ast
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import phase08_kaggle

    s = phase08_kaggle.merge_script("adapter_epok2", "sayman-lora-epok2.gguf", "f16")
    ast.parse(s)
    ns = {"__name__": "deneme"}
    exec(compile(s, "birlestir", "exec"), ns)
    assert (ns["ADAPTER"], ns["LORA_GGUF"], ns["ARA_TUR"]) == ("adapter_epok2", "sayman-lora-epok2.gguf", "f16")
    assert ns["summarize"].__module__ == "deneme" and callable(ns["compare"])  # the reader is inside the file
    assert "# <<gguf_ozet>>" not in s
