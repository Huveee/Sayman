"""Phase 8b: generates the new test set, checks leakage and freezes it (before any model is run).

Set: tests/support/phase08b_questions.py (groups S, K, T, F, G). The customers and invoices are chosen from those not used
in the two Phase 7 sets. The expected values are found by running the tool functions directly, as in Phase 7.

Checks (the results are written to the file):
- Leakage: the Phase 8 check (pattern, question, identifier, name, rate) between the Phase 8 training data
  (data/egitim/sorular.json) and this set. If there is a violation the file is not written.
- Similarity to Phase 7: the closest similarity of the new patterns and questions to the Phase 7 set (for information; it was
  not trained on the Phase 7 set, but the Phase 8b rules were written by looking at that set).
- Customer hint: the verifier's decision "the question asks about a specific customer" on all the questions; detection on
  the customer-specific questions and false alarms on the others. Whatever the result is, it is written; the patterns are
  not changed accordingly.

Output:  reports/faz8b_soru_seti.json (questions, targets, preregistration, checks and digest)

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08b_generate_questions.py
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import support.phase07_questions as f7  # noqa: E402
import support.phase08b_questions as y  # noqa: E402

from sayman.assistant.verifier import is_customer_question  # noqa: E402
from sayman.documents import library  # noqa: E402
from sayman.training.leakage import EvaluationData, audit, skeleton, jaccard, normalize, question_similarity  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

OUTPUT = ROOT / "reports" / "faz8b_soru_seti.json"
PHASE7_SET = ROOT / "reports" / "faz7_soru_seti.json"
TRAINING_QUESTIONS = ROOT / "data" / "egitim" / "sorular.json"


def leakage_check(questions: list[dict], entities: list[str], training_questions: list[dict]) -> dict:
    patterns = [k for sb, _, _ in y.TEMPLATES for k in sb.patterns] + [k for k, _ in y.COMPANY_WIDE]
    rate_keys = {(a["argumanlar"]["para_birimi"], str(a["anahtarlar"][0]["deger"]))
              for s in questions for a in s.get("alternatifler", []) if a["arac"] == "doviz_kuru"}
    names = set()
    for file in ("musteriler.csv", "tedarikciler.csv"):
        with open(ROOT / "data" / "uretilen" / file, encoding="utf-8") as f:
            names |= {normalize(r["ad"]) for r in csv.DictReader(f)}
    chunk_texts = {p.metin for p in library.to_chunks(library.read_documents(ROOT / "data" / "belge" / "belgeler.jsonl"))}
    d = EvaluationData(patterns=patterns, questions=[s["soru"] for s in questions], identifiers=set(entities),
                      names=names, article_texts=chunk_texts, rates=rate_keys)
    r = audit(training_questions, d)
    return {"egitim_sorusu": len(training_questions), "en_yakin_kalip_benzerligi": round(r.closest_pattern, 3),
            "en_yakin_soru_benzerligi": round(r.closest_question, 3),
            "ihlal_sayisi": {k: len(v) for k, v in r.violations.items()}, "ihlaller": r.violations, "temiz": r.clean}


def phase7_similarity(questions: list[dict], phase7: dict) -> dict:
    phase7_patterns = [k for sb in f7.TEMPLATES for k in sb.patterns]
    phase7_patterns += [k for ks in f7.FORBIDDEN_PATTERNS.values() for k in ks] + list(f7.OTHER_CUSTOMER_PATTERNS)
    phase7_patterns += [m for m, _, _ in f7.ROLE_CLAIM_PATTERNS + f7.INJECTION_PATTERNS]
    phase7_skeletons = {skeleton(k): k for k in phase7_patterns}
    pattern_rows = []
    for k in [k for sb, _, _ in y.TEMPLATES for k in sb.patterns] + [k for k, _ in y.COMPANY_WIDE]:
        i = skeleton(k)
        score, closest = max(((1.0 if i == di else jaccard(i, di)), dk) for di, dk in phase7_skeletons.items())
        pattern_rows.append({"kalip": k, "faz7": closest, "benzerlik": round(score, 3)})
    pattern_rows.sort(key=lambda x: -x["benzerlik"])
    phase7_questions = [s["soru"] for s in phase7["sorular"]]
    question_rows = []
    for s in questions:
        b, closest = max((question_similarity(s["soru"], f), f) for f in phase7_questions)
        question_rows.append({"soru_id": s["soru_id"], "soru": s["soru"], "faz7": closest, "benzerlik": round(b, 3)})
    question_rows.sort(key=lambda x: -x["benzerlik"])
    return {"en_yakin_kaliplar": pattern_rows[:8], "en_yakin_sorular": question_rows[:8],
            "kalip_0_6_ustu": sum(x["benzerlik"] >= 0.6 for x in pattern_rows),
            "soru_0_8_ustu": sum(x["benzerlik"] >= 0.8 for x in question_rows)}


def customer_hint(questions: list[dict]) -> dict:
    customer_questions = [s for s in questions if s.get("diger_musteri")]
    other_questions = [s for s in questions if not s.get("diger_musteri")]
    missed = [s["soru_id"] for s in customer_questions if not is_customer_question(s["soru"])]
    false_alarms = [{"soru_id": s["soru_id"], "soru": s["soru"]} for s in other_questions if is_customer_question(s["soru"])]
    return {"musteri_sorusu": len(customer_questions), "yakalanan": len(customer_questions) - len(missed), "kacan": missed,
            "oteki_soru": len(other_questions), "yanlis_alarm": false_alarms,
            "not": "Kalıpların çoğu doğrulayıcının ipucunu (\"müşterisi\", \"firması\", şirket türü) içerir; bu yüzden "
                   "yakalama oranı kuralın bağımsız bir sınaması değildir, yalnızca bu setteki kapsamını gösterir."}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    clock = read_clock(ROOT / "data" / "saat.json")
    phase7 = json.loads(PHASE7_SET.read_text(encoding="utf-8"))
    training_questions = json.loads(TRAINING_QUESTIONS.read_text(encoding="utf-8"))["sorular"]
    excluded = set(phase7["varliklar"]["gelistirme"]) | set(phase7["varliklar"]["saklanan"])
    with tempfile.TemporaryDirectory() as temp_dir:
        o = y.NewSampler(ROOT / "data" / "ambar" / "sayman.duckdb", clock.simdi, Path(temp_dir), excluded)
        try:
            questions = y.question_set(o, {s["niyet"] for s in training_questions})
            entities = sorted(o.used_entities[y.NEW_SET])
            skipped = o.skipped_company_wide
        finally:
            o.close()
    question_ids = [s["soru_id"] for s in questions]
    assert len(question_ids) == len(set(question_ids)), "soru kimlikleri tekil olmalı"
    assert not set(entities) & excluded, "Faz 7 varlıkları yeni sette olmamalı"
    sz = leakage_check(questions, entities, training_questions)
    print("sızıntı:", json.dumps({k: v for k, v in sz.items() if k != "ihlaller"}, ensure_ascii=False))
    if not sz["temiz"]:
        print(json.dumps(sz["ihlaller"], ensure_ascii=False, indent=1))
        raise SystemExit("sızıntı var; set yazılmadı")
    data = {
        "aciklama": "Faz 8b yeni test seti. Hiçbir model (kâhin dahil) çalıştırılmadan önce üretildi ve donduruldu; "
                    "hedefler ve ön kayıt aynı anda yazıldı. Üretim: scripts/phase08b_generate_questions.py, şablonlar: "
                    "tests/phase08b_questions.py.",
        "yazilma_tarihi": y.WRITTEN_ON, "simulasyon_gunu": clock.simdi.isoformat(),
        "ozet": f7.digest(questions),
        "sayilar": {"grup": dict(sorted(Counter(s["grup"] for s in questions).items())),
                    "grup_rol": {f"{g}/{r}": n for (g, r), n in sorted(Counter((s["grup"], s["rol"])
                                                                              for s in questions).items())},
                    "egitimde": dict(Counter((s["grup"] + "/" + s["egitimde"]) for s in questions)),
                    "niyet": dict(Counter(s["niyet"] for s in questions))},
        "sablonlar": {sb.name: {"grup": g, "niyet": n, "adet": sb.question_count, "kaliplar": list(sb.patterns)}
                      for sb, g, n in y.TEMPLATES},
        "tum_sirket_kaliplari": [k for k, _ in y.COMPANY_WIDE], "tum_sirket_atlanan": skipped,
        "hedefler": y.TARGETS, "hata_turleri": f7.ERROR_KINDS, "on_kayit": y.PRE_REGISTRATION,
        "denetimler": {"sizinti": sz, "faz7_benzerligi": phase7_similarity(questions, phase7), "musteri_ipucu": customer_hint(questions)},
        "varliklar": {y.NEW_SET: entities},
        "sorular": questions,
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(data["sayilar"], ensure_ascii=False))
    d = data["denetimler"]
    print("Faz 7 benzerliği:", {k: v for k, v in d["faz7_benzerligi"].items() if not k.startswith("en_yakin")},
          d["faz7_benzerligi"]["en_yakin_kaliplar"][:3], d["faz7_benzerligi"]["en_yakin_sorular"][:3])
    print("ipucu:", {k: v for k, v in d["musteri_ipucu"].items() if k != "not"})
    print("özet", data["ozet"])


if __name__ == "__main__":
    main()
