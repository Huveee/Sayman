"""Phase 5 scoring: at what rank is the correct chunk among the chunks a search ranked?

This file was written before the search code and was not changed during tuning.

- The search returns a list of (parca_id, skor). Order is by score descending; equal scores are ordered by chunk
  identifier (the search's own order is not used, so ties are not left to chance).
- hit@k (`isabet@k`): whether the correct chunk is within the first k results. The acceptance criterion is hit@5 >= 0.85
  (since there is a single correct chunk this equals recall@5).
- MRR@10: the reciprocal of the correct chunk's rank (0 if not within the first 10); for the report.
- Results are also given separately by question kind and question style (close/far pattern).
"""

from __future__ import annotations

from collections import defaultdict


def order_chunks(results: list[tuple[str, float]]) -> list[str]:
    return [p for p, _ in sorted(results, key=lambda x: (-x[1], x[0]))]


def rank_of(results: list[tuple[str, float]], correct_chunk: str) -> int | None:
    """1-based rank of the correct chunk; None if it is not in the list."""
    s = order_chunks(results)
    return s.index(correct_chunk) + 1 if correct_chunk in s else None


def score(questions: list[dict], results: dict[str, list[tuple[str, float]]], k: int = 5) -> dict:
    """results: `soru_id` -> list of (parca_id, skor)."""
    rows, groups, styles = [], defaultdict(list), defaultdict(list)
    for s in questions:
        r = rank_of(results[s["soru_id"]], s["dogru_parca"])
        rows.append({"soru_id": s["soru_id"], "sira": r})
        groups[s["tur"]].append(r)
        styles[s.get("stil", "-")].append(r)
    all_ranks = [x["sira"] for x in rows]

    def summarize(ranks):
        n = len(ranks)
        return {"soru": n, f"isabet@{k}": round(sum(1 for r in ranks if r and r <= k) / n, 4) if n else None,
                "isabet@1": round(sum(1 for r in ranks if r == 1) / n, 4) if n else None,
                "mrr@10": round(sum(1 / r for r in ranks if r and r <= 10) / n, 4) if n else None}

    return {**summarize(all_ranks), "ture_gore": {t: summarize(v) for t, v in sorted(groups.items())},
            "stile_gore": {t: summarize(v) for t, v in sorted(styles.items())},
            "kacanlar": [x["soru_id"] for x in rows if not (x["sira"] and x["sira"] <= k)]}
