"""Fingerprint of the measured system: system prompt, settings, tool definitions, fixed texts, roles and source code.

The Phase 7 report writes a digest of the measured system; Phase 8 measures the fine-tuned model with the same system
digest. So the before/after difference comes only from the model. The model part (model name, options, the model
digest in Ollama) is kept separate.

When the source code is digested, line endings are normalized (on Windows git may convert them to CRLF).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCES = ("src/sayman/assistant/orchestrator.py", "src/sayman/assistant/verifier.py",
             "src/sayman/tools/catalog.py", "src/sayman/tools/server.py", "src/sayman/tools/scope.py",
             "src/sayman/tools/roles.json", "src/sayman/warehouse/metrics.py", "src/sayman/documents/citation.py",
             "src/sayman/documents/search.py", "src/sayman/documents/semantic.py")


def _digest(data) -> str:
    if not isinstance(data, (bytes, str)):
        data = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    return _digest(Path(path).read_bytes().replace(b"\r\n", b"\n"))


def system(document_method: str = "anlam") -> dict:
    from sayman.tools import catalog
    from sayman.assistant import orchestrator

    settings = dataclasses.asdict(orchestrator.Settings())
    parts = {
        "sistem_istemi": _digest(orchestrator.SYSTEM_PROMPT),
        "metinler": _digest(orchestrator.FIXED_TEXTS),
        "arac_tanimlari": _digest(orchestrator.tool_definitions()),
        "ayar": settings,
        "belge": {"yontem": document_method, "varsayilan": catalog.DOCUMENT_DEFAULT_LIMIT, "en_fazla": catalog.DOCUMENT_MAX_LIMIT,
                  "metin_siniri": catalog.DOCUMENT_TEXT_LIMIT},
        "kaynak_kodu": {k: file_digest(ROOT / k) for k in SOURCES},
    }
    return {"ozet": _digest({k: v for k, v in parts.items() if k != "kaynak_kodu"}),
            "kod_ozeti": _digest(parts["kaynak_kodu"]), **parts}


def model(ollama_digest: bool = True, model_name: str | None = None) -> dict:
    """Fingerprint of the model part. In Phase 8 the fine-tuned model is given as `model_name`; the options stay the
    same."""
    from sayman.assistant.model import MODEL, OPTIONS

    name = model_name or MODEL
    m = {"model": name, "secenekler": OPTIONS, "think": False}
    if ollama_digest:
        try:
            import ollama
            listing = ollama.Client(timeout=10).list()
            m["ollama_ozeti"] = next((x.digest for x in listing.models if x.model in (name, f"{name}:latest")), None)
        except Exception as e:  # if Ollama is not running, no digest is written
            m["ollama_ozeti"] = f"okunamadi: {type(e).__name__}"
    m["ozet"] = _digest({k: v for k, v in m.items() if k != "ozet"})
    return m
