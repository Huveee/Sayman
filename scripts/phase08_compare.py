"""Phase 8 comparison: the base model (Phase 7 measurement) and the fine-tuned model, question by question on the same questions.

Reads the two measurement reports (`reports/<label>_olcum_<set>.json`). It first checks that both were taken with the same
question set and the same system (prompt, code, tools, documents) and stops otherwise. Then it writes two tables:

- target table: the Phase 7 targets, the value of each model and whether it passed;
- pairing: for each metric the number of questions that improved / got worse and the McNemar p value. Tool questions are also
  split into "not in training" (the 8 templates that Phase 8 training never saw) and "in training"; unauthorized attempts
  are split by kind.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08_compare.py --set gelistirme
    .venv\\Scripts\\python -I scripts\\phase08_compare.py --set saklanan
    .venv\\Scripts\\python -I scripts\\phase08_compare.py --set gelistirme --temel faz7 --ayarli faz7   # trial
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

from support.phase08_training_questions import EXCLUDED_INTENTS  # noqa: E402
from sayman.evaluation import pairing  # noqa: E402

QUESTION_SET = ROOT / "reports" / "faz7_soru_seti.json"


def load_report(label: str, subset: str) -> dict:
    p = ROOT / "reports" / f"{label}_olcum_{subset}.json"
    if not p.exists():
        raise SystemExit(f"rapor yok: {p.relative_to(ROOT)}")
    return json.loads(p.read_text(encoding="utf-8"))


def check_comparable(base: dict, tuned: dict) -> list[str]:
    """Differences that invalidate the comparison (errors) and points to watch (warnings)."""
    errors = []
    for field in ("set", "soru_seti_ozeti"):
        if base[field] != tuned[field]:
            errors.append(f"{field} farklı: {base[field]} ↔ {tuned[field]}")
    if base["sistem"]["ozet"] != tuned["sistem"]["ozet"]:
        differing = [k for k in base["sistem"] if base["sistem"][k] != tuned["sistem"].get(k)]
        errors.append(f"sistem özeti farklı ({', '.join(differing)})")
    for name, r in (("temel", base), ("ayarli", tuned)):
        done, total = r["tamamlanan"].split("/")
        if done != total:
            errors.append(f"{name} ölçümü bitmemiş: {r['tamamlanan']}")
    if [h["ad"] for h in base["hedefler"]] != [h["ad"] for h in tuned["hedefler"]]:
        errors.append("hedef listeleri farklı")
    return errors


def summary_differences(r: dict, section_result: dict, side: str) -> list[str]:
    """Are this script's rates the same as those in the Phase 7 summary? If a definition drifts the comparison is wrong."""
    differences = []
    for section, record in section_result.items():
        for name, row in record["metrikler"].items():
            expected = r["ozet"][section].get(name)
            if expected is None:
                continue
            found = row[side]
            if isinstance(expected, int) and not isinstance(expected, bool):  # leakage is a count in the summary
                found = round(found * row["n"])
            if expected != found:
                differences.append(f"{side} {section}.{name}: özet {expected}, eşleştirme {found}")
    return differences


def groups(subset: str) -> dict[str, str]:
    question_set = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    g = {}
    for s in question_set["sorular"]:
        if s["set"] != subset:
            continue
        if s["bolum"] == "arac":
            g[s["soru_id"]] = "egitimde_yok" if s["sablon"] in EXCLUDED_INTENTS else "egitimde_var"
        elif s["bolum"] == "yetkisiz":
            g[s["soru_id"]] = s["tur"]
    return g


def print_tables(result: dict) -> None:
    print(f"\nHedefler ({result['set']}, {result['temel']} ↔ {result['ayarli']})")
    print(f"  {'hedef':26} {'eşik':>8} {'temel':>8}    {'ayarlı':>8}")
    for h in result["hedefler"]:
        sign = "≤" if h["yon"] == "en_cok" else "≥"
        print(f"  {h['ad']:26} {sign}{h['esik']:>7} {h['temel']:>8} {'✓' if h['temel_gecti'] else '✗'}  "
              f"{h['ayarli']:>8} {'✓' if h['ayarli_gecti'] else '✗'}")
    print("\nSoru soru (iyileşen / kötüleşen, p)")
    for section, record in result["eslestirme"]["bolum"].items():
        print(f"  {section} (n={record['n']})")
        for name, m in record["metrikler"].items():
            print(f"    {name:24} {m['temel']!s:>7} → {m['ayarli']!s:<7}  +{m['iyilesen']} / -{m['kotulesen']}  "
                  f"p={m['p']}")
        for g, gk in record["gruplar"].items():
            main_metric = "cevap_dogrulugu" if section == "arac" else "reddetti"
            m = gk["metrikler"].get(main_metric)
            if m:
                print(f"      · {g:22} n={gk['n']:<3} {main_metric}: {m['temel']} → {m['ayarli']}  "
                      f"+{m['iyilesen']} / -{m['kotulesen']}  p={m['p']}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8: temel ↔ ince ayarlı model eşleştirmesi")
    ap.add_argument("--set", required=True, choices=("gelistirme", "saklanan"))
    ap.add_argument("--temel", default="faz7")
    ap.add_argument("--ayarli", default="faz8_ayarli")
    ap.add_argument("--cikti", type=Path, default=None)
    arg = ap.parse_args()

    base, tuned = load_report(arg.temel, arg.set), load_report(arg.ayarli, arg.set)
    errors = check_comparable(base, tuned)
    if errors:
        raise SystemExit("karşılaştırılamaz:\n  " + "\n  ".join(errors))
    warnings = []
    if base["model_izi"]["ozet"] == tuned["model_izi"]["ozet"]:
        warnings.append("iki ölçümün model izi aynı (aynı model karşılaştırılıyor)")

    e = pairing.pair(base["sorular"], tuned["sorular"], groups(arg.set))
    differences = summary_differences(base, e["bolum"], "temel") + summary_differences(tuned, e["bolum"], "ayarli")
    if differences:
        raise SystemExit("eşleştirme oranları özetle uyuşmuyor:\n  " + "\n  ".join(differences))

    result = {
        "set": arg.set, "temel": arg.temel, "ayarli": arg.ayarli,
        "soru_seti_ozeti": base["soru_seti_ozeti"], "sistem_ozeti": base["sistem"]["ozet"],
        "model_izi": {"temel": base["model_izi"], "ayarli": tuned["model_izi"]},
        "egitimde_olmayan_sablonlar": list(EXCLUDED_INTENTS),
        "uyarilar": warnings,
        "hedefler": pairing.target_table(base["hedefler"], tuned["hedefler"]),
        "durumlar": {"temel": base["ozet"]["genel"]["durumlar"], "ayarli": tuned["ozet"]["genel"]["durumlar"]},
        "sure": {"temel": base["ozet"]["sure"], "ayarli": tuned["ozet"]["sure"]},
        "eslestirme": e,
    }
    output = arg.cikti or ROOT / "reports" / f"faz8_karsilastirma_{arg.set}.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print_tables(result)
    for u in warnings:
        print("uyarı:", u)
    print(f"\nyazıldı: {output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")


if __name__ == "__main__":
    main()
