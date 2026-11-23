"""Phase 8b identifier measurement: generates the question set, checks it and freezes it (before any model, the oracle included, is run).

Set: tests/support/phase08b_identifier_questions.py (groups U, Y, D; the preregistration is in the same file).

Left out (excluded): the entities of the two Phase 7 sets and of the new Phase 8b set, the pilot's customers and numbers
(data/olcum/faz8b_kimlik_pilot_seti.json, development data) and the correct answers of the pilot questions (found from
the database).

Checks (the results are written to the file; if there is a violation the file is not written):
- Leakage: the Phase 8 check (pattern, question, identifier, name) between the Phase 8 training data (data/egitim/sorular.json)
  and this set. All the correct identifiers go into the identifier check together with the entities.
- Overlap: in the questions other than sinir_ustu the correct identifiers and entities do not overlap with the excluded ones.
  The overlap of the sinir_ustu lists is counted and written.
- Similarity: the closest similarity of every question to the pilot questions and to the Phase 7 / Phase 8b questions (for
  information).

Output:  reports/faz8b_kimlik_soru_seti.json

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08b_generate_identifier_questions.py
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import support.phase07_questions as f7  # noqa: E402
import support.phase08b_identifier_questions as k  # noqa: E402

from sayman.warehouse import metrics  # noqa: E402
from sayman.training.leakage import EvaluationData, audit, normalize, question_similarity  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

OUTPUT = ROOT / "reports" / "faz8b_kimlik_soru_seti.json"
PHASE7_SET = ROOT / "reports" / "faz7_soru_seti.json"
PHASE8B_SET = ROOT / "reports" / "faz8b_soru_seti.json"
PILOT_SET = ROOT / "data" / "olcum" / "faz8b_kimlik_pilot_seti.json"
TRAINING_QUESTIONS = ROOT / "data" / "egitim" / "sorular.json"
WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"


def pilot_exclusions(v: k.FullWarehouse, pilot: dict) -> tuple[set[str], set[str], dict]:
    """(the pilot's entities and single answers, the pilot's P2 list, description). The pilot questions were written by
    hand; their answers are found here from the database."""
    text = " ".join(s["soru"] for s in pilot["sorular"])
    customers = {mid for mid, m in v.customer_info_by_id.items() if m["ad"] in text}
    q = v.query_rows
    related_invoice = q("SELECT iliskili_fatura_id FROM faturalar WHERE fatura_id = 'F035906'")[0][0]
    source_invoice = q("SELECT fatura_id FROM faturalar WHERE kaynak_fatura_no = '489440'")[0][0]
    p2 = [f["fatura_id"] for f in v.overdue_invoices(60)[:15]]
    p3 = [f["fatura_id"] for f in sorted(v.overdue_invoices(90), key=lambda f: f["acik_try_kurus"])[:1]]
    p5 = [r[0] for r in q("SELECT hareket_id FROM banka_hareketleri WHERE tarih BETWEEN '2026-03-01' AND '2026-03-31' "
                          "ORDER BY tutar_kurus LIMIT 3")]
    p6 = [r[0] for r in q("SELECT tahsilat_id FROM tahsilatlar WHERE tarih BETWEEN '2026-08-01' AND '2026-08-31' "
                          "ORDER BY tutar_kurus DESC LIMIT 3")]
    open_invoices = [f for f in metrics.overdue_receivables(v.con, v.d, en_az_gun=-10**6)["faturalar"]
            if f["musteri_id"] in customers]
    p7 = sorted(open_invoices, key=lambda f: (f["vade_tarihi"], f["fatura_id"]))[:3]
    payments = metrics.upcoming_payments(v.con, v.d, 60)["odemeler"]
    p8 = [o["alis_fatura_id"] for o in sorted(payments, key=lambda o: -o["acik_kurus"])[:3]]
    unique_ids = customers | {"F035906", related_invoice, source_invoice, "489440"} | set(p3) | set(p5) | set(p6) \
        | {f["fatura_id"] for f in p7} | set(p8)
    return unique_ids, set(p2), {"musteriler": sorted(customers), "tekil": sorted(unique_ids), "p2_listesi": p2}


def leakage_check(questions: list[dict], entities: set[str], training_questions: list[dict]) -> dict:
    names = set()
    for file in ("musteriler.csv", "tedarikciler.csv"):
        with open(ROOT / "data" / "uretilen" / file, encoding="utf-8") as f:
            names |= {normalize(r["ad"]) for r in csv.DictReader(f)}
    identifiers = set(entities) | {x for s in questions for x in s["dogru_kimlikler"]}
    d = EvaluationData(patterns=[s["soru"] for s in questions], questions=[s["soru"] for s in questions],
                      identifiers=identifiers, names=names, article_texts=set(), rates=set())
    r = audit(training_questions, d)
    return {"egitim_sorusu": len(training_questions), "denetlenen_kimlik": len(identifiers),
            "en_yakin_kalip_benzerligi": round(r.closest_pattern, 3),
            "en_yakin_soru_benzerligi": round(r.closest_question, 3),
            "ihlal_sayisi": {a: len(b) for a, b in r.violations.items()}, "ihlaller": r.violations, "temiz": r.clean}


def similarity_ranking(questions: list[dict], others: list[str]) -> list[dict]:
    result = []
    for s in questions:
        b, closest = max((question_similarity(normalize(s["soru"]), normalize(o)), o) for o in others)
        result.append({"soru_id": s["soru_id"], "soru": s["soru"], "en_yakin": closest, "benzerlik": round(b, 3)})
    return sorted(result, key=lambda x: -x["benzerlik"])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    clock = read_clock(ROOT / "data" / "saat.json")
    phase7 = json.loads(PHASE7_SET.read_text(encoding="utf-8"))
    phase8b = json.loads(PHASE8B_SET.read_text(encoding="utf-8"))
    pilot = json.loads(PILOT_SET.read_text(encoding="utf-8"))
    training_questions = json.loads(TRAINING_QUESTIONS.read_text(encoding="utf-8"))["sorular"]
    previous = set(phase7["varliklar"]["gelistirme"]) | set(phase7["varliklar"]["saklanan"]) | set(phase8b["varliklar"]["yeni"])
    v = k.FullWarehouse(WAREHOUSE, clock.simdi, set())
    try:
        pilot_unique, pilot_p2, pilot_description = pilot_exclusions(v, pilot)
        v.excluded_entities = previous | pilot_unique | pilot_p2
        questions = k.question_set(v)
        entities = sorted(v.used_entities)
    finally:
        v.close()

    identifiers = [s["soru_id"] for s in questions]
    assert len(identifiers) == len(set(identifiers)), "soru kimlikleri tekil olmalı"
    excluded = previous | pilot_unique | pilot_p2
    violations = [{"soru_id": s["soru_id"], "ortak": sorted((set(s["dogru_kimlikler"]) | set(s.get("varliklar", []))) & excluded)}
             for s in questions if s["mekanizma"] != "sinir_ustu"
             and (set(s["dogru_kimlikler"]) | set(s.get("varliklar", []))) & excluded]
    over_limit_rows = [{"soru_id": s["soru_id"], "dogru": len(s["dogru_kimlikler"]),
              "faz7_faz8b": len(set(s["dogru_kimlikler"]) & previous),
              "pilot_p2": len(set(s["dogru_kimlikler"]) & pilot_p2)} for s in questions if s["mekanizma"] == "sinir_ustu"]
    print("örtüşme ihlali:", violations)
    print("sinir_ustu örtüşmesi:", over_limit_rows)
    if violations:
        raise SystemExit("dışarıda tutulan kayıtlarla örtüşme var; set yazılmadı")
    sz = leakage_check(questions, set(entities), training_questions)
    print("sızıntı:", json.dumps({a: b for a, b in sz.items() if a != "ihlaller"}, ensure_ascii=False))
    if not sz["temiz"]:
        print(json.dumps(sz["ihlaller"], ensure_ascii=False, indent=1))
        raise SystemExit("sızıntı var; set yazılmadı")
    pilot_similarity = similarity_ranking(questions, [s["soru"] for s in pilot["sorular"]])
    previous_similarity = similarity_ranking(questions, [s["soru"] for s in phase7["sorular"]] + [s["soru"] for s in phase8b["sorular"]])
    data = {
        "aciklama": "Faz 8b kimlik ölçümü soru seti. Hiçbir model (kâhin dahil) çalıştırılmadan önce üretildi ve "
                    "donduruldu; ön kayıt aynı anda yazıldı. Üretim: scripts/phase08b_generate_identifier_questions.py, sorular: "
                    "tests/phase08b_identifier_questions.py, analiz: scripts/phase08b_identifier_analysis.py.",
        "yazilma_tarihi": k.WRITTEN_ON, "dondurma_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "simulasyon_gunu": clock.simdi.isoformat(),
        "ozet": f7.digest(questions),
        "sayilar": {"grup": dict(sorted(Counter(s["grup"] for s in questions).items())),
                    "mekanizma": dict(Counter(f"{s['grup']}/{s['mekanizma']}" for s in questions)),
                    "rol": dict(Counter(s["rol"] for s in questions))},
        "hedefler": [], "on_kayit": k.PRE_REGISTRATION,
        "denetimler": {"sizinti": sz, "ortusme": {"ihlal": violations, "sinir_ustu": over_limit_rows, "pilot": pilot_description},
                       "pilot_benzerligi": pilot_similarity[:8], "faz7_faz8b_benzerligi": previous_similarity[:8]},
        "varliklar": {k.IDENTIFIER_SET: entities},
        "sorular": questions,
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(data["sayilar"], ensure_ascii=False))
    print("pilota en yakın:", [(x["soru_id"], x["benzerlik"]) for x in pilot_similarity[:5]])
    print("Faz 7/8b'ye en yakın:", [(x["soru_id"], x["benzerlik"], x["en_yakin"]) for x in previous_similarity[:3]])
    print("özet", data["ozet"])


if __name__ == "__main__":
    main()
