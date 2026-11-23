"""Teacher: a fake model that shows the correct behavior on the training questions (the Phase 7 oracle, for training).

Differences from the oracle:
- The answers are natural short Turkish sentences, not labelled lines (prepared when the question is generated).
- In document questions it looks at the results the real searcher returned: if the right article came, it quotes it;
  if it did not, it searches once more with the name of the counterparty and the topic; if it still does not come, it
  gives no answer (this question does not enter training).
- The first step does not depend on the role: the model does not see the role of the user (the system prompt and the
  tool list are the same for everyone), so the same question starts with the same tool call in every role; the gate
  enforces permission. The role changes only the tool result, and so the second step.

Fields of the question record: bolum, cagri ([tool, arguments] or None), cevap (for tool questions and for answers
without tools), and in document questions dogru_parca, anahtarlar, ikinci_sorgu, konu_cumlesi.
"""

from __future__ import annotations

import json
import random
import re

from sayman.assistant.orchestrator import ModelResponse
from sayman.evaluation.oracle import citation_window


class NoAnswer(Exception):
    """The teacher does not know a correct answer in this case; the turn is not taken into training."""


def _tool_results(messages: list[dict]) -> list[dict]:
    return [json.loads(m["content"]) for m in messages if m["role"] == "tool"]


def _document_results(results: list[dict]) -> list[list[dict]]:
    """The articles returned by every belge_ara call (in order)."""
    return [(r.get("sonuc") or {}).get("sonuclar", []) for r in results if r.get("arac") == "belge_ara"]


def document_answer(question: dict, article_text: str, chunk_id: str) -> str:
    """A short summary (only if its digits occur verbatim in the article) + a verbatim quote from the article + the
    chunk identifier."""
    window = citation_window(article_text, question.get("anahtarlar") or [])
    digits = re.findall(r"\d+(?:[.,]\d+)*", question.get("deger") or "")
    if question.get("deger") and all(r in article_text for r in digits):
        lead = f"{question['konu_cumlesi']}: {question['deger']}."
    else:
        lead = f"{question['konu_cumlesi']} maddede şöyle geçiyor:"
    return f"{lead} \"{window}\" [{chunk_id}]"


def not_found_answer(question: dict, first_chunk: dict | None, rng: random.Random) -> str:
    """The information that was searched for is not in the articles that came. The verifier asks for a citation if the
    document search returned results: a short quote is given from the first article that was looked at."""
    lead = rng.choice(["Görebildiğim belgelerde bu soruyu cevaplayan bir madde bulamadım.",
                      "Erişebildiğim belgelerde bu konuda bir hüküm bulamadım."])
    if not first_chunk:
        return lead
    window = " ".join(first_chunk["metin"].split()[:8]).rstrip(" .,;:")
    if '"' in window:
        window = window.split('"')[0].rstrip(" .,;:")
    return f"{lead} Arama en yakın olarak şunu getirdi: \"{window}\" [{first_chunk['parca_id']}]"


class Teacher:
    """Given to the Assistant as its model; the runner fills the `soru` field before every question."""

    def __init__(self):
        self.soru: dict | None = None

    def __call__(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelResponse:
        s = self.soru
        rng = random.Random(f"ogretmen:{s['soru_id']}")
        results = _tool_results(messages)
        has_assistant_message = any(m["role"] == "assistant" for m in messages)
        if sum(m["role"] == "user" for m in messages) > 1:
            raise NoAnswer("düzeltme turu: öğretmenin cevabı doğrulayıcıdan geçmedi")
        if not results and not has_assistant_message:
            if s.get("cagri"):
                tool, arg = s["cagri"]
                return ModelResponse(tool_calls=[(tool, dict(arg))])
            return ModelResponse(content=s["cevap"])
        last = results[-1]
        if last.get("hata"):
            raise NoAnswer(f"araç hata verdi: {last.get('hata')}")
        if s["bolum"] == "arac":
            return ModelResponse(content=s["cevap"])
        if s["bolum"] == "belge":
            return self._document_step(s, results, rng)
        # unauthorized: the tool returned a result but out of scope (customer not found or the hidden article missing)
        if s.get("tur") == "belge_yetkisi":
            retrieved = [m for group in _document_results(results) for m in group]
            if any(m["parca_id"] == s["gizli_parca"] for m in retrieved):
                raise NoAnswer("gizli madde aramada geldi: kapı denetimi beklenmedik")
            return ModelResponse(content=not_found_answer(s, retrieved[0] if retrieved else None, rng))
        if (last.get("sonuc") or {}).get("bulunamadi"):
            return ModelResponse(content=s["cevap"])
        raise NoAnswer("yetkisiz denemede araç sonuç döndürdü")

    def _document_step(self, s: dict, results: list[dict], rng: random.Random) -> ModelResponse:
        groups = _document_results(results)
        for group in groups:
            for m in group:
                if m["parca_id"] == s["dogru_parca"]:
                    return ModelResponse(content=document_answer(s, m["metin"], m["parca_id"]))
        if len(groups) == 1 and s.get("ikinci_sorgu"):
            return ModelResponse(tool_calls=[("belge_ara", {"sorgu": s["ikinci_sorgu"]})])
        raise NoAnswer("doğru madde iki aramada da gelmedi")
