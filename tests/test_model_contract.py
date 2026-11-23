"""Freezes everything the fine-tuned model and the demo front end see.

The fine-tuned model (sayman-ayarli) was trained on the Turkish system prompt, tool names, argument names and output
keys. Code may be renamed or restructured freely, but these must not change. This test drives the real demo back end
with a scripted model that calls every tool for every role and records:

- the messages and tool definitions handed to the model on each turn,
- the tool results the model reads back,
- the JSON every /api route returns to the browser.

Random call ids and timings are normalised. To rewrite the golden file after an intentional contract change, run with
SAYMAN_UPDATE_GOLDEN=1 and review the diff.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path

import support.authorization_trials as fd
from fastapi.testclient import TestClient
from test_phase02_warehouse import D, _elle_tablolar

from sayman.warehouse.loader import ambar_kur
from sayman.tools.permissions import yetki_oku
from sayman.assistant.orchestrator import ModelCevabi
from sayman.demo.app import Demo, uygulama_kur
from sayman.data.table_io import csv_yaz
from sayman.data.clock import Saat

GOLDEN = Path(__file__).resolve().parent / "golden" / "model_contract.json"
CALL_ID = re.compile(r"\bC[0-9a-f]{12}\b")
VOLATILE_KEYS = re.compile(r"(^|_)(sure|sn|ms)($|_)")


class RecordingModel:
    """Calls the queued tool on its first turn, then answers with a fixed sentence; records what it was given."""

    def __init__(self):
        self.queue: list[tuple[str, dict]] = []
        self.seen: list[dict] = []
        self.distinct: dict[str, str] = {}  # system prompts and tool lists repeat on every turn; store each once

    def _ref(self, kind: str, value) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
        return self.distinct.setdefault(text, f"{kind}#{len(self.distinct)}")

    def __call__(self, mesajlar, araclar, zaman_asimi):
        messages = json.loads(json.dumps(mesajlar, ensure_ascii=False, default=str))
        system = [self._ref("system", m) if m["role"] == "system" else m for m in messages]
        self.seen.append({"messages": system, "tools": self._ref("tools", araclar)})
        if mesajlar[-1]["role"] != "tool" and self.queue:
            return ModelCevabi(arac_cagrilari=[self.queue.pop(0)], istem_token=100, uretim_token=10)
        return ModelCevabi(icerik="Bilgi verildi.", istem_token=100, uretim_token=10)


def _normalise(value):
    if isinstance(value, dict):
        return {k: ("<volatile>" if VOLATILE_KEYS.search(k) and isinstance(v, (int, float)) else _normalise(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, str):
        return CALL_ID.sub("<call_id>", value)
    return value


def _capture(tmp_path: Path) -> dict:
    tables = _elle_tablolar()
    csv_yaz(tables, tmp_path / "csv")
    ambar_kur(tmp_path / "csv", tmp_path / "sayman.duckdb").close()
    model = RecordingModel()
    demo = Demo(ambar=tmp_path / "sayman.duckdb", durum_klasoru=tmp_path / "demo", baslangic=Saat(D, dt.date(2025, 6, 30)),
                anahtar=bytes(range(32)), yetki=yetki_oku(), tablolar=tables, model=model, zaman=lambda: 1_750_000_000.0)
    customer = tables["musteriler"][0]["ad"]
    calls = fd.en_genis_cagrilar(D, customer) + [("belge_ara", {"sorgu": "erken ödeme iskontosu"})]
    out: dict = {"api": {}, "tool_turns": {}}
    try:
        app = uygulama_kur(demo)
        anonymous = TestClient(app)
        out["api"]["GET /api/kullanicilar"] = anonymous.get("/api/kullanicilar").json()
        out["api"]["GET /api/durum"] = anonymous.get("/api/durum").json()
        for role, user in fd.KULLANICILAR.items():
            client = TestClient(app)
            api = out["api"][role] = {"POST /api/giris": client.post("/api/giris", json={"kullanici": user}).json()}
            for path in ("/api/ben", "/api/ornek_sorular", "/api/uyarilar", "/api/rapor"):
                api[f"GET {path}"] = client.get(path).json()
            turns = out["tool_turns"][role] = []
            for tool, args in calls:
                model.queue, model.seen = [(tool, args)], []
                answer = client.post("/api/sor", json={"soru": f"{tool} sorusu"}).json()
                turns.append({"call": [tool, args], "model_inputs": model.seen, "api_answer": answer})
            api["POST /api/cikis"] = client.post("/api/cikis").json()
    finally:
        demo.kapat()
    out["distinct"] = {ref: json.loads(text) for text, ref in model.distinct.items()}
    return _normalise(out)


def test_model_facing_contract_is_frozen(tmp_path):
    current = _capture(tmp_path)
    if os.environ.get("SAYMAN_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert current == expected, "Model-facing contract changed; see tests/test_model_contract.py docstring"
