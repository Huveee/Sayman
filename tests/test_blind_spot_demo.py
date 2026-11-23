"""End-to-end acceptance test of the record number fix: real data, the demo back end, the Phase 3 gate, the orchestrator
and the verifier.

The live model's way of writing cannot be controlled; in the live trial the model never wrote the number in the form the
fix targets (README). Here a scripted model calls fatura_ara for the question that comes from /api/sor and writes the
source invoice number returned by the tool in exactly that form: "564713'tür (fatura tarihi ...)". The old verifier stopped
this answer (because of the "fatura" after the number it was taken as a count, and the source number as an amount).
A model that writes the number wrong is still stopped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import support.authorization_trials as fd
import pytest
from fastapi.testclient import TestClient

from sayman.tools.permissions import read_permissions
from sayman.assistant.orchestrator import ModelResponse
from sayman.demo.app import Demo, build_app
from sayman.data.table_io import read_csv
from sayman.data.clock import read_clock

ROOT = Path(__file__).resolve().parents[1]
REAL_WAREHOUSE, REAL_CLOCK, GENERATED = (ROOT / "data" / "ambar" / "sayman.duckdb", ROOT / "data" / "saat.json",
                                       ROOT / "data" / "uretilen")
CUSTOMER = "Akın Doğa Oyuncak San. ve Tic. Ltd. Şti."
QUESTION = f"{CUSTOMER} firmasına kestiğimiz son açık faturanın numarası ne?"

pytestmark = pytest.mark.skipif(not (REAL_WAREHOUSE.exists() and REAL_CLOCK.exists() and GENERATED.exists()),
                                reason="Önce Faz 1-2 betikleri çalıştırılmalı")


class RecordNumberWriterModel:
    """On the first turn it calls fatura_ara; then it writes the source number of the first invoice in the last tool result
    (shifted by `offset`) in the form "…'tür (fatura tarihi …)". Even if a correction is requested it writes the same
    answer."""

    def __init__(self, offset: int = 0):
        self.kaydir = offset
        self.written: list[str] = []

    def __call__(self, messages, tools, timeout):
        tool_messages = [m for m in messages if m["role"] == "tool"]
        if not tool_messages:
            return ModelResponse(tool_calls=[("fatura_ara", {"musteri_adi": CUSTOMER, "durum": "acik"})],
                               prompt_tokens=100, generated_tokens=10)
        f = json.loads(tool_messages[-1]["content"])["sonuc"]["faturalar"][0]
        text = f"Son açık faturanın numarası {int(f['kaynak_fatura_no']) + self.kaydir}'tür (fatura tarihi " \
                f"{f['fatura_tarihi']})."
        self.written.append(text)
        return ModelResponse(content=text, prompt_tokens=100, generated_tokens=10)


def _ask(tmp_path: Path, model: RecordNumberWriterModel) -> dict:
    demo = Demo(warehouse=REAL_WAREHOUSE, state_dir=tmp_path / "demo", initial_clock=read_clock(REAL_CLOCK),
                key=bytes(range(32)), permissions=read_permissions(), tables=read_csv(GENERATED), model=model,
                time_fn=lambda: 1_750_000_000.0)
    try:
        c = TestClient(build_app(demo))
        assert c.post("/api/giris", json={"kullanici": fd.USERS["cfo"]}).status_code == 200
        r = c.post("/api/sor", json={"soru": QUESTION})
        assert r.status_code == 200
        return r.json()
    finally:
        demo.close()


def test_answer_is_shown_when_record_number_is_written_correctly(tmp_path):
    model = RecordNumberWriterModel()
    j = _ask(tmp_path, model)
    assert [a["arac"] for a in j["araclar"]] == ["fatura_ara"]
    assert j["durum"] == "tamam", j
    m = re.search(r"(\d{6})'tür \(fatura tarihi (\d{4}-\d{2}-\d{2})\)", j["cevap"])
    assert m, j["cevap"]
    assert model.written[0] in j["cevap"]   # the first draft was shown without being stopped
    assert len(model.written) == 1


def test_answer_is_stopped_when_record_number_is_written_wrong(tmp_path):
    model = RecordNumberWriterModel(offset=2)
    j = _ask(tmp_path, model)
    assert j["durum"] != "tamam", j
    wrong_number = re.search(r"\d{6}", model.written[0]).group(0)
    assert wrong_number not in j["cevap"]
