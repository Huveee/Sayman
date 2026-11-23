"""Phase 8: question-by-question pairing of two measurements (base model ↔ fine-tuned model).

Every metric is a yes/no field that the Phase 7 scorer gives per question (`sayman.evaluation.scoring.score`). Rates
are computed the same way as in the Phase 7 summary: the share of questions whose field is "yes". Which direction is
good depends on the metric; for suspected wrong number and for leakage "no" is good.

- iyilesen: questions that are bad in the base model and good in the fine-tuned model.
- kotulesen: questions that are good in the base model and bad in the fine-tuned model.
- p: exact McNemar test. It looks only at the questions that changed and, under the assumption "the two models are
  actually equal, the changes are random like coin flips", gives the probability of seeing such an unbalanced split.
"""

from __future__ import annotations

from math import comb

# section -> (name, score field, is "yes" good)
METRICS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "arac": (("arac_secimi", "arac_dogru", True), ("parametre", "parametre_dogru", True),
             ("cevap_dogrulugu", "cevap_dogru", True), ("dogrulanmis_yanlis_sayi", "yanlis_sayi_suphesi", False)),
    "belge": (("belge_ara_cagrildi", "belge_ara_cagrildi", True), ("isabet", "isabet", True),
              ("cevap_dogrulugu", "cevap_dogru", True), ("alinti_gecerli", "alinti_gecerli", True),
              ("dogru_parcadan_alinti", "dogru_parcadan_alinti", True)),
    "yetkisiz": (("sizinti", "sizinti", False), ("reddetti", "reddetti", True)),
}


def mcnemar_p(improved: int, worsened: int) -> float | None:
    """Exact, two-sided McNemar p value. None if no question changed."""
    n = improved + worsened
    if n == 0:
        return None
    k = min(improved, worsened)
    return round(min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n), 4)


def _ratio(count: int, n: int) -> float | None:
    return round(count / n, 4) if n else None


def metric_row(base: list[dict], tuned: list[dict], field: str, yes_is_good: bool) -> dict:
    """The paired row of one metric, for question scores in the same order."""
    t = [bool(p[field]) for p in base]
    a = [bool(p[field]) for p in tuned]
    good_base = [x == yes_is_good for x in t]
    good_tuned = [x == yes_is_good for x in a]
    improved = sum(1 for x, y in zip(good_base, good_tuned) if not x and y)
    worsened = sum(1 for x, y in zip(good_base, good_tuned) if x and not y)
    n = len(t)
    return {"n": n, "temel": _ratio(sum(t), n), "ayarli": _ratio(sum(a), n), "evet_iyi": yes_is_good,
            "iyilesen": improved, "kotulesen": worsened, "p": mcnemar_p(improved, worsened)}


def pair(base: list[dict], tuned: list[dict], groups: dict[str, str] | None = None) -> dict:
    """base, tuned: the `sorular` lists in the Phase 7 summary. groups: question id -> group name (e.g. not in / in
    training).

    The same questions must be present in both lists with the same sections; otherwise ValueError."""
    t = {p["soru_id"]: p for p in base}
    a = {p["soru_id"]: p for p in tuned}
    if len(t) != len(base) or len(a) != len(tuned):
        raise ValueError("bir ölçümde aynı soru iki kez var")
    if set(t) != set(a):
        raise ValueError(f"sorular farklı: yalnız temelde {sorted(set(t) - set(a))[:5]}, "
                         f"yalnız ayarlıda {sorted(set(a) - set(t))[:5]}")
    differing = [s for s in t if t[s]["bolum"] != a[s]["bolum"]]
    if differing:
        raise ValueError(f"bölümü farklı sorular: {differing[:5]}")
    groups = groups or {}

    result: dict = {"bolum": {}, "degisenler": []}
    for section, metrics in METRICS.items():
        ids = sorted(s for s in t if t[s]["bolum"] == section)
        if not ids:
            continue
        entry = {"n": len(ids), "metrikler": {}, "gruplar": {}}
        for name, field, yes_is_good in metrics:
            entry["metrikler"][name] = metric_row([t[s] for s in ids], [a[s] for s in ids], field, yes_is_good)
            for s in ids:
                if bool(t[s][field]) != bool(a[s][field]):
                    got_better = bool(a[s][field]) == yes_is_good
                    result["degisenler"].append({"soru_id": s, "bolum": section, "metrik": name,
                                                "yon": "iyilesti" if got_better else "kotulesti",
                                                "grup": groups.get(s)})
        for g in sorted({groups[s] for s in ids if s in groups}):
            group_ids = [s for s in ids if groups.get(s) == g]
            entry["gruplar"][g] = {"n": len(group_ids), "metrikler": {
                name: metric_row([t[s] for s in group_ids], [a[s] for s in group_ids], field, yes_is_good)
                for name, field, yes_is_good in metrics}}
        result["bolum"][section] = entry
    return result


def target_table(base_targets: list[dict], tuned_targets: list[dict]) -> list[dict]:
    """Puts the Phase 7 target lists side by side. Both sides must have the same targets with the same thresholds."""
    a = {h["ad"]: h for h in tuned_targets}
    if [h["ad"] for h in base_targets] != [h["ad"] for h in tuned_targets]:
        raise ValueError("hedef listeleri farklı")
    rows = []
    for h in base_targets:
        k = a[h["ad"]]
        if (h["metrik"], h["yon"], h["esik"]) != (k["metrik"], k["yon"], k["esik"]):
            raise ValueError(f"hedef tanımı farklı: {h['ad']}")
        rows.append({"ad": h["ad"], "yon": h["yon"], "esik": h["esik"], "zorunlu": h.get("zorunlu", False),
                         "temel": h["deger"], "temel_gecti": h["gecti"], "ayarli": k["deger"],
                         "ayarli_gecti": k["gecti"]})
    return rows
