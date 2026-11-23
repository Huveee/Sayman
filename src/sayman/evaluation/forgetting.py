"""Phase 8 forgetting test: did fine-tuning damage the model's general Turkish?

A fixed subset is chosen from the Turkish file of Belebele (reading comprehension, 4 options, CC BY-SA 4.0). In every
question the model reads a passage and writes the number of the correct option. The base model and the fine-tuned
model are measured on the same questions, with the same Ollama options (temperature 0, seed 42, thinking off), without
tools and without a system prompt.

Target (written before fine-tuning): the accuracy of the fine-tuned model may be at most 5 points lower than that of
the base model. On 200 questions the standard error of a single rate is about 3 points; so the difference is also
reported paired (questions where one model is right and the other is wrong).
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from sayman.evaluation.pairing import mcnemar_p

QUESTION_COUNT = 200
SEED = 20261120
MAX_DROP = 5.0       # points (percentage points)
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_batch": 64, "num_predict": 8}

_DIGIT = re.compile(r"(?<!\d)([1-4])(?!\d)")


def read_questions(path: Path) -> list[dict]:
    lines = [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]
    questions = []
    for s in lines:
        questions.append({"soru_id": f"{s['link']}#{s['question_number']}", "metin": s["flores_passage"],
                        "soru": s["question"], "siklar": [s[f"mc_answer{i}"] for i in range(1, 5)],
                        "dogru": int(s["correct_answer_num"])})
    return sorted(questions, key=lambda s: s["soru_id"])


def subset(questions: list[dict], n: int = QUESTION_COUNT, seed: int = SEED) -> list[dict]:
    """A fixed subset chosen with a seed from the list sorted by question identifier (its order is fixed too)."""
    return random.Random(seed).sample(questions, n)


def build_prompt(question: dict) -> str:
    options = "\n".join(f"{i}) {s}" for i, s in enumerate(question["siklar"], 1))
    return (f"Aşağıdaki metni oku ve soruyu cevapla.\n\nMetin: {question['metin']}\n\nSoru: {question['soru']}\n\n{options}\n\n"
            "Yalnızca doğru seçeneğin numarasını yaz (1, 2, 3 ya da 4).")


def answer_number(text: str) -> int | None:
    """The first standalone digit 1-4 in the answer; None if there is none (counts as wrong)."""
    m = _DIGIT.search(text or "")
    return int(m.group(1)) if m else None


def summarize(records: list[dict]) -> dict:
    n = len(records)
    correct = sum(1 for k in records if k["secilen"] == k["dogru"])
    return {"soru": n, "dogru": correct, "dogruluk": round(100 * correct / n, 1) if n else None,
            "okunamayan": sum(1 for k in records if k["secilen"] is None)}


def compare(base: list[dict], tuned: list[dict]) -> dict:
    """Comparison of the two models on the same questions, and the result against the target."""
    t = {k["soru_id"]: k["secilen"] == k["dogru"] for k in base}
    a = {k["soru_id"]: k["secilen"] == k["dogru"] for k in tuned}
    common = sorted(set(t) & set(a))
    if not common:
        raise ValueError("ortak soru yok")
    base_acc = 100 * sum(t[s] for s in common) / len(common)
    tuned_acc = 100 * sum(a[s] for s in common) / len(common)
    only_base = sum(1 for s in common if t[s] and not a[s])
    only_tuned = sum(1 for s in common if a[s] and not t[s])
    return {"soru": len(common), "temel_dogruluk": round(base_acc, 1), "ayarli_dogruluk": round(tuned_acc, 1),
            "fark": round(tuned_acc - base_acc, 1), "yalniz_temel_dogru": only_base, "yalniz_ayarli_dogru": only_tuned,
            "p": mcnemar_p(only_tuned, only_base),
            "esik": -MAX_DROP, "gecti": tuned_acc - base_acc >= -MAX_DROP}
