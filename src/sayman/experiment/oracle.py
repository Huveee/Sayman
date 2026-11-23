"""Phase 9: the oracle of the generated arm (a fake model that knows every answer).

The oracle of the hand-written tools (`sayman.evaluation.oracle.Oracle`) makes only a single call in the first turn.
In the generated arm some answers need a chain (e.g. customer name -> customer identifier -> customer card) or the
comparison of two calls (the first due of the cheques in portfolio and in collection). This oracle walks the plan in
the frozen classification file (reports/faz9_siniflar.json) step by step: it calls one tool on every model call; the
argument of a step may come from an earlier result ({"$": [call index, path...]}). When the plan is finished it reads
the answer from the tool results (not from the expected value in the question set) and writes it in the same format as
the hand-written oracle.

In the questions whose class is "toplama" or "yok" (the answer needs collecting records, or a definition that is not
in the API) the oracle calls no tool and says it cannot answer: this is the ceiling the generated arm can reach in
this setup.
"""

from __future__ import annotations

import json

from sayman.assistant.orchestrator import ModelResponse
from sayman.evaluation.oracle import write_value
from sayman.evaluation.scoring import get_path

DIRECT = ("tek_cagri", "zincir")
NO_ANSWER = ("Bu soru, kayıtları toplamayı ya da kendim hesaplama yapmayı gerektiriyor; araç sonuçlarında hazır bir "
            "değer olmadığı için cevap veremiyorum.")


def resolve(value, results: list[dict]):
    """Replaces the {"$": [i, path...]} placeholders with the value in the "sonuc" field of the i-th tool result."""
    if isinstance(value, dict):
        if set(value) == {"$"}:
            i, *path = value["$"]
            return get_path(results[i]["sonuc"], path)
        return {k: resolve(v, results) for k, v in value.items()}
    return value


class GeneratedOracle:
    """Given to the Assistant as its model. Before every question the runner fills the `soru` field with the record in
    the question set."""

    def __init__(self, plans: dict[str, dict]):
        self.plans = plans
        self.soru: dict | None = None

    def __call__(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelResponse:
        s = self.soru
        p = self.plans[s["soru_id"]]
        if p["sinif"] not in DIRECT:
            return ModelResponse(content=NO_ANSWER)
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if any("sonuc" not in r for r in results):
            return ModelResponse(content="Sonuç alınamadı.")
        if len(results) < len(p["plan"]):
            step = p["plan"][len(results)]
            return ModelResponse(tool_calls=[(step["arac"], resolve(step["argumanlar"], results))])
        keys = s["alternatifler"][p["alternatif"]]["anahtarlar"]
        lines = []
        for c in p["cevap"]:
            a = keys[c["anahtar"]]
            lines.append(f"{a['etiket']}: {write_value({**a, 'deger': get_path(results[c['cagri']]['sonuc'], c['yol'])})}")
        return ModelResponse(content="\n".join(lines))
