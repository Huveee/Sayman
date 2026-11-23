"""Leakage check between the training data and the evaluation set.

The training questions come from a separate company (seed 43) and from separately written patterns. This module
verifies that itself; if it finds a violation, the training data is not written. What is checked:

1. Pattern: the skeleton of a training pattern (variables masked) must not be the same as the skeleton of an
   evaluation pattern, nor as similar as PATTERN_THRESHOLD as word sets.
2. Question: a filled training question must not be as similar as QUESTION_THRESHOLD to a filled evaluation question.
3. Entity: the customer, invoice or counterparty identifier in a training question must not be an identifier in the
   evaluation questions; the customer/counterparty name must not be the same (in its simplified form) as a name in the
   evaluation company.
4. Article: the text of the target article of a document question must not occur verbatim in the library of the
   evaluation company (articles made of pattern sentences can come out the same in the two companies).
5. Rate: the expected (currency, rate) value of a currency rate question must not be the expected value of a rate
   question in the evaluation set (the two companies use the same TCMB table).

The thresholds were set while this module was written, before the training patterns were checked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PATTERN_THRESHOLD = 0.6   # the Jaccard similarity of the skeleton word sets must be below this
QUESTION_THRESHOLD = 0.8    # the character-sequence similarity of filled questions (difflib ratio) must be below this

_TR_LOWER = str.maketrans("İIÇĞÖŞÜÂÎÛ", "iıçğöşüâîû")


def lower_tr(text: str) -> str:
    return text.translate(_TR_LOWER).lower()


def normalize(text: str) -> str:
    """Lowercase, no punctuation, single spaces."""
    return " ".join(re.sub(r"[^\w{}<>]+", " ", lower_tr(text)).split())


def skeleton(pattern: str) -> str:
    """'{musteri} için açık alacak?' → '<v> için açık alacak'. A variable with a capital letter ({Donem}) is masked
    too."""
    return normalize(re.sub(r"\{[^}]+\}", " <v> ", pattern))


def jaccard(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x | y else 1.0


def question_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, normalize(a), normalize(b), autojunk=False).ratio()


@dataclass
class EvaluationData:
    """Everything to be compared on the evaluation side."""
    patterns: list[str]                      # Phase 7 tool/unauthorized patterns and Phase 5 document patterns
    questions: list[str]                       # filled Phase 7 questions (two sets)
    identifiers: set[str]                      # customer and invoice identifiers in the question set
    names: set[str]                          # all customer and supplier names of the evaluation company (normalized)
    article_texts: set[str]                # all article texts in the evaluation library
    rates: set[tuple[str, str]]             # expected (currency, buying rate) values


@dataclass
class Report:
    violations: dict[str, list] = field(default_factory=dict)
    closest_pattern: float = 0.0
    closest_question: float = 0.0

    @property
    def clean(self) -> bool:
        return not any(self.violations.values())


def audit(training: list[dict], d: EvaluationData, compare_questions: bool = True) -> Report:
    """training: every question {soru_id, soru, kalip, varliklar?, adlar?, hedef_metin?, kur?}."""
    r = Report(violations={"kalip": [], "soru": [], "kimlik": [], "ad": [], "madde": [], "kur": []})
    eval_skeletons = {skeleton(k): k for k in d.patterns}
    eval_questions = [(s, normalize(s)) for s in d.questions]
    pattern_results: dict[str, tuple[float, str]] = {}
    for s in training:
        k = s.get("kalip")
        if k is not None:
            if k not in pattern_results:
                i = skeleton(k)
                pattern_results[k] = max(((1.0 if i == eval_skeleton else jaccard(i, eval_skeleton)), eval_pattern) for eval_skeleton, eval_pattern in eval_skeletons.items())
            score, eval_pattern = pattern_results[k]
            r.closest_pattern = max(r.closest_pattern, score)
            if score >= PATTERN_THRESHOLD:
                r.violations["kalip"].append({"soru_id": s["soru_id"], "kalip": k, "degerlendirme": eval_pattern,
                                            "benzerlik": round(score, 3)})
        if compare_questions:
            n = normalize(s["soru"])
            # Fast pre-filter: pairs with few common words cannot have a high sequence similarity.
            words = set(n.split())
            best, best_question = 0.0, None
            for eval_question, eval_normalized in eval_questions:
                if len(words & set(eval_normalized.split())) * 2 < min(len(words), len(eval_normalized.split())):
                    continue
                b = question_similarity(n, eval_normalized)
                if b > best:
                    best, best_question = b, eval_question
            r.closest_question = max(r.closest_question, best)
            if best >= QUESTION_THRESHOLD:
                r.violations["soru"].append({"soru_id": s["soru_id"], "soru": s["soru"], "degerlendirme": best_question,
                                           "benzerlik": round(best, 3)})
        for v in s.get("varliklar", []):
            if v in d.identifiers:
                r.violations["kimlik"].append({"soru_id": s["soru_id"], "kimlik": v})
        for name in s.get("adlar", []):
            if normalize(name) in d.names:
                r.violations["ad"].append({"soru_id": s["soru_id"], "ad": name})
        if s.get("hedef_metin") is not None and s["hedef_metin"] in d.article_texts:
            r.violations["madde"].append({"soru_id": s["soru_id"], "parca": s.get("hedef_parca")})
        if s.get("kur") is not None and tuple(s["kur"]) in d.rates:
            r.violations["kur"].append({"soru_id": s["soru_id"], "kur": list(s["kur"])})
    return r
