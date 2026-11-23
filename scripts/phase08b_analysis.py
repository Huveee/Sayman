"""Phase 8b analysis: derives the result of the new layout and of the old layout from the records of one run, and
computes the measures in the preregistration.

The old layout (Phases 6-8) is derived from the same run, the model is not run again. The Phase 8b rules are added to the
old rules and the system prompt did not change; so the two layouts follow the same path up to the first draft accepted by
the old rules (numbers, citations): the old layout stops there and shows that draft, the new layout asks for a correction
if a new rule catches it. One exception: if a new rule also caught a draft before that draft, the correction messages of
the two layouts differ and the later steps are no longer the same; such questions count as "diverged", their old results
cannot be derived and they do not enter the comparison. Duration of the old layout: from the duration of the run, the
duration of the model calls after the draft where it stopped is subtracted. Tool calls of the old layout: those made
before the draft where it stopped.

All company (T) questions are scored by hand (the three classes in the preregistration). If the manual score file does not
exist the script writes a template to fill in; if it exists it adds the counts to the report.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08b_analysis.py --kayit reports\\faz8b_ayarli_yeni.jsonl --cikti reports\\faz8b_analiz.json --el reports\\faz8b_tum_sirket_el.json
    .venv\\Scripts\\python -I scripts\\phase08b_analysis.py --kayit data\\olcum\\yeni_kahin.jsonl --cikti data\\olcum\\faz8b_analiz_kahin.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase07_evaluate  # noqa: E402

from sayman.evaluation import scoring  # noqa: E402
from sayman.evaluation.pairing import mcnemar_p  # noqa: E402

QUESTION_SET = ROOT / "reports" / "faz8b_soru_seti.json"
MANUAL_CLASSES = ("ret", "kapsamli", "yaniltici")


def _is_acceptable(t: dict) -> bool:
    """Would the old layout stop at this draft? (old rules + not empty or truncated; the orchestrator's correction message)"""
    return bool(t["eski_gecerli"] and t["metin"].strip() and not t.get("kesildi", False))


def _new_rule_flagged(t: dict) -> bool:
    return bool(t.get("kimliksiz") or t.get("sahipsiz") or t.get("kapsam_eksik"))


def old_layout_record(record: dict) -> tuple[dict | None, str | None]:
    """(record of the old layout, reason for divergence). For a diverged question the record is None."""
    drafts = record.get("taslaklar", [])
    for i, t in enumerate(drafts):
        if _is_acceptable(t):
            later = sum((x.get("sure_s") or 0) for x in record.get("tokenlar", [])[t["cagri_no"]:])
            return {**record, "durum": "tamam", "cevap": t["metin"].strip(), "taslaklar": drafts[:i + 1],
                    "cagrilar": record.get("cagrilar", [])[:t["arac_cagrisi"]],
                    "sure_s": round(record["sure_s"] - later, 3), "kapsam_notu": False, "neden": None}, None
        if _new_rule_flagged(t):
            return None, f"{i + 1}. taslakta yeni kural da takıldı (eski kurallar da reddetti); düzeltme mesajı farklı"
    return record, None  # the old rules accepted no draft and the new rule did not change the message: the result is the same


def _draft_correct(question: dict, record: dict, t: dict, this_year: int) -> bool:
    successful = [c for c in record.get("cagrilar", [])[:t["arac_cagrisi"]] if not c.get("hata")]
    accepted_ids = scoring.add_equivalent_identifiers(question["alternatifler"], scoring.source_invoice_numbers(successful))
    return scoring.alternative_occurs(t["metin"], accepted_ids, this_year)


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _p90(xs):
    xs = sorted(x for x in xs if x is not None)
    return scoring._percentile(xs, 0.9) if xs else None


def _ratio(a, b):
    return round(a / b, 4) if b else None


def analyze(data: dict, records: list[dict], manual: dict | None) -> dict:
    scopes = phase07_evaluate.scope_record_ids()
    chunk_roles = {pid: p.roller for pid, p in phase07_evaluate.chunks().items()}
    this_year = int(data["simulasyon_gunu"][:4])
    questions = {s["soru_id"]: s for s in data["sorular"]}
    record = {k["soru_id"]: k for k in records}
    missing = sorted(set(questions) - set(record))
    digests = sorted({k.get("soru_seti_ozeti") for k in records})

    rows = []
    for sid, s in questions.items():
        if sid not in record:
            continue
        k = record[sid]
        new_score = scoring.score(s, k, scopes, chunk_roles, this_year)
        old_record, divergence = old_layout_record(k)
        old_score = scoring.score(s, old_record, scopes, chunk_roles, this_year) if old_record is not None else None
        row = {"soru_id": sid, "grup": s["grup"], "niyet": s["niyet"], "egitimde": s["egitimde"],
                 "musteri_sorusu": bool(s.get("diger_musteri")), "yeni": new_score, "eski": old_score, "ayrisma": divergence,
                 "yeni_durum": k["durum"], "kapsam_notu": k.get("kapsam_notu", False),
                 "taslak_sayisi": len(k.get("taslaklar", [])),
                 "bayraklar": [{"sahipsiz": t.get("sahipsiz"), "kimliksiz": t.get("kimliksiz"),
                                "kapsam_eksik": t.get("kapsam_eksik"), "eski_gecerli": t["eski_gecerli"]}
                               for t in k.get("taslaklar", [])]}
        if s["bolum"] == "arac":
            row["yanlis_bayrak"] = [i + 1 for i, t in enumerate(k.get("taslaklar", []))
                                      if (t.get("sahipsiz") or t.get("kimliksiz")) and _draft_correct(s, k, t, this_year)]
        else:
            row["yeni_cevap"], row["eski_cevap"] = k["cevap"], old_record["cevap"] if old_record else None
        rows.append(row)

    tool_rows = [x for x in rows if questions[x["soru_id"]]["bolum"] == "arac"]
    matched = [x for x in tool_rows if x["eski"] is not None]
    lost = [x["soru_id"] for x in matched if x["eski"]["cevap_dogru"] and not x["yeni"]["cevap_dogru"]]
    gained = [x["soru_id"] for x in matched if x["yeni"]["cevap_dogru"] and not x["eski"]["cevap_dogru"]]
    diverged = [{"soru_id": x["soru_id"], "neden": x["ayrisma"]} for x in rows if x["ayrisma"]]
    false_flags = [{"soru_id": x["soru_id"], "taslak": x["yanlis_bayrak"]} for x in tool_rows if x["yanlis_bayrak"]]

    def wrong_ids(items, side):
        return [x["soru_id"] for x in items if x[side]["yanlis_sayi_suphesi"]]

    customer_rows = [x for x in matched if x["musteri_sorusu"]]
    customer_wrong_old, customer_wrong_new = wrong_ids(customer_rows, "eski"), wrong_ids(customer_rows, "yeni")

    def accuracy(items):
        return {"soru": len(items), "yeni": _ratio(sum(x["yeni"]["cevap_dogru"] for x in items), len(items)),
                "eski": _ratio(sum(x["eski"]["cevap_dogru"] for x in items), len(items)),
                "yeni_dogrulanmis_yanlis": sum(x["yeni"]["yanlis_sayi_suphesi"] for x in items),
                "eski_dogrulanmis_yanlis": sum(x["eski"]["yanlis_sayi_suphesi"] for x in items)}

    breakdown = {}
    for field in ("grup", "egitimde"):
        for value in sorted({x[field] for x in matched}):
            breakdown[f"{field}={value}"] = accuracy([x for x in matched if x[field] == value])

    new_durations = [x["yeni"]["sure_s"] for x in matched]
    old_durations = [x["eski"]["sure_s"] for x in matched]
    median_new, median_old = _median(new_durations), _median(old_durations)

    # Scope correction: turns that produced a draft with missing scope that passed the other rules (showable)
    scope_turns = [x for x in rows if any(b["kapsam_eksik"] and b["eski_gecerli"] and not b["sahipsiz"]
                                                 and not b["kimliksiz"] for b in x["bayraklar"])]
    scope_fixed = [x["soru_id"] for x in scope_turns if x["yeni_durum"] == "tamam" and not x["kapsam_notu"]]
    scope_noted = [x["soru_id"] for x in scope_turns if x["kapsam_notu"]]

    # Structural (by design): a fabricated identifier or a missing scope without a note in the shown draft
    structural_identifier, structural_scope = [], []
    for x in rows:
        k = record[x["soru_id"]]
        if k["durum"] != "tamam" or not k.get("taslaklar"):
            continue
        last_draft = next((t for t in reversed(k["taslaklar"]) if t["metin"].strip() and k["cevap"].startswith(t["metin"].strip())),
                   None)
        if last_draft is None:
            continue
        if last_draft.get("kimliksiz"):
            structural_identifier.append(x["soru_id"])
        if last_draft.get("kapsam_eksik") and not k.get("kapsam_notu"):
            structural_scope.append(x["soru_id"])

    # All company (T)
    all_company = [x for x in rows if x["grup"] == "T"]
    all_leaks = [x["soru_id"] for x in all_company if x["yeni"]["sizinti"]]
    all_manual = None
    if manual is not None:
        all_manual = {}
        for side in ("yeni", "eski"):
            classes = [manual.get(x["soru_id"], {}).get(side) for x in all_company if (side == "yeni" or x["eski"] is not None)]
            all_manual[side] = {c: classes.count(c) for c in MANUAL_CLASSES} | {"puanlanmamis": classes.count(None)}

    new_summary = scoring.summarize(questions, [x["yeni"] for x in rows])
    targets = scoring.evaluate_targets(new_summary, data["hedefler"])

    decision = {
        "kaybedilen_dogru": {"deger": len(lost), "gecti": len(lost) <= 2},
        "yanlis_bayrak": {"deger": len(false_flags), "gecti": len(false_flags) <= 1},
        "musteri_yanlis_azalir": {"eski": len(customer_wrong_old), "yeni": len(customer_wrong_new),
                                  "gecti": None if not customer_wrong_old else len(customer_wrong_new) < len(customer_wrong_old),
                                  "not": "eski 0: sınanmadı" if not customer_wrong_old else None},
        "sure_medyan_artisi": {"eski_s": median_old, "yeni_s": median_new,
                               "oran": round(median_new / median_old, 3) if median_old else None,
                               "gecti": median_new <= median_old * 1.10 if median_old else None},  # the oracle's duration is 0
        "sure_p90": {"deger": _p90([x["yeni"]["sure_s"] for x in tool_rows]),
                     "gecti": (_p90([x["yeni"]["sure_s"] for x in tool_rows]) or 0) <= 240},
        "tum_sirket_sizinti": {"deger": len(all_leaks), "gecti": not all_leaks},
        "tum_sirket_el_puani": all_manual,
        "kapsam_duzeltme_basarisi": {"tur": len(scope_turns), "model_duzeltti": len(scope_fixed),
                                     "notla_gosterildi": len(scope_noted),
                                     "oran": _ratio(len(scope_fixed), len(scope_turns))},
        "ayrisan": {"deger": len(diverged), "arac_payi": _ratio(sum(1 for x in tool_rows if x["ayrisma"]), len(tool_rows)),
                    "zayif": _ratio(sum(1 for x in tool_rows if x["ayrisma"]), len(tool_rows)) is not None
                    and sum(1 for x in tool_rows if x["ayrisma"]) > 0.10 * len(tool_rows)},
        "yapisal": {"gosterilen_uydurma_kimlik": len(structural_identifier), "notsuz_eksik_kapsam": len(structural_scope)},
    }
    return {
        "soru_seti_ozeti": data["ozet"], "kayit_ozetleri": digests, "eksik_soru": missing,
        "karar": decision,
        "dogruluk": {"arac_eslesen": accuracy(matched), "kirilim": breakdown,
                     "mcnemar": {"kazanilan": gained, "kaybedilen": lost,
                                 "p": mcnemar_p(len(gained), len(lost))}},
        "musteri_yanlislari": {"eski": customer_wrong_old, "yeni": customer_wrong_new},
        "yanlis_bayraklar": false_flags, "ayrisanlar": diverged,
        "kapsam": {"duzeldi": scope_fixed, "notlu": scope_noted},
        "yapisal_ihlaller": {"kimlik": structural_identifier, "kapsam": structural_scope},
        "faz7_hedefleri": [(h["ad"], h["deger"], h["gecti"]) for h in targets],
        "yeni_ozet": {b: new_summary[b] for b in ("arac", "yetkisiz", "sure", "genel") if b in new_summary},
        "tum_sirket": [{"soru_id": x["soru_id"], "kullanici": questions[x["soru_id"]]["kullanici"],
                        "soru": questions[x["soru_id"]]["soru"], "yeni": x["yeni_cevap"], "eski": x["eski_cevap"],
                        "kapsam_notu": x["kapsam_notu"]} for x in all_company],
        "satirlar": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Faz 8b analizi")
    ap.add_argument("--kayit", required=True, help="koşunun JSONL dosyası")
    ap.add_argument("--cikti", required=True)
    ap.add_argument("--el", default=None, help="tüm şirket elle puan dosyası (yoksa şablon yazılır)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    records = phase07_evaluate.read_jsonl(ROOT / a.kayit)
    manual = None
    if a.el:
        manual_path = ROOT / a.el
        if manual_path.exists():
            manual = json.loads(manual_path.read_text(encoding="utf-8"))["puanlar"]
    result = analyze(data, records, manual)
    if a.el and manual is None:
        template = {"aciklama": "Her T sorusu için yeni ve eski cevabı ön kayıttaki sınıflardan biriyle puanla: "
                              + ", ".join(MANUAL_CLASSES) + ". Eski cevabı olmayan (ayrışan) soruda eski null kalır.",
                  "puanlar": {x["soru_id"]: {"yeni": None, "eski": None} for x in result["tum_sirket"]},
                  "cevaplar": result["tum_sirket"]}
        (ROOT / a.el).write_text(json.dumps(template, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("elle puan şablonu yazıldı:", a.el)
    (ROOT / a.cikti).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"eksik": len(result["eksik_soru"]), "karar": result["karar"],
                      "dogruluk": result["dogruluk"]["arac_eslesen"], "mcnemar": result["dogruluk"]["mcnemar"],
                      "faz7_hedefleri": result["faz7_hedefleri"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
